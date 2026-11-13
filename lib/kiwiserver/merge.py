"""Two copies of a fleet that both changed since they last agreed, made one.

Every sync leaves a base behind: what both sides held then (`control/base.age`, encrypted
for the store's keys like everything else at rest). With it each file is told apart:
changed here, changed there, or on both sides. A file one side changed is taken from that
side. A file both changed is merged when it is a list that only grows (the machines' host
keys), and named otherwise: then a person says which copy counts.
"""
import os
import shutil
import tempfile

from . import store as storemod, util
from .util import KiwiError

BASE = os.path.join("control", "base.age")
LISTS = ("secrets/known_hosts", "secrets/known_hosts.initramfs")    # lines; both sides' are kept


def merge(base, here, there):
    """({path: content} to hold from now on, paths taken from there, paths both changed).
    The three arguments are {path: bytes}; a path that is missing is a file that is not there."""
    merged, theirs, both = {}, [], []
    for path in sorted(set(base) | set(here) | set(there)):
        b, h, t = base.get(path), here.get(path), there.get(path)
        if h == t or t == b:
            pick = h
        elif h == b:
            pick = t
            theirs.append(path)
        elif path in LISTS and h is not None and t is not None:
            lines = h.decode(errors="replace").splitlines()
            lines += [x for x in t.decode(errors="replace").splitlines() if x not in lines]
            pick = ("\n".join(lines) + "\n").encode()
            theirs.append(path)
        else:
            both.append(path)
            pick = h
        if pick is not None:
            merged[path] = pick
    return merged, theirs, both


def read_tree(root):
    """{relative path: bytes} of every file below root (links are not followed)."""
    out = {}
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            if os.path.isfile(p) and not os.path.islink(p):
                out[os.path.relpath(p, root)] = util.read_bytes(p)
    return out


def local_tree(fleet_dir, fleet_name, plain):
    """This computer's copy: the fleet files by their names on the node, the open store's
    files below secrets/."""
    out = {}
    for name in plain:
        path = os.path.join(fleet_dir, fleet_name if name == "fleet.yaml" else name)
        if os.path.isfile(path):
            out[name] = util.read_bytes(path)
    secrets = os.path.join(fleet_dir, "secrets")
    if not os.path.isdir(secrets):
        raise KiwiError("the secret store is locked: kiwi-server secrets unlock first")
    for rel, data in read_tree(secrets).items():
        out["secrets/" + rel] = data
    return out


def put_tree(fleet_dir, fleet_name, plain, tree):
    """Make this computer's copy the given one: fleet files and the open store's files."""
    for name in plain:
        path = os.path.join(fleet_dir, fleet_name if name == "fleet.yaml" else name)
        if name in tree:
            util.write_bytes(path + ".tmp", tree[name], 0o600)
            os.replace(path + ".tmp", path)
        elif os.path.isfile(path):
            os.remove(path)
    secrets = os.path.join(fleet_dir, "secrets")
    want = {p[len("secrets/"):]: d for p, d in tree.items() if p.startswith("secrets/")}
    for rel in read_tree(secrets):
        if rel not in want:
            os.remove(os.path.join(secrets, rel))
    for rel, data in want.items():
        path = os.path.join(secrets, rel)
        if os.path.isfile(path) and util.read_bytes(path) == data:
            continue
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        util.write_bytes(path + ".tmp", data, 0o600)
        os.replace(path + ".tmp", path)


def _pack(tree):
    d = tempfile.mkdtemp(prefix="kiwi-base-", dir=os.environ.get("XDG_RUNTIME_DIR") or "/dev/shm")
    try:
        for rel, data in tree.items():
            path = os.path.join(d, rel)
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            util.write_bytes(path, data, 0o600)
        return storemod.snapshot(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _unpack(data):
    d = tempfile.mkdtemp(prefix="kiwi-base-", dir=os.environ.get("XDG_RUNTIME_DIR") or "/dev/shm")
    try:
        storemod.extract(data, os.path.join(d, "t"))
        return read_tree(os.path.join(d, "t"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def save_base(fleet_dir, st, tree):
    """What both sides hold now, for the next time they differ."""
    path = os.path.join(fleet_dir, BASE)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    util.write_bytes(path + ".tmp", storemod.encrypt_bytes(st.recipients, _pack(tree)), 0o600)
    os.replace(path + ".tmp", path)


def load_base(fleet_dir, identity):
    """The tree of the last sync, or None when there was none yet (or it cannot be read)."""
    path = os.path.join(fleet_dir, BASE)
    if not os.path.isfile(path):
        return None
    try:
        return _unpack(storemod.decrypt_file(identity, path))
    except KiwiError:
        return None


def store_tree(blob, identity):
    """{path: bytes} below secrets/ of an encrypted store as it came from the other side."""
    d = tempfile.mkdtemp(prefix="kiwi-there-", dir=os.environ.get("XDG_RUNTIME_DIR") or "/dev/shm")
    try:
        path = os.path.join(d, "secrets.age")
        util.write_bytes(path, blob, 0o600)
        return {"secrets/" + rel: data for rel, data in _unpack(storemod.decrypt_file(identity, path)).items()}
    finally:
        shutil.rmtree(d, ignore_errors=True)
