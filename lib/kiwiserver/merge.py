"""Two copies of a fleet that both changed since they last agreed, made one.

Every sync leaves a base behind: what both sides held then (`control/base.age`, encrypted
for the store's keys like everything else at rest). With it each file is told apart:
changed here, changed there, or on both sides. A file one side changed is taken from that
side. A file both changed is merged when it is a list that only grows (the machines' host
keys), or the fleet file, key by key, when the two sides changed different keys (one machine's
setting on the page, another's in the app); it is named otherwise: then a person says which
copy counts.
"""
import json
import time
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
# YAML files merged key by key: the page changes one machine's setting, the app another's, and
# both are kept. Only a key both sides changed, to different values, needs a person.
YAMLS = ("fleet.yaml", "federation.yaml")
# The secret store's JSON maps, merged key by key as well (a machine's MAC on one side, a pin on
# the other); of one key both changed, the entry with the later "seen" or "at" counts.
JSONS = ("secrets/macs.json", "secrets/pins.json")
# Files of the store that hold nothing secret: what differs in them is shown as it is.
OPEN = ("secrets/macs.json",)
_MISSING, _CONFLICT = object(), object()


def _is_token(path):
    return path.startswith("secrets/") and path.endswith(".token") and "/" not in path[len("secrets/"):]


def _token_at(data):
    """When a login token was kept (its "at"), 0 when it does not say."""
    try:
        return int(json.loads(data.decode()).get("at") or 0)
    except (ValueError, AttributeError, TypeError):
        return 0


def _merge_value(b, h, t):
    if h == t or t == b:
        return h
    if h == b:
        return t
    if isinstance(h, dict) and isinstance(t, dict) and isinstance(b, (dict, type(_MISSING))):
        base = b if isinstance(b, dict) else {}
        out = {}
        for k in list(h) + [k for k in t if k not in h] + [k for k in base if k not in h and k not in t]:
            v = _merge_value(base.get(k, _MISSING), h.get(k, _MISSING), t.get(k, _MISSING))
            if v is _CONFLICT:
                return _CONFLICT
            if v is not _MISSING:
                out[k] = v
        return out
    return _CONFLICT


def _when(v):
    """The time a JSON entry says it was seen or kept (seen, at), or None."""
    if isinstance(v, dict):
        for k in ("seen", "at"):
            if isinstance(v.get(k), (int, float)):
                return v[k]
    return None


def merge_json(b, h, t):
    """Three JSON maps (bytes; base may be None) merged key by key, as bytes, or None when a key
    changed on both sides and no time says which is newer."""
    try:
        bd = json.loads(b.decode()) if b is not None else {}
        hd, td = json.loads(h.decode()), json.loads(t.decode())
    except (ValueError, UnicodeDecodeError):
        return None
    if not all(isinstance(x, dict) for x in (bd, hd, td)):
        return None
    out = {}
    for k in list(hd) + [k for k in td if k not in hd] + [k for k in bd if k not in hd and k not in td]:
        hv, tv = hd.get(k, _MISSING), td.get(k, _MISSING)
        v = _merge_value(bd.get(k, _MISSING), hv, tv)
        if v is _CONFLICT:
            wh, wt = _when(hv), _when(tv)
            if wh is None or wt is None:
                return None
            v = hv if wh >= wt else tv
        if v is not _MISSING:
            out[k] = v
    if out == hd:
        return h
    if out == td:
        return t
    return (json.dumps(out, indent=1, sort_keys=True) + "\n").encode()


def _inner_comments(text):
    """The comment lines after the leading ones: a merge writes the file anew and would lose them."""
    lines = (text or b"").decode(errors="replace").splitlines()
    i = 0
    while i < len(lines) and lines[i].startswith("#"):
        i += 1
    return [ln for ln in lines[i:] if ln.lstrip().startswith("#") or " #" in ln]


def merge_yaml(b, h, t):
    """Three YAML files (bytes; base may be None) merged key by key, as bytes, or None when a key
    changed on both sides to different values (or a side is no YAML). The text of the side whose
    data the result equals is kept as it is; a new text keeps here's leading comment lines. A file
    with comments of its own further down is not merged (they would be lost): a person decides,
    as before. The page writes the fleet file without them."""
    import yaml
    if any(_inner_comments(x) for x in (b, h, t)):
        return None
    try:
        bd = yaml.safe_load(b) if b is not None else _MISSING
        hd, td = yaml.safe_load(h), yaml.safe_load(t)
    except yaml.YAMLError:
        return None
    out = _merge_value(bd if bd is not None else _MISSING, hd, td)
    if out is _CONFLICT or out is _MISSING:
        return None
    if out == hd:
        return h
    if out == td:
        return t
    head = []
    for line in h.decode(errors="replace").splitlines():
        if not line.startswith("#"):
            break
        head.append(line)
    text = yaml.safe_dump(out, sort_keys=False, allow_unicode=True, width=1000)
    return (("\n".join(head) + "\n") if head else "").encode() + text.encode()


def merge(base, here, there, keep=None):
    """({path: content} to hold from now on, paths taken from there, paths both changed).
    The three arguments are {path: bytes}; a path that is missing is a file that is not there.
    `keep` ("here" or "there"): a file both changed is taken from that side, everything else is
    merged as always (a person's answer to "which counts?", for what both changed only)."""
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
        elif _is_token(path) and h is not None and t is not None:
            # a login both sides renewed (netcup's refresh token changes when it is used): the newer
            pick = h if _token_at(h) > _token_at(t) else t
            if pick is t:
                theirs.append(path)
        elif path in YAMLS and h is not None and t is not None and merge_yaml(b, h, t) is not None:
            pick = merge_yaml(b, h, t)
            theirs.append(path)
        elif path in JSONS and h is not None and t is not None and merge_json(b, h, t) is not None:
            pick = merge_json(b, h, t)
            theirs.append(path)
        elif keep == "there":
            pick = t
            theirs.append(path)
        elif keep == "here":
            pick = h
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
        # does the next sync make one of the two by itself? Then there is nothing to decide about it
        if base is not None:
            b = base.get(path)
            one = lambda x: {} if x is None else {path: x}          # noqa: E731
            row["auto"] = not merge(one(b), one(h), one(t))[2]
        else:
            row["auto"] = False
        if path in ("fleet.yaml", "federation.yaml"):
            old = [masked(x) for x in (t or b"").decode(errors="replace").splitlines()]
            new = [masked(x) for x in (h or b"").decode(errors="replace").splitlines()]
            row["lines"] = list(difflib.unified_diff(old, new, "the other copy", "this computer", n=2, lineterm=""))[2:]
        elif path in LISTS:             # host keys are public: which hosts each side has more
            old = set((t or b"").decode(errors="replace").splitlines())
            new = set((h or b"").decode(errors="replace").splitlines())
            row["lines"] = (["- %s (only in the other copy)" % " ".join(x.split()[:2]) for x in sorted(old - new)] +
                            ["+ %s (only on this computer)" % " ".join(x.split()[:2]) for x in sorted(new - old)])
        elif path in OPEN:
            row["lines"] = open_summary(h, t)
        else:
            row["lines"] = secret_summary(h, t)
        out.append(row)
    return out


def open_summary(here, there):
    """Of a JSON map of the store that holds nothing secret (the machines' MACs): each entry that
    differs, as it is on each side, and which side has it newer."""
    def load(data):
        try:
            v = json.loads(data.decode()) if data is not None else {}
            return v if isinstance(v, dict) else {}
        except (ValueError, UnicodeDecodeError):
            return {}

    def said(v):
        if v is _MISSING:
            return "not there"
        if isinstance(v, dict):
            parts = ["%s %s" % (k, v[k]) for k in sorted(v) if k not in ("seen", "at")]
            w = _when(v)
            if w is not None:
                parts.append("seen %s" % time.strftime("%d %b %H:%M", time.localtime(w)))
            return ", ".join(parts)
        return str(v)
    a, b = load(there), load(here)
    lines = []
    for k in sorted(set(a) | set(b)):
        tv, hv = a.get(k, _MISSING), b.get(k, _MISSING)
        if tv == hv:
            continue
        wt, wh = _when(tv), _when(hv)
        newer = "" if wt is None or wh is None else (" (newer: this computer)" if wh > wt else " (newer: the other copy)" if wt > wh else "")
        lines.append("@ %s%s" % (k, newer))
        lines.append("    this computer:  %s" % said(hv))
        lines.append("    the other copy: %s" % said(tv))
    return lines or ["  The same entries: the files differ only in their order or spacing."]


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
