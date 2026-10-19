"""Profiles: what a machine's stack is — a module list (or presets of them),
module defaults and the stack settings. One directory per profile with a
role.yaml (the format kiwi-server's roles use; kiwi-server adds the install
side: targets and apply.sh)."""
import os

import yaml

from . import util
from .schema import load_settings
from .util import KiwiError, deep_merge


class Profile:
    def __init__(self, path, roles_root):
        self.dir = path
        self.name = os.path.basename(path)
        meta = yaml.safe_load(util.read_text(os.path.join(path, "role.yaml"))) or {}
        if meta.get("name") and meta["name"] != self.name:
            raise KiwiError("%s/role.yaml: name %r does not match the directory" % (self.name, meta["name"]))
        self.title = str(meta.get("title") or self.name)
        self.description = str(meta.get("description") or "")
        self.status = str(meta.get("status") or "stable")
        self.targets = [str(t) for t in (meta.get("targets") or ["coreos", "ucore", "debian"])]
        self.modules = [str(m) for m in (meta.get("modules") or [])]
        self.module_defaults = {str(k): dict(v or {}) for k, v in (meta.get("module_defaults") or {}).items()}
        # presets: named module lists (with their own module defaults) the host
        # picks with `preset:`; `modules:` in the host's block still overrides
        self.presets = {}
        for pname, pdef in (meta.get("presets") or {}).items():
            pdef = pdef or {}
            self.presets[str(pname)] = {
                "title": str(pdef.get("title") or pname), "description": str(pdef.get("description") or ""),
                "modules": [str(m) for m in (pdef.get("modules") or [])],
                "module_defaults": {str(k): dict(v or {}) for k, v in (pdef.get("module_defaults") or {}).items()}}
        self.stack = bool(self.modules or self.presets)
        self.node_type = str(meta.get("node_type") or ("master" if "vpn-server" in self.modules else "node"))
        raw_settings = []
        for inc in meta.get("settings_include") or []:
            p = os.path.join(roles_root, str(inc) + ".yaml")
            if not os.path.isfile(p):
                raise KiwiError("%s/role.yaml: settings_include %s not found" % (self.name, inc))
            raw_settings += (yaml.safe_load(util.read_text(p)) or {}).get("settings") or []
        raw_settings += meta.get("settings") or []
        self.settings = load_settings("%s/role.yaml" % self.name, raw_settings)

    def as_dict(self, modset=None):
        d = {"name": self.name, "title": self.title, "description": self.description,
             "status": self.status, "targets": self.targets, "stack": self.stack,
             "node_type": self.node_type, "modules": self.modules,
             "module_defaults": self.module_defaults, "presets": self.presets,
             "settings": [s.as_dict() for s in self.settings]}
        if self.stack and modset is not None:
            names = list(self.modules)
            for pr in self.presets.values():
                names += [m for m in pr["modules"] if m not in names]
            d["module_schemas"] = [modset.get(m).as_dict() for m in modset.resolve(names)]
        return d

    def preset_for(self, settings):
        """The preset a host picked (its `preset` role setting), or None."""
        if not self.presets:
            return None
        name = str((settings or {}).get("preset") or "")
        if not name:
            name = next(iter(self.presets))
        if name not in self.presets:
            raise KiwiError("%s: unknown preset %r (have: %s)" % (self.name, name, ", ".join(self.presets)))
        return self.presets[name]

    def default_modules(self, settings=None):
        pr = self.preset_for(settings)
        return list(pr["modules"]) if pr else list(self.modules)

    def defaults_for(self, name, settings=None):
        """The module's defaults: the role's, then the preset's on top."""
        out = dict(self.module_defaults.get(name) or {})
        pr = self.preset_for(settings)
        if pr:
            out = deep_merge(out, pr["module_defaults"].get(name) or {})
        return out




def discover(path=None):
    path = path or util.profiles_root()
    if not os.path.isdir(path):
        raise KiwiError("profiles directory not found: %s" % path)
    out = {}
    for name in sorted(os.listdir(path)):
        d = os.path.join(path, name)
        if os.path.isfile(os.path.join(d, "role.yaml")):
            out[name] = Profile(d, path)
    if not out:
        raise KiwiError("no profiles found in %s" % path)
    return out
