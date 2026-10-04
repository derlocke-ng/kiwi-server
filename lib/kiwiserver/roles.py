"""Roles: what a machine becomes after the base install.

A role is a directory under roles/ with two files:

    role.yaml   name, description, supported targets and a flat settings
                schema — the same schema validates fleet.yaml and builds the
                GUI form, so a new role needs no GUI code
    apply.sh    bash defining `ks_role_apply()`; it runs as root, once, on
                the first boot of the installed system (and can be re-run by
                hand on any machine)

The rendered script is self-contained: generated settings as KS_* variables,
embedded files, the common library (roles/common/lib.sh), the role body, and
a footer that runs it. Secrets are inside it, so it is written mode 0600.
"""
import os
import re

import yaml

from . import util
from .util import KiwiError

SETTING_TYPES = ("string", "text", "int", "bool", "enum", "file", "list", "map", "secret")
_KEY = re.compile(r"^[a-z][a-z0-9_]*$")


class Setting:
    def __init__(self, role, d):
        if not isinstance(d, dict) or not d.get("key"):
            raise KiwiError("%s/role.yaml: every setting needs a key" % role)
        self.key = str(d["key"])
        if not _KEY.match(self.key):
            raise KiwiError("%s/role.yaml: setting key %r must be snake_case" % (role, self.key))
        self.type = str(d.get("type") or "string")
        if self.type not in SETTING_TYPES:
            raise KiwiError("%s/role.yaml: setting %s has unknown type %s" % (role, self.key, self.type))
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
            raise KiwiError("%s/role.yaml: enum setting %s needs options" % (role, self.key))

    def as_dict(self):
        return {"key": self.key, "type": self.type, "label": self.label, "help": self.help,
                "required": self.required, "options": self.options, "targets": self.targets,
                "default": self.default, "placeholder": self.placeholder, "group": self.group}


class Role:
    def __init__(self, path):
        self.dir = path
        self.name = os.path.basename(path)
        meta = yaml.safe_load(util.read_text(os.path.join(path, "role.yaml"))) or {}
        if meta.get("name") and meta["name"] != self.name:
            raise KiwiError("%s/role.yaml: name %r does not match the directory" % (self.name, meta["name"]))
        self.title = str(meta.get("title") or self.name)
        self.description = str(meta.get("description") or "")
        self.status = str(meta.get("status") or "stable")
        self.targets = [str(t) for t in (meta.get("targets") or ["coreos", "ucore", "debian"])]
        self.settings = [Setting(self.name, s) for s in (meta.get("settings") or [])]
        keys = [s.key for s in self.settings]
        if len(keys) != len(set(keys)):
            raise KiwiError("%s/role.yaml: duplicate setting keys" % self.name)
        self.apply_path = os.path.join(path, "apply.sh")
        if not os.path.isfile(self.apply_path):
            raise KiwiError("%s: apply.sh is missing" % self.name)

    def as_dict(self):
        return {"name": self.name, "title": self.title, "description": self.description,
                "status": self.status, "targets": self.targets,
                "settings": [s.as_dict() for s in self.settings]}


def roles_dir():
    return os.environ.get("KIWI_SERVER_ROLES") or os.path.join(util.home_dir(), "roles")


def discover(path=None):
    path = path or roles_dir()
    if not os.path.isdir(path):
        raise KiwiError("roles directory not found: %s" % path)
    out = {}
    for name in sorted(os.listdir(path)):
        d = os.path.join(path, name)
        if os.path.isfile(os.path.join(d, "role.yaml")):
            out[name] = Role(d)
    if not out:
        raise KiwiError("no roles found in %s" % path)
    return out


def common_lib(path=None):
    p = os.path.join(path or roles_dir(), "common", "lib.sh")
    if not os.path.isfile(p):
        raise KiwiError("roles/common/lib.sh is missing")
    return util.read_text(p)


# ---- settings ---------------------------------------------------------------

def _coerce(setting, value, host):
    t = setting.type
    if value is None:
        return None
    if t == "int":
        try:
            return int(value)
        except (TypeError, ValueError):
            raise KiwiError("%s: %s.%s must be an integer" % (host.name, host.role, setting.key))
    if t == "bool":
        if isinstance(value, bool):
            return value
        if str(value).lower() in ("1", "true", "yes", "on"):
            return True
        if str(value).lower() in ("0", "false", "no", "off", ""):
            return False
        raise KiwiError("%s: %s.%s must be true or false" % (host.name, host.role, setting.key))
    if t == "list":
        if isinstance(value, str):
            return [v for v in value.split() if v]
        if not isinstance(value, list):
            raise KiwiError("%s: %s.%s must be a list" % (host.name, host.role, setting.key))
        return [str(v) for v in value]
    if t == "map":
        if not isinstance(value, dict):
            raise KiwiError("%s: %s.%s must be a mapping" % (host.name, host.role, setting.key))
        return {str(k): "" if v is None else str(v) for k, v in value.items()}
    if t == "enum":
        v = str(value)
        if v not in setting.options:
            raise KiwiError("%s: %s.%s must be one of %s" % (host.name, host.role, setting.key,
                                                             ", ".join(setting.options)))
        return v
    return str(value)


def resolve_settings(role, host):
    """Fill defaults, coerce and check the host's block for this role. File
    settings are read now, relative to the fleet file, and kept on the host
    so the script renderer can embed them."""
    raw = host.role_settings_raw or {}
    unknown = sorted(set(raw) - {s.key for s in role.settings})
    if unknown:
        raise KiwiError("%s: unknown %s setting(s): %s" % (host.name, role.name, ", ".join(unknown)))
    values, files = {}, {}
    for s in role.settings:
        if s.targets and host.target not in s.targets:
            values[s.key] = _coerce(s, s.default, host)
            continue
        v = raw.get(s.key, s.default)
        v = _coerce(s, v, host)
        empty = v is None or v == "" or v == [] or v == {}
        if s.required and empty:
            raise KiwiError("%s: %s.%s is required" % (host.name, role.name, s.key))
        if s.type == "file" and not empty:
            p = host.fleet.resolve_path(v)
            if not os.path.isfile(p):
                raise KiwiError("%s: %s.%s: file not found: %s" % (host.name, role.name, s.key, v))
            files[s.key] = util.read_bytes(p)
            v = os.path.basename(p)
        values[s.key] = v
    host.role_settings = values
    host.role_files = files
    return values


# ---- script rendering ----------------------------------------------------------

def _q(value):
    """Always single-quote. shlex.quote leaves plain words bare, and shellcheck
    then reads `KS_ROLE_USER=admin` as a possible command (SC2209)."""
    return "'" + str(value).replace("'", "'\\''") + "'"


def _bash_var(name, value):
    if isinstance(value, bool):
        return "%s=%d" % (name, 1 if value else 0)
    if isinstance(value, int):
        return "%s=%d" % (name, value)
    if isinstance(value, list):
        return "%s=(%s)" % (name, " ".join(_q(v) for v in value))
    if isinstance(value, dict):
        items = " ".join("[%s]=%s" % (_q(k), _q(v)) for k, v in value.items())
        return "declare -A %s=(%s)" % (name, items)
    return "%s=%s" % (name, _q("" if value is None else value))


def render_script(host, role, version, lib_text=None):
    lib_text = lib_text if lib_text is not None else common_lib()
    body = util.read_text(role.apply_path)
    if "ks_role_apply()" not in body and "ks_role_apply ()" not in body:
        raise KiwiError("%s/apply.sh must define ks_role_apply()" % role.name)
    u = host.updates()
    lines = [
        "#!/usr/bin/env bash",
        "# kiwi-server %s — role script for host %s" % (version, host.name),
        "#   role:   %s (%s)" % (role.name, role.title),
        "#   target: %s    hostname: %s" % (host.target, host.hostname),
        "#",
        "# Runs as root, once, on the first boot of the installed system "
        "(kiwi-role.service), and can be run again by hand on any machine:",
        "#   sudo bash %s.role.sh" % host.name,
        "# It embeds this host's secrets — keep it private (mode 0600).",
        "#",
        "# shellcheck disable=SC2034   # generated settings are read by the role body",
        "set -euo pipefail",
        "umask 022",
        "export LC_ALL=C.UTF-8 DEBIAN_FRONTEND=noninteractive",
        "",
        "# ---- generated settings ----------------------------------------------",
        "KS_VERSION=%s" % util.quote(version),
        _bash_var("KS_HOST", host.name),
        _bash_var("KS_HOSTNAME", host.hostname),
        _bash_var("KS_DOMAIN", host.domain),
        _bash_var("KS_TARGET", host.target),
        _bash_var("KS_ROLE", role.name),
        _bash_var("KS_ADMIN_USER", host.admin["user"]),
        _bash_var("KS_TIMEZONE", host.cfg["timezone"]),
        _bash_var("KS_UPDATES_ENABLED", bool(u["enabled"])),
        _bash_var("KS_UPDATES_DAYS", u["days"]),
        _bash_var("KS_UPDATES_TIME", u["time"]),
        "declare -A KS_FILES=()",
    ]
    for key, data in sorted(host.role_files.items()):
        lines.append("KS_FILES[%s]=%s" % (key, _q(util.b64(data))))
    for s in role.settings:
        lines.append(_bash_var("KS_ROLE_" + s.key.upper(), host.role_settings.get(s.key)))
    lines += ["", "# ---- common library (roles/common/lib.sh) ------------------------------",
              lib_text.rstrip("\n"), "",
              "# ---- role: %s (roles/%s/apply.sh) --------------------------------" % (role.name, role.name),
              body.rstrip("\n"), "",
              "# ---- post script (fleet.yaml post_script) -----------------------------",
              "ks_post_script() {"]
    post = (host.cfg.get("post_script") or "").rstrip("\n")
    lines += (["    " + ln if ln.strip() else "" for ln in post.splitlines()] if post.strip() else ["    :"])
    lines += ["}", "", "ks_main \"$@\"", ""]
    return "\n".join(lines)
