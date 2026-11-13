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
import hashlib
import io
import os
import shlex
import tarfile

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


def _stamp(fleet_dir):
    """(this side's digest, the node's digest) at the last sync."""
    p = os.path.join(fleet_dir, STAMP)
    parts = util.read_text(p).split() if os.path.isfile(p) else []
    return (parts[0], parts[1]) if len(parts) == 2 else ("", "")


def _set_stamp(fleet_dir, here, there):
    os.makedirs(os.path.join(fleet_dir, "control"), mode=0o700, exist_ok=True)
    util.write_text(os.path.join(fleet_dir, STAMP), "%s %s\n" % (here, there), 0o600)


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
    blobs = {}
    for name in PLAIN:
        path = _local(fleet_dir, fleet_name, name)
        if os.path.isfile(path):
            blobs[name + ".age"] = storemod.encrypt_bytes(st.recipients, util.read_bytes(path))
    for name in AS_IS:
        blobs[name] = util.read_bytes(os.path.join(fleet_dir, name))
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
    tmp = os.path.join(fleet_dir, "control", ".pull.age")
    plain = {}
    try:                    # everything is opened before anything here is replaced
        for name in PLAIN:
            if name + ".age" in got:
                util.write_bytes(tmp, got[name + ".age"], 0o600)
                plain[name] = storemod.decrypt_file(key, tmp)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    for name, content in list(plain.items()) + [(n, got[n]) for n in AS_IS if n in got]:
        dest = _local(fleet_dir, fleet_name, name)
        os.makedirs(os.path.dirname(dest), mode=0o700, exist_ok=True)
        util.write_bytes(dest + ".tmp", content, 0o644 if name == "secrets.recipients" else 0o600)
        os.replace(dest + ".tmp", dest)
    if st.is_open():
        st.unlock(key)      # the open tree was the old store's (saved above, so nothing is lost)
    remote.run(tgt, "printf '%%s\\n' %s > %s/%s" % (there, shlex.quote(rdir), STAMP))
    _set_stamp(fleet_dir, here_digest(fleet_dir, fleet_name), there)
    return len(plain) + len([n for n in AS_IS if n in got])
