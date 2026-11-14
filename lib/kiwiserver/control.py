"""The control node's core, without a web framework: who may log in, the jobs it runs, the
looks it takes by itself, and the audit log. `serve` (web.py) puts pages and an API in front.

Everything it does is the CLI: a job is `kiwi-server <command> <fleet> ...` in a subprocess,
its output kept as the job's log, so the web page, the GTK app and the terminal do the same
thing the same way.

Secrets: the control node holds the fleet's secrets only as the encrypted store (secrets.age).
Its password and the salt in control/salt make an age key (store.password_identity) that is
one of the store's recipients; logging in with it opens the store into RAM, and until then
the node can do nothing with the fleet. No hash of the password and no key is on its disk.
The fleet file rests encrypted the same way (fleet.yaml.age, for the store's recipients):
it names every machine and may hold passwords. While the store is open, fleet.yaml is a link
into RAM, as secrets/ is. What a job prints and what the machines said last stay in RAM too.
"""
import hashlib
import hmac
import json
import os
import re
import secrets as pysecrets
import shutil
import subprocess
import sys
import threading
import time

from . import VERSION, store as storemod, util
from .util import KiwiError

HOST = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
FLEET_FILES = ("fleet.yaml", "federation.yaml")     # at rest on the node: <name>.age
SESSION_HOURS = 12
MIN_PASSWORD = 12
MEDIA_ROOM = 1000 * 1024 * 1024     # what one machine's install media may take
# what a page may start: the command and whether it takes hosts
ACTIONS = {
    "status": ("status", True), "plan": ("plan", True), "apply": ("apply", True),
    "check": ("check", False), "watch": ("watch", True), "backup": ("backup", False),
    "save": ("backups", True), "update": ("apply", False), "rotate": ("rotate", False),
    "rotate-seed": ("rotate", False), "setup": ("next", True), "build": ("build", True),
    "restore": ("backups", True), "unlock": ("unlock", True), "remove": ("remove", True),
}
CHANGES = {"apply", "backup", "update", "save", "rotate", "rotate-seed", "setup", "build", "restore", "unlock", "remove"}
MODULE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
WHEN = re.compile(r"^\d{4}-\d\d-\d\d[T ]\d\d:\d\d(:\d\d(\.\d+)?)?(Z|[+-]\d\d:?\d\d)?$")      # a snapshot's time, as `backups --porcelain` gives it     # these change machines or the fleet: the audit log says who


class Control:
    def __init__(self, fleet_path, kiwi_cmd=None, status_minutes=10, watch_seconds=60, clock=time.time, run_dir=None):
        self.fleet_path = os.path.abspath(fleet_path)
        self.fleet_dir = os.path.dirname(self.fleet_path)
        self.dir = os.path.join(self.fleet_dir, "control")          # on disk: the salt, the audit log, the sync stamp
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        base = os.environ.get("XDG_RUNTIME_DIR") or "/dev/shm"      # in RAM: job logs, the last look
        self.run = run_dir or os.path.join(base, "kiwi-control-%s" % hashlib.sha256(self.fleet_path.encode()).hexdigest()[:12])
        self.jobs_dir = os.path.join(self.run, "jobs")
        os.makedirs(self.jobs_dir, mode=0o700, exist_ok=True)
        self.store = storemod.Store(self.fleet_dir)
        self.kiwi = kiwi_cmd or [sys.executable, "-m", "kiwiserver"]
        self.status_minutes = status_minutes
        self.watch_seconds = watch_seconds
        self.clock = clock
        self.lock = threading.Lock()          # one job at a time: two applies never cross
        self.failures = {}                    # address -> [times] of failed logins
        self._session_key = pysecrets.token_bytes(32)   # sessions end with the process, as the open store does
        self._identity = None                 # the key the password made, while the store is open
        self._saved = {}                      # name -> sha256 of the fleet file as it was last saved or opened
        self._age = {}                        # name -> sha256 of its encrypted file on disk, as this process left it
        self._arming = threading.Lock()       # see arm_auto
        self._join = None                     # the join code that is out, if one is
        self._setup_code = None               # the first run's code, while there is no network here yet
        self._tokens = {}                     # token -> (controller, expires): who may sync

    # ---- the password and the store ----------------------------------------------------
    @property
    def salt_file(self):
        return os.path.join(self.dir, "salt")

    def _fleet_file(self, name):
        return os.path.join(self.fleet_dir, os.path.basename(self.fleet_path) if name == "fleet.yaml" else name)

    def ready(self):
        """Is there a store this node's password can open, and a fleet file (encrypted on a
        control node; a plain file where the operator runs serve on their own computer)?"""
        fleet = self._fleet_file("fleet.yaml")
        plain = os.path.isfile(fleet) and not os.path.islink(fleet)
        return self.store.exists() and os.path.isfile(self.salt_file) and (plain or os.path.isfile(fleet + ".age"))

    # ---- the first run, from this machine's page -------------------------------------
    # A machine with nothing on it yet (no store, no fleet file) offers the questionnaire of
    # `kiwi-server init` on its page. Whoever opens the page first would own the new network,
    # so the page asks for a code that only the person who started it can read: `serve`
    # prints it (the container's log, the machine's console). The new fleet is made in RAM
    # and reaches the disk encrypted only, as everything on a control node.
    def fresh(self):
        fleet = self._fleet_file("fleet.yaml")
        return not (self.store.exists() or os.path.lexists(fleet) or os.path.isfile(fleet + ".age"))

    def setup_code(self):
        if not self._setup_code:
            raw = pysecrets.token_hex(4)
            self._setup_code = "%s-%s" % (raw[:4], raw[4:])
        return self._setup_code

    def setup_code_ok(self, code, address="?"):
        """Five wrong tries from an address: ten minutes of nothing, as at the login."""
        now = self.clock()
        recent = [t for t in self.failures.get(address, []) if now - t < 600]
        self.failures[address] = recent
        if len(recent) >= 5:
            return False
        if hmac.compare_digest((code or "").strip().lower().encode(), self.setup_code().encode()):
            return True
        recent.append(now)
        return False

    def first_run(self, code, password, answers, address="?"):
        """The new network from the questionnaire's answers. Returns (recovery key, session token)."""
        from . import setup as setupmod
        with self.lock:
            if not self.fresh():
                raise KiwiError("this machine has a network already")
            if not self.setup_code_ok(code, address):
                raise KiwiError("that is not the setup code")
            password = (password or "").strip()
            if len(password) < MIN_PASSWORD:
                raise KiwiError("the password needs %d characters at least: it guards every secret of the network" % MIN_PASSWORD)
            bad = setupmod.check(answers)
            if bad:
                raise KiwiError("; ".join(bad.values()))
            tmp = os.path.join(self.run, "first-run")       # in RAM
            shutil.rmtree(tmp, ignore_errors=True)
            os.makedirs(tmp, mode=0o700)
            try:
                data, _made = setupmod.make(answers, tmp)
                setupmod.write(os.path.join(tmp, "fleet.yaml"), data)
                st = storemod.Store(tmp)
                recovery, _saved = st.init()
                made = Control(os.path.join(tmp, "fleet.yaml"), run_dir=os.path.join(tmp, "run"))
                made.set_password(password)
                st.remove("desktop")            # a keyring's way: there is no keyring on a node
                st.lock()
                blob = storemod.encrypt_bytes(st.recipients, util.read_bytes(os.path.join(tmp, "fleet.yaml")))
                for name in (storemod.RECIPIENTS, storemod.FILE):
                    shutil.copyfile(os.path.join(tmp, name), os.path.join(self.fleet_dir, name))
                os.chmod(os.path.join(self.fleet_dir, storemod.FILE), 0o600)
                util.write_bytes(self._fleet_file("fleet.yaml") + ".age", blob, 0o600)
                shutil.copyfile(made.salt_file, self.salt_file)
                os.chmod(self.salt_file, 0o600)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            self.store = storemod.Store(self.fleet_dir)
            self._setup_code = None
        self.audit("admin", "first run: the network was made on this machine's page", address=address)
        return recovery, self.login(password, address)

    # ---- a network that was lost, back from its copy ---------------------------------
    # The other thing a machine with nothing on it can do: take a copy of a network (backup.py,
    # the kind a store's keys encrypt) and the recovery key that opens it. The same guard as
    # the first run, and the same rule: it is opened in RAM and reaches the disk encrypted only.
    def restore_run(self, code, password, archive, recovery, address="?"):
        """A network from its copy, with a new password for this page. Returns a session token."""
        from . import backup as backupmod
        with self.lock:
            if not self.fresh():
                raise KiwiError("this machine has a network already")
            if not self.setup_code_ok(code, address):
                raise KiwiError("that is not the setup code")
            password = (password or "").strip()
            if len(password) < MIN_PASSWORD:
                raise KiwiError("the password needs %d characters at least: it guards every secret of the network" % MIN_PASSWORD)
            recovery = "".join((recovery or "").split()).upper()
            if not recovery.startswith("AGE-SECRET-KEY-"):
                raise KiwiError("a recovery key starts with AGE-SECRET-KEY-")
            kind = backupmod.kind(archive or b"")
            if kind == "passphrase":
                raise KiwiError("this copy is of the older kind, locked with a passphrase: it comes back with "
                                "`kiwi-server restore` on a computer")
            if kind != "store":
                raise KiwiError("that file is not a copy of a network")
            try:
                fleet_name, plain, kept = backupmod.open_copy(archive, recovery)
            except KiwiError as e:
                if "does not open" in str(e):       # guessed keys wait as guessed passwords do
                    self.failures.setdefault(address, []).append(self.clock())
                    self.audit("admin", "a copy was not brought back: the recovery key does not open it", address=address)
                    raise KiwiError("that recovery key does not open this copy")
                raise
            names = {fleet_name: "fleet.yaml", "federation.yaml": "federation.yaml"}     # what a node keeps
            left = sorted(n for n in plain if n not in names)
            tmp = os.path.join(self.run, "restore-run")     # in RAM
            shutil.rmtree(tmp, ignore_errors=True)
            os.makedirs(tmp, mode=0o700)
            st, opened = storemod.Store(tmp), False
            try:
                util.write_bytes(st.file, kept[storemod.FILE], 0o600)
                util.write_bytes(st.recipients, kept[storemod.RECIPIENTS], 0o644)
                util.write_bytes(os.path.join(tmp, "fleet.yaml"), plain[fleet_name], 0o600)
                opened = True
                st.unlock(recovery)
                made = Control(os.path.join(tmp, "fleet.yaml"), run_dir=os.path.join(tmp, "run"))
                made.set_password(password)     # in place of an older page's password
                if any(name == "desktop" for name, _key in st.ways()):
                    st.remove("desktop")        # a keyring's way: there is no keyring on a node
                st.lock()
                blobs = {names[n] + ".age": storemod.encrypt_bytes(st.recipients, plain[n]) for n in names if n in plain}
                for name in (storemod.RECIPIENTS, storemod.FILE):
                    shutil.copyfile(os.path.join(tmp, name), os.path.join(self.fleet_dir, name))
                os.chmod(os.path.join(self.fleet_dir, storemod.FILE), 0o600)
                for name, blob in blobs.items():
                    util.write_bytes(self._fleet_file(name[:-4]) + ".age", blob, 0o600)
                shutil.copyfile(made.salt_file, self.salt_file)
                os.chmod(self.salt_file, 0o600)
            finally:
                if opened:                      # whatever went wrong: nothing of it stays open in RAM
                    shutil.rmtree(st.runtime(), ignore_errors=True)
                shutil.rmtree(tmp, ignore_errors=True)
            self.store = storemod.Store(self.fleet_dir)
            self._setup_code = None
        self.audit("admin", "the network was brought back from a copy on this machine's page%s" % (
            "; %d of its files have no place on a control node and were left out" % len(left) if left else ""), address=address)
        return self.login(password, address)

    def network_copy(self, user="admin"):
        """(file name, bytes) of a copy of the network as it is now: the store and the fleet
        file, for the store's keys (backup.create_copy). Made in memory; nothing is kept here."""
        from . import backup as backupmod, config
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        with self.lock:                       # after the running job: it may be changing the store
            fleet = config.Fleet.load(self.fleet_path)
            data, name = backupmod.create_copy(fleet), backupmod.archive_name(fleet)
        self.audit(user, "fetched a copy of the network")
        return name, data

    def _open(self, identity):
        """The store into RAM, then the fleet files beside it; fleet.yaml becomes a link there."""
        self.store.unlock(identity)
        ram = os.path.join(self.store.runtime(), "fleet")
        os.makedirs(ram, mode=0o700, exist_ok=True)
        for name in FLEET_FILES:
            path = self._fleet_file(name)
            if not os.path.isfile(path + ".age"):
                continue
            data = storemod.decrypt_file(identity, path + ".age")
            util.write_bytes(os.path.join(ram, name), data, 0o600)
            if os.path.islink(path) or os.path.exists(path):
                os.remove(path)
            os.symlink(os.path.join(ram, name), path)
            self._saved[name] = hashlib.sha256(data).hexdigest()
            self._age[name] = util.sha256_file(path + ".age")

    def _pushed(self):
        """Did someone else replace an encrypted file on disk (`control push`)? The store
        knows its own file; the fleet files' are remembered here."""
        try:
            self.store._unchanged_on_disk()
        except KiwiError:
            return True
        return any(not os.path.isfile(self._fleet_file(n) + ".age") or
                   util.sha256_file(self._fleet_file(n) + ".age") != sha for n, sha in self._age.items())

    def save_fleet(self):
        """A fleet file a job changed, encrypted back to disk (needs the public keys only)."""
        for name, was in list(self._saved.items()):
            path = self._fleet_file(name)
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as fh:
                data = fh.read()
            if hashlib.sha256(data).hexdigest() == was:
                continue
            blob = storemod.encrypt_bytes(self.store.recipients, data)
            with open(os.open(path + ".age.tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as fh:
                fh.write(blob)
            os.replace(path + ".age.tmp", path + ".age")
            self._saved[name] = hashlib.sha256(data).hexdigest()
            self._age[name] = util.sha256_file(path + ".age")

    # ---- unlocking by itself: a ring on another machine ------------------------------
    # Opt-in (`unlock: { control: <ring> }` on the control node). After a login the key the
    # password made is encrypted for that Tang ring (clevis) and kept in control/auto.jwe. After
    # a restart the node asks the ring; when it answers, the store opens with nobody there.
    # A stolen disk alone opens nothing: the thief would also have to reach the ring, the same
    # trade the encrypted disks of the fleet make.
    @property
    def auto_file(self):
        return os.path.join(self.dir, "auto.jwe")

    def auto_state(self):
        """{"ring", "server"} when it unlocks by itself, else None."""
        path = self.auto_file + ".json"
        if os.path.isfile(self.auto_file) and os.path.isfile(path):
            try:
                return json.loads(util.read_text(path))
            except ValueError:
                return None
        return None

    def arm_auto(self):
        """After a login: make (or remove) what lets the ring open the store. Says what it did.
        One at a time: two logins in a row would write the same file."""
        with self._arming:
            return self._arm_auto()

    def _arm_auto(self):
        if self.locked() or not self._identity:
            return None
        r = subprocess.run(list(self.kiwi) + ["control", "ring", self.fleet_path], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, cwd=self.fleet_dir)
        try:
            pin = json.loads(r.stdout) if r.returncode == 0 else None
        except ValueError:
            pin = None
        if pin is None and r.returncode == 0:
            return None
        if pin is None:
            self.audit("control", "unlock by itself was not set up: %s" % (
                (r.stderr or r.stdout).strip().replace("error: ", "").splitlines() or ["?"])[-1][:200])
            return None
        if not pin:                              # not wanted (any more)
            for f in (self.auto_file, self.auto_file + ".json"):
                if os.path.exists(f):
                    os.remove(f)
            return None
        try:
            c = subprocess.run(["clevis", "encrypt", "tang", json.dumps({"url": pin["url"], "adv": pin["adv"]})],
                               input=self._identity.encode(), capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            self.audit("control", "unlock by itself was not set up: %s" % e)
            return None
        if c.returncode != 0 or not c.stdout.strip():
            self.audit("control", "unlock by itself was not set up: clevis refused")
            return None
        util.write_bytes(self.auto_file, c.stdout, 0o600)
        util.write_text(self.auto_file + ".json", json.dumps({"ring": pin["ring"], "server": pin["server"]}), 0o600)
        return pin["ring"]

    def try_auto(self):
        """Locked, and a ring may open it: ask the ring. True when it opened."""
        if not self.locked() or not self.ready() or not os.path.isfile(self.auto_file):
            return False
        try:
            c = subprocess.run(["clevis", "decrypt"], input=util.read_bytes(self.auto_file), capture_output=True, timeout=45)
        except (OSError, subprocess.TimeoutExpired):
            return False
        identity = c.stdout.decode(errors="replace").strip()
        if c.returncode != 0 or not identity.startswith("AGE-SECRET-KEY-"):
            return False
        try:
            with self.lock:
                self._open(identity)
        except KiwiError:
            return False                         # an older password's key: the next login makes a new one
        self._identity = identity
        self.audit("control", "unlocked by the ring %s" % ((self.auto_state() or {}).get("ring") or "?"))
        return True

    def _for_the_keys_now(self):
        """The store's keys changed: the fleet files are encrypted again, for those keys (the
        store did its own file). Otherwise a new controller could not open them, and a removed
        one still could."""
        for name in self._saved:
            self._saved[name] = ""
        self.save_fleet()

    def _close(self):
        if not self._pushed():
            self.save_fleet()
        for name in list(self._saved):
            if os.path.islink(self._fleet_file(name)):
                os.remove(self._fleet_file(name))
        self._saved, self._age = {}, {}
        self.store.lock()
        self._identity = None

    def locked(self):
        return not self.store.is_open()

    def set_password(self, password):
        """On the operator's computer, the store open: the password becomes one more way to
        open it. `control push` takes the result to the node."""
        password = password.strip()         # a phone's keyboard likes to add a space
        if len(password) < MIN_PASSWORD:
            raise KiwiError("the control password needs %d characters at least: it guards every secret of the fleet" % MIN_PASSWORD)
        if not self.store.exists():
            raise KiwiError("the control node takes the fleet's secrets only as the encrypted store: "
                            "kiwi-server secrets init %s first" % self.fleet_path)
        if not self.store.is_open():
            raise KiwiError("the store is locked: kiwi-server secrets unlock %s first" % self.fleet_path)
        salt = pysecrets.token_hex(16)
        public = storemod.public_of(storemod.password_identity(password, salt))
        if any(name == "control" for name, _key in self.store.ways()):
            self.store.remove("control")
        self.store.add("control", public)
        util.write_text(self.salt_file, salt + "\n", 0o600)

    def change_password(self, old, new, user="admin"):
        """A new password for the page. The old one's key leaves the store: it opens nothing
        any more, not even an older copy of the disk's files made after this."""
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        salt = util.read_text(self.salt_file).strip()
        if storemod.public_of(storemod.password_identity(old.strip(), salt)) != self._control_key():
            raise KiwiError("the current password is not right")
        with self.lock:
            self.set_password(new)
            self._identity = storemod.password_identity(new.strip(), util.read_text(self.salt_file).strip())
            self._for_the_keys_now()
        self.audit(user, "changed the control password")
        self.arm_auto()                     # the ring held the old password's key

    def reset_password(self, recovery, new, address="?"):
        """The recovery key in place of a forgotten password: it opens the store, and the page
        gets a new password. A session token, or None when the key is not the store's."""
        now = self.clock()
        recent = [t for t in self.failures.get(address, []) if now - t < 600]
        self.failures[address] = recent
        if len(recent) >= 5 or not self.ready():
            return None
        recovery = "".join((recovery or "").split()).upper()
        try:
            if not recovery.startswith("AGE-SECRET-KEY-") or storemod.public_of(recovery) != dict(self.store.ways()).get("recovery"):
                raise KiwiError("not the recovery key")
        except KiwiError:
            recent.append(now)
            self.audit("admin", "recovery failed", address=address)
            return None
        if len((new or "").strip()) < MIN_PASSWORD:
            raise KiwiError("the password needs %d characters at least" % MIN_PASSWORD)
        with self.lock:
            if self.locked():
                self._open(recovery)
            self.set_password(new)
            self._identity = storemod.password_identity(new.strip(), util.read_text(self.salt_file).strip())
            self._for_the_keys_now()
        self.audit("admin", "the recovery key set a new control password", address=address)
        self.arm_auto()
        return self.login(new, address)

    def rotation(self):
        """Machines still on the old SSH keys (pending) or the old seed (seed_pending), and when
        each was last replaced."""
        path = os.path.join(self.fleet_dir, "secrets", "rotation.json")
        try:
            state = json.loads(util.read_text(path))
        except (OSError, ValueError):
            return {"pending": [], "last": None, "seed_pending": [], "seed_last": None}
        names = [h["name"] for h in (self.fleet_info() or {}).get("hosts") or []]

        def waits(what):
            done = (state.get(what) or {}).get("done")
            return [] if done is None else [n for n in names if n not in done]
        last = state.get("last") or {}
        return {"pending": waits("ssh"), "last": last.get("ssh"), "seed_pending": waits("seed"), "seed_last": last.get("seed")}

    def _control_key(self):
        return next((key for name, key in self.store.ways() if name == "control"), None)

    def login(self, password, address="?"):
        """A session token, or None. The password is right when the key it makes opens the
        store (locked) or is the store's control key (open). Five wrong ones from one address
        in ten minutes: it waits until the oldest is ten minutes old."""
        now = self.clock()
        recent = [t for t in self.failures.get(address, []) if now - t < 600]
        self.failures[address] = recent
        if len(recent) >= 5 or not self.ready():
            return None
        identity = storemod.password_identity(password.strip(), util.read_text(self.salt_file).strip())
        try:
            if self.locked():
                with self.lock:
                    self._open(identity)
                self.audit("admin", "unlocked", address=address)
            elif storemod.public_of(identity) != self._control_key():
                raise KiwiError("wrong password")
        except KiwiError:
            recent.append(now)
            self.audit("admin", "login failed", address=address)
            return None
        self._identity = identity
        self.audit("admin", "login", address=address)
        threading.Thread(target=self.arm_auto, daemon=True).start()     # the ring, if one is named
        expires = int(now + SESSION_HOURS * 3600)
        body = "admin|%d" % expires
        return "%s|%s" % (body, hmac.new(self._session_key, body.encode(), hashlib.sha256).hexdigest())

    def session(self, token):
        """The user a token belongs to, or None. A locked store has no sessions: the password opens it."""
        try:
            user, expires, mac = (token or "").split("|")
        except ValueError:
            return None
        body = "%s|%s" % (user, expires)
        good = hmac.new(self._session_key, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(good, mac) or int(expires) < self.clock() or self.locked():
            return None
        return user

    def csrf(self, token):
        """What a form of this session carries, so that only a page of ours can post for it."""
        return hmac.new(self._session_key, ("csrf|%s" % (token or "")).encode(), hashlib.sha256).hexdigest()

    def lock_store(self, user="admin"):
        with self.lock:                       # after the running job
            self._close()
        self.audit(user, "locked")

    def reload_if_pushed(self):
        """`control push` replaced the encrypted files under the open store: open the new ones.
        (The push refuses when this node changed its own since the last sync.) When the
        password changed with it, the old key opens nothing: locked until the new one logs in."""
        if self.locked() or not self._identity:
            return None
        with self.lock:
            if not self._pushed():
                return None
            try:
                self._open(self._identity)
                self.audit("control", "took the pushed fleet")
                return "reloaded"
            except KiwiError:
                self._close()
                self.audit("control", "locked: the pushed store does not open with the old password")
                return "locked"

    # ---- controllers: the computers that can open the store ------------------------------
    # (design/controllers.md, step 1: every controller is fully trusted.) A controller is one
    # of the store's keys. A computer becomes one with an invite link from the page;
    # an invite made on the page lets one computer in; a controller proves who it is by opening
    # a challenge encrypted to its key, and may then fetch and replace the encrypted fleet.
    def controllers(self):
        return self.store.ways() if self.store.exists() else []

    def ca_pem(self):
        """The fleet CA's certificate (public): from the open store, remembered on disk so a
        joining computer can be given it while the store is locked."""
        cached = os.path.join(self.dir, "ca.pem")
        live = os.path.join(self.fleet_dir, "secrets", "ca", "kiwiCA.pem")
        if not self.locked() and os.path.isfile(live):
            if not os.path.isfile(cached) or util.read_text(cached) != util.read_text(live):
                util.write_text(cached, util.read_text(live), 0o644)
        return util.read_text(cached) if os.path.isfile(cached) else ""

    def new_invite(self, base_url, user="admin"):
        """A link for one computer, good for fifteen minutes: the control node's address and a
        code after the #, which a browser never sends anywhere. The code's last part names the
        CA, so the computer knows it talks to this fleet before it sends anything."""
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        fp = hashlib.sha256(self.ca_pem().encode()).hexdigest()[:8]
        raw = "".join(pysecrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(20))
        code = "%s.%s" % (raw, fp)
        self._join = {"hash": hashlib.sha256(code.encode()).hexdigest(), "expires": self.clock() + 900, "wrong": 0}
        self.audit(user, "made an invite")
        return "%s/#join=%s" % (base_url.rstrip("/"), code)

    def join(self, code, name, key, address="?"):
        """A computer joins with an invite. The invite is the owner's yes: it was made on the
        page by someone logged in, works once and for fifteen minutes. Returns the name it got
        (a taken one gets a number), or None."""
        j = getattr(self, "_join", None)
        if not j or j["expires"] < self.clock() or self.locked():
            return None
        if not hmac.compare_digest(j["hash"], hashlib.sha256((code or "").strip().encode()).hexdigest()):
            j["wrong"] += 1
            if j["wrong"] >= 3:                 # three wrong guesses: the invite is void
                self._join = None
            self.audit("?", "join refused: wrong invite", address=address)
            return None
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,27}", name or "") or not re.fullmatch(r"age1[0-9a-z]{58}", key or ""):
            return None
        taken = dict(self.controllers())
        if key in taken.values():
            return None
        base, n = name, 2
        while name in taken:
            name, n = "%s-%d" % (base, n), n + 1
        self._join = None                       # used
        with self.lock:
            self.store.add(name, key)
            self._for_the_keys_now()
        self.audit(name, "joined with an invite", address=address)
        return name

    def remove(self, name, user="admin"):
        """Its key leaves the store: it reads nothing newer. What it read before, it knows
        (design/controllers.md, Rotation)."""
        if name == "control":
            raise KiwiError("that is this node's own key: a new password replaces it (serve --set-password, then push)")
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        with self.lock:
            self.store.remove(name)
            self._for_the_keys_now()
        self._tokens = {t: v for t, v in self._tokens.items() if v[0] != name}
        self.audit(user, "removed controller %s" % name)

    def challenge(self, name):
        """A token only that controller can read: encrypted to its key. None when there is no
        such controller."""
        key = dict(self.controllers()).get(name)
        if not key:
            return None
        token = pysecrets.token_hex(32)
        self._tokens[token] = (name, self.clock() + 600)
        return storemod.encrypt_to(key, token.encode())

    def controller(self, token):
        name, expires = self._tokens.get(token or "", (None, 0))
        if not name or expires < self.clock() or name not in dict(self.controllers()):
            return None
        return name

    def revision(self):
        from . import controlsync
        return controlsync.node_digest(self.fleet_dir)

    def export(self):
        """(revision, {name: bytes}) of the encrypted fleet as it rests here."""
        from . import controlsync
        with self.lock:
            if not self.locked() and not self._pushed():
                self.save_fleet()
            files = {}
            for name in controlsync.ON_NODE:
                path = os.path.join(self.fleet_dir, name)
                if os.path.isfile(path):
                    files[name] = util.read_bytes(path)
            return controlsync.node_digest(self.fleet_dir), files

    def replace(self, base, files, name):
        """A controller's push: taken when it builds on what is here. Returns the new revision,
        or None when this node's copy is another than `base` (pull first)."""
        from . import controlsync
        if set(files) - set(controlsync.ON_NODE) or not {"secrets.age", "secrets.recipients", "fleet.yaml.age"} <= set(files):
            raise KiwiError("not a fleet: %s" % ", ".join(sorted(files)))
        with self.lock:
            if not self.locked() and not self._pushed():
                self.save_fleet()
            if base != controlsync.node_digest(self.fleet_dir):
                return None
            for n in controlsync.ON_NODE:
                path = os.path.join(self.fleet_dir, n)
                if n in files:
                    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
                    util.write_bytes(path + ".tmp", files[n], 0o644 if n == "secrets.recipients" else 0o600)
                    os.replace(path + ".tmp", path)
                elif os.path.isfile(path):
                    os.remove(path)
            new = controlsync.node_digest(self.fleet_dir)
        self.audit(name, "pushed the fleet")
        self.reload_if_pushed()
        return new

    # ---- audit ----------------------------------------------------------------------
    def audit(self, user, what, **more):
        line = dict({"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "user": user, "what": what}, **more)
        path = os.path.join(self.dir, "audit.jsonl")
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a") as fh:
            fh.write(json.dumps(line) + "\n")

    def audit_tail(self, n=50):
        path = os.path.join(self.dir, "audit.jsonl")
        if not os.path.isfile(path):
            return []
        with open(path) as fh:
            return [json.loads(x) for x in fh.readlines()[-n:]][::-1]

    # ---- jobs -----------------------------------------------------------------------
    def argv(self, action, hosts=(), at="", module=""):
        if action not in ACTIONS:
            raise KiwiError("unknown action %r" % action)
        if action == "update":              # every machine, one after the other: the maintenance button
            names = [h["name"] for h in (self.fleet_info() or {}).get("hosts") or []]
            if not names:
                raise KiwiError("there are no machines yet")
            me = os.environ.get("KIWI_CONTROL_HOST")    # this node last: its own apply may restart this page
            names = [n for n in names if n != me] + [n for n in names if n == me]
            return list(self.kiwi) + ["apply", self.fleet_path, "--update"] + names
        if action == "backup":              # the network's own copy: where the fleet file says, else every machine that is set up
            argv = list(self.kiwi) + ["backup", self.fleet_path]
            if self._backup_hosts():
                return argv
            names = [h["name"] for h in (self.fleet_info() or {}).get("hosts") or [] if h.get("phase") == "applied"]
            if not names:
                raise KiwiError("no machine is set up yet, so there is nowhere to copy it to. \"Fetch a copy\" gives you the file")
            return argv + names
        if action == "save":                # one machine's data, to its backup machines, now
            if len(hosts) != 1:
                raise KiwiError("which machine?")
            return list(self.kiwi) + ["backups", self.fleet_path, hosts[0], "--now"]
        # new SSH keys, or a new seed, on every machine that still has the old ones
        if action in ("rotate", "rotate-seed"):
            argv = list(self.kiwi) + ["rotate", self.fleet_path, "ssh" if action == "rotate" else "seed"]
            for h in hosts:                 # left out: gone for good, or about to be installed anew
                argv += ["--waive", h]
            return argv
        if action == "setup":               # a machine's next step, as `kiwi-server next` takes it
            if len(hosts) != 1:
                raise KiwiError("which machine?")
            if at:      # it got its address from the router: the person at the page says which
                import ipaddress
                try:
                    ipaddress.ip_address(at)
                except ValueError:
                    raise KiwiError("An address looks like 192.168.1.30.")
                row = next((h for h in (self.fleet_info() or {}).get("hosts") or [] if h["name"] == hosts[0]), None)
                if not row:
                    raise KiwiError("no such machine: %s" % hosts[0])
                return list(self.kiwi) + ["apply", self.fleet_path, hosts[0], "--ssh",
                                          "%s@%s" % (row.get("admin_user") or "core", at), "--new-host-key"]
            return list(self.kiwi) + ["next", self.fleet_path, hosts[0], "--yes"]
        if action == "restore":             # a part of a machine back to a snapshot, or all of it to the newest
            if len(hosts) != 1:
                raise KiwiError("which machine?")
            if module and not MODULE.match(module):
                raise KiwiError("not a part's name: %r" % module)
            if at and not WHEN.match(at):
                raise KiwiError("not a snapshot's time: %r" % at)
            return (list(self.kiwi) + ["backups", self.fleet_path, hosts[0]] + ([module] if module else [])
                    + ["--restore"] + (["--at", at] if at else []))
        if action == "remove":              # a machine leaves the network for good
            if len(hosts) != 1:
                raise KiwiError("which machine?")
            if hosts[0] == os.environ.get("KIWI_CONTROL_HOST"):
                raise KiwiError("this is the machine the page runs on: it cannot remove itself")
            return list(self.kiwi) + ["remove", self.fleet_path, hosts[0], "--yes"]
        if action == "unlock":              # a machine whose encrypted disk waits at boot
            if len(hosts) != 1:
                raise KiwiError("which machine?")
            return list(self.kiwi) + ["unlock", self.fleet_path, hosts[0]]
        if action == "build":       # about 1 GB of memory each: one at a time, asked for by name
            if len(hosts) != 1:
                raise KiwiError("which machine?")
            os.makedirs(self.media_dir(), mode=0o700, exist_ok=True)
            if shutil.disk_usage(self.media_dir()).free < MEDIA_ROOM:
                raise KiwiError("there is no room for more install media here: remove another machine's first "
                                "(on its page), or give this machine more memory for them (the control module's ram_mb)")
        cmd, takes_hosts = ACTIONS[action]
        argv = list(self.kiwi) + [cmd, self.fleet_path]
        if takes_hosts:
            argv += list(hosts)
        if action == "watch":
            argv.append("--once")
        return argv

    def start(self, action, hosts=(), user="admin", wait=False, at="", module=""):
        """A job in a thread; its id. The jobs wait for each other."""
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        for h in hosts:
            if not HOST.match(h):
                raise KiwiError("not a host name: %r" % h)
        argv = self.argv(action, hosts, at.strip(), module.strip())
        jid = time.strftime("%Y%m%d-%H%M%S-") + pysecrets.token_hex(3)
        meta = {"id": jid, "action": action, "hosts": list(hosts), "user": user,
                "state": "waiting", "started": None, "ended": None, "rc": None}
        self._write(meta)
        if action in CHANGES:
            more = {k: v for k, v in (("part", module.strip()), ("from", at.strip())) if v and action == "restore"}
            self.audit(user, action, hosts=list(hosts), job=jid, **more)
        t = threading.Thread(target=self._run, args=(meta, argv), daemon=True)
        t.start()
        if wait:
            t.join()
        return jid

    def _run(self, meta, argv):
        with self.lock:
            meta.update(state="running", started=self.clock())
            self._write(meta)
            log = os.path.join(self.jobs_dir, meta["id"] + ".log")
            env = self._env()
            with open(os.open(log, os.O_WRONLY | os.O_CREAT, 0o600), "w") as fh:
                try:
                    rc = subprocess.run(argv, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                        env=env, cwd=self.fleet_dir).returncode
                except OSError as e:
                    fh.write("could not start: %s\n" % e)
                    rc = 127
            try:
                if not self._pushed():      # a push during the job wins: the next look takes it
                    self.save_fleet()
            except (KiwiError, OSError) as e:
                util.warn("control: the fleet file was not saved: %s" % e)
            meta.update(state="ok" if rc == 0 else "failed", ended=self.clock(), rc=rc)
            self._write(meta)
        try:
            self.fleet_info(fresh=True)         # an apply changes what the next step is
        except (OSError, ValueError):
            pass
        if meta["action"] == "setup" and rc == 0:       # set up: its install media have done their work
            row = next((h for h in (self.fleet_info() or {}).get("hosts") or [] if h["name"] in meta["hosts"]), None)
            if row and row.get("phase") == "applied" and self.media(row["name"]):
                os.remove(self.media(row["name"])[0])
        if meta["action"] in ("status", "apply", "save", "update", "setup", "restore", "unlock"):
            self.look()                         # what the pages show is what is true now

    # ---- what a machine's backups hold ------------------------------------------------
    def snapshots(self, host):
        """The snapshots of a machine, by part: {part: [(time, [servers])]}, newest first, and the
        backup machines that did not answer. Asks the backup machines, so it takes a moment."""
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        if not HOST.match(host or ""):
            raise KiwiError("not a host name: %r" % host)
        r = subprocess.run(list(self.kiwi) + ["backups", self.fleet_path, host, "--porcelain"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, cwd=self.fleet_dir, env=self._env(), timeout=180)
        try:
            data = json.loads(r.stdout)
        except ValueError:
            lines = [x for x in (r.stderr or r.stdout).strip().splitlines() if x.strip()]
            raise KiwiError((lines[-1] if lines else "no answer").replace("error: ", "")[:300])
        times = {}
        for sv in data.get("servers") or []:
            for sn in sv.get("snapshots") or []:
                at, servers = times.setdefault(sn["module"], {}).get(sn["time"][:16], ("", set()))
                servers.add(sv["server"].removeprefix("backup.").split(".")[0])
                times[sn["module"]][sn["time"][:16]] = (max(at, sn["time"]), servers)
        parts = {m: [(at, sorted(servers)) for _k, (at, servers) in sorted(t.items(), reverse=True)[:30]] for m, t in sorted(times.items())}
        return {"parts": parts, "missed": [sv["server"] for sv in data.get("servers") or [] if not sv.get("ok")]}

    # ---- machines: a new one, its install media ---------------------------------------
    def media_dir(self):
        """Where install media are made. They carry the machine's secrets: on a control node
        (the fleet file is a link into memory) they stay in memory and go with a restart."""
        if os.path.islink(self._fleet_file("fleet.yaml")):
            return os.path.join(self.run, "media")
        return os.path.join(self.fleet_dir, "output")

    def _env(self):
        return dict(os.environ, PYTHONUNBUFFERED="1", KIWI_MEDIA_DIR=self.media_dir())

    def media(self, name):
        """The install media of a machine, when they are made: (path, size)."""
        if not HOST.match(name or ""):
            return None
        path = os.path.join(self.media_dir(), name, name + ".iso")
        return (path, os.path.getsize(path)) if os.path.isfile(path) else None

    def drop_media(self, name, user="admin"):
        m = self.media(name)
        if m:
            os.remove(m[0])
            self.audit(user, "install media removed", hosts=[name])
            self.fleet_info(fresh=True)

    def add_machine(self, preset, address="", name="", target="", user="admin"):
        """A machine at home, added to the fleet file as the first run's questions add one."""
        from . import setup as setupmod
        import ipaddress
        import yaml
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        if preset not in setupmod.PRESETS:
            raise KiwiError("what should it do?")
        if target not in ("", "ucore", "debian"):
            raise KiwiError("the system is ucore or debian")
        with self.lock:
            path = os.path.realpath(self.fleet_path)
            data = yaml.safe_load(util.read_text(path)) or {}
            taken = set(data.get("hosts") or {})
            name = (name or "").strip().lower() or setupmod.free_name("node", taken)
            nets = [h.get("network") or {} for h in (data.get("hosts") or {}).values() if isinstance(h, dict)]
            gateway = next((str(n["gateway"]) for n in nets if n.get("gateway")), "")     # as the machines it has
            like = next((str(n["address"]) for n in nets if "/" in str(n.get("address") or "")), "")
            address = (address or "").strip()
            net = None
            if address:
                try:
                    ip = ipaddress.ip_interface(address)
                except ValueError:
                    raise KiwiError("An address looks like 192.168.1.30.")
                prefix = ip.network.prefixlen if "/" in address else int(like.rsplit("/", 1)[1]) if like else 24
                net = "{ dhcp: false, address: %s/%d%s }" % (ip.ip, prefix, ", gateway: %s" % gateway if gateway else "")
            setupmod.add_host(path, name, "node", preset, target or "ucore")
            if net:
                text = util.read_text(path).rstrip("\n")
                util.write_text(path, text + "\n    disk: auto\n    network: %s\n" % net, 0o600)
            else:
                util.write_text(path, util.read_text(path).rstrip("\n") + "\n    disk: auto\n", 0o600)
            self.save_fleet()
        self.audit(user, "machine added", hosts=[name])
        self.fleet_info(fresh=True)
        return name

    # ---- settings: what the app's form edits, from the page ---------------------------
    def schema(self):
        """The settings every role and module has, the base fields and the targets, as the CLI
        publishes them (`roles --porcelain`, `targets --porcelain`). They change with the
        program only: asked once per version, kept in RAM."""
        path = os.path.join(self.run, "schema-%s.json" % VERSION)
        if not os.path.isfile(path):
            out = {}
            for what in ("roles", "targets"):
                r = subprocess.run(list(self.kiwi) + [what, "--porcelain"], capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL, cwd=self.fleet_dir, env=self._env())
                try:
                    out.update(json.loads(r.stdout))
                except (ValueError, TypeError):
                    raise KiwiError("the settings could not be read here: %s" % (
                        (r.stderr or r.stdout).strip().replace("error: ", "").splitlines() or ["no answer"])[-1][:200])
            util.write_text(path, json.dumps(out), 0o600)
        return json.loads(util.read_text(path))

    def settings(self, host=None):
        """What the settings form shows for one machine, or for the defaults every machine
        inherits (host None): {"host", "groups"} (form.groups)."""
        from . import form
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        if host is not None and not HOST.match(host):
            raise KiwiError("not a host name: %r" % host)
        schema = self.schema()
        doc = form.Doc.parse(util.read_text(os.path.realpath(self.fleet_path)))
        doc.block(host)             # says so when there is no such machine
        return {"host": host, "groups": form.groups(schema, schema.get("targets") or {}, doc, host)}

    def _problems(self):
        """What `validate` finds wrong in the fleet file as it is now. A machine that is not on
        the VPN yet is a step still to take ("steps"), not a mistake. KiwiError when it cannot say."""
        r = subprocess.run(list(self.kiwi) + ["validate", self.fleet_path, "--porcelain"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, cwd=self.fleet_dir, env=self._env())
        try:
            return [str(e) for e in json.loads(r.stdout).get("errors") or []]
        except (ValueError, AttributeError):
            # no answer is not "nothing wrong": the file could not even be read, or the check broke
            lines = (r.stderr or r.stdout).strip().splitlines()
            said = [x[7:] for x in lines if x.startswith("error: ")] or lines[-1:] or ["the fleet file could not be checked"]
            raise KiwiError(said[0][:300])

    def save_settings(self, host, changes, user="admin"):
        """Changed settings of a machine, or of the defaults (host None), into the fleet file:
        {path: value}, None takes an override away. `validate` has the last word: when the
        change brings a mistake, the file stays as it was and KiwiError says the first one.
        What was wrong before is not held against the change, or two machines with a mistake
        each could never be put right. Returns the paths that changed."""
        from . import form
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        with self.lock:
            path = os.path.realpath(self.fleet_path)        # in RAM on a control node: nothing beside the link
            before = util.read_text(path)
            doc = form.Doc.parse(before)
            keys = doc.apply(host, changes)
            if not keys:
                return []
            text = doc.dump()
            kept = False
            try:
                util.write_text(path, text, 0o600)
                wrong = self._problems()
                if wrong:
                    util.write_text(path, before, 0o600)
                    was = self._problems()
                    wrong = [e for e in wrong if e not in was]
                    if wrong:
                        schema = self.schema()
                        found = form.groups(schema, schema.get("targets") or {}, form.Doc.parse(before), host)
                        raise KiwiError(form.plain(wrong[0], found, host) + (
                            "" if len(wrong) == 1 else " (and %d more)" % (len(wrong) - 1)))
                    util.write_text(path, text, 0o600)
                kept = True
            finally:
                if not kept:        # whatever went wrong: the file as it was
                    util.write_text(path, before, 0o600)
            self.save_fleet()
        # which settings, never what they are now: a value may be a secret
        self.audit(user, "settings changed", hosts=[host] if host else [], keys=keys)
        self.fleet_info(fresh=True)
        return keys

    def _write(self, meta):
        os.makedirs(self.jobs_dir, mode=0o700, exist_ok=True)
        util.write_text(os.path.join(self.jobs_dir, meta["id"] + ".json"), json.dumps(meta), 0o600)

    def job(self, jid):
        if not jid.replace("-", "").isalnum():
            return None
        path = os.path.join(self.jobs_dir, jid + ".json")
        return json.loads(util.read_text(path)) if os.path.isfile(path) else None

    def log(self, jid, offset=0):
        path = os.path.join(self.jobs_dir, jid + ".log")
        if not self.job(jid) or not os.path.isfile(path):
            return "", offset
        with open(path, "rb") as fh:
            fh.seek(offset)
            data = fh.read()
        return data.decode(errors="replace"), offset + len(data)

    def jobs(self, n=20):
        names = sorted((f for f in os.listdir(self.jobs_dir) if f.endswith(".json")), reverse=True)[:n]
        return [json.loads(util.read_text(os.path.join(self.jobs_dir, f))) for f in names]

    # ---- the looks it takes by itself ------------------------------------------------
    def status(self):
        """The last `status --porcelain`, or None."""
        path = os.path.join(self.run, "status.json")
        return json.loads(util.read_text(path)) if os.path.isfile(path) else None

    looking = False                         # a look at every machine is under way

    def look(self):
        """A look at every machine, in the background; one at a time."""
        if not self.looking and not self.locked():
            self.looking = True             # from now, so that the page that follows says so
            threading.Thread(target=self.refresh_status, daemon=True).start()

    def refresh_status(self):
        if self.locked():
            return None
        self.looking = True
        try:
            with self.lock:                 # after the running job: every command saves the store when it ends
                r = subprocess.run(list(self.kiwi) + ["status", self.fleet_path, "--porcelain"], capture_output=True,
                                   text=True, stdin=subprocess.DEVNULL, cwd=self.fleet_dir)
        finally:
            self.looking = False
        try:
            data = json.loads(r.stdout)
        except ValueError:
            data = {"hosts": [], "error": (r.stderr or r.stdout or "no output").strip()[-500:]}
        data["time"] = self.clock()
        util.write_text(os.path.join(self.run, "status.json"), json.dumps(data), 0o600)
        try:
            self.fleet_info(fresh=True)
        except (OSError, ValueError):
            pass
        return data

    def fleet_info(self, fresh=False):
        """What the fleet file says: every machine with its role, web addresses and next step
        (`list --porcelain`). Kept in RAM; asked again after a job and with every look."""
        path = os.path.join(self.run, "fleet.json")
        if self.locked():
            return None
        if fresh or not os.path.isfile(path):
            r = subprocess.run(list(self.kiwi) + ["list", self.fleet_path, "--porcelain"], capture_output=True,
                               text=True, stdin=subprocess.DEVNULL, cwd=self.fleet_dir, env=self._env())
            try:
                json.loads(r.stdout)
            except ValueError:
                return None
            util.write_text(path, r.stdout, 0o600)
        return json.loads(util.read_text(path))

    # ---- logins: what a person types to get into a service ------------------------------
    def logins(self, host):
        """The logins of one machine's services with their passwords (`logins --porcelain`):
        [{module, label, address, user, has_password, password, note}]. Asked anew every time
        and handed on: nothing of it is kept here, in memory or on a disk. KiwiError says why not;
        only what the command wrote as its error is in it, never what it printed."""
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        if not HOST.match(host or ""):
            raise KiwiError("not a host name: %r" % host)
        try:
            r = subprocess.run(list(self.kiwi) + ["logins", self.fleet_path, host, "--porcelain"], capture_output=True,
                               text=True, stdin=subprocess.DEVNULL, cwd=self.fleet_dir, env=self._env(), timeout=120)
        except subprocess.TimeoutExpired:
            raise KiwiError("the logins of %s took too long to work out" % host)
        try:
            return next(h["logins"] for h in json.loads(r.stdout)["hosts"] if h["name"] == host)
        except (ValueError, KeyError, TypeError, StopIteration):
            lines = [x for x in (r.stderr or "").strip().splitlines() if x.strip()]
            raise KiwiError((lines[-1] if lines else "no answer").replace("error: ", "")[:300])

    # ---- devices: phones and laptops on the VPN ---------------------------------------
    def devices(self):
        d = os.path.join(self.fleet_dir, "secrets", "devices")
        if self.locked() or not os.path.isdir(d):
            return []
        return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".conf") and HOST.match(f[:-5]))

    def device_config(self, name):
        """A device's WireGuard config (from the open store), or None."""
        if self.locked() or not HOST.match(name or ""):
            return None
        path = os.path.join(self.fleet_dir, "secrets", "devices", name + ".conf")
        return util.read_text(path) if os.path.isfile(path) else None

    def add_device(self, name, group="", user="admin"):
        """A new phone or laptop on the VPN: the master makes its key and address
        (`enroll`). Returns its config; KiwiError says why not."""
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        if not HOST.match(name or ""):
            raise KiwiError("a name for the device: lowercase letters, digits and dashes, like annas-phone")
        if name in self.devices():
            raise KiwiError("a device called %s exists already" % name)
        argv = list(self.kiwi) + ["enroll", self.fleet_path, name] + (["--group", group] if group else [])
        with self.lock:
            r = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=self.fleet_dir)
            if not self._pushed():
                self.save_fleet()
        if r.returncode != 0:
            raise KiwiError(((r.stderr or r.stdout).strip().replace("error: ", "").splitlines()
                             or ["the master did not take the new device"])[-1][:300])
        self.audit(user, "added the device %s" % name)
        return self.device_config(name)

    def remove_device(self, name, user="admin"):
        """A device off the VPN (a lost phone): the master forgets its key."""
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        if name not in self.devices():
            raise KiwiError("there is no device called %s" % name)
        with self.lock:
            r = subprocess.run(list(self.kiwi) + ["enroll", self.fleet_path, name, "--remove"], capture_output=True,
                               text=True, stdin=subprocess.DEVNULL, cwd=self.fleet_dir)
            if not self._pushed():
                self.save_fleet()
        if r.returncode != 0:
            raise KiwiError(((r.stderr or r.stdout).strip().replace("error: ", "").splitlines()
                             or ["the master did not remove the device"])[-1][:300])
        self.audit(user, "removed the device %s" % name)

    @staticmethod
    def qr_svg(text):
        """The config as a QR code a phone's WireGuard app scans, or ''."""
        from . import qr
        try:
            return qr.svg(text)
        except KiwiError:
            return ""

    def loop(self, stop):
        """The timers: the fleet's status every status_minutes, `watch --once` (RAM masters)
        every watch_seconds. Runs until `stop` (a threading.Event) is set."""
        last_status = 0
        while not stop.is_set():
            now = self.clock()
            try:
                if self.locked():           # nothing to do it with until someone logs in, or the ring answers
                    if self.try_auto():
                        self.look()
                    else:
                        stop.wait(30)
                    continue
                self.reload_if_pushed()
                if now - last_status >= self.status_minutes * 60:
                    last_status = now
                    self.refresh_status()
                if self.watch_seconds and self.has_ram_masters():
                    with self.lock:
                        subprocess.run(self.argv("watch"), capture_output=True, stdin=subprocess.DEVNULL, cwd=self.fleet_dir)
            except Exception as e:              # a look that fails must not end the timers
                util.warn("control: %s" % e)
            stop.wait(self.watch_seconds or 60)

    def _backup_hosts(self):
        """Does the fleet file say where its own copy goes (backup.hosts)?"""
        import yaml
        try:
            with open(self.fleet_path) as fh:
                data = yaml.safe_load(fh) or {}
            return bool(((data.get("defaults") or {}).get("backup") or {}).get("hosts"))
        except (OSError, ValueError, AttributeError, yaml.YAMLError):
            return False

    def has_ram_masters(self):
        try:
            import yaml
            with open(self.fleet_path) as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, ValueError):
            return False
        default = (data.get("defaults") or {}).get("target")
        return any(((h or {}).get("target") or default) == "coreos-ram" for h in (data.get("hosts") or {}).values())
