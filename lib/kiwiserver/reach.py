"""What one machine's render reads of the other machines (docs/design/scale.md, "The plan for that
step"): so that a change of one part's settings on one machine renders that machine again, and the
others only when they read that part.

A machine's part-level data (`module_settings`, `module_files`, the part blocks of its role block in
`cfg` and `role_settings_raw`) is wrapped in a `Watched` mapping. While `reading(name)`
is in effect, a look into another machine's part-level data is written down as (machine, part);
"*" for a look at all of its parts (iterating, copying, dumping). A machine's own data is not
written down: its fingerprint holds all of it.

The rest of every machine (its name, role, parts, addresses, the rest of its settings) is in every
fingerprint as the fleet's shared view, so nothing else needs watching."""
import contextlib
import copy
import hashlib
import threading

_state = threading.local()


def _who():
    return getattr(_state, "who", None)


def _note(owner, part):
    seen = getattr(_state, "seen", None)
    who = _who()
    if seen is not None and who is not None and owner != who:
        seen.add((owner, part))


@contextlib.contextmanager
def reading(who, seen):
    """Reads of other machines' part-level data, by `who`, go into the set `seen`."""
    before = (_who(), getattr(_state, "seen", None))
    _state.who, _state.seen = who, seen
    try:
        yield seen
    finally:
        _state.who, _state.seen = before


@contextlib.contextmanager
def own(who):
    """Reads while `who`'s own settings are resolved count as its own (prepare)."""
    before = _who()
    _state.who = who
    try:
        yield
    finally:
        _state.who = before


class Watched(dict):
    """A dict whose keys are parts (or 'part/key' for files) of the machine `owner`."""
    __slots__ = ("owner", "split")

    def __init__(self, data=(), owner="", split=False):
        super().__init__(data)
        self.owner, self.split = owner, split

    def _part(self, key):
        k = str(key)
        return k.split("/", 1)[0] if self.split else k

    def __getitem__(self, key):
        _note(self.owner, self._part(key))
        return super().__getitem__(key)

    def get(self, key, default=None):
        _note(self.owner, self._part(key))
        return super().get(key, default)

    def __contains__(self, key):
        _note(self.owner, self._part(key))
        return super().__contains__(key)

    def setdefault(self, key, default=None):
        _note(self.owner, self._part(key))
        return super().setdefault(key, default)

    def pop(self, key, *default):
        _note(self.owner, self._part(key))
        return super().pop(key, *default)

    def _all(self):
        _note(self.owner, "*")

    def __iter__(self):
        self._all()
        return super().__iter__()

    def keys(self):
        self._all()
        return super().keys()

    def values(self):
        self._all()
        return super().values()

    def items(self):
        self._all()
        return super().items()

    def copy(self):
        self._all()
        return dict(super().items())

    def __len__(self):
        self._all()
        return super().__len__()

    def __eq__(self, other):
        self._all()
        return dict.__eq__(self, other)

    __hash__ = None

    def __repr__(self):
        self._all()
        return dict.__repr__(self)

    def __reduce_ex__(self, protocol):
        self._all()
        return (dict, (dict(super().items()),))

    def __deepcopy__(self, memo):
        self._all()
        return copy.deepcopy(dict(super().items()), memo)


def watch(host, part_names):
    """`host`'s part-level data, wrapped. `part_names`: every part the network knows (so that the
    part blocks inside a role block are told apart from its other keys). Idempotent."""
    name = host.name
    host.module_settings = _wrap(host.module_settings, name)
    host.module_files = _wrap(host.module_files, name, split=True)
    block = dict.get(host.cfg, host.role) if isinstance(host.cfg, dict) and host.role else None
    if isinstance(block, dict) and not isinstance(block, _PartBlock):
        host.cfg[host.role] = _PartBlock(block, name, part_names)
    if host.role and isinstance(host.role_settings_raw, dict) and not isinstance(host.role_settings_raw, _PartBlock):
        host.role_settings_raw = _PartBlock(host.role_settings_raw, name, part_names)


def _wrap(d, owner, split=False):
    if isinstance(d, Watched):
        return d
    return Watched(d or {}, owner, split)


class _PartBlock(Watched):
    """A role block: its part keys watched, its other keys (vpn_ip, modules) part of the shared view."""
    __slots__ = ("parts",)

    def __init__(self, data=(), owner="", parts=()):
        super().__init__(data, owner)
        self.parts = frozenset(parts)

    def _part(self, key):
        return str(key) if str(key) in self.parts else None

    def __getitem__(self, key):
        p = self._part(key)
        if p:
            _note(self.owner, p)
        return dict.__getitem__(self, key)

    def get(self, key, default=None):
        p = self._part(key)
        if p:
            _note(self.owner, p)
        return dict.get(self, key, default)

    def __contains__(self, key):
        p = self._part(key)
        if p:
            _note(self.owner, p)
        return dict.__contains__(self, key)


def shared_view(host, part_names):
    """What every fingerprint holds of `host`: all of it but its part-level data."""
    def strip(block):
        return {k: v for k, v in dict.items(block or {}) if k not in part_names} if isinstance(block, dict) else block
    cfg = {k: (strip(v) if k == host.role else v) for k, v in dict.items(host.cfg or {})}
    return {"name": host.name, "hostname": host.hostname, "role": host.role, "target": host.target,
            "standby_of": host.standby_of, "standby_endpoint": host.standby_endpoint, "modules": list(host.modules or []),
            "cfg": cfg, "role_settings": strip(getattr(host, "role_settings", None) or {})}


def part_data(host, part):
    """The part-level data of one part of `host` ("*": all of it), for a fingerprint."""
    if part == "*":
        parts = sorted(set(dict.keys(host.module_settings or {})) | {k.split("/", 1)[0] for k in dict.keys(host.module_files or {})}
                       | {k for k in dict.keys(host.cfg.get(host.role) or {}) if isinstance(dict.get(host.cfg.get(host.role) or {}, k), dict)})
        return {p: part_data(host, p) for p in parts}
    block = dict.get(host.cfg, host.role) if host.role else None
    raw = dict.get(block, part) if isinstance(block, dict) else None
    files = {k: hashlib.sha256(v if isinstance(v, bytes) else str(v).encode()).hexdigest()
             for k, v in dict.items(host.module_files or {}) if k.split("/", 1)[0] == part}
    return {"settings": dict.get(host.module_settings or {}, part), "raw": raw, "files": files}
