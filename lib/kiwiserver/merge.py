"""Two copies of a fleet that both changed since they last agreed, made one.

Every sync leaves a base behind: what both sides held then (`control/base.age`, encrypted
for the store's keys like everything else at rest). With it each file is told apart:
changed here, changed there, or on both sides. A file one side changed is taken from that
side. A file both changed is merged when it is a list that only grows (the machines' host
keys), and named otherwise: then a person says which copy counts.
"""
import json
import hashlib
import difflib
import os
import re
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


_SECRET_KEY = re.compile(r"(pass|secret|private|token|key|psk|salt|credential|hash)", re.I)
_YAML_PAIR = re.compile(r"^(\s*(?:#\s*)?-?\s*)([A-Za-z0-9_.-]+)(\s*[:=]\s*)(.+)$")
_LONG_TOKEN = re.compile(r"[A-Za-z0-9+/=_-]{32,}")


def masked(line):
    """A line of the fleet file fit to show: the value of a key that sounds secret hidden, and
    any long token (a key, a hash) wherever it stands."""
    m = _YAML_PAIR.match(line)
    if m and _SECRET_KEY.search(m.group(2)) and m.group(4).strip() not in ("|", "|-", ">", "{}", "[]", '""', "''"):
        line = m.group(1) + m.group(2) + m.group(3) + "•••"
    return _LONG_TOKEN.sub("•••", line)


def _names(data):
    """The names in a secret file, never its values: the keys of a JSON or YAML mapping, the
    variables of KEY=value lines, the first word of other lines when it is no secret itself
    (a host in known_hosts). None when the file is not text of that kind."""
    try:
        text = data.decode()
    except UnicodeDecodeError:
        return None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return {str(k): hashlib.sha256(json.dumps(v, sort_keys=True).encode()).hexdigest() for k, v in obj.items()}
    except ValueError:
        pass
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-----"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_.-]{0,63})\s*[=:]\s*(.*)$", line)
        if not m:
            return None                 # not a file of names and values: nothing of it is shown
        out[m.group(1)] = hashlib.sha256(m.group(2).encode()).hexdigest()
    return out or None


def secret_summary(here, there):
    """Lines a person may read about two versions of a secret file: how big each is, and, where
    the file is a list of names with values, which names were added, removed or changed. No
    value, and nothing a value could be worked out from."""
    def size(data):
        if data is None:
            return "not there"
        text = data.decode(errors="replace")
        return "%d bytes, %d line%s" % (len(data), len(text.splitlines()), "" if len(text.splitlines()) == 1 else "s")
    lines = ["  the other copy: %s" % size(there), "  this computer:  %s" % size(here)]
    a, b = (_names(there) if there is not None else {}), (_names(here) if here is not None else {})
    if a is None or b is None:
        lines.append("  Its content is secret as a whole: only that the two differ can be said.")
        return lines
    for name in sorted(set(a) | set(b)):
        if name not in a:
            lines.append("+ %s (only on this computer)" % name)
        elif name not in b:
            lines.append("- %s (only in the other copy)" % name)
        elif a[name] != b[name]:
            lines.append("@ %s has another value on each side" % name)
    if len(lines) == 2:
        lines.append("  The same names with the same values: the files differ only in their order or spacing.")
    return lines


def differences(base, here, there):
    """What differs between this copy and the other one, for a person to see before saying
    which counts: [{path, who, state, lines}]. who: here, there or both (who changed it since
    the last sync; unknown without a base). lines: of the fleet files a diff with every
    secret value hidden; of the known hosts which hosts each side has more; of the other
    secrets their sizes and the names that differ (secret_summary), never what they hold."""
    out = []
    for path in sorted(set(here) | set(there)):
        h, t = here.get(path), there.get(path)
        if h == t:
            continue
        if base is None:
            who = "unknown"
        else:
            b = base.get(path)
            who = "there" if h == b else "here" if t == b else "both"
        row = {"path": path, "who": who, "state": "only here" if t is None else "only there" if h is None else "changed",
               "lines": []}
        if path in ("fleet.yaml", "federation.yaml"):
            old = [masked(x) for x in (t or b"").decode(errors="replace").splitlines()]
            new = [masked(x) for x in (h or b"").decode(errors="replace").splitlines()]
            row["lines"] = list(difflib.unified_diff(old, new, "the other copy", "this computer", n=2, lineterm=""))[2:]
        elif path in LISTS:             # host keys are public: which hosts each side has more
            old = set((t or b"").decode(errors="replace").splitlines())
            new = set((h or b"").decode(errors="replace").splitlines())
            row["lines"] = (["- %s (only in the other copy)" % " ".join(x.split()[:2]) for x in sorted(old - new)] +
                            ["+ %s (only on this computer)" % " ".join(x.split()[:2]) for x in sorted(new - old)])
        else:
            row["lines"] = secret_summary(h, t)
        out.append(row)
    return out


def read_tree(root):
    """{relative path: bytes} of every file below root (links are not followed)."""
    out = {}
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            if fn != ".kiwi-lock" and os.path.isfile(p) and not os.path.islink(p):     # (a writer's lock)
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
            util.write_bytes(path, tree[name], 0o600)
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
        util.write_bytes(path, data, 0o600)


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
    util.write_bytes(path, storemod.encrypt_bytes(st.recipients, _pack(tree)), 0o600)


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
