"""The settings schema shared by roles and modules.

A setting is one user-facing value: its key, type, default, whether it is
required, and how to present it. The same list validates fleet.yaml and
builds the GUI form, so a new role or module needs no GUI code.
"""
import re

from .util import KiwiError

SETTING_TYPES = ("string", "text", "int", "bool", "enum", "file", "list", "map", "secret")
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
        self.options = [str(o) for o in (d.get("options") or [])]
        self.targets = [str(t) for t in (d.get("targets") or [])]
        self.placeholder = str(d.get("placeholder") or "")
        self.group = str(d.get("group") or "Settings")
        if "default" in d:
            self.default = d["default"]
        else:
            self.default = {"int": 0, "bool": False, "list": [], "map": {}}.get(self.type, "")
        if self.type == "enum" and not self.options:
            raise KiwiError("%s: enum setting %s needs options" % (owner, self.key))

    def as_dict(self):
        return {"key": self.key, "type": self.type, "label": self.label, "help": self.help,
                "required": self.required, "options": self.options, "targets": self.targets,
                "default": self.default, "placeholder": self.placeholder, "group": self.group}


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
            return [v for v in value.split() if v]
        if not isinstance(value, list):
            raise KiwiError("%s.%s must be a list" % (where, setting.key))
        return [str(v) for v in value]
    if t == "map":
        if not isinstance(value, dict):
            raise KiwiError("%s.%s must be a mapping" % (where, setting.key))
        return {str(k): "" if v is None else str(v) for k, v in value.items()}
    if t == "enum":
        v = str(value)
        if v not in setting.options:
            raise KiwiError("%s.%s must be one of %s" % (where, setting.key, ", ".join(setting.options)))
        return v
    return str(value)


def is_empty(v):
    return v is None or v == "" or v == [] or v == {}


def load_settings(owner, raw_list):
    settings = [Setting(owner, s) for s in (raw_list or [])]
    keys = [s.key for s in settings]
    if len(keys) != len(set(keys)):
        raise KiwiError("%s: duplicate setting keys" % owner)
    return settings
