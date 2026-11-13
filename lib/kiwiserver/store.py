"""The secret store: secrets/ as one age-encrypted file, opened into tmpfs.

secrets.age is a tar of what secrets/ holds; secrets.recipients lists the public
keys it is encrypted to, one per way of opening it. While it is open, `secrets`
in the fleet directory is a symlink into $XDG_RUNTIME_DIR, so everything that
reads or writes secrets/ goes on working unchanged, and every command saves what
it changed before it exits: a disk key that exists only in tmpfs would be lost
with the next power cut. Nothing but age and tar is needed to open it by hand.

Design: docs/design/control-plane.md, Secrets.
"""
import getpass
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

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
    os.makedirs(target, mode=0o700, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
        for m in tar.getmembers():
            if os.path.isabs(m.name) or ".." in m.name.split("/"):
                raise KiwiError("the store holds a path that is not allowed: %s" % m.name)
            dest = os.path.join(target, m.name)
            if m.isdir():
                os.makedirs(dest, mode=0o700, exist_ok=True)
            elif m.issym():
                os.symlink(m.linkname, dest)
            elif m.isreg():
                os.makedirs(os.path.dirname(dest), mode=0o700, exist_ok=True)
                fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
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
        tmp = self.recipients + ".tmp"
        with open(tmp, "w") as f:
            f.write("\n".join(lines) + "\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, self.recipients)

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
        tmp = self.file + ".tmp"
        r = subprocess.run([_tool("age"), "-e", "-R", self.recipients, "-o", tmp], input=data, capture_output=True)
        if r.returncode != 0:
            raise KiwiError("age could not encrypt the store: %s" % r.stderr.decode(errors="replace").strip()[:200])
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.file)
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
        """Encrypt what changed. Needs only the public keys, so it works from any command."""
        if not self.is_open():
            return False
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

    def identity(self, path=None):
        """The key to open it with: a file named, the keyring, or typed."""
        if path:
            with open(path) as f:
                keys = [line.strip() for line in f if line.startswith("AGE-SECRET-KEY-")]
            if not keys:
                raise KiwiError("%s holds no age secret key" % path)
            return keys[0]
        key = keyring_get(self.store_id())
        if key:
            return key
        if not sys.stdin.isatty():
            raise KiwiError("no key in the keyring and nobody to ask: give the recovery key with --identity FILE")
        key = getpass.getpass("Recovery key (AGE-SECRET-KEY-...): ").strip()
        if not key:
            raise KiwiError("no key given")
        return key


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
