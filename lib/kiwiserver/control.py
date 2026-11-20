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
import tempfile
import sys
import threading
import time
import yaml

from . import VERSION, store as storemod, util
from .util import KiwiError

HOST = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
FLEET_FILES = ("fleet.yaml", "federation.yaml")     # at rest on the node: <name>.age
SESSION_HOURS = 12             # a login lasts this long at most
SESSION_IDLE_MINUTES = 30      # and ends this long after its last use (the page's own looks do not count)
MIN_PASSWORD = 12
MEDIA_ROOM = 1000 * 1024 * 1024     # what one machine's install media may take
# what a page may start: the command and whether it takes hosts
ACTIONS = {
    "status": ("status", True), "plan": ("plan", True), "apply": ("apply", True),
    "check": ("check", False), "watch": ("watch", True), "backup": ("backup", False),
    "save": ("backups", True), "update": ("apply", False), "rotate": ("rotate", False),
    "rotate-seed": ("rotate", False), "setup": ("next", True), "build": ("build", True),
    "rotate-ring": ("rotate", False),   # new keys for an unlock ring (module: its name), every disk bound again
    "restore": ("backups", True), "unlock": ("unlock", True), "remove": ("remove", True),
    "wake": ("wake", True),             # a machine at home that is off: another of its LAN sends the magic packet
    "tell-test": ("notify", False), "mtu": ("mtu", True),
    "start-new": ("backups", True),     # a rebuilt machine whose backups wait: a new history instead
    "prune": ("prune", True),           # old snapshots off a backup machine, by its keep_* settings
    "doctor": ("doctor", False),        # are this node's own tools here (install media, checks)
    "adopt": ("adopt", True),           # a waiting starter, taken in (take_in)
    "service": ("service", True),       # one container of a machine's stack: restart, stop, start, logs
    "reboot": ("power", True),          # a machine restarted, or switched off (power.py)
    "poweroff": ("power", True),
}
CHANGES = {"apply", "backup", "update", "save", "rotate", "rotate-seed", "rotate-ring", "setup", "build", "restore", "unlock", "remove",
           "start-new", "prune", "service", "reboot", "poweroff"}
MODULE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
WHEN = re.compile(r"^\d{4}-\d\d-\d\d[T ]\d\d:\d\d(:\d\d(\.\d+)?)?(Z|[+-]\d\d:?\d\d)?$")      # a snapshot's time, as `backups --porcelain` gives it     # these change machines or the fleet: the audit log says who



def starter_handshake(fleet_path, fleet_dir, address, code, preset, name, domain, ask=None):
    """The first half of taking a waiting starter in, the same from the control node and from a
    computer (`kiwi-server take-in`): the address and the name checked, the starter told the
    network's key with the code from its screen, its host keys kept for its address. (address, name)."""
    import ipaddress
    import yaml
    from . import setup as setupmod
    address = (address or "").strip()
    try:
        ipaddress.ip_address(address)
    except ValueError:
        raise KiwiError("An address looks like 192.168.1.30: the waiting machine shows its own on its screen.")
    if preset not in setupmod.PRESETS:
        raise KiwiError("what should it do?")
    name = (name or "").strip().lower()
    hosts = (yaml.safe_load(util.read_text(fleet_path)) or {}).get("hosts") or {}
    if name and name in hosts:
        raise KiwiError("there is already a machine called %s" % name)
    if any(str((h or {}).get("network", {}).get("address", "")).split("/")[0] == address for h in hosts.values()):
        raise KiwiError("a machine of the network has that address already")
    pub = os.path.join(fleet_dir, "secrets", "admin_ed25519.pub")
    if not os.path.isfile(pub):
        raise KiwiError("the network's own SSH key is missing (secrets/admin_ed25519.pub)")
    key = util.read_text(pub).strip()
    nonce = pysecrets.token_hex(16)
    answer = (ask or _ask_starter)(address, {"nonce": nonce, "key": key, "network": domain,
                                             "mac": join_mac(code, "kiwi-join-1", nonce, domain, key)})
    keys, hostname = list(answer.get("host_keys") or []), str(answer.get("hostname") or "")
    # the answer counts only from the machine that knows the code, and only as keys, nothing else
    if not hmac.compare_digest(str(answer.get("mac") or ""), join_mac(code, "kiwi-join-2", nonce, "\n".join(keys), hostname)):
        raise KiwiError("the answer at %s is not from the machine whose code you typed: nothing was kept" % address)
    if not keys or len(keys) > 4 or not all(isinstance(k, str) and HOST_KEY.match(k) for k in keys):
        raise KiwiError("the machine at %s sent something that is not its SSH host keys: nothing was kept" % address)
    known = os.path.join(fleet_dir, "secrets", "known_hosts")
    subprocess.run(["ssh-keygen", "-R", address, "-f", known], capture_output=True)
    if os.path.isfile(known + ".old"):
        os.remove(known + ".old")
    with open(known, "a") as fh:
        for k in keys:
            fh.write("%s %s\n" % (address, k))
    return address, name


HOST_KEY = re.compile(r"^(ssh-ed25519|ecdsa-sha2-nistp(256|384|521)|ssh-rsa) [A-Za-z0-9+/]{40,1000}={0,2}$")
HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,62})$")


def join_mac(code, *parts):
    """The proof of the code on a starter's screen, without sending the code: HMAC over the parts.
    Both sides know the code (the person typed it on one, the other shows it); a device that only
    answers at the address does not, so it can neither take the network's key nor send keys of its own."""
    msg = "\x1f".join(str(p) for p in parts).encode()
    return hmac.new((code or "").strip().lower().encode(), msg, hashlib.sha256).hexdigest()


def _ask_starter(address, body, timeout=30):
    """The waiting starter's answer to being taken in (join_as_starter, through its page's API),
    or KiwiError with what it said. HTTPS with the starter's own certificate, which nobody vouches
    for: what proves the answer is its HMAC with the code (join_mac), not the certificate."""
    import ssl
    import urllib.error
    import urllib.request
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    req = urllib.request.Request("https://%s:8743/api/join-me" % address, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            out = json.loads(r.read(100000) or b"{}")
    except urllib.error.HTTPError as e:
        with e:
            try:
                why = json.loads(e.read(10000) or b"{}").get("error")
            except ValueError:
                why = None
        raise KiwiError(why or "the machine at %s said no (%d)" % (address, e.code))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise KiwiError("no starter answers at %s: is it switched on, and does its screen show that address? (%s)"
                        % (address, getattr(e, "reason", e)))
    if not out.get("host_keys"):
        raise KiwiError("the machine at %s gave no host keys" % address)
    return out


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
        self._riseup_at = 0         # when Riseup certificates were last looked at (loop)
        self.clock = clock
        self.lock = threading.Lock()          # one job at a time: two applies never cross
        self._reads = {}                      # what a `--porcelain` read worked out, until the fleet changes (_read_once)
        self._gen = 0                         # counted up with every job that ends: nothing kept outlives one
        self.failures = {}                    # address -> [times] of failed logins
        self._session_key = pysecrets.token_bytes(32)   # sessions end with the process, as the open store does
        self._identity = None                 # the key the password made, while the store is open
        self._saved = {}                      # name -> sha256 of the fleet file as it was last saved or opened
        self._age = {}                        # name -> sha256 of its encrypted file on disk, as this process left it
        self._arming = threading.Lock()       # see arm_auto
        self._join = None                     # the join code that is out, if one is
        self._setup_code = None               # the first run's code, while there is no network here yet
        self._joined = ""                     # a starter: the network whose control node is taking it in
        self._hoster = {}                     # the first run: a sign-in at a hoster, by its handle (hoster_start)
        self._tokens = {}                     # token -> (controller, expires): who may sync
        self._accounts_kept = {}              # the Accounts page's reads: argv -> (until, answer); "down" -> (until, error)
        self._play_last = {}                  # the game page: (machine, container) -> when it was last clicked
        self._play_conf = (0, None)           # the accounts' OpenID configuration, as last asked: (until, conf)

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

    def damaged(self):
        """The files of the network here that are there but empty: what a power cut in the
        middle of a write left behind before 2.103.0. With one of them empty neither the
        password nor the ring opens anything, and the page has to say why."""
        names = [n + ".age" for n in FLEET_FILES] + ["secrets.age", "secrets.recipients", os.path.join("control", "salt")]
        return [n for n in names if os.path.isfile(os.path.join(self.fleet_dir, n))
                and os.path.getsize(os.path.join(self.fleet_dir, n)) == 0]

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

    def setup_code_ok(self, code, address="?", mac=None):
        """Five wrong tries from an address: ten minutes of nothing, as at the login. `mac`: instead
        of the code, (parts, proof) made with it (join_mac), so that the code itself never travels."""
        now = self.clock()
        recent = [t for t in self.failures.get(address, []) if now - t < 600]
        self.failures[address] = recent
        if len(recent) >= 5 or sum(len(v) for v in self.failures.values()) >= 50:     # and a cap over all addresses
            return False
        if mac is not None:
            parts, proof = mac
            if hmac.compare_digest(str(proof or ""), join_mac(self.setup_code(), *parts)):
                return True
        elif hmac.compare_digest((code or "").strip().lower().encode(), self.setup_code().encode()):
            return True
        recent.append(now)
        return False

    # ---- a starter that joins a network that exists ---------------------------------------
    # A second machine started from the starter stick shows the same first-run page and setup
    # code. The network's control node takes it in: the person types the code from this machine's
    # screen into the control page, which sends it here with the key it will log in with. The
    # code is the proof that the person is at this machine; the key goes to the admin user's
    # authorized keys, through the starter key, and this machine's own host keys go back, so that
    # the control node knows it is talking to this machine and no other.
    def joined(self):
        return self._joined

    def join_as_starter(self, nonce, mac, key, network, address="?"):
        """This starter is taken into a network: (its SSH host keys, its hostname, and the proof that
        this machine, which shows the code, sent them). Once. The code never travels: `mac` proves it."""
        from .cli import STARTER_KEY
        if os.environ.get("KIWI_STARTER") != "1" or not self.fresh():
            raise KiwiError("this machine is no starter waiting to be set up")
        if self._joined:
            raise KiwiError("this machine is being taken into %s already" % self._joined)
        nonce, key, network_raw = str(nonce or ""), (key or "").strip(), str(network or "")
        if not re.match(r"^[0-9a-f]{32}$", nonce) or not self.setup_code_ok(
                None, address, mac=(("kiwi-join-1", nonce, network_raw, key), mac)):
            raise KiwiError("that is not the code on this machine's screen")
        if "\n" in key or not re.match(r"^(ssh-ed25519|ecdsa-sha2-nistp(256|384|521)|ssh-rsa) [A-Za-z0-9+/]+={0,2}( [^\s]{0,80})?$", key):
            raise KiwiError("that is not a public SSH key")
        network = re.sub(r"[^A-Za-z0-9 ._-]", "", network_raw)[:60] or "a network"
        script = ('read -r k; h=/home/core/.ssh; grep -qxF "$k" "$h/authorized_keys" 2>/dev/null '
                  '|| printf "%s\\n" "$k" >> "$h/authorized_keys"; cat /etc/ssh/ssh_host_*_key.pub; hostname')
        try:
            r = subprocess.run(["ssh", "-i", STARTER_KEY, "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                                "-o", "IdentityAgent=none", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                                "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=10", "core@127.0.0.1", script],
                               input=key + "\n", capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise KiwiError("this machine could not reach itself: %s" % e)
        lines = [x.strip() for x in r.stdout.splitlines() if x.strip()]
        keys = [x for x in lines if re.match(r"^(ssh-ed25519|ecdsa-sha2-nistp\d+|ssh-rsa) ", x)]
        if r.returncode != 0 or not keys:
            raise KiwiError("this machine could not let the key in: %s" % (r.stderr.strip()[-200:] or "no answer"))
        host_keys = [" ".join(k.split()[:2]) for k in keys]
        hostname = lines[-1] if lines[-1] not in keys else ""
        proof = join_mac(self.setup_code(), "kiwi-join-2", nonce, "\n".join(host_keys), hostname)
        self._joined, self._setup_code = network, None        # the code worked once: no first run here any more
        self.audit("starter", "taken in by %s" % network, address=address)
        return {"host_keys": host_keys, "hostname": hostname, "mac": proof}

    def take_in(self, address, code, preset, name="", user="admin", ask=None):
        """A machine that runs the starter stick and waits on the home network: told the network's
        key, with the code from its screen as the proof that the person is at it; added to the fleet
        with the address it has; then taken over and set up by a job (`kiwi-server adopt`). The job's id."""
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        info = self.fleet_info() or {}
        # what adding it would refuse (its name, its address, what it should do), said before the stick
        # is told anything: a stick that was taken in and then refused waits until it is restarted
        from . import setup as setupmod
        trial = tempfile.mkdtemp(dir=self.run)          # RAM, as the fleet file itself
        try:
            shutil.copyfile(os.path.realpath(self.fleet_path), os.path.join(trial, "fleet.yaml"))
            name = setupmod.add_home_machine(os.path.join(trial, "fleet.yaml"), preset, (address or "").strip(), name, "coreos")
        finally:
            shutil.rmtree(trial, ignore_errors=True)
        address, name = starter_handshake(os.path.realpath(self.fleet_path), self.fleet_dir, address, code, preset, name,
                                          str(info.get("domain") or ""), ask=ask)
        made = self.add_machine(preset, address, name, target="coreos", user=user)
        self.audit(user, "a waiting starter taken in", hosts=[made], address=address)
        return self.start("adopt", [made], user=user, at=address)

    # ---- the first run: a hoster's servers, after signing in there ------------------------
    # The page's "Sign in and pick it": the device login runs here (the person confirms it at
    # the hoster, in their browser), then the account's servers are listed. The login lands in
    # RAM; first_run puts it into the new network's sealed secrets, as `init` does on a computer.
    def hoster_start(self, provider, code, address="?", user=""):
        """A sign-in at the hoster, started: its handle, for hoster_state. The setup code guards it on
        a machine with nothing on it; `user`: someone logged in, adding a master to a network without one."""
        from . import providers
        if user:
            if self.locked():
                raise KiwiError("the fleet is locked: log in")
        elif not self.fresh():
            raise KiwiError("this machine has a network already")
        elif not self.setup_code_ok(code, address):
            raise KiwiError("that is not the setup code. After five wrong tries, wait ten minutes")
        who = providers.get(provider)
        handle = pysecrets.token_urlsafe(18)
        shutil.rmtree(os.path.join(self.run, "hoster"), ignore_errors=True)    # one sign-in at a time: an older one is dropped
        # a folder of its own: an older sign-in still finishing writes its login nowhere this one reads
        d = os.path.join(self.run, "hoster", handle)
        os.makedirs(d, mode=0o700)
        state = {"provider": who.id, "name": who.name, "state": "starting", "url": "", "rows": [], "error": "",
                 "token": os.path.join(d, "%s.token" % who.id)}
        self._hoster = {handle: state}

        def work():
            try:
                who.login(state["token"], lambda url: state.update(state="signing-in", url=url))
                rows = who.servers(state["token"])
                state.update(rows=rows, state="listed" if rows else "failed",
                             error="" if rows else "%s lists no server for this account" % who.name)
            except Exception as e:      # noqa: BLE001 - said on the page, whatever it was
                state.update(state="failed", error=str(e).replace("error: ", "")[:200])
        threading.Thread(target=work, daemon=True).start()
        return handle

    def hoster_state(self, handle):
        """{provider, name, state (starting, signing-in, listed, failed), url, rows, error}, or None."""
        s = self._hoster.get(handle or "")
        return None if s is None else {k: s[k] for k in ("provider", "name", "state", "url", "rows", "error")}

    def _hoster_pick(self, handle, answers):
        """The picked server, checked against the list the sign-in gave: (token path, its row)."""
        s = self._hoster.get(handle or "")
        if not s or s["state"] != "listed" or s["provider"] != answers.get("master_where"):
            raise KiwiError("the sign-in at the hoster is gone (this machine restarted?): sign in there again, "
                            "or type the address")
        row = next((r for r in s["rows"] if str(r["id"]) == str(answers["provider_server"])), None)
        if row is None or not os.path.isfile(s["token"]):
            raise KiwiError("that server is not in the hoster's list: sign in there again")
        return s["token"], row

    def first_run(self, code, password, answers, address="?", hoster=""):
        """The new network from the questionnaire's answers. Returns (recovery key, session token).
        `hoster`: the handle of a sign-in at the hoster whose server was picked (hoster_start)."""
        from . import providers, setup as setupmod
        with self.lock:
            if not self.fresh():
                raise KiwiError("this machine has a network already")
            if self._joined:
                raise KiwiError("this machine is being taken into %s" % self._joined)
            if not self.setup_code_ok(code, address):
                raise KiwiError("that is not the setup code")
            password = (password or "").strip()
            if len(password) < MIN_PASSWORD:
                raise KiwiError("the password needs %d characters at least: it guards every secret of the network" % MIN_PASSWORD)
            bad = setupmod.check(answers)
            if bad:
                raise KiwiError("; ".join(bad.values()))
            login = None
            if answers.get("provider_server"):
                login, row = self._hoster_pick(hoster, answers)
                answers = dict(answers, provider_server=row["id"])
            tmp = os.path.join(self.run, "first-run")       # in RAM
            shutil.rmtree(tmp, ignore_errors=True)
            os.makedirs(tmp, mode=0o700)
            try:
                data, _made = setupmod.make(answers, tmp)
                setupmod.write(os.path.join(tmp, "fleet.yaml"), data)
                if login:                       # the hoster's login is the network's now, sealed with its secrets
                    keep = os.path.join(tmp, providers.get(answers["master_where"]).token_file())
                    os.makedirs(os.path.dirname(keep), mode=0o700, exist_ok=True)
                    util.write_bytes(keep, util.read_bytes(login), 0o600)
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
            self._hoster = {}
            shutil.rmtree(os.path.join(self.run, "hoster"), ignore_errors=True)
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
                if "no signature" in str(e):       # a choice for the terminal, with its own words
                    raise KiwiError("this copy has no signature: an older copy of yours comes back with "
                                    "`kiwi-server restore FILE --unsigned` on a computer")
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
        from . import recovery
        recovery.record_copy(fleet, fetched=True)
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
            tmp = util.tmp_name(path + ".age")
            with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as fh:
                fh.write(blob)
            util.settle(tmp, path + ".age")
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
        rings = pin.get("rings") or [pin]
        t = max(1, min(int(pin.get("threshold") or 1), len(rings)))
        if len(rings) == 1:
            how = ["tang", json.dumps({"url": rings[0]["url"], "adv": rings[0]["adv"]})]
        else:                                    # t of them together (1: any one, no single machine it hangs on)
            how = ["sss", json.dumps({"t": t, "pins": {"tang": [{"url": r["url"], "adv": r["adv"]} for r in rings]}})]
        try:
            c = subprocess.run(["clevis", "encrypt"] + how, input=self._identity.encode(), capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            self.audit("control", "unlock by itself was not set up: %s" % e)
            return None
        if c.returncode != 0 or not c.stdout.strip():
            self.audit("control", "unlock by itself was not set up: clevis refused")
            return None
        names = ", ".join(r["ring"] for r in rings) + (" (%d of %d together)" % (t, len(rings)) if t > 1 else "")
        util.write_bytes(self.auto_file, c.stdout, 0o600)
        util.write_text(self.auto_file + ".json", json.dumps({"ring": names, "server": ", ".join(r["server"] for r in rings)}), 0o600)
        return names

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
            state = {}
        names = [h["name"] for h in (self.fleet_info() or {}).get("hosts") or []]

        def waits(what):
            done = (state.get(what) or {}).get("done")
            return [] if done is None else [n for n in names if n not in done]
        last = state.get("last") or {}
        # the unlock rings: those the machines name, with what waits and when they were last replaced
        try:
            import yaml
            data = yaml.safe_load(util.read_text(os.path.realpath(self.fleet_path))) or {}
            named = sorted({str(r) for h in (data.get("hosts") or {}).values() if isinstance(h, dict)
                            for r in ((h.get("unlock") or {}).get("rings") or [])} - {"operator"})
        except (OSError, ValueError):
            named = []
        rings = [{"name": r, "pending": list(((state.get("rings") or {}).get(r) or {}).get("pending") or []),
                  "under_way": bool((state.get("rings") or {}).get(r)), "last": last.get("ring:" + r)} for r in named]
        return {"pending": waits("ssh"), "last": last.get("ssh"), "seed_pending": waits("seed"), "seed_last": last.get("seed"),
                "rings": rings}

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
        return self._token("admin", int(now))

    def _token(self, user, issued):
        """user|issued|expires|mac: it ends SESSION_IDLE_MINUTES after its last use, and
        SESSION_HOURS after the login whatever happens."""
        expires = min(int(self.clock()) + SESSION_IDLE_MINUTES * 60, issued + SESSION_HOURS * 3600)
        body = "%s|%d|%d" % (user, issued, expires)
        return "%s|%s" % (body, hmac.new(self._session_key, body.encode(), hashlib.sha256).hexdigest())

    def _parts(self, token):
        """(user, issued, expires) of a token whose mark is ours, else None."""
        parts = (token or "").split("|")
        if len(parts) == 4:
            user, issued, expires, mac = parts
        elif len(parts) == 3:               # one made before 2.174.0: no idle end
            (user, expires, mac), issued = parts, None
        else:
            return None
        body = "|".join(parts[:-1])
        good = hmac.new(self._session_key, body.encode(), hashlib.sha256).hexdigest()
        try:
            return (user, int(issued) if issued is not None else None, int(expires)) if hmac.compare_digest(good, mac) else None
        except ValueError:
            return None

    def session(self, token):
        """The user a token belongs to, or None. A locked store has no sessions: the password opens it."""
        got = self._parts(token)
        if not got or got[2] < self.clock() or self.locked():
            return None
        return got[0]

    def refresh(self, token):
        """The token of a session that was just used, its idle end moved on; None when it has none."""
        got = self._parts(token)
        if not got or got[1] is None or got[2] < self.clock() or self.locked():
            return None
        return self._token(got[0], got[1])

    def csrf(self, token):
        """What a form of this session carries, so that only a page of ours can post for it: of the
        login (who and when), so a form stays good while the token's idle end moves on."""
        got = self._parts(token)
        stable = "%s|%s" % (got[0], got[1]) if got and got[1] is not None else (token or "")
        return hmac.new(self._session_key, ("csrf|%s" % stable).encode(), hashlib.sha256).hexdigest()

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
                    util.write_bytes(path, files[n], 0o644 if n == "secrets.recipients" else 0o600)
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
        out = []
        with open(path, errors="replace") as fh:
            for x in fh.readlines()[-n:]:
                try:            # a line cut off by a power cut is left out, not the whole history
                    d = json.loads(x)
                except ValueError:
                    continue
                if isinstance(d, dict):
                    out.append(d)
        return out[::-1]

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
        if action == "service":             # module: the container's name, at: what to do with it
            from .cli import SERVICE_ACTIONS, SERVICE_NAME
            if len(hosts) != 1:
                raise KiwiError("which machine?")
            if not SERVICE_NAME.match(module or "") or at not in SERVICE_ACTIONS:
                raise KiwiError("which service, and restart, stop, start or its log?")
            return list(self.kiwi) + ["service", self.fleet_path, hosts[0], module, at]
        if action == "doctor":              # about this node, not the fleet
            return list(self.kiwi) + ["doctor"]
        if action == "tell-test":           # one test message on every channel that is set
            return list(self.kiwi) + ["notify", self.fleet_path, "--test"]
        if action == "save":                # one machine's data, to its backup machines, now
            if len(hosts) != 1:
                raise KiwiError("which machine?")
            return list(self.kiwi) + ["backups", self.fleet_path, hosts[0], "--now"]
        if action in ("start-new", "prune"):
            if len(hosts) != 1:
                raise KiwiError("which machine?")
            if action == "prune":
                return list(self.kiwi) + ["prune", self.fleet_path, hosts[0]]
            return list(self.kiwi) + ["backups", self.fleet_path, hosts[0], "--new"]
        # new SSH keys, or a new seed, on every machine that still has the old ones
        if action == "rotate-ring":         # module: the ring; hosts: left out (gone for good)
            if not MODULE.match(module or ""):
                raise KiwiError("which ring?")
            argv = list(self.kiwi) + ["rotate", self.fleet_path, "ring", module]
            for h in hosts:
                argv += ["--waive", h]
            return argv
        if action in ("rotate", "rotate-seed"):
            argv = list(self.kiwi) + ["rotate", self.fleet_path, "ssh" if action == "rotate" else "seed"]
            for h in hosts:                 # left out: gone for good, or about to be installed anew
                argv += ["--waive", h]
            return argv
        if action == "adopt":               # a waiting starter: its key is in, its host keys are known
            import ipaddress
            if len(hosts) != 1:
                raise KiwiError("which machine?")
            try:
                ipaddress.ip_address(at)
            except ValueError:
                raise KiwiError("An address looks like 192.168.1.30.")
            return list(self.kiwi) + ["adopt", self.fleet_path, hosts[0], "--as", "core@%s" % at,
                                      "--key", os.path.join(self.fleet_dir, "secrets", "admin_ed25519")]
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
        if action in ("reboot", "poweroff"):    # the page asked first, with what it means
            if len(hosts) != 1:
                raise KiwiError("which machine?")
            return list(self.kiwi) + ["power", self.fleet_path, hosts[0], "off" if action == "poweroff" else "restart", "--yes"]
        if action == "wake":                # Wake-on-LAN, from a machine of its LAN
            if len(hosts) != 1:
                raise KiwiError("which machine?")
            return list(self.kiwi) + ["wake", self.fleet_path, hosts[0]]
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
        me = os.environ.get("KIWI_CONTROL_HOST")    # this node last: its own apply may restart this page
        hosts = [h for h in hosts if h != me] + [h for h in hosts if h == me]
        argv = self.argv(action, hosts, at.strip(), module.strip())
        if action == "update" and not hosts:    # every machine: each one's History says so, and how it went for it
            hosts = argv[argv.index("--update") + 1:]
        jid = time.strftime("%Y%m%d-%H%M%S-") + pysecrets.token_hex(3)
        meta = {"id": jid, "action": action, "hosts": list(hosts), "user": user,
                "state": "waiting", "started": None, "ended": None, "rc": None}
        self._write(meta)
        if action in CHANGES:
            more = {k: v for k, v in (("part", module.strip()), ("from", at.strip())) if v and action == "restore"}
            if action == "service":
                more = {"part": "%s: %s" % (module.strip(), at.strip())}
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
            if meta["action"] == "remove" and rc == 0:      # gone from the last look before the job says done:
                try:                                        # a page that reads "ok" never sees it half there
                    self._forget_in_status(meta["hosts"])
                except (OSError, ValueError) as e:
                    util.warn("control: the last look still names %s: %s" % (", ".join(meta["hosts"]), e))
            self._gen += 1          # whatever a job did: nothing that was read before it is kept
            meta.update(state="ok" if rc == 0 else "failed", ended=self.clock(), rc=rc)
            self._write(meta)
        if meta["action"] in CHANGES:           # how it ended, in the log that outlives a restart (a machine's History)
            failed = self._failed_hosts(meta) if len(meta["hosts"]) > 1 and meta["state"] == "failed" else None
            if failed:                          # several at once: each machine's own result
                for res, names in (("ok", [h for h in meta["hosts"] if h not in failed]),
                                   ("failed", [h for h in meta["hosts"] if h in failed])):
                    if names:
                        self.audit(meta["user"], meta["action"], hosts=names, job=meta["id"], result=res)
            else:
                self.audit(meta["user"], meta["action"], hosts=meta["hosts"], job=meta["id"], result=meta["state"])
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

    def _failed_hosts(self, meta):
        """The machines a job of several names as failed (`failed: a, b`, its last words), or None."""
        try:
            with open(os.path.join(self.jobs_dir, meta["id"] + ".log")) as fh:
                tail = fh.read()[-2000:]
        except OSError:
            return None
        m = re.search(r"failed: ([a-z0-9, -]+)\s*$", tail)
        if not m:
            return None
        names = {x.strip() for x in m.group(1).split(",")}
        return names & set(meta["hosts"]) or None

    # ---- linked networks (federation.yaml): what each may reach, and the way out -------
    def federation(self):
        """The partners as federation.yaml has them: {name: entry}. Linking a network needs
        both fleets, so it stays with the computer that holds them (`kiwi-server federate`)."""
        from . import federation as fedmod
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        return fedmod.partners(self.fleet_dir)

    def partner_cas(self):
        """{partner: {offered, why, mark, pem}}: whether this network's devices may import each
        linked network's CA (federation.ca_offer); pem only when they may."""
        from . import certs
        from . import federation as fedmod
        own = (self.fleet_info() or {}).get("domain") or ""
        out = {}
        for name, p in self.federation().items():
            try:
                ok, why = fedmod.ca_offer(p, own, certs.ca_permitted(p["ca"]) if p.get("ca") else [])
            except KiwiError as e:
                ok, why = False, "its CA could not be read: %s" % e
            pem = str(p.get("ca") or "") if ok else ""
            out[name] = {"offered": ok, "why": why, "pem": pem,
                         "mark": hashlib.sha256(pem.encode()).hexdigest()[:8] if pem else ""}
        return out

    def save_federation(self, changes, user="admin"):
        """{partner: {share: [...], from: [...], exit: bool, internet: bool}} into federation.yaml.
        The file says no when the change makes no sense (two exits, out through each other)."""
        import ipaddress
        import yaml
        from . import federation as fedmod
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        path = os.path.realpath(self._fleet_file(fedmod.FILE))
        with self.lock:
            if not os.path.isfile(path):
                raise KiwiError("this network is linked to no other")
            before = util.read_text(path)
            data = yaml.safe_load(before) or {}
            for name, c in changes.items():
                if name not in data:
                    raise KiwiError("no partner called %s" % name)
                share = [x for x in c.get("share") or [] if x]
                frm = [x for x in c.get("from") or [] if x]
                for x in share:
                    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,120}(:[0-9]{1,5})?", x):
                        raise KiwiError("%r is not a machine, machine:port or service.machine" % x)
                for x in frm:
                    try:
                        ipaddress.ip_network(x, strict=False)
                    except ValueError:
                        raise KiwiError("%r is not an address or a network like 10.10.3.0/24" % x)
                entry = dict(data[name] or {}, share=share, **{"from": frm})
                for k in ("exit", "internet"):
                    if c.get(k):
                        entry[k] = True
                    else:
                        entry.pop(k, None)
                data[name] = entry
            util.write_text(path, fedmod.dump(data), 0o644)
            try:
                fedmod.partners(self.fleet_dir)
            except KiwiError:
                util.write_text(path, before, 0o644)
                raise
            self.save_fleet()
        self.audit(user, "federation changed", partners=sorted(changes))
        self.fleet_info(fresh=True)

    # ---- reads that are kept until the fleet changes (docs/design/ui.md: fast) ---------------
    def _rev(self):
        """What the reads below depend on, as it is now: the fleet's files by time and size. A page
        view costs a few stats instead of a start of kiwi-server (0.3 to 0.6 s each)."""
        out = []
        for rel in ("fleet.yaml", "federation.yaml", ".kiwi-server.json", "secrets", "secrets/devices", "secrets/onion"):
            try:
                st = os.stat(os.path.join(self.fleet_dir, rel))
                out.append((rel, st.st_mtime_ns, st.st_size))
            except OSError:
                out.append((rel, 0, 0))
        try:
            st = os.stat(self.fleet_path)
            out.append(("fleet", st.st_mtime_ns, st.st_size))
        except OSError:
            pass
        return tuple(out)

    def _read_once(self, name, make, also=()):
        """`make()`'s answer, kept while the fleet's files and `also` stay as they are and no job
        ended (five minutes at most). A failure is never kept."""
        import copy
        key = (self._gen, self._rev(), tuple(also), tuple(self.kiwi))
        hit = self._reads.get(name)
        if hit and hit[0] == key and time.monotonic() - hit[1] < 300:
            return copy.deepcopy(hit[2])
        value = make()
        self._reads[name] = (key, time.monotonic(), value)
        return copy.deepcopy(value)

    # ---- who may reach what ------------------------------------------------------------
    def access(self):
        """The VPN's groups and the linked networks with what each may reach, and the
        machines a rule can name (`access --porcelain`): {master, who, machines}."""
        if self.locked():
            raise KiwiError("the fleet is locked: log in")

        def read():
            r = subprocess.run(list(self.kiwi) + ["access", self.fleet_path, "--porcelain"], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, cwd=self.fleet_dir, env=self._env())
            try:
                return json.loads(r.stdout)
            except ValueError:
                raise KiwiError((r.stderr.strip().splitlines() or ["the rules could not be read"])[-1].replace("error: ", ""))
        return self._read_once("access", read)

    def save_access(self, changes, new="", new_as="nothing", user="admin", new_lan=""):
        """{who: {reach: [...], peers, internet, admin}} into the fleet file (a group) or
        federation.yaml (a linked network), through `kiwi-server access`, which says no to
        a name that is none. Only what differs from now is written. `new`: a group to make.
        Returns the names that changed."""
        from . import access as accessmod
        now = {x["name"]: x for x in self.access()["who"]}
        todo = []
        if new:
            if not accessmod.NAME.match(new) or new in now:
                raise KiwiError("a new group needs a name of its own: small letters, digits and -")
            # what a new group starts with: nothing, every app, or everything with the admin pages
            if new_lan:             # a group of the home network: a subnet at home (docs/design/lans.md)
                todo.append((new, [new, "--new", "--lan", new_lan] + {"apps": ["apps"], "all": ["all", "--admin", "yes"]}.get(new_as, [])))
            else:
                todo.append((new, [new, "--new"] + {"apps": ["apps", "--peers", "yes"],
                                                     "all": ["all", "--peers", "yes", "--admin", "yes"]}.get(new_as, [])))
        for name, c in changes.items():
            x = now.get(name)
            if x is None:
                raise KiwiError("no group or linked network called %s" % name)
            reach = [str(e) for e in c.get("reach") or []]
            for e in reach:
                if not (accessmod.WORD.match(e) or accessmod._address(e)) or e.startswith("-"):
                    raise KiwiError("%r is not a machine, a service like cloud.papaya or a port like papaya:22" % e)
            argv = []
            if sorted(reach) != sorted(x["reach"]):
                argv = [name] + (reach or ["nothing"])
            if x["kind"] in ("group", "lan"):
                keys = accessmod.FLAGS if x["kind"] == "group" else ("admin",)       # the home's router decides the rest
                flags = [("--%s" % k, "yes" if c.get(k) else "no") for k in keys if bool(c.get(k)) != bool(x[k])]
                if flags:
                    argv = (argv or [name, "--add"]) + [w for f in flags for w in f]
            if argv:
                todo.append((name, argv))
        with self.lock:
            for name, argv in todo:
                r = subprocess.run(list(self.kiwi) + ["access", self.fleet_path] + argv, capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL, cwd=self.fleet_dir, env=self._env())
                if r.returncode:
                    self.save_fleet()           # what was written before this one stays
                    raise KiwiError("%s: %s" % (name, (r.stderr.strip().splitlines() or ["not saved"])[-1].replace("error: ", "")))
            if todo:
                self.save_fleet()
        if todo:
            self.audit(user, "who may reach what changed", who=[n for n, _a in todo])
            self.fleet_info(fresh=True)
        return [n for n, _a in todo]

    # ---- hidden services ---------------------------------------------------------------
    def _onion(self, *argv):
        r = subprocess.run(list(self.kiwi) + ["onion", self.fleet_path] + list(argv), capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, cwd=self.fleet_dir, env=self._env())
        if r.returncode:
            raise KiwiError((r.stderr.strip().splitlines() or ["not done"])[-1].replace("error: ", ""))
        return r.stdout

    def accounts(self, *argv, user="admin"):
        """The network's accounts (`kiwi-server accounts`): JSON as it answers. What changes
        something (a code made, a request answered, an account made, a link, one deleted) is in
        the activity list."""
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        r = subprocess.run(list(self.kiwi) + ["accounts", self.fleet_path] + [str(a) for a in argv], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, cwd=self.fleet_dir, env=self._env(), timeout=180)
        if r.returncode:
            raise KiwiError((r.stderr.strip().splitlines() or ["not done"])[-1].replace("error: ", ""))
        words = [str(a) for a in argv if not str(a).startswith("--")]
        if words and words[0] not in ("list", "invites", "requests"):
            self._accounts_kept = {}          # something changed: the page asks anew
        if words and words[0] in ("invite", "revoke", "accept", "deny", "create", "link", "delete", "jabber", "connect", "member"):
            self.audit(user, "accounts: %s%s" % ("jabber " if "--jabber" in argv else "", words[0]),
                       name=" ".join(words[1:4] if words[0] == "member" else words[1:2]))
        try:
            return json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else None
        except (ValueError, IndexError):
            raise KiwiError("the accounts' answer could not be read")

    def accounts_read(self, *argv):
        """What the Accounts page shows (list, invites, requests), kept 30 s. An accounts machine
        that is off answers after its time-out: once a look then, not once a list."""
        now, key = self.clock(), tuple(str(a) for a in argv)
        down = self._accounts_kept.get("down")
        if down and down[0] > now:
            raise down[1]
        kept = self._accounts_kept.get(key)
        if kept and kept[0] > now:
            return kept[1]
        try:
            answer = self.accounts(*argv)
        except KiwiError as e:
            self._accounts_kept["down"] = (now + 30, e)
            raise
        self._accounts_kept[key] = (now + 30, answer)
        return answer

    # ---- the fleet's SSH CA: keys let in for a while (kiwi-server sshca) --------------------
    def sshca(self):
        """Is there a CA, and its fingerprint (what `sshca` shows)."""
        pub = os.path.join(self.fleet_dir, "secrets", "ssh_ca.pub")
        if self.locked() or not os.path.isfile(pub):
            return {"made": False, "fingerprint": ""}
        r = subprocess.run(["ssh-keygen", "-l", "-f", pub], capture_output=True, text=True)
        return {"made": True, "fingerprint": (r.stdout.split() or ["", ""])[1] if r.returncode == 0 else ""}

    def sshca_do(self, *argv, user="admin"):
        """`kiwi-server sshca FLEET …`, its last words."""
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        r = subprocess.run(list(self.kiwi) + ["sshca", self.fleet_path] + [str(a) for a in argv], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, cwd=self.fleet_dir, env=self._env(), timeout=120)
        if r.returncode:
            raise KiwiError((r.stderr.strip().splitlines() or ["not done"])[-1].replace("error: ", ""))
        return (r.stdout + r.stderr).strip()

    def sshca_init(self, user="admin"):
        self.sshca_do("init", user=user)
        self.save_fleet()
        self.audit(user, "SSH CA made")

    def sshca_sign(self, pubkey, days=30, user="admin"):
        """A certificate (text) that lets this public key in on every machine for `days` days, as the
        fleet's admin user. The key goes through RAM only."""
        import re
        import tempfile
        pubkey = (pubkey or "").strip()
        if "\n" in pubkey or not re.match(r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp\d+|sk-ssh-ed25519@openssh\.com) [A-Za-z0-9+/=]+( .*)?$", pubkey):
            raise KiwiError("that is not a public key: one line like ssh-ed25519 AAAA… name (the .pub file's contents)")
        days = max(1, min(int(days or 30), 365))
        with tempfile.TemporaryDirectory(dir=self.run if os.path.isdir(self.run) else None) as d:
            key = os.path.join(d, "key.pub")
            util.write_text(key, pubkey + "\n", 0o600)
            self.sshca_do("sign", key, "--days", str(days), user=user)
            cert = util.read_text(os.path.join(d, "key-cert.pub")).strip()
        self.audit(user, "a key let in for %d days" % days, name=(pubkey.split(" ", 2) + [""])[2][:60])
        return cert

    def has_accounts(self):
        return any("accounts" in (h.get("modules") or []) for h in (self.fleet_info() or {}).get("hosts") or [])

    def has_jabber_join(self):
        """Jabber's sign-up page runs somewhere (xmpp: invites or requests). The list does not say
        the settings, so this asks once and remembers the answer until the next look."""
        if not any("xmpp" in (h.get("modules") or []) for h in (self.fleet_info() or {}).get("hosts") or []):
            return False
        try:
            self.accounts("invites", "--jabber")
            return True
        except KiwiError:
            return False

    def onion(self):
        """The hidden services (`onion --porcelain`): {"<service>.<machine>": {address, clients, site}}."""
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        def read():
            try:
                rows = json.loads(self._onion("--porcelain"))["services"]
            except (ValueError, KeyError):
                raise KiwiError("the hidden services could not be read")
            return {"%s.%s" % (x["service"], x["host"]): x for x in rows}
        return self._read_once("onion", read)

    def save_onion(self, wanted, user="admin"):
        """{"<service>.<machine>": [names] or None (no hidden service)} against what is there:
        made, limited to names, opened to anyone, taken away. Returns what changed."""
        from . import onion as onionmod
        now = self.onion()
        done = []
        with self.lock:
            try:
                for what, names in sorted(wanted.items()):
                    service, _, machine = what.partition(".")
                    if not (onionmod.NAME.match(service) and HOST.match(machine)) or any(
                            not onionmod.NAME.match(n) for n in names or []):
                        raise KiwiError("%s: a service, a machine and people are named in small letters, digits and -" % what)
                    has = now.get(what)
                    if names is None:
                        if has:
                            self._onion(what, "--remove")
                            done.append(what)
                        continue
                    if not has:
                        self._onion(what)
                    for n in names:
                        if not has or n not in has["clients"]:
                            self._onion(what, "--client", n)
                    for n in (has["clients"] if has else []):
                        if n not in names:
                            self._onion(what, "--remove", "--client", n)
                    if not has or sorted(names) != sorted(has["clients"]):
                        done.append(what)
            finally:
                self.save_fleet()
        if done:
            self.audit(user, "hidden services changed", services=done)
            self.fleet_info(fresh=True)
        return done

    def onion_new(self, what, user="admin"):
        """A hidden service's next address (`kiwi-server onion … --new-address`): the old one
        stops answering with the machine's next apply."""
        from . import onion as onionmod
        service, _, machine = what.partition(".")
        if not (onionmod.NAME.match(service) and HOST.match(machine)):
            raise KiwiError("no such name")
        with self.lock:
            try:
                self._onion(what, "--new-address")
            finally:
                self.save_fleet()
        self.audit(user, "gave %s a new hidden-service address" % what, host=machine)
        self.fleet_info(fresh=True)
        return (self.onion().get(what) or {}).get("address", "")

    def onion_key(self, what, client, user="admin"):
        """What a named person types into Tor Browser for one hidden service."""
        from . import onion as onionmod
        service, _, machine = what.partition(".")
        if not (onionmod.NAME.match(service) and HOST.match(machine) and onionmod.NAME.match(client or "")):
            raise KiwiError("no such name")
        out = self._onion(what, "--show", client)
        self.audit(user, "showed the Tor key of %s for %s" % (client, what))
        return out.strip().splitlines()[-1].strip()

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

    # ---- if something breaks: the report of `kiwi-server recovery` --------------------
    def recovery(self):
        """{checks, cases} (recovery.py), kept until the fleet or the last look changes."""
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        try:
            st = os.stat(os.path.join(self.run, "status.json"))
            looked = (st.st_mtime_ns, st.st_size)
        except OSError:
            looked = ()
        return self._read_once("recovery", self._recovery, also=looked)

    def _recovery(self):
        """{checks, cases} (recovery.py), with how old each machine's backup was at the last
        look when there was one. Reads only what is here; asks no machine."""
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        argv = list(self.kiwi) + ["recovery", self.fleet_path, "--porcelain"]
        looked = os.path.join(self.run, "status.json")
        if os.path.isfile(looked):
            argv += ["--status", looked]
        try:
            r = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=self.fleet_dir,
                               env=self._env(), timeout=120)
        except subprocess.TimeoutExpired:
            raise KiwiError("the report took too long to work out")
        try:
            data = json.loads(r.stdout)
            if not isinstance(data, dict) or not isinstance(data.get("checks"), list):
                raise ValueError
            return data
        except ValueError:
            lines = [x for x in (r.stderr or "").strip().splitlines() if x.strip()]
            raise KiwiError((lines[-1] if lines else "no answer").replace("error: ", "")[:300])

    def unlock_picture(self):
        """Who opens whom (unlock.picture), as `kiwi-server unlock FLEET --porcelain` gives it."""
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        return self._read_once("unlock", self._unlock_picture)

    def _unlock_picture(self):
        try:
            r = subprocess.run(list(self.kiwi) + ["unlock", self.fleet_path, "--porcelain"], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, cwd=self.fleet_dir, env=self._env(), timeout=120)
        except subprocess.TimeoutExpired:
            raise KiwiError("the picture took too long to work out")
        try:
            rows = json.loads(r.stdout)["machines"]
            if not isinstance(rows, list):
                raise ValueError
            return rows
        except (ValueError, KeyError, TypeError):
            lines = [x for x in (r.stderr or "").strip().splitlines() if x.strip()]
            raise KiwiError((lines[-1] if lines else "no answer").replace("error: ", "")[:300])

    # ---- machines: a new one, its install media ---------------------------------------
    def media_dir(self):
        """Where install media are made. They carry the machine's secrets: on a control node
        (the fleet file is a link into memory) they stay in memory and go with a restart."""
        if os.path.islink(self._fleet_file("fleet.yaml")):
            return os.path.join(self.run, "media")
        return os.path.join(self.fleet_dir, "output")

    def _env(self):
        return dict(os.environ, PYTHONUNBUFFERED="1", KIWI_MEDIA_DIR=self.media_dir())

    # ---- the game page (play.py) --------------------------------------------------------
    def play_client(self):
        """The game page's client at the network's accounts ({client, secret, issuer}), once
        `kiwi-server accounts <fleet> connect <this node> control` made it; None before, or locked."""
        me = os.environ.get("KIWI_CONTROL_HOST") or ""
        if self.locked() or not HOST.match(me):
            return None
        try:
            with open(os.path.join(self.fleet_dir, "secrets", "accounts", "%s-control.json" % me)) as f:
                got = json.load(f)
        except (OSError, ValueError):
            return None
        return got if all(isinstance(got.get(k), str) and got.get(k) for k in ("client", "secret", "issuer")) else None

    def play_ca(self):
        return os.path.join(self.fleet_dir, "secrets", "ca", "kiwiCA.pem")

    def play_conf(self, issuer, opener=None):
        """The accounts' OpenID configuration, asked again after an hour."""
        from . import play as playmod
        until, conf = self._play_conf
        if conf is None or until < self.clock() or conf.get("_issuer") != issuer:
            conf = dict(playmod.discover(issuer, self.play_ca(), opener=opener), _issuer=issuer)
            self._play_conf = (self.clock() + 3600, conf)
        return conf

    def play_start(self, person, machine, container, action, extra=()):
        """A click on the game page: a job of `kiwi-server service`, as play:<person>. Only for what
        the page lists, and one change per server every few seconds."""
        from . import play as playmod
        if action not in playmod.ACTIONS:
            raise KiwiError("start, stop, restart or its log")
        if not playmod.allowed(self.fleet_info(), extra, machine, container):
            raise KiwiError("%s on %s is not on the game page" % (container, machine))
        now = self.clock()
        if action != "logs":
            if now - self._play_last.get((machine, container), 0) < playmod.PAUSE:
                raise KiwiError("one moment: %s was just told something" % container)
            self._play_last[(machine, container)] = now
        return self.start("service", [machine], user="play:" + person, at=action, module=container)

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
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        with self.lock:
            name = setupmod.add_home_machine(os.path.realpath(self.fleet_path), preset, address, name, target)
            self.save_fleet()
        self.audit(user, "machine added", hosts=[name])
        self.fleet_info(fresh=True)
        return name

    def add_master(self, address, name="", user="admin", hoster="", server=""):
        """A network without a master gets one (docs/design/no-master.md): its block in the fleet
        file, and every machine at home back onto the VPN. Returns (its name, the machines moved).
        `hoster`, `server`: a server picked after signing in at the hoster (hoster_start): its address,
        and the login, which goes into the network's secrets as the first run's does."""
        from . import providers, setup as setupmod, fleettext
        import yaml
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        picked = None
        if server:
            s = self._hoster.get(hoster or "")
            login, row = self._hoster_pick(hoster, {"master_where": s["provider"] if s else "",
                                                    "provider_server": server})
            picked = (s["provider"], row, login)
            address = address or row["address"]
        problem = setupmod.master_problem(address)
        if problem:
            raise KiwiError(problem)
        with self.lock:
            path = os.path.realpath(self.fleet_path)
            data = yaml.safe_load(util.read_text(path)) or {}
            if not setupmod.alone(data):
                raise KiwiError("this network has a master already")
            name = (name or "").strip().lower() or setupmod.free_name("master", set(data.get("hosts") or {}))
            setupmod.add_host(path, name, "master", address=str(address).strip())
            if picked:              # the install knows where and which, and has the login
                provider, row, login = picked
                vps = {"provider": provider, "server": row["id"]}
                text = fleettext.put(util.read_text(path), ["hosts", name, "vps"], vps)
                if text is None:
                    data = yaml.safe_load(util.read_text(path))
                    data["hosts"][name]["vps"] = vps
                    text = yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
                util.write_text(path, text, 0o600)
                keep = os.path.join(self.fleet_dir, providers.get(provider).token_file())
                os.makedirs(os.path.dirname(keep), mode=0o700, exist_ok=True)
                util.write_bytes(keep, util.read_bytes(login), 0o600)
                self.store.sync()           # sealed with the network's other secrets
                self._hoster = {}
                shutil.rmtree(os.path.join(self.run, "hoster"), ignore_errors=True)
            moved = setupmod.onto_the_vpn(path)
            self.save_fleet()
        self.audit(user, "master added", hosts=[name] + moved)
        self.fleet_info(fresh=True)
        return name, moved

    # ---- settings: what the app's form edits, from the page ---------------------------
    def schema(self):
        """The settings every role and module has, the base fields and the targets, as the CLI
        publishes them (`roles --porcelain`, `targets --porcelain`). They change with the
        program only: asked once per version, kept in RAM."""
        # the fleet's module sources add modules (and their settings): they are part of the key
        try:
            raw = (yaml.safe_load(util.read_text(self.fleet_path)) or {}).get("module_sources") if not self.locked() else None
        except (OSError, yaml.YAMLError):
            raw = None
        key = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()[:10] if raw else "built-in"
        path = os.path.join(self.run, "schema-%s-%s.json" % (VERSION, key))
        if not os.path.isfile(path):
            out = {}
            for what in ("roles", "targets"):
                more = ["--fleet", self.fleet_path] if raw and what == "roles" else []
                r = subprocess.run(list(self.kiwi) + [what, "--porcelain"] + more, capture_output=True, text=True,
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
        groups = form.groups(schema, schema.get("targets") or {}, doc, host)
        kept = self._kept(host)
        for g in groups:            # a password kept in the secret store (`secrets keep`) is set too
            for f in g["fields"]:
                if f.get("secret") and self._pin_key(host, f["path"], kept) in kept["pins"]:
                    f["set"] = True
        return {"host": host, "groups": groups}

    def _kept(self, host):
        """The secret store's pins and this machine's host name: {"pins", "hostname"}."""
        pins = {}
        path = os.path.join(self.fleet_dir, "secrets", "pins.json")
        if os.path.isfile(path):
            try:
                pins = json.loads(util.read_text(path))
            except ValueError:
                pins = {}
        hostname = ""
        if host is not None:
            hostname = next((h.get("hostname") for h in (self.fleet_info() or {}).get("hosts") or [] if h.get("name") == host), "") or ""
        return {"pins": pins, "hostname": hostname}

    @staticmethod
    def _pin_key(host, path, kept):
        """hostname/module/key of a module's setting (role, module, key), notify/channel/key of the
        messages' password or token (the defaults), or None."""
        path = tuple(path.split(".")) if isinstance(path, str) else tuple(path)
        if host is None and len(path) == 3 and path[0] == "notify":
            return "notify/%s/%s" % (path[1], path[2])
        if host is None or len(path) != 3 or not kept["hostname"]:
            return None
        return "%s/%s/%s" % (kept["hostname"], path[1], path[2])

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

    def sources_run(self, args, user="admin"):
        """`kiwi-server sources FLEET …` (add, update, remove) on this copy: the same checks as in a
        terminal, then the fleet saved. (it worked, what it said)."""
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        if not args or args[0] not in ("add", "update", "remove"):
            raise KiwiError("module sources: add, update or remove")
        with self.lock:
            r = subprocess.run(list(self.kiwi) + ["sources", self.fleet_path] + [str(a) for a in args], capture_output=True,
                               text=True, stdin=subprocess.DEVNULL, cwd=self.fleet_dir, env=self._env(), timeout=600)
            said = "\n".join(line[3:] if line.startswith(":: ") else line for line in (r.stdout + r.stderr).strip().splitlines())
            if r.returncode:
                return False, said.replace("error: ", "", 1)
            self.save_fleet()
        self.audit(user, "module source %s" % {"add": "added", "update": "updated", "remove": "removed"}[args[0]],
                   keys=[a for a in args[1:2]])
        self.fleet_info(fresh=True)
        return True, said

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
            # a password taken away that lives in the secret store, not in the file (`secrets keep`)
            kept = self._kept(host)
            forget = {self._pin_key(host, p, kept): (p if isinstance(p, str) else ".".join(p))
                      for p, v in changes.items() if v is None and self._pin_key(host, p, kept) in kept["pins"]}
            keys = doc.apply(host, changes)
            if forget:
                for k in forget:
                    kept["pins"].pop(k, None)
                util.write_text(os.path.join(self.fleet_dir, "secrets", "pins.json"),
                                json.dumps(kept["pins"], indent=1, sort_keys=True) + "\n", 0o600)
                keys += [n for n in forget.values() if n not in keys]
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
            if any("password" in k or "secret" in k or "token" in k or k.endswith("_key") for k in keys):
                # a password typed into the form goes into the secret store, not the fleet file, which
                # computers that join keep on their disks (`secrets keep`; the same value)
                subprocess.run(list(self.kiwi) + ["secrets", "keep", self.fleet_path, "--quiet"], capture_output=True,
                               stdin=subprocess.DEVNULL, cwd=self.fleet_dir, env=self._env(), timeout=120)
            self.save_fleet()
        # which settings, never what they are now: a value may be a secret
        self.audit(user, "settings changed", hosts=[host] if host else [], keys=keys)
        self.fleet_info(fresh=True)
        return keys

    def _forget_in_status(self, names):
        """A removed machine out of the last look (status.json): the pages showed it until the next
        look, and its Remove then said "no such machine" (the owner, 19 Nov: "still half there")."""
        st = self.status() or {}
        left = [h for h in st.get("hosts") or [] if h.get("host") not in set(names)]
        if len(left) != len(st.get("hosts") or []):
            st["hosts"] = left
            self._put(os.path.join(self.run, "status.json"), json.dumps(st))
        gone = self.gone()                  # and "has not answered for 50 minutes" of the recovery watch
        kept = [e for e in gone if e.get("host") not in set(names)]
        if len(kept) != len(gone):
            self._put(os.path.join(self.run, "recover.json"), json.dumps(kept))

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
    @staticmethod
    def _put(path, text):
        """What a look found, in one piece: a page asked in the same moment reads the old
        file or the new one, never half of one."""
        tmp = "%s.%d.tmp" % (path, threading.get_ident())
        util.write_text(tmp, text, 0o600)
        os.replace(tmp, path)

    def status(self):
        """The last `status --porcelain`, or None."""
        path = os.path.join(self.run, "status.json")
        try:
            return json.loads(util.read_text(path)) if os.path.isfile(path) else None
        except (OSError, ValueError):
            return None

    looking = False                         # a look at every machine is under way

    def look(self):
        """A look at every machine, in the background; one at a time."""
        if not self.looking and not self.locked():
            self.looking = True             # from now, so that the page that follows says so
            threading.Thread(target=self.refresh_status, daemon=True).start()

    def refresh_status(self):
        """A look at every machine. Each machine's answer is written as it comes (`status --stream`):
        one that is off costs its own timeout, and the others are on the page meanwhile."""
        if self.locked():
            return None
        self.looking = True
        path = os.path.join(self.run, "status.json")
        try:
            before = self.status() or {}
            rows = {h.get("host"): h for h in before.get("hosts") or [] if h.get("host")}
            order, seen, tail = list(rows), [], []
            with self.lock:                 # after the running job: every command saves the store when it ends
                p = subprocess.Popen(list(self.kiwi) + ["status", self.fleet_path, "--porcelain", "--stream"],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL,
                                     cwd=self.fleet_dir)
                for line in p.stdout:
                    tail.append(line)
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(row, dict) or not row.get("host"):
                        continue
                    row["seen"] = self.clock()
                    rows[row["host"]] = row
                    seen.append(row["host"])
                    if row["host"] not in order:
                        order.append(row["host"])
                    self._put(path, json.dumps(dict(before, hosts=[rows[n] for n in order], time=before.get("time") or self.clock())))
                err = p.stderr.read()
                p.wait()
                p.stdout.close()
                p.stderr.close()
            if seen:                        # the machines of this look, in the fleet's order; one that left is gone
                data = {"hosts": [rows[n] for n in order if n in seen]}
            else:
                try:                        # all at once (a kiwi-server before --stream), or nothing that can be read
                    data = json.loads("".join(tail))
                    if not isinstance(data, dict) or not isinstance(data.get("hosts"), list):
                        raise ValueError
                except ValueError:
                    data = {"hosts": [], "error": (err or "".join(tail) or "no output").strip()[-500:]}
            data["time"] = self.clock()
            self._put(path, json.dumps(data))
            try:
                self.fleet_info(fresh=True)
            except (OSError, ValueError):
                pass
            return data
        finally:
            # over only once what it found is written: a page that says "checking" until then never
            # shows the look before as the new one
            self.looking = False

    def recover(self):
        """After a look: what the fleet's `recovery:` allows done about a machine that is gone
        (`kiwi-server gone`): told on the page, or restarted at its provider."""
        seen = os.path.join(self.run, "status.json")
        if self.locked() or not os.path.isfile(seen):
            return self.gone()
        with self.lock:
            r = subprocess.run(list(self.kiwi) + ["gone", self.fleet_path, "--porcelain", "--status", seen],
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=self.fleet_dir, env=self._env())
        try:
            events = json.loads((r.stdout.strip().splitlines() or [""])[-1])["events"]
        except (ValueError, KeyError, TypeError):
            return self.gone()
        self._put(os.path.join(self.run, "recover.json"), json.dumps(events))
        for e in events:
            if e.get("kind") == "restarted":
                self.audit("control", "restarted %s at its provider: it did not answer" % e["host"], host=e["host"])
            elif e.get("kind") == "unlocked" and e["text"].endswith("it starts now"):
                self.audit("control", "forwarded the unlock ring of %s: its disk waited at boot" % e["host"], host=e["host"])
        return events

    def tell(self):
        """After a look: every service asked for a page, what is wrong kept with its time, and a
        message about what changed (`kiwi-server notify`). The file it keeps is on the disk, with
        no secret in it: the status page reads it, also while the keys are locked."""
        seen = os.path.join(self.run, "status.json")
        if self.locked() or not os.path.isfile(seen):
            return None
        argv = list(self.kiwi) + ["notify", self.fleet_path, "--porcelain", "--status", seen, "--state", self.health_file]
        if os.path.isfile(os.path.join(self.run, "recover.json")):
            argv += ["--events", os.path.join(self.run, "recover.json")]
        with self.lock:
            r = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=self.fleet_dir, env=self._env())
        try:
            said = json.loads((r.stdout.strip().splitlines() or [""])[-1])
        except ValueError:
            return None
        for way in said.get("sent") or []:
            self.audit("control", ("told by %s: %s" % (way["channel"], "; ".join(e["text"] for e in said.get("events") or [])[:300]))
                       if way["ok"] else "could not tell by %s: %s" % (way["channel"], way["why"]))
        self.write_public()
        return said

    def write_public(self):
        """What the public pages' own container may show (KIWI_PUBLIC_DIR, mounted there read-only):
        the services open to the Internet and whether they work, the network's domain, its CA's
        certificate. No fleet, no key, nothing about the machines: a page for strangers never runs
        where the network is kept (the owner, 18 Nov 2026: "this whole thing is about security")."""
        folder = os.environ.get("KIWI_PUBLIC_DIR", "")
        if not folder or not os.path.isdir(folder):
            return False
        from . import health as healthmod
        try:
            self._put(os.path.join(folder, "health.json"), json.dumps(healthmod.for_strangers(self.health())))
            self._put(os.path.join(folder, "info.json"), json.dumps({"domain": (self.fleet_info() or {}).get("domain") or ""}))
            pem = self.ca_pem() or ""
            if pem.startswith("-----BEGIN CERTIFICATE-----") and "PRIVATE" not in pem:
                self._put(os.path.join(folder, "ca.pem"), pem)
        except (OSError, KiwiError) as e:
            util.warn("control: the public pages' data was not written: %s" % e)
            return False
        return True

    @property
    def health_file(self):
        return os.path.join(self.dir, "health.json")

    def health(self):
        """What the looks have kept (health.py): {} before the first one."""
        try:
            return json.loads(util.read_text(self.health_file)) if os.path.isfile(self.health_file) else {}
        except (OSError, ValueError):
            return {}

    def gone(self):
        """What the last look said about machines that do not answer: [{host, kind, text}]."""
        path = os.path.join(self.run, "recover.json")
        try:
            return json.loads(util.read_text(path)) if os.path.isfile(path) else []
        except ValueError:
            return []

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
                data = json.loads(r.stdout)
                from . import health as healthmod
                healthmod.register(data.get("pages"))      # the modules' own names of their pages
            except ValueError:
                return None
            self._put(path, r.stdout)
            return data
        try:
            data = json.loads(util.read_text(path))
        except (OSError, ValueError):
            return None
        from . import health as healthmod
        healthmod.register(data.get("pages"))
        return data

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

    def role_script(self, host):
        """The role script Apply would send to a machine (`script --offline`), for reading.
        Rendered anew each time into the open store's memory; it holds the machine's passwords,
        so it is handed on and kept nowhere here."""
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        if not HOST.match(host or ""):
            raise KiwiError("not a host name: %r" % host)
        try:
            r = subprocess.run(list(self.kiwi) + ["script", self.fleet_path, host, "--offline"], capture_output=True,
                               text=True, stdin=subprocess.DEVNULL, cwd=self.fleet_dir, env=self._env(), timeout=180)
        except subprocess.TimeoutExpired:
            raise KiwiError("the role script of %s took too long to make" % host)
        if r.returncode != 0 or not r.stdout.strip():
            lines = [x for x in (r.stderr or "").strip().splitlines() if x.strip()]
            raise KiwiError((lines[-1] if lines else "no script came out").replace("error: ", "")[:300])
        return r.stdout

    # ---- devices: phones and laptops on the VPN ---------------------------------------
    def devices(self):
        d = os.path.join(self.fleet_dir, "secrets", "devices")
        if self.locked() or not os.path.isdir(d):
            return []
        return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".conf") and HOST.match(f[:-5]))

    def device_rows(self):
        """Every device with what its file says: [{name, address, group, split}]. Its address and
        what goes through the tunnel are read; its key stays where it is."""
        import ipaddress
        names = self.devices()
        if not names:
            return []
        d = os.path.join(self.fleet_dir, "secrets", "devices")
        groups = (self.fleet_info() or {}).get("groups") or {}

        def read():
            nets = []
            for g, cidr in groups.items():
                try:
                    nets.append((g, ipaddress.ip_network(str(cidr), strict=False)))
                except ValueError:
                    pass
            out = []
            for n in names:
                try:
                    text = util.read_text(os.path.join(d, n + ".conf"))
                except OSError:
                    text = ""
                m = re.search(r"^\s*Address\s*=\s*([0-9A-Fa-f.:]+)", text, re.M)
                allowed = re.search(r"^\s*AllowedIPs\s*=\s*(.*)$", text, re.M)
                addr, group = (m.group(1) if m else ""), ""
                try:
                    ip = ipaddress.ip_address(addr) if addr else None
                    group = next((g for g, net in nets if ip is not None and ip in net), "")
                except ValueError:
                    pass
                out.append({"name": n, "address": addr, "group": group,
                            "split": bool(allowed) and "0.0.0.0/0" not in allowed.group(1)})
            return out
        try:
            changed = os.stat(d).st_mtime_ns
        except OSError:
            changed = 0
        return self._read_once("devices", read, also=(tuple(names), changed, tuple(sorted(groups.items()))))

    def device_config(self, name):
        """A device's WireGuard config (from the open store), or None."""
        if self.locked() or not HOST.match(name or ""):
            return None
        path = os.path.join(self.fleet_dir, "secrets", "devices", name + ".conf")
        return util.read_text(path) if os.path.isfile(path) else None

    def add_device(self, name, group="", user="admin", split=False):
        """A new phone or laptop on the VPN: the master makes its key and address
        (`enroll`). `split`: only the network (and the linked networks) through the tunnel, not
        everything. Returns its config; KiwiError says why not."""
        if self.locked():
            raise KiwiError("the fleet is locked: log in")
        if not HOST.match(name or ""):
            raise KiwiError("a name for the device: lowercase letters, digits and dashes, like annas-phone")
        if name in self.devices():
            raise KiwiError("a device called %s exists already" % name)
        argv = list(self.kiwi) + ["enroll", self.fleet_path, name] + (["--group", group] if group else []) + (["--split"] if split else [])
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
                    events = self.recover()
                    said = self.tell()
                    if said and said.get("down"):      # a service does not answer: look again soon, for its message and its return
                        last_status = now - self.status_minutes * 60 + 180
                    if any(e.get("kind") in ("restarted", "unlocked") for e in events):
                        last_status = now - self.status_minutes * 60 + 180      # look again when it had time to start
                    elif any(e.get("kind") == "waiting" for e in events):
                        last_status = now - self.status_minutes * 60 + 120      # something is gone: look again soon
                if now - self._riseup_at >= 6 * 3600:      # a Riseup certificate running out: renewed by an apply
                    self._riseup_at = now
                    for name in self.riseup_due():
                        self.start("apply", [name], user="riseup")
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

    def riseup_due(self):
        """The machines whose VPN client is Riseup's and whose certificate runs out within
        riseup.RENEW_DAYS, or is not there yet."""
        from . import riseup
        try:
            import yaml
            with open(self.fleet_path) as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, ValueError):
            return []
        defaults, due = data.get("defaults") or {}, []
        for name, h in (data.get("hosts") or {}).items():
            role = str((h or {}).get("role") or "")
            vc = ((h or {}).get(role) or {}).get("vpn-client") or (defaults.get(role) or {}).get("vpn-client") or {}
            if not (isinstance(vc, dict) and vc.get("vpn_provider") == "riseup"):
                continue
            full = os.path.join(self.fleet_dir, "secrets", "riseup", "%s.ovpn" % name)
            try:
                left = riseup.days_left(util.read_text(full))
            except OSError:
                left = None
            if left is None or left < riseup.RENEW_DAYS:
                due.append(name)
        return due

    def has_ram_masters(self):
        try:
            import yaml
            with open(self.fleet_path) as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, ValueError):
            return False
        default = (data.get("defaults") or {}).get("target")
        return any(((h or {}).get("target") or default) == "coreos-ram" for h in (data.get("hosts") or {}).values())
