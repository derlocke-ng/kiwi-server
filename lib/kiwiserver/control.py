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
import subprocess
import sys
import threading
import time

from . import store as storemod, util
from .util import KiwiError

HOST = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
FLEET_FILES = ("fleet.yaml", "federation.yaml")     # at rest on the node: <name>.age
SESSION_HOURS = 12
MIN_PASSWORD = 12
# what a page may start: the command and whether it takes hosts
ACTIONS = {
    "status": ("status", True), "plan": ("plan", True), "apply": ("apply", True),
    "check": ("check", False), "watch": ("watch", True), "backup": ("backup", False),
    "save": ("backups", True),
}
CHANGES = {"apply", "backup"}         # these change machines or the fleet: the audit log says who


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
        self._join = None                     # the join code that is out, if one is
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
    def argv(self, action, hosts=()):
        if action not in ACTIONS:
            raise KiwiError("unknown action %r" % action)
        if action == "save":                # one machine's data, to its backup machines, now
            if len(hosts) != 1:
                raise KiwiError("which machine?")
            return list(self.kiwi) + ["backups", self.fleet_path, hosts[0], "--now"]
        cmd, takes_hosts = ACTIONS[action]
        argv = list(self.kiwi) + [cmd, self.fleet_path]
        if takes_hosts:
            argv += list(hosts)
        if action == "watch":
            argv.append("--once")
        return argv

    def start(self, action, hosts=(), user="admin", wait=False):
        """A job in a thread; its id. The jobs wait for each other."""
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        for h in hosts:
            if not HOST.match(h):
                raise KiwiError("not a host name: %r" % h)
        argv = self.argv(action, hosts)
        jid = time.strftime("%Y%m%d-%H%M%S-") + pysecrets.token_hex(3)
        meta = {"id": jid, "action": action, "hosts": list(hosts), "user": user,
                "state": "waiting", "started": None, "ended": None, "rc": None}
        self._write(meta)
        if action in CHANGES:
            self.audit(user, action, hosts=list(hosts), job=jid)
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
            env = dict(os.environ, PYTHONUNBUFFERED="1")
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
        if meta["action"] in ("status", "apply", "save"):
            self.look()                         # what the pages show is what is true now

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
                               text=True, stdin=subprocess.DEVNULL, cwd=self.fleet_dir)
            try:
                json.loads(r.stdout)
            except ValueError:
                return None
            util.write_text(path, r.stdout, 0o600)
        return json.loads(util.read_text(path))

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
                             or ["the hub did not take the new device"])[-1][:300])
        self.audit(user, "added the device %s" % name)
        return self.device_config(name)

    @staticmethod
    def qr_svg(text):
        """The config as a QR code a phone's WireGuard app scans (qrencode), or ''."""
        try:
            r = subprocess.run(["qrencode", "-t", "SVG", "-m", "2", "-o", "-"], input=text.encode(), capture_output=True)
        except OSError:
            return ""
        svg = r.stdout.decode(errors="replace") if r.returncode == 0 else ""
        return svg[svg.find("<svg"):] if "<svg" in svg else ""

    def loop(self, stop):
        """The timers: the fleet's status every status_minutes, `watch --once` (RAM masters)
        every watch_seconds. Runs until `stop` (a threading.Event) is set."""
        last_status = 0
        while not stop.is_set():
            now = self.clock()
            try:
                if self.locked():           # nothing to do it with until someone logs in
                    stop.wait(15)
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

    def has_ram_masters(self):
        try:
            import yaml
            with open(self.fleet_path) as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, ValueError):
            return False
        default = (data.get("defaults") or {}).get("target")
        return any(((h or {}).get("target") or default) == "coreos-ram" for h in (data.get("hosts") or {}).values())
