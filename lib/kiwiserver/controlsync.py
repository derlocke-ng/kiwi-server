"""The fleet between this computer and the control node (the host with the `control` module):
`control push` sends it there, `control pull` brings it back. The control node works on its
copy (an apply from the phone, an enroll), so each side remembers the state both had at the
last sync and refuses to overwrite a side that changed since — pull first, or --force.

What travels: the encrypted store (secrets.age, secrets.recipients), control/salt, and the
fleet file and federation.yaml encrypted for the store's recipients (fleet.yaml.age): the
fleet file names every machine and may hold passwords. Nothing rests on the node in the
clear; it opens all of it into RAM with its password (control.py). Not output/, not the
node's own control/ files.
"""
import base64
import hashlib
import http.client
import io
import json
import os
import time
import re
import shlex
import socket
import ssl
import tarfile
import urllib.parse

from . import remote, store as storemod, util
from .util import KiwiError

PLAIN = ("fleet.yaml", "federation.yaml")                    # encrypted on the way: <name>.age on the node
AS_IS = ("secrets.age", "secrets.recipients", "control/salt")
ON_NODE = tuple(n + ".age" for n in PLAIN) + AS_IS
STAMP = "control/synced"

# the node's side: sha256sum lines of its files that exist, in this order; then its stamp
REMOTE_DIGEST = ("cd %(dir)s 2>/dev/null || exit 0; [ -f secrets.age ] || exit 0; "
                 "for f in " + " ".join(ON_NODE) + "; do [ -f $f ] && sha256sum $f; done | sha256sum | cut -d' ' -f1; "
                 "cat " + STAMP + " 2>/dev/null || true")


def control_host(fleet, name=None):
    hosts = [h for h in fleet.hosts.values() if "control" in (h.modules or [])]
    if name:
        hosts = [h for h in hosts if h.name == name]
    if len(hosts) != 1:
        raise KiwiError("which control node? %s" % (
            "no host has the control module" if not hosts else "several have it: --to <host>"))
    return hosts[0]


def remote_dir(host):
    return "%s/kn-control" % (host.role_settings.get("docker_dir") or "/home/user/docker")


def _local(fleet_dir, fleet_name, name):
    return os.path.join(fleet_dir, fleet_name if name == "fleet.yaml" else name)


def here_digest(fleet_dir, fleet_name):
    """This side's state: the files as they are here (the fleet file in the clear)."""
    h = hashlib.sha256()
    for name in PLAIN + AS_IS:
        path = _local(fleet_dir, fleet_name, name)
        if os.path.isfile(path):
            h.update(("%s  %s\n" % (util.sha256_file(path), name)).encode())
    return h.hexdigest()


def blobs_digest(blobs):
    """What REMOTE_DIGEST will say once these are the node's files."""
    h = hashlib.sha256()
    for name in ON_NODE:
        if name in blobs:
            h.update(("%s  %s\n" % (hashlib.sha256(blobs[name]).hexdigest(), name)).encode())
    return h.hexdigest()


def pack(blobs):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in blobs.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o600
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _stamp(fleet_dir, name=STAMP):
    """(this side's digest, the other side's digest) at the last sync."""
    p = os.path.join(fleet_dir, name)
    parts = util.read_text(p).split() if os.path.isfile(p) else []
    return (parts[0], parts[1]) if len(parts) == 2 else ("", "")


def _set_stamp(fleet_dir, here, there, name=STAMP):
    os.makedirs(os.path.join(fleet_dir, "control"), mode=0o700, exist_ok=True)
    util.write_text(os.path.join(fleet_dir, name), "%s %s\n" % (here, there), 0o600)


def remote_state(tgt, rdir):
    """(digest of the node's files or '', its stamp or '')."""
    out = remote.run(tgt, REMOTE_DIGEST % {"dir": shlex.quote(rdir)}).decode().split("\n")
    return (out[0].strip(), out[1].strip() if len(out) > 1 else "")


def _store(fleet_dir):
    """This side's store, saved; it must be one, with a control password."""
    st = storemod.Store(fleet_dir)
    if not st.exists():
        raise KiwiError("the control node takes the secrets only as the encrypted store: kiwi-server secrets init first")
    st.sync()               # what the open tree holds is in secrets.age now
    if not os.path.isfile(os.path.join(fleet_dir, "control", "salt")):
        raise KiwiError("no control password yet: kiwi-server serve <fleet> --set-password")
    return st


def push(fleet, host, tgt, force=False):
    fleet_dir, fleet_name = os.path.dirname(fleet.path), os.path.basename(fleet.path)
    rdir = remote_dir(host)
    st = _store(fleet_dir)
    there, stamp = remote_state(tgt, rdir)
    if there and there != stamp and not force:
        raise KiwiError("the control node's copy changed since the last sync (an apply or enroll there): "
                        "kiwi-server control pull first, or push --force to overwrite it")
    blobs = _blobs(fleet_dir, fleet_name, st)
    d = blobs_digest(blobs)
    script = ("set -e; d=%(d)s; install -d -m 0700 \"$d\" \"$d/control\"; rm -rf \"$d/.incoming\"; "
              "install -d -m 0700 \"$d/.incoming\"; tar -xz -C \"$d/.incoming\"; cd \"$d\"; "
              "for f in %(files)s; do rm -f \"$f\"; if [ -f \".incoming/$f\" ]; then mv -f \".incoming/$f\" \"$f\"; fi; done; "
              # nothing of an older layout stays in the clear: a plain fleet file, job logs on disk
              "for f in %(plain)s; do if [ -f \"$f\" ] && [ ! -L \"$f\" ]; then rm -f \"$f\"; fi; done; "
              "rm -rf .incoming control/jobs control/status.json; printf '%%s\\n' %(s)s > %(stamp)s; chmod 600 %(stamp)s"
              % {"d": shlex.quote(rdir), "files": " ".join(ON_NODE), "plain": " ".join(PLAIN), "s": d, "stamp": STAMP})
    remote.run(tgt, script, pack(blobs))
    _set_stamp(fleet_dir, here_digest(fleet_dir, fleet_name), d)
    return len(blobs)


def pull(fleet, host, tgt, force=False, identity=None):
    fleet_dir, fleet_name = os.path.dirname(fleet.path), os.path.basename(fleet.path)
    rdir = remote_dir(host)
    st = _store(fleet_dir)
    here, then = _stamp(fleet_dir)
    if here and here_digest(fleet_dir, fleet_name) != here and not force:
        raise KiwiError("this copy changed since the last sync: kiwi-server control push first, "
                        "or pull --force to overwrite it")
    there, _their_stamp = remote_state(tgt, rdir)
    if not there:
        raise KiwiError("%s holds no fleet yet: kiwi-server control push" % host.name)
    if there == then:
        return 0            # the node did not change either
    key = identity or storemod.keyring_get(st.store_id())
    if not key:
        raise KiwiError("pull opens what comes with this computer's key, and the keyring has none: "
                        "kiwi-server control pull <fleet> --identity FILE (the recovery key)")
    data = remote.run(tgt, "cd %s && tar -cz $(for f in %s; do [ -f $f ] && echo $f; done)"
                      % (shlex.quote(rdir), " ".join(ON_NODE)))
    got = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar.getmembers():
            if m.isfile() and m.name in ON_NODE:
                got[m.name] = tar.extractfile(m).read()
    n = _take(fleet_dir, fleet_name, st, got, key)
    remote.run(tgt, "printf '%%s\\n' %s > %s/%s" % (there, shlex.quote(rdir), STAMP))
    _set_stamp(fleet_dir, here_digest(fleet_dir, fleet_name), there)
    return n


# ---- through the control node's API (design/controllers.md) --------------------------------
# A controller needs no shell on the control node: it proves it holds its key by opening a
# challenge encrypted to it, and then fetches or replaces the encrypted fleet by revision.
REMOTE = "control/remote"       # {"url", "name", "store", "address"?} of the control node this copy syncs with
CA = "control/ca.pem"


def node_digest(fleet_dir):
    """The revision of the encrypted fleet in a directory: what REMOTE_DIGEST says there."""
    h = hashlib.sha256()
    found = False
    for name in ON_NODE:
        path = os.path.join(fleet_dir, name)
        if os.path.isfile(path):
            found = True
            h.update(("%s  %s\n" % (util.sha256_file(path), name)).encode())
    return h.hexdigest() if found and os.path.isfile(os.path.join(fleet_dir, "secrets.age")) else ""


class _At(http.client.HTTPSConnection):
    """Connect to an address, check the certificate for the name."""

    def __init__(self, name, address, port, ctx):
        super().__init__(name, port, timeout=60, context=ctx)
        self._at, self._ctx = address, ctx

    def connect(self):
        self.sock = self._ctx.wrap_socket(socket.create_connection((self._at, self.port), self.timeout),
                                          server_hostname=self.host)


class Api:
    """HTTPS to the control node, its certificate checked against the fleet's CA. `address`:
    where to connect when the name does not resolve here (the name still has to match)."""

    def __init__(self, url, ca_file=None, address=None, verify=True):
        self.u = urllib.parse.urlsplit(url.rstrip("/"))
        if self.u.scheme not in ("https", "http"):
            raise KiwiError("the control node's address is a URL: https://control.<host>.<domain>")
        self.ca_file, self.address, self.verify = ca_file, address, verify
        self.token = None

    def _connection(self):
        host, port = self.u.hostname, self.u.port
        if self.u.scheme == "http":
            return http.client.HTTPConnection(self.address or host, port or 80, timeout=60)
        ctx = ssl.create_default_context(cafile=self.ca_file) if self.verify else ssl._create_unverified_context()
        if self.address:
            return _At(host, self.address, port or 443, ctx)
        return http.client.HTTPSConnection(host, port or 443, timeout=60, context=ctx)

    def call(self, method, path, body=None):
        """(status, parsed JSON or {})."""
        headers = {"Host": self.u.netloc, "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        conn = self._connection()
        try:
            conn.request(method, (self.u.path or "") + path, body=data, headers=headers)
            r = conn.getresponse()
            raw = r.read()
        except (OSError, ssl.SSLError) as e:
            raise KiwiError("the control node at %s does not answer: %s" % (self.u.netloc, e))
        finally:
            conn.close()
        try:
            return r.status, json.loads(raw or b"{}")
        except ValueError:
            return r.status, {}

    def login(self, name, key, scratch_dir):
        """Open the node's challenge with this controller's key; the token in it is the pass."""
        code, out = self.call("POST", "/api/controller/hello", {"name": name})
        if code != 200 or "challenge" not in out:
            raise KiwiError("the control node does not know a controller called %s (not accepted yet, or removed)" % name)
        tmp = os.path.join(scratch_dir, ".challenge.age")
        os.makedirs(scratch_dir, mode=0o700, exist_ok=True)
        try:
            util.write_bytes(tmp, base64.b64decode(out["challenge"]), 0o600)
            self.token = storemod.decrypt_file(key, tmp).decode().strip()
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


def remote_of(fleet_dir):
    p = os.path.join(fleet_dir, REMOTE)
    return json.loads(util.read_text(p)) if os.path.isfile(p) else None


def api_for(fleet_dir, key=None):
    """(api logged in as this computer, its key) for the control node this copy belongs to."""
    r = remote_of(fleet_dir)
    if not r:
        raise KiwiError("this fleet directory is joined to no control node: kiwi-server control join")
    key = key or storemod.keyring_get(r["store"])
    if not key:
        raise KiwiError("this computer's key for the fleet is not in the keyring: --identity FILE")
    ca = os.path.join(fleet_dir, CA)
    api = Api(r["url"], ca if os.path.isfile(ca) else None, r.get("address"))
    api.login(r["name"], key, os.path.join(fleet_dir, "control"))
    return api, key


def parse_invite(link):
    """(the control node's URL, the code) of an invite link: https://control.<host>/#join=<code>."""
    link = (link or "").strip()
    url, sep, frag = link.partition("#join=")
    if not sep or "." not in frag or not url.startswith(("https://", "http://")):
        raise KiwiError("that is not an invite link (the control page makes one: Computers, Add a computer)")
    return url.rstrip("/"), frag.strip()


def join(fleet_dir, link, name, address=None, key_file=None):
    """This computer joins the fleet an invite is from, and fetches it. Makes its key (keyring,
    or key_file), learns the fleet's CA and checks it against the invite before sending
    anything, sends the public key. Returns the name it got."""
    url, code = parse_invite(link)
    address = address or os.environ.get("KIWI_CONTROL_ADDRESS") or None     # where its name does not resolve
    if os.path.exists(os.path.join(fleet_dir, "secrets.age")) or remote_of(fleet_dir):
        raise KiwiError("%s holds a fleet already: join into an empty folder" % fleet_dir)
    first = Api(url, address=address, verify=False)     # who this is gets checked next, by the code
    status, out = first.call("GET", "/api/ca")
    if status != 200 or not out.get("ca") or not out.get("store"):
        raise KiwiError("%s is not a control node, or has no CA to show yet" % url)
    if hashlib.sha256(out["ca"].encode()).hexdigest()[:8] != code.rsplit(".", 1)[1]:
        raise KiwiError("the fleet at %s is not the one this invite is from: nothing was sent" % url)
    if key_file and os.path.isfile(key_file):
        raise KiwiError("%s exists: a key file is written once" % key_file)
    if not key_file and storemod.keyring_get(out["store"]):
        raise KiwiError("this computer already has a key for this fleet in its keyring (it holds the fleet "
                        "somewhere, or joined before): nothing was sent")
    secret, public = storemod.keygen()
    os.makedirs(os.path.join(fleet_dir, "control"), mode=0o700, exist_ok=True)
    util.write_text(os.path.join(fleet_dir, CA), out["ca"], 0o644)
    api = Api(url, os.path.join(fleet_dir, CA), address)
    status, got = api.call("POST", "/api/join", {"code": code, "name": name, "key": public})
    if status != 200 or not got.get("name"):
        raise KiwiError("the invite did not work: it is used, or older than fifteen minutes. Make a new one on the control page")
    # only now is the key worth keeping
    if key_file:
        util.write_text(key_file, secret + "\n", 0o600)
    elif not storemod.keyring_put(out["store"], secret):
        raise KiwiError("joined as %s, but no keyring took this computer's key (secret-tool): remove %s on the "
                        "control page and join again with --identity FILE, on a disk that is encrypted"
                        % (got["name"], got["name"]))
    remote = {"url": url, "name": got["name"], "store": out["store"]}
    if address:
        remote["address"] = address
    util.write_text(os.path.join(fleet_dir, REMOTE), json.dumps(remote) + "\n", 0o600)
    pull_api(fleet_dir, "fleet.yaml", key=secret)
    return got["name"]


def remember(fleet_dir, host):
    """The first computer pushed over SSH: from now on it can sync through the control node's
    page like every other one (its key is the store's `desktop` key)."""
    st = storemod.Store(fleet_dir)
    ca = os.path.join(os.path.realpath(os.path.join(fleet_dir, "secrets")), "ca", "kiwiCA.pem")
    if remote_of(fleet_dir) or not os.path.isfile(ca) or not any(n == "desktop" for n, _k in st.ways()):
        return
    util.write_text(os.path.join(fleet_dir, CA), util.read_text(ca), 0o644)
    util.write_text(os.path.join(fleet_dir, REMOTE), json.dumps(
        {"url": "https://control.%s" % host.hostname, "name": "desktop", "store": st.store_id()}) + "\n", 0o600)


def _blobs(fleet_dir, fleet_name, st):
    blobs = {}
    for name in PLAIN:
        path = _local(fleet_dir, fleet_name, name)
        if os.path.isfile(path):
            blobs[name + ".age"] = storemod.encrypt_bytes(st.recipients, util.read_bytes(path))
    for name in AS_IS:
        if os.path.isfile(os.path.join(fleet_dir, name)):      # (control/salt: only a fleet with a control node)
            blobs[name] = util.read_bytes(os.path.join(fleet_dir, name))
    return blobs


def _take(fleet_dir, fleet_name, st, got, key):
    """What came from the node, opened with this computer's key and put in place."""
    tmp = os.path.join(fleet_dir, "control", ".pull.age")
    os.makedirs(os.path.dirname(tmp), mode=0o700, exist_ok=True)
    plain = {}
    try:                    # everything is opened before anything here is replaced
        for name in PLAIN:
            if name + ".age" in got:
                util.write_bytes(tmp, got[name + ".age"], 0o600)
                plain[name] = storemod.decrypt_file(key, tmp)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    was_open = st.is_open()
    for name, content in list(plain.items()) + [(n, got[n]) for n in AS_IS if n in got]:
        dest = _local(fleet_dir, fleet_name, name)
        os.makedirs(os.path.dirname(dest), mode=0o700, exist_ok=True)
        util.write_bytes(dest + ".tmp", content, 0o644 if name == "secrets.recipients" else 0o600)
        os.replace(dest + ".tmp", dest)
    if was_open or not os.path.lexists(st.link):
        st.unlock(key)      # the open tree was the old store's (saved before, so nothing is lost)
    return len(plain) + len([n for n in AS_IS if n in got])


def push_api(fleet_dir, fleet_name, force=False, key=None):
    st = _store(fleet_dir)
    api, _key = api_for(fleet_dir, key)
    status, out = api.call("GET", "/api/fleet/revision")
    if status != 200:
        raise KiwiError("the control node refused (%d)" % status)
    there = out.get("revision") or ""
    _here, then = _stamp(fleet_dir)
    if there and there != then and not force:
        raise KiwiError("the control node's copy changed since this computer last synced (another controller, "
                        "or an apply there): kiwi-server control pull first, or push --force to overwrite it")
    blobs = _blobs(fleet_dir, fleet_name, st)
    status, out = api.call("PUT", "/api/fleet", {"base": there, "files": {
        n: base64.b64encode(b).decode() for n, b in blobs.items()}})
    if status == 409:
        raise KiwiError("the control node's copy changed just now: kiwi-server control pull first")
    if status != 200:
        raise KiwiError("the control node did not take the push (%d)" % status)
    _set_stamp(fleet_dir, here_digest(fleet_dir, fleet_name), out["revision"])
    return len(blobs)


def pull_api(fleet_dir, fleet_name, force=False, key=None):
    st = storemod.Store(fleet_dir)
    here, then = _stamp(fleet_dir)
    if st.exists():
        st.sync()
        if here and here_digest(fleet_dir, fleet_name) != here and not force:
            raise KiwiError("this copy changed since the last sync: kiwi-server control push first, "
                            "or pull --force to overwrite it")
    api, key = api_for(fleet_dir, key)
    status, out = api.call("GET", "/api/fleet")
    if status != 200 or not out.get("files"):
        raise KiwiError("the control node holds no fleet, or refused (%d)" % status)
    if out["revision"] == then and st.exists():
        return 0
    got = {n: base64.b64decode(b) for n, b in out["files"].items() if n in ON_NODE}
    n = _take(fleet_dir, fleet_name, st, got, key)
    _set_stamp(fleet_dir, here_digest(fleet_dir, fleet_name), out["revision"])
    return n


# ---- a synced folder (Nextcloud, Syncthing, a stick): design/controllers.md, step 2 ---------
# The same encrypted files in a folder that something else carries between computers: for a
# fleet without a control node, or beside one. Whoever runs the syncing sees fleet.yaml.age and
# secrets.age, never a secret; they can delete or put back an older copy, not read or forge.
# A manifest, written last, says which files belong together: a folder caught in the middle of
# syncing is refused, not half taken. A computer asks to join by leaving its public key in
# requests/; a computer that can open the store accepts or denies it.
FOLDER = "control/folder"           # the synced folder this copy keeps in step with
FOLDER_STAMP = "control/synced-folder"
MANIFEST = "kiwi-fleet.json"
REQUESTS = "requests"


def folder_of(fleet_dir):
    p = os.path.join(fleet_dir, FOLDER)
    return util.read_text(p).strip() if os.path.isfile(p) else None


def _remember_folder(fleet_dir, folder):
    os.makedirs(os.path.join(fleet_dir, "control"), mode=0o700, exist_ok=True)
    util.write_text(os.path.join(fleet_dir, FOLDER), os.path.abspath(folder) + "\n", 0o600)


def folder_state(folder):
    """The revision of the fleet in the folder ('' when it holds none). KiwiError when its
    files do not match its manifest: still syncing, or tampered with."""
    path = os.path.join(folder, MANIFEST)
    if not os.path.isfile(path):
        return ""
    try:
        manifest = json.loads(util.read_text(path))
        files = dict(manifest["files"])
    except (ValueError, KeyError, TypeError):
        raise KiwiError("%s is not readable: is the folder still syncing?" % path)
    for name, sha in files.items():
        f = os.path.join(folder, name)
        if name not in ON_NODE or not os.path.isfile(f) or util.sha256_file(f) != sha:
            raise KiwiError("the folder %s is not complete yet (%s differs from its list): wait until it has "
                            "finished syncing, then try again" % (folder, name))
    return str(manifest.get("revision") or "")


def _plain_store(fleet_dir):
    st = storemod.Store(fleet_dir)
    if not st.exists():
        raise KiwiError("only an encrypted store goes into a synced folder: kiwi-server secrets init first")
    st.sync()
    return st


def push_dir(fleet_dir, fleet_name, folder, force=False):
    st = _plain_store(fleet_dir)
    folder = os.path.abspath(os.path.expanduser(folder))
    there = folder_state(folder)
    _here, then = _stamp(fleet_dir, FOLDER_STAMP)
    if there and there != then and not force:
        raise KiwiError("the folder's copy changed since this computer last synced (another computer saved "
                        "there): kiwi-server control pull first, or push --force to overwrite it")
    blobs = _blobs(fleet_dir, fleet_name, st)
    os.makedirs(os.path.join(folder, "control"), exist_ok=True)
    manifest = os.path.join(folder, MANIFEST)
    if os.path.exists(manifest):
        os.remove(manifest)                 # from here to the new manifest the folder is "not complete"
    for name in ON_NODE:
        path = os.path.join(folder, name)
        if name in blobs:
            util.write_bytes(path, blobs[name], 0o600)
        elif os.path.isfile(path):
            os.remove(path)
    revision = blobs_digest(blobs)
    util.write_text(manifest, json.dumps({"revision": revision, "files": {
        n: hashlib.sha256(b).hexdigest() for n, b in blobs.items()}}, indent=1) + "\n", 0o644)
    _set_stamp(fleet_dir, here_digest(fleet_dir, fleet_name), revision, FOLDER_STAMP)
    _remember_folder(fleet_dir, folder)
    return len(blobs)


def pull_dir(fleet_dir, fleet_name, folder, force=False, key=None):
    folder = os.path.abspath(os.path.expanduser(folder))
    st = storemod.Store(fleet_dir)
    here, then = _stamp(fleet_dir, FOLDER_STAMP)
    if st.exists() and os.path.isfile(_local(fleet_dir, fleet_name, "fleet.yaml")):
        st.sync()
        if here and here_digest(fleet_dir, fleet_name) != here and not force:
            raise KiwiError("this copy changed since the last sync: kiwi-server control push first, "
                            "or pull --force to overwrite it")
    there = folder_state(folder)
    if not there:
        raise KiwiError("%s holds no fleet yet: kiwi-server control push --dir, on a computer that has it" % folder)
    if there == then and os.path.isfile(_local(fleet_dir, fleet_name, "fleet.yaml")):
        _remember_folder(fleet_dir, folder)
        return 0
    got = {n: util.read_bytes(os.path.join(folder, n)) for n in ON_NODE if os.path.isfile(os.path.join(folder, n))}
    if not st.exists():                     # a new computer: the list of keys first, to find its own
        util.write_bytes(st.recipients, got["secrets.recipients"], 0o644)
    key = key or storemod.keyring_get(st.store_id())
    if not key:
        raise KiwiError("this computer has no key for the fleet in its keyring: --identity FILE, or join first "
                        "(kiwi-server control join <fleet> --dir %s)" % folder)
    try:
        n = _take(fleet_dir, fleet_name, st, got, key)
    except KiwiError:
        raise KiwiError("this computer's key does not open the fleet in %s: it is not accepted yet (on a computer "
                        "that has the fleet: kiwi-server control accept), or it was removed" % folder)
    _set_stamp(fleet_dir, here_digest(fleet_dir, fleet_name), there, FOLDER_STAMP)
    _remember_folder(fleet_dir, folder)
    return n


def request_join(fleet_dir, folder, name, key_file=None):
    """A new computer asks to join through the folder: it makes its key and leaves the public
    half in requests/. Returns the public key."""
    folder = os.path.abspath(os.path.expanduser(folder))
    if not folder_state(folder):
        raise KiwiError("%s holds no fleet: has the computer that has it pushed there, and has the folder synced?" % folder)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,27}", name or ""):
        raise KiwiError("a name for this computer: lowercase letters, digits and dashes, like laptop")
    st = storemod.Store(fleet_dir)
    if st.exists() or os.path.isfile(os.path.join(fleet_dir, "fleet.yaml")):
        raise KiwiError("%s holds a fleet already: join into an empty folder" % fleet_dir)
    os.makedirs(os.path.join(fleet_dir, "control"), mode=0o700, exist_ok=True)
    util.write_bytes(st.recipients, util.read_bytes(os.path.join(folder, "secrets.recipients")), 0o644)
    if any(n == name for n, _k in st.ways()):
        os.remove(st.recipients)
        raise KiwiError("a computer called %s can open this fleet already: choose another name (--name)" % name)
    if key_file and os.path.isfile(key_file):
        raise KiwiError("%s exists: a key file is written once" % key_file)
    if not key_file and storemod.keyring_get(st.store_id()):
        raise KiwiError("this computer already has a key for this fleet in its keyring (it holds the fleet "
                        "somewhere, or asked before): kiwi-server control pull %s --dir %s"
                        % (os.path.join(fleet_dir, "fleet.yaml"), folder))
    secret, public = storemod.keygen()
    if key_file:
        util.write_text(key_file, secret + "\n", 0o600)
    elif not storemod.keyring_put(st.store_id(), secret):
        raise KiwiError("no keyring took this computer's key (secret-tool): name a file for it with --identity FILE, "
                        "on a disk that is encrypted")
    os.makedirs(os.path.join(folder, REQUESTS), exist_ok=True)
    util.write_text(os.path.join(folder, REQUESTS, name + ".json"), json.dumps(
        {"name": name, "key": public, "host": socket.gethostname(), "time": int(time.time())}) + "\n", 0o644)
    _remember_folder(fleet_dir, folder)
    return public


def requests(folder):
    """The computers that ask to join: [{"name", "key", "host", "time"}]."""
    d = os.path.join(os.path.abspath(os.path.expanduser(folder)), REQUESTS)
    out = []
    for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not f.endswith(".json"):
            continue
        try:
            r = json.loads(util.read_text(os.path.join(d, f)))
        except ValueError:
            continue
        if (isinstance(r, dict) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,27}", str(r.get("name") or ""))
                and r["name"] + ".json" == f and re.fullmatch(r"age1[0-9a-z]{58}", str(r.get("key") or ""))):
            out.append({"name": r["name"], "key": r["key"], "host": str(r.get("host") or "?")[:60],
                        "time": int(r.get("time") or 0)})
    return out


def answer(fleet_dir, fleet_name, folder, name, accept):
    """Accept (the computer's key becomes one of the store's, and the folder gets the fleet
    encrypted for it too) or deny a request. Needs the open store to accept."""
    folder = os.path.abspath(os.path.expanduser(folder))
    req = next((r for r in requests(folder) if r["name"] == name), None)
    if not req:
        raise KiwiError("nobody called %s asks to join" % name)
    path = os.path.join(folder, REQUESTS, name + ".json")
    if not accept:
        os.remove(path)
        return
    st = _plain_store(fleet_dir)
    if not st.is_open():
        raise KiwiError("the store is locked: kiwi-server secrets unlock first")
    there = folder_state(folder)
    _here, then = _stamp(fleet_dir, FOLDER_STAMP)
    if there and there != then:
        raise KiwiError("the folder's copy is newer than this computer's: kiwi-server control pull first")
    st.add(name, req["key"])
    os.remove(path)
    push_dir(fleet_dir, fleet_name, folder)
