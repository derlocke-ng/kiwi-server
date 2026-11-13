"""The settings schema shared by roles and modules.

A setting is one user-facing value: its key, type, default, whether it is
required, and how to present it. The same list validates fleet.yaml and
builds the GUI form, so a new role or module needs no GUI code.
"""
import re

from .util import KiwiError

SETTING_TYPES = ("string", "text", "int", "bool", "enum", "file", "list", "map", "secret", "object")
_KEY = re.compile(r"^[a-z][a-z0-9_]*$")


class Setting:
    def __init__(self, owner, d):
        if not isinstance(d, dict) or not d.get("key"):
            raise KiwiError("%s: every setting needs a key" % owner)
        self.key = str(d["key"])
        if not _KEY.match(self.key):
            raise KiwiError("%s: setting key %r must be snake_case" % (owner, self.key))
        self.type = str(d.get("type") or "string")
        if self.type not in SETTING_TYPES:
            raise KiwiError("%s: setting %s has unknown type %s" % (owner, self.key, self.type))
        self.label = str(d.get("label") or self.key.replace("_", " "))
        self.help = str(d.get("help") or "")
        self.required = bool(d.get("required", False))
        self.required_unless = str(d.get("required_unless") or "")   # required while that key is empty
        self.options = [str(o) for o in (d.get("options") or [])]
        self.targets = [str(t) for t in (d.get("targets") or [])]
        self.placeholder = str(d.get("placeholder") or "")
        self.group = str(d.get("group") or "Settings")
        # a secret the renderer makes up when it is empty: derived from the
        # fleet's seed, so every render gives the same value (see config.Fleet.seed)
        self.generate = bool(d.get("generate", False)) and self.type == "secret"
        # how a generated secret takes a new value when the fleet's seed is replaced. "restart":
        # the containers read it at every start and keep it nowhere else, so new files and a
        # restart are all it takes. Empty: it lives somewhere (a database, an account), and
        # the fleet keeps the old value until the module has a step that changes it there.
        # A mapping says how it is changed where it lives, before the restart:
        #   rotate: { in: immich-db, run: "psql … PASSWORD '$NEW'" }
        # `in` is a container of the module (after the prefix), `run` a shell command in it
        # with the new value in $NEW. It must work whatever the value is now, and twice.
        rot = d.get("rotate") if self.generate else None
        self.rotate_in = self.rotate_run = ""
        if isinstance(rot, dict):
            self.rotate, self.rotate_in, self.rotate_run = "exec", str(rot.get("in") or ""), str(rot.get("run") or "")
            if not self.rotate_in or "$NEW" not in self.rotate_run or set(rot) - {"in", "run"}:
                raise KiwiError("%s: setting %s: rotate needs in: (a container) and run: (a command with $NEW)" % (owner, self.key))
        else:
            self.rotate = str(rot or "")
            if self.rotate not in ("", "restart"):
                raise KiwiError("%s: setting %s: rotate is %r, not restart or {in, run}" % (owner, self.key, self.rotate))
        # a map of password hashes, one per value of the list setting `generate_each`, for
        # the secret each of those machines derives for itself as `generate_hash_of`
        # ("<module>/<key>"): what a server keeps of its clients' passwords
        self.generate_each = str(d.get("generate_each") or "") if self.type == "map" else ""
        self.generate_hash_of = str(d.get("generate_hash_of") or "")
        # a file setting that, left empty, is looked for at a conventional path
        # ({hostname} and {name} are filled in): what `kiwi-server enroll` writes
        self.fallback_file = str(d.get("fallback_file") or "") if self.type == "file" else ""
        if "default" in d:
            self.default = d["default"]
        else:
            self.default = {"int": 0, "bool": False, "list": [], "map": {}, "object": {}}.get(self.type, "")
        if self.type == "enum" and not self.options:
            raise KiwiError("%s: enum setting %s needs options" % (owner, self.key))
        # a regular expression a string value must match in full; the empty value always passes
        self.pattern = str(d.get("pattern") or "")
        # for a map: the pattern is for its values, this one for its keys
        self.key_pattern = str(d.get("key_pattern") or "")
        for pattern in (self.pattern, self.key_pattern):
            try:
                re.compile(pattern)
            except re.error as e:
                raise KiwiError("%s: setting %s has a bad pattern: %s" % (owner, self.key, e))

    def as_dict(self):
        return {"key": self.key, "type": self.type, "label": self.label, "help": self.help,
                "required": self.required, "options": self.options, "targets": self.targets,
                "default": self.default, "placeholder": self.placeholder, "group": self.group,
                "generate": self.generate, "rotate": self.rotate, "fallback_file": self.fallback_file, "pattern": self.pattern}


def coerce(setting, value, where):
    """Turn a YAML value into the setting's type, or raise a readable error.
    `where` names the host and block for the message."""
    t = setting.type
    if value is None:
        return None
    if t == "int":
        try:
            return int(value)
        except (TypeError, ValueError):
            raise KiwiError("%s.%s must be an integer" % (where, setting.key))
    if t == "bool":
        if isinstance(value, bool):
            return value
        if str(value).lower() in ("1", "true", "yes", "on"):
            return True
        if str(value).lower() in ("0", "false", "no", "off", ""):
            return False
        raise KiwiError("%s.%s must be true or false" % (where, setting.key))
    if t == "list":
        if isinstance(value, str):
            items = [v for v in value.split() if v]
        elif isinstance(value, list):
            items = [str(v) for v in value]
        else:
            raise KiwiError("%s.%s must be a list" % (where, setting.key))
        for v in items:
            if setting.pattern and not re.fullmatch(setting.pattern, v):
                raise KiwiError("%s.%s: %r is not a valid value" % (where, setting.key, v))
        return items
    if t == "map":
        if not isinstance(value, dict):
            raise KiwiError("%s.%s must be a mapping" % (where, setting.key))
        items = {str(k): "" if v is None else str(v) for k, v in value.items()}
        for k, v in items.items():
            if setting.key_pattern and not re.fullmatch(setting.key_pattern, k):
                raise KiwiError("%s.%s: %r is not a valid name" % (where, setting.key, k))
            if setting.pattern and not re.fullmatch(setting.pattern, v):
                raise KiwiError("%s.%s: %r is not a valid value" % (where, setting.key, v))
        return items
    if t == "object":
        if not isinstance(value, (dict, list)):
            raise KiwiError("%s.%s must be a mapping or a list" % (where, setting.key))
        return value
    if t == "enum":
        v = str(value)
        if v not in setting.options:
            raise KiwiError("%s.%s must be one of %s" % (where, setting.key, ", ".join(setting.options)))
        return v
    v = str(value)
    if setting.pattern and v and not re.fullmatch(setting.pattern, v):
        like = " (like %s)" % setting.placeholder if setting.placeholder else ""
        shown = "" if t == "secret" else " %r" % v      # a secret is never printed
        raise KiwiError("%s.%s:%s is not a valid value%s" % (where, setting.key, shown, like))
    return v


def is_empty(v):
    return v is None or v == "" or v == [] or v == {}


def load_settings(owner, raw_list):
    settings = [Setting(owner, s) for s in (raw_list or [])]
    keys = [s.key for s in settings]
    if len(keys) != len(set(keys)):
        raise KiwiError("%s: duplicate setting keys" % owner)
    return settings
