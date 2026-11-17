"""The secret store: secrets/ as one age-encrypted file, opened into tmpfs.

secrets.age is a tar of what secrets/ holds; secrets.recipients lists the public
keys it is encrypted to, one per way of opening it. While it is open, `secrets`
in the fleet directory is a symlink into $XDG_RUNTIME_DIR, so everything that
reads or writes secrets/ goes on working unchanged, and every command saves what
it changed before it exits: a disk key that exists only in tmpfs would be lost
with the next power cut. Nothing but age and tar is needed to open it by hand.

Design: docs/design/control-plane.md, Secrets.
"""
import base64
import getpass
import glob
import hashlib
import hmac
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

from . import util
from .util import KiwiError

FILE = "secrets.age"
RECIPIENTS = "secrets.recipients"
_BREW = "/home/linuxbrew/.linuxbrew/bin"     # not on PATH for a desktop launcher or a timer
_KEYRING = ("service", "kiwi-server", "store")


def _tool(name):
    path = shutil.which(name) or shutil.which(name, path=_BREW)
    if not path:
        raise KiwiError("%s is not installed: brew install age, or your distribution's age package" % name)
    return path


COPY_KEY = "copy-auth.key"     # in secrets/: what signs the network's copies (backup.py)


def copy_key_from(identity):
    """The key that signs a network's copies, from its recovery key: whoever restores with that
    key can check a copy is the network's own, and a computer that opens the store can sign."""
    # as age reads it: one key in capitals or in small letters (bech32), around it nothing
    return hashlib.sha256(b"kiwi-server copy key 1\0" + str(identity).strip().upper().encode()).hexdigest()


def recovery_public(recipients_text):
    """The recovery key's public half, as a store's list of keys names it, or ""."""
    lines = [l.strip() for l in str(recipients_text).splitlines()]
    for i, line in enumerate(lines):
        if line.lower().startswith("# recovery") and i + 1 < len(lines):
            return lines[i + 1]
    return ""


def keygen():
    """(secret key, public key) of a new age identity."""
    out = subprocess.run([_tool("age-keygen")], capture_output=True, text=True, check=True).stdout
    secret = next(line for line in out.splitlines() if line.startswith("AGE-SECRET-KEY-"))
    public = next(line.split(": ", 1)[1] for line in out.splitlines() if line.startswith("# public key:"))
    return secret, public


# ---- a key from a password: how the control node opens the store ---------------------------
# No hash and no wrapped key on disk: the password and a salt make an age identity (scrypt),
# its public key is one more recipient of secrets.age, and the password is right when the
# store opens. Whoever holds the disk has to guess against scrypt (64 MiB per guess).
_BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
SCRYPT = {"n": 2 ** 16, "r": 8, "p": 1}


def _bech32(hrp, data):
    def polymod(values):
        chk = 1
        for v in values:
            top = chk >> 25
            chk = (chk & 0x1ffffff) << 5 ^ v
            for i, g in enumerate((0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3)):
                chk ^= g if (top >> i) & 1 else 0
        return chk
    acc, bits, five = 0, 0, []
    for b in data:
        acc, bits = (acc << 8) | b, bits + 8
        while bits >= 5:
            bits -= 5
            five.append((acc >> bits) & 31)
    if bits:
        five.append((acc << (5 - bits)) & 31)
    expand = [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]
    mod = polymod(expand + five + [0] * 6) ^ 1
    check = [(mod >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(_BECH32[d] for d in five + check)


def password_identity(password, salt_hex):
    """The age secret key a password and a salt make."""
    raw = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), dklen=32, maxmem=2 ** 28, **SCRYPT)
    return _bech32("age-secret-key-", raw).upper()


def public_of(identity):
    r = subprocess.run([_tool("age-keygen"), "-y"], input=identity + "\n", capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.startswith("age1"):
        raise KiwiError("age-keygen did not take the key")
    return r.stdout.strip()


def encrypt_bytes(recipients_file, data):
    """Data for the keys listed in a recipients file; needs no secret key."""
    r = subprocess.run([_tool("age"), "-e", "-R", recipients_file], input=data, capture_output=True)
    if r.returncode != 0:
        raise KiwiError("age could not encrypt: %s" % r.stderr.decode(errors="replace").strip()[:200])
    return r.stdout


def encrypt_to(public, data):
    """Data for one public key."""
    r = subprocess.run([_tool("age"), "-e", "-r", public], input=data, capture_output=True)
    if r.returncode != 0:
        raise KiwiError("age could not encrypt: %s" % r.stderr.decode(errors="replace").strip()[:200])
    return r.stdout


def decrypt_file(identity, path):
    r = subprocess.run([_tool("age"), "-d", "-i", "-", path], input=identity.encode() + b"\n", capture_output=True)
    if r.returncode != 0:
        raise KiwiError("that key does not open %s" % os.path.basename(path))
    return r.stdout


def snapshot(root):
    """The tree as a tar, byte for byte the same while the tree is the same."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for here, dirs, files in os.walk(root):
            dirs.sort()
            for name in sorted(dirs + files):
                if name == ".kiwi-lock":        # a writer's lock (util.dir_lock), not a secret
                    continue
                path = os.path.join(here, name)
                info = tar.gettarinfo(path, arcname=os.path.relpath(path, root))
                info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, "", ""
                if info.isreg():
                    with open(path, "rb") as f:
                        tar.addfile(info, f)
                else:
                    tar.addfile(info)
    return buf.getvalue()


def extract(data, target):
    """A store's tar into a directory. A store is encrypted for public keys, which anyone can
    read: a file that opens is not proof of who made it. So nothing in it may write outside
    the directory, not by its name and not through a link an earlier entry made."""
    os.makedirs(target, mode=0o700, exist_ok=True)
    root = os.path.realpath(target)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
        for m in tar.getmembers():
            if os.path.isabs(m.name) or ".." in m.name.split("/"):
                raise KiwiError("the store holds a path that is not allowed: %s" % m.name)
            dest = os.path.join(target, m.name)
            if os.path.commonpath([root, os.path.realpath(os.path.dirname(dest))]) != root or (
                    os.path.islink(dest) and not m.issym()):
                raise KiwiError("the store holds a path that leaves it through a link: %s" % m.name)
            if m.isdir():
                os.makedirs(dest, mode=0o700, exist_ok=True)
            elif m.issym():
                os.symlink(m.linkname, dest)
            elif m.isreg():
                os.makedirs(os.path.dirname(dest), mode=0o700, exist_ok=True)
                fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as out:
                    shutil.copyfileobj(tar.extractfile(m), out)
                os.chmod(dest, m.mode & 0o777)
            else:
                raise KiwiError("the store holds something that is not a file: %s" % m.name)


def keyring_put(store_id, secret):
    tool = shutil.which("secret-tool")
    if not tool:
        return False
    try:
        r = subprocess.run([tool, "store", "--label=kiwi-server secret store %s" % store_id, *_KEYRING, store_id],
                           input=secret, text=True, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        return False
    return r.returncode == 0


def keyring_get(store_id):
    tool = shutil.which("secret-tool")
    if not tool:
        return None
    try:
        r = subprocess.run([tool, "lookup", *_KEYRING, store_id], text=True, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        return None
    return (r.stdout.strip() or None) if r.returncode == 0 else None


class Store:
    def __init__(self, fleet_dir):
        self.dir = os.path.abspath(fleet_dir)
        self.file = os.path.join(self.dir, FILE)
        self.recipients = os.path.join(self.dir, RECIPIENTS)
        self.link = os.path.join(self.dir, "secrets")

    @classmethod
    def for_fleet(cls, fleet_path):
        return cls(os.path.dirname(os.path.abspath(fleet_path)))

    def exists(self):
        return os.path.isfile(self.file)

    def store_id(self):
        """What each computer's keyring files its key under: the hash of the first key, or the
        `# store:` line written once that key had to go."""
        with open(self.recipients) as f:
            lines = f.read().splitlines()
        for line in lines:
            if line.startswith("# store: "):
                return line.split(":", 1)[1].strip()
        keys = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
        return hashlib.sha256(keys[0].encode()).hexdigest()[:16]

    # ---- who can open it: one key per computer, plus the recovery key ----------------------

    def ways(self):
        """[(name, public key)] in the order of secrets.recipients."""
        out, name = [], ""
        with open(self.recipients) as f:
            for line in f.read().splitlines():
                if line.startswith("# ") and "who can open" not in line and not line.startswith("# store: "):
                    name = line[2:].split("(")[0].strip()
                elif line.startswith("age1"):
                    out.append((name or "unnamed", line.strip()))
                    name = ""
        return out

    def _write_ways(self, ways, store_id):
        lines = ["# who can open secrets.age: one public key per way of unlocking it", "# store: %s" % store_id]
        for name, key in ways:
            note = "its secret key is in the keyring" if name == "desktop" else (
                "kept by the owner" if name == "recovery" else
                "the control node's password makes its secret key" if name == "control" else
                "its secret key is in that computer's keyring")
            lines += ["# %s (%s)" % (name, note), key]
        tmp = util.tmp_name(self.recipients)
        with open(tmp, "w") as f:
            f.write("\n".join(lines) + "\n")
        os.chmod(tmp, 0o644)
        util.settle(tmp, self.recipients)

    def _reencrypt(self):
        """For the keys now listed: from the open tree, after making sure nobody saved over it."""
        if not self.is_open():
            raise KiwiError("the store is locked: kiwi-server secrets unlock first")
        self._unchanged_on_disk()
        data = snapshot(self._tree())
        self._encrypt(data)
        with open(self._digest_file(), "w") as f:
            f.write(hashlib.sha256(data).hexdigest())

    def join(self):
        """A key for this computer, kept in its keyring; the public half goes to a computer that
        can open the store already (`add`). Refuses when this computer has a key for it."""
        if keyring_get(self.store_id()):
            raise KiwiError("this computer's keyring already holds a key for this store: it can open it, or joined before "
                            "and waits for `kiwi-server secrets add` on another computer")
        secret, public = keygen()
        if not keyring_put(self.store_id(), secret):
            raise KiwiError("no keyring took the key (secret-tool): a computer without one opens the store with its recovery key")
        return public

    def add(self, name, public):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,30}", name or ""):
            raise KiwiError("a name for the computer: lowercase letters, digits and dashes, e.g. laptop")
        if not re.fullmatch(r"age1[0-9a-z]{58}", public or ""):
            raise KiwiError("%r is not an age public key (age1…, as `kiwi-server secrets join` prints it)" % public)
        ways = self.ways()
        if any(n == name for n, _k in ways):
            raise KiwiError("%s can open the store already" % name)
        if any(k == public for _n, k in ways):
            raise KiwiError("that key opens the store already, as %s" % next(n for n, k in ways if k == public))
        sid = self.store_id()
        self._write_ways(ways + [(name, public)], sid)
        self._reencrypt()

    def new_recovery(self, keep=None, secret=None):
        """A new recovery key in place of the old one (lost, or seen by someone): the store is
        encrypted for it, the copies are signed with its copy key from now on. `keep(secret)` is
        asked first and must say it was kept (shown and typed back, or written to a file): nothing
        changes before. Returns its secret. Copies made before open with the old key, and only with it."""
        if not self.is_open():
            raise KiwiError("the store is locked: kiwi-server secrets unlock first (this computer's key opens it)")
        if secret:                  # made before and kept already (the app shows it, then hands it back)
            if not str(secret).startswith("AGE-SECRET-KEY-1"):
                raise KiwiError("that is not an age secret key")
            public = public_of(secret)
            if public == recovery_public(util.read_text(self.recipients)):
                raise KiwiError("that is the recovery key already")
        else:
            secret, public = keygen()
        if keep is not None and not keep(secret):
            raise KiwiError("nothing changed: the old recovery key is still the one")
        path = os.path.join(self._tree(), COPY_KEY)
        fd = os.open(path + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(copy_key_from(secret) + "\n")
        os.replace(path + ".new", path)
        ways = [(n, k) for n, k in self.ways() if n != "recovery"] + [("recovery", public)]
        self._write_ways(ways, self.store_id())
        self._reencrypt()
        data = snapshot(self._tree())
        if self._decrypt(secret) != data:
            raise KiwiError("the store did not open with the new recovery key: keep the old one, and say so")
        return secret

    def remove(self, name):
        ways = self.ways()
        keep = [(n, k) for n, k in ways if n != name]
        if len(keep) == len(ways):
            raise KiwiError("nothing called %s opens the store (%s)" % (name, ", ".join(n for n, _k in ways)))
        if not keep:
            raise KiwiError("%s is the last way to open the store" % name)
        sid = self.store_id()        # kept in the file, so the other computers' keyrings still find it
        self._write_ways(keep, sid)
        self._reencrypt()

    # ---- the file on disk: another computer may have saved it since it was opened ----------

    def _file_digest_path(self):
        return os.path.join(self.runtime(), "file-sha256")

    def _remember_file(self):
        with open(self.file, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        with open(self._file_digest_path(), "w") as f:
            f.write(digest)

    def _unchanged_on_disk(self):
        try:
            with open(self._file_digest_path()) as f:
                opened = f.read().strip()
        except OSError:
            return          # opened by an older release: nothing to compare with
        with open(self.file, "rb") as f:
            now = hashlib.sha256(f.read()).hexdigest()
        if now != opened:
            raise KiwiError("%s changed on disk since this computer opened it (another computer saved it): "
                            "nothing is saved over it. Lock, take the other copy, unlock again; what changed here "
                            "is still in %s until then" % (self.file, self._tree()))

    def runtime(self):
        base = os.environ.get("XDG_RUNTIME_DIR") or "/dev/shm"
        return os.path.join(base, "kiwi-server-%s" % self.store_id())

    def _tree(self):
        return os.path.join(self.runtime(), "secrets")

    def _digest_file(self):
        return os.path.join(self.runtime(), "digest")

    def _locks_at_file(self):
        return os.path.join(self.runtime(), "locks_at")

    def locks_at(self):
        """When the timer locks it again (epoch seconds), or None."""
        try:
            with open(self._locks_at_file()) as f:
                return int(f.read())
        except (OSError, ValueError):
            return None

    def is_open(self):
        return self.exists() and os.path.islink(self.link) and os.path.isdir(self.link)

    def locked(self):
        return self.exists() and not self.is_open()

    def _encrypt(self, data):
        tmp = util.tmp_name(self.file)
        r = subprocess.run([_tool("age"), "-e", "-R", self.recipients, "-o", tmp], input=data, capture_output=True)
        if r.returncode != 0:
            raise KiwiError("age could not encrypt the store: %s" % r.stderr.decode(errors="replace").strip()[:200])
        os.chmod(tmp, 0o600)
        util.settle(tmp, self.file)
        if os.path.isdir(self.runtime()):
            self._remember_file()

    def _decrypt(self, identity):
        r = subprocess.run([_tool("age"), "-d", "-i", "-", self.file], input=identity.encode() + b"\n", capture_output=True)
        if r.returncode != 0:
            raise KiwiError("that key does not open this store")
        return r.stdout

    def private_copy(self, identity):
        """The store decrypted into a directory of its own for one process: the shared link, and
        a session that opens or locks the store meanwhile, are not touched. Returns a handle for
        save_private."""
        data = self._decrypt(identity)
        base = os.environ.get("XDG_RUNTIME_DIR") or "/dev/shm"
        d = tempfile.mkdtemp(prefix="kiwi-private-", dir=base)
        tree = os.path.join(d, "secrets")
        extract(data, tree)
        with open(self.file, "rb") as f:
            on_disk = hashlib.sha256(f.read()).hexdigest()
        return {"dir": d, "tree": tree, "digest": hashlib.sha256(snapshot(tree)).hexdigest(), "file": on_disk}

    def save_private(self, copy):
        """What the process changed in its copy into the store, unless the file changed under it;
        the copy is removed either way."""
        try:
            data = snapshot(copy["tree"])
            if hashlib.sha256(data).hexdigest() == copy["digest"]:
                return False
            with open(self.file, "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() != copy["file"]:
                    raise KiwiError("%s changed on disk meanwhile: this run's changes are not saved" % self.file)
            self._encrypt(data)
            return True
        finally:
            shutil.rmtree(copy["dir"], ignore_errors=True)

    def _open(self, data):
        shutil.rmtree(self.runtime(), ignore_errors=True)
        os.makedirs(self.runtime(), mode=0o700)
        extract(data, self._tree())
        with open(self._digest_file(), "w") as f:
            f.write(hashlib.sha256(snapshot(self._tree())).hexdigest())
        with open(os.path.join(self.runtime(), "fleet-dir"), "w") as f:    # whose it is: lock_all saves it first
            f.write(self.dir)

    def init(self):
        """secrets/ into the store. Returns (recovery key, whether the keyring took the desktop key).
        The plaintext goes only after both keys have been seen to open the file."""
        if self.exists():
            raise KiwiError("%s already exists" % self.file)
        if os.path.islink(self.link) or not os.path.isdir(self.link):
            raise KiwiError("there is no secrets/ directory in %s" % self.dir)
        desktop, recovery = keygen(), keygen()
        fd = os.open(self.recipients, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "w") as f:
            f.write("# who can open secrets.age: one public key per way of unlocking it\n"
                    "# desktop (its secret key is in the keyring)\n%s\n# recovery\n%s\n" % (desktop[1], recovery[1]))
        fd = os.open(os.path.join(self.link, COPY_KEY), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:       # the copies' signature, checked with the recovery key
            f.write(copy_key_from(recovery[0]) + "\n")
        data = snapshot(self.link)
        try:
            self._encrypt(data)
            for secret, _public in (desktop, recovery):
                if self._decrypt(secret) != data:
                    raise KiwiError("the store did not decrypt to what went in; nothing was removed")
        except KiwiError:
            for leftover in (self.file, self.recipients):
                if os.path.exists(leftover):
                    os.remove(leftover)
            raise
        saved = keyring_put(self.store_id(), desktop[0])
        self._open(data)
        self._remember_file()
        aside = self.link + ".replaced"
        os.rename(self.link, aside)
        os.symlink(self._tree(), self.link)
        shutil.rmtree(aside)
        return recovery[0], saved

    def unlock(self, identity):
        self._open(self._decrypt(identity))
        self._remember_file()
        if os.path.islink(self.link):
            os.remove(self.link)
        elif os.path.exists(self.link):
            raise KiwiError("%s exists and is not the store's link: move it away first" % self.link)
        os.symlink(self._tree(), self.link)

    def sync(self):
        """Encrypt what changed. Needs only the public keys, so it works from any command.
        One at a time (a lock in the open store's RAM): two at once could write the older
        snapshot last while the digest says the newer one, and a secret written in between
        never reached secrets.age."""
        if not self.is_open():
            return False
        with util.dir_lock(self.runtime()):
            data = snapshot(self._tree())
            digest = hashlib.sha256(data).hexdigest()
            with open(self._digest_file()) as f:
                if f.read() == digest:
                    return False
            self._unchanged_on_disk()
            self._encrypt(data)
            with open(self._digest_file(), "w") as f:
                f.write(digest)
            return True

    def lock(self):
        self.sync()
        cancel_lock(self)
        if os.path.islink(self.link):
            os.remove(self.link)
        shutil.rmtree(self.runtime(), ignore_errors=True)

    password_given = None       # from --password-stdin (the app), else asked at the terminal

    def password(self):
        if Store.password_given is not None:
            return Store.password_given
        if not sys.stdin.isatty():
            raise KiwiError("this computer's key has a password: give it (the app asks; in a terminal it is asked)")
        return getpass.getpass("Password for this computer's key: ")

    def identity(self, path=None):
        """The key to open it with: a file named, the keyring, or typed."""
        if path:
            with open(path) as f:
                keys = [line.strip() for line in f if line.startswith("AGE-SECRET-KEY-")]
            if not keys:
                raise KiwiError("%s holds no age secret key" % path)
            return keys[0]
        key = keyring_get(self.store_id())
        if key and is_wrapped(key):         # this computer's key wants its password too
            return unwrap(key, self.password())
        if key:
            return key
        if not sys.stdin.isatty():
            raise KiwiError("no key in the keyring and nobody to ask: give the recovery key with --identity FILE")
        key = getpass.getpass("Recovery key (AGE-SECRET-KEY-...): ").strip()
        if not key:
            raise KiwiError("no key given")
        return key


# ---- locking by itself (docs/security.md): after a while unused, and with the screen -------

AUTOLOCK_DEFAULT = {"idle": "30m", "screen": True}


def _autolock_file():
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "kiwi-server", "autolock.json")


def autolock():
    """This computer's choice: {idle: a time like 30m, or 0 for never; screen: lock with the screen}."""
    try:
        data = json.loads(util.read_text(_autolock_file()))
    except (OSError, ValueError):
        data = {}
    return dict(AUTOLOCK_DEFAULT, **{k: v for k, v in (data if isinstance(data, dict) else {}).items() if k in AUTOLOCK_DEFAULT})


def set_autolock(**changes):
    now = autolock()
    if changes.get("idle") is not None and not re.fullmatch(r"\d+[smhd]?", str(changes["idle"])):
        raise KiwiError("--idle takes a time like 30m, 1h or 0 (never)")
    now.update({k: v for k, v in changes.items() if v is not None})
    os.makedirs(os.path.dirname(_autolock_file()), mode=0o700, exist_ok=True)
    util.write_text(_autolock_file(), json.dumps(now, indent=1) + "\n", 0o600)
    return now


def touch(store, fleet_path):
    """It is in use: it locks itself the idle time from now (not from when it was opened)."""
    idle = str(autolock()["idle"])
    try:            # the timer never stands in a command's way
        if store.is_open() and not re.fullmatch(r"0+[smhd]?", idle):
            schedule_lock(store, fleet_path, idle)
    except (KiwiError, OSError, AttributeError):
        pass


def open_stores():
    """The folders of the networks whose store is open on this computer now."""
    base = os.environ.get("XDG_RUNTIME_DIR") or "/dev/shm"
    out = []
    for d in sorted(glob.glob(os.path.join(base, "kiwi-server-*"))):
        try:
            out.append(util.read_text(os.path.join(d, "fleet-dir")).strip())
        except OSError:
            continue
    return [d for d in out if d]


def lock_all():
    """Every open store locked, what changed in it saved first. The names of those it locked."""
    done = []
    for d in open_stores():
        try:
            Store(d).lock()
            done.append(d)
        except (KiwiError, OSError) as e:
            util.warn("%s: not locked: %s" % (d, e))
    return done


SCREEN_SIGNALS = ("org.gnome.ScreenSaver", "org.freedesktop.ScreenSaver")


def screen_locked_lines(lines):
    """From dbus-monitor's lines: one True for each time a screen saver says it went on."""
    waiting = False
    for line in lines:
        if "member=ActiveChanged" in line and any(("interface=%s;" % i) in line for i in SCREEN_SIGNALS):
            waiting = True
        elif waiting and line.strip().startswith("boolean"):
            waiting = False
            if line.strip() == "boolean true":
                yield True


def watch_screen(on_lock=lock_all):
    """Lock every open store whenever the screen locks (GNOME, KDE and others that say so on D-Bus)."""
    rules = ["type='signal',interface='%s',member='ActiveChanged'" % i for i in SCREEN_SIGNALS]
    p = subprocess.Popen(["dbus-monitor", "--session"] + rules, stdout=subprocess.PIPE, text=True)
    for _ in screen_locked_lines(p.stdout):
        names = on_lock()
        if names:
            util.say("the screen locked: so are the secret stores of %s" % ", ".join(names))


SCREEN_UNIT = "kiwi-server-screenlock.service"


def screen_watcher(on=True, runner=subprocess.run):
    """The user service that runs watch_screen, on or off. False where there is no systemd here."""
    if not shutil.which("systemctl"):
        return False
    path = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "systemd", "user", SCREEN_UNIT)
    if not on:
        runner(["systemctl", "--user", "disable", "--now", SCREEN_UNIT], capture_output=True)
        try:
            os.remove(path)
        except OSError:
            pass
        return True
    me = shutil.which("kiwi-server") or os.path.abspath(sys.argv[0])
    text = ("[Unit]\nDescription=kiwi-server: lock the secret stores when the screen locks\n\n"
            "[Service]\nExecStart=%s secrets screen-watch -\nRestart=on-failure\nRestartSec=10\n\n"
            "[Install]\nWantedBy=default.target\n" % me)
    try:
        same = util.read_text(path) == text
    except OSError:
        same = False
    if not same:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        util.write_text(path, text, 0o644)
        runner(["systemctl", "--user", "daemon-reload"], capture_output=True)
    r = runner(["systemctl", "--user", "enable", "--now", SCREEN_UNIT], capture_output=True)
    return r.returncode == 0


# ---- an extra password for this computer's key (optional; docs/security.md) ----------------

WRAPPED = "kiwipw1:"


def wrap(secret, password):
    """This computer's key, kept in the keyring only together with a password: the keyring
    alone (an unlocked session) no longer opens the store."""
    if len(password or "") < 8:
        raise KiwiError("a password of 8 characters or more")
    salt, data = os.urandom(16), secret.encode()
    k = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 15, r=8, p=1, dklen=len(data) + 32, maxmem=2 ** 26)
    enc = bytes(a ^ b for a, b in zip(data, k))
    mac = hmac.new(k[len(data):], salt + enc, hashlib.sha256).digest()
    return WRAPPED + base64.b64encode(salt + enc + mac).decode()


def unwrap(value, password):
    raw = base64.b64decode(value[len(WRAPPED):])
    salt, enc, mac = raw[:16], raw[16:-32], raw[-32:]
    k = hashlib.scrypt(str(password).encode(), salt=salt, n=2 ** 15, r=8, p=1, dklen=len(enc) + 32, maxmem=2 ** 26)
    if not hmac.compare_digest(mac, hmac.new(k[len(enc):], salt + enc, hashlib.sha256).digest()):
        raise KiwiError("that password does not open this computer's key")
    return bytes(a ^ b for a, b in zip(enc, k)).decode()


def is_wrapped(value):
    return str(value or "").startswith(WRAPPED)


def _cached_key(store_id):
    """While a store is open: this computer's key, unwrapped once at the unlock, in its RAM folder."""
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/dev/shm", "kiwi-server-%s" % store_id, "this-key")


def keyring_key(store_id):
    """This computer's key for a store, as age takes it: from the keyring, its password asked
    once per unlock when it has one (kept in the open store's RAM folder until it locks)."""
    value = keyring_get(store_id)
    if not is_wrapped(value):
        return value
    try:
        return util.read_text(_cached_key(store_id)).strip()
    except OSError:
        pass
    probe = Store.__new__(Store)
    return unwrap(value, probe.password())


def remember_key(store_id, key):
    path = _cached_key(store_id)
    if os.path.isdir(os.path.dirname(path)):
        util.write_text(path, key + "\n", 0o600)


def _lock_unit(store):
    return "kiwi-lock-%s" % store.store_id()


def cancel_lock(store):
    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "stop", _lock_unit(store) + ".timer"], capture_output=True)
    try:
        os.remove(store._locks_at_file())
    except OSError:
        pass


def schedule_lock(store, fleet_path, after):
    """A user timer that locks the store again. False where there is none to set."""
    if not re.fullmatch(r"\d+[smhd]?", after):
        raise KiwiError("--for takes a time like 8h, 30m or 0 (stay open)")
    cancel_lock(store)
    if re.fullmatch(r"0+[smhd]?", after) or not shutil.which("systemd-run"):
        return False
    me = shutil.which("kiwi-server") or os.path.abspath(sys.argv[0])
    r = subprocess.run(["systemd-run", "--user", "--quiet", "--unit", _lock_unit(store), "--on-active", after,
                        me, "secrets", "lock", os.path.abspath(fleet_path)], capture_output=True)
    if r.returncode != 0:
        return False
    seconds = int(after.rstrip("smhd")) * {"m": 60, "h": 3600, "d": 86400}.get(after[-1], 1)
    with open(store._locks_at_file(), "w") as f:
        f.write(str(int(time.time()) + seconds))
    return True
