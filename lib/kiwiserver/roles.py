"""Roles: what a machine becomes after the base install.

A role is a directory under roles/ with two files:

    role.yaml   name, description, supported targets, a settings schema and —
                for a *module role* — the list of kiwi-v2 modules it deploys
    apply.sh    bash defining `ks_role_apply()`; it runs as root, once, on
                the first boot of the installed system (and can be re-run by
                hand on any machine)

A module role's settings come from two places: the role's own (the stack:
docker directory, service user, VPN address …) and each enabled module's
`settings:` in its module.yaml. In the fleet file that looks like

    node-cloud:
      vpn_ip: 10.8.0.25
      vpn-client: { wireguard_config: secrets/sh3.conf }
      cloud:      { nc_hostname: cloud.sh3.kiwi }

The rendered script is self-contained: generated settings as KS_* variables,
embedded files (the rendered stack among them), the common library
(roles/common/lib.sh), the role body, and a footer that runs it. Secrets are
inside it, so it is written mode 0600.
"""
import os
import re

import yaml

from . import modules as modmod, util
from .config import deep_merge
from .schema import coerce, is_empty, load_settings
from .util import KiwiError


class Role:
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
        self.stack = bool(self.modules)
        self.node_type = str(meta.get("node_type") or ("master" if "vpn-server" in self.modules else "node"))
        raw_settings = []
        for inc in meta.get("settings_include") or []:
            p = os.path.join(roles_root, str(inc) + ".yaml")
            if not os.path.isfile(p):
                raise KiwiError("%s/role.yaml: settings_include %s not found" % (self.name, inc))
            raw_settings += (yaml.safe_load(util.read_text(p)) or {}).get("settings") or []
        raw_settings += meta.get("settings") or []
        self.settings = load_settings("%s/role.yaml" % self.name, raw_settings)
        self.apply_path = os.path.join(path, "apply.sh")
        if not os.path.isfile(self.apply_path):
            raise KiwiError("%s: apply.sh is missing" % self.name)

    def as_dict(self, modset=None):
        d = {"name": self.name, "title": self.title, "description": self.description,
             "status": self.status, "targets": self.targets, "stack": self.stack,
             "node_type": self.node_type, "modules": self.modules,
             "module_defaults": self.module_defaults,
             "settings": [s.as_dict() for s in self.settings]}
        if self.stack and modset is not None:
            d["module_schemas"] = [modset.get(m).as_dict() for m in modset.resolve(self.modules)]
        return d


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
            out[name] = Role(d, path)
    if not out:
        raise KiwiError("no roles found in %s" % path)
    return out


def common_lib(path=None):
    p = os.path.join(path or roles_dir(), "common", "lib.sh")
    if not os.path.isfile(p):
        raise KiwiError("roles/common/lib.sh is missing")
    return util.read_text(p)


# ---- settings ---------------------------------------------------------------

def _resolve_block(settings, block, host, where, files, file_prefix):
    values = {}
    for s in settings:
        if s.targets and host.target not in s.targets:
            values[s.key] = coerce(s, s.default, where)
            continue
        given = block.get(s.key)
        v = coerce(s, s.default if given is None else given, where)
        if s.required and is_empty(v):
            raise KiwiError("%s.%s is required" % (where, s.key))
        if s.type == "file" and not is_empty(v):
            p = host.fleet.resolve_path(v)
            if not os.path.isfile(p):
                raise KiwiError("%s.%s: file not found: %s" % (where, s.key, v))
            files[file_prefix + s.key] = util.read_bytes(p)
            v = os.path.basename(p)
        values[s.key] = v
    return values


_WG_ADDRESS = re.compile(r"^\s*Address\s*=\s*([0-9.]+)(?:/\d+)?", re.M)


def _mesh_address_from_wireguard(host, module_files, where):
    """A node's vpn_ip is the Address of the WireGuard config wg-easy issued
    for it: fill it in when it is empty, warn when the two disagree (the DNAT
    rules and the fleet's DNS records would point at the wrong address)."""
    data = module_files.get("vpn-client/wireguard_config")
    if not data:
        return
    m = _WG_ADDRESS.search(data.decode("utf-8", "replace"))
    if not m:
        return
    addr, vpn_ip = m.group(1), host.role_settings.get("vpn_ip") or ""
    if not vpn_ip:
        host.role_settings["vpn_ip"] = addr
    elif vpn_ip != addr:
        host.warnings.append("%s: vpn_ip is %s but the WireGuard config's Address is %s — "
                             "mesh traffic for this host arrives at %s" % (where, vpn_ip, addr, addr))


def resolve_settings(role, host, modset=None):
    """Fill defaults, coerce and check the host's block for this role, and
    for a module role the block of every enabled module. File settings are
    read now, relative to the fleet file, and kept on the host so the
    renderer can embed them."""
    raw = host.role_settings_raw or {}
    if not isinstance(raw, dict):
        raise KiwiError("%s: the %s block must be a mapping" % (host.name, role.name))
    where = "%s: %s" % (host.name, role.name)
    role_keys = {s.key for s in role.settings}
    files, module_files = {}, {}
    host.role_settings = _resolve_block(role.settings, raw, host, where, files, "")
    host.modules, host.module_settings = [], {}
    if role.stack:
        ms = modset or modmod.discover()
        wanted = raw.get("modules")
        if wanted is None:
            wanted = role.modules
        elif isinstance(wanted, str):
            wanted = wanted.split()
        elif not isinstance(wanted, list):
            raise KiwiError("%s.modules must be a list of module names" % where)
        ordered = ms.resolve([str(m) for m in wanted], role.node_type)
        host.modules = ordered
        for name in ordered:
            mod = ms.get(name)
            block = raw.get(name) or {}
            if not isinstance(block, dict):
                raise KiwiError("%s.%s must be a mapping" % (where, name))
            block = deep_merge(role.module_defaults.get(name) or {}, block)
            keys = {s.key for s in mod.settings} | {"container_ip"}
            unknown = sorted(set(block) - keys)
            if unknown:
                raise KiwiError("%s.%s: unknown setting(s): %s (known: %s)" % (
                    where, name, ", ".join(unknown), ", ".join(sorted(keys))))
            vals = _resolve_block(mod.settings, block, host, "%s.%s" % (where, name),
                                  module_files, name + "/")
            if block.get("container_ip"):
                vals["container_ip"] = str(block["container_ip"])
            host.module_settings[name] = vals
        _mesh_address_from_wireguard(host, module_files, where)
        allowed = role_keys | {"modules"} | set(ordered)
        unknown = sorted(set(raw) - allowed)
        if unknown:
            hint = [u for u in unknown if u in ms.modules]
            msg = "%s: unknown key(s): %s" % (where, ", ".join(unknown))
            if hint:
                msg += " — module %s is not enabled here; add it to modules:" % ", ".join(hint)
            raise KiwiError(msg)
    else:
        unknown = sorted(set(raw) - role_keys)
        if unknown:
            raise KiwiError("%s: unknown setting(s): %s" % (where, ", ".join(unknown)))
    host.role_files = files
    host.module_files = module_files
    return host.role_settings


def stack_spec(host, role, dns_records=None, master_ip=""):
    rs = host.role_settings
    vpn_ip = rs.get("vpn_ip") or ""
    if not vpn_ip and "vpn-server" in host.modules:
        vpn_ip = host.module_settings["vpn-server"].get("server_ip") or ""
    return modmod.StackSpec(
        node_type=role.node_type, hostname=host.hostname, modules=host.modules,
        docker_dir=rs.get("docker_dir") or "/home/user/docker",
        docker_subnet=rs.get("docker_subnet") or None, mtu=rs.get("mtu") or 1412,
        timezone=host.cfg.get("timezone") or "UTC", vpn_ip=vpn_ip,
        pub_iface=rs.get("pub_iface") or "", variables=rs.get("variables") or {},
        module_config=host.module_settings, files=host.module_files,
        dns_records=dns_records or [], service_user=service_user(host),
        master_ip=rs.get("master_ip") or master_ip or "", mesh_subnet=rs.get("mesh_subnet") or "10.8.0.0/16",
        domain=host.domain)


def service_user(host):
    """Who owns the stack's data directories: the stack setting, or the admin
    user — uid 1000 on a fresh CoreOS or Debian install, which is what the
    containers that drop privileges (PUID, sftp_uid) default to."""
    return (host.role_settings or {}).get("service_user") or host.admin["user"]


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


def render_script(host, role, version, lib_text=None, bundle=None, ca_cert=None):
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
    ]
    if host.modules:
        lines.append("#   modules: %s" % ", ".join(host.modules))
    lines += [
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
        "KS_VERSION=%s" % _q(version),
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
    if ca_cert:
        lines.append("KS_FILES[ca_cert]=%s" % _q(util.b64(ca_cert)))
    for s in role.settings:
        lines.append(_bash_var("KS_ROLE_" + s.key.upper(), host.role_settings.get(s.key)))
    lines.append(_bash_var("KS_STACK", bool(bundle)))
    if bundle is not None:
        rs = host.role_settings
        lines += [
            _bash_var("KS_STACK_DIR", rs.get("docker_dir") or "/home/user/docker"),
            _bash_var("KS_STACK_USER", service_user(host)),
            _bash_var("KS_STACK_PREFIX", bundle.prefix),
            _bash_var("KS_STACK_MODULES", list(bundle.modules)),
            _bash_var("KS_STACK_DIRS", list(bundle.dirs)),
            _bash_var("KS_STACK_PORTS", list(bundle.ports)),
            _bash_var("KS_STACK_UNITS", sorted(bundle.units)),
            _bash_var("KS_STACK_SYSCTL", ["%s=%s" % kv for kv in sorted(bundle.sysctl.items())]),
            _bash_var("KS_STACK_KMODS", list(bundle.kernel_modules)),
            _bash_var("KS_STACK_FILES", ["%s:%04o" % (rel, mode) for rel, (_c, mode) in sorted(bundle.files.items())]),
            _bash_var("KS_STACK_SERVICES", list(bundle.services)),
            _bash_var("KS_STACK_URLS", list(bundle.urls)),
            _bash_var("KS_STACK_VPN_CONTAINER", "%s-vpn-client" % bundle.prefix if "vpn-client" in bundle.modules else ""),
            _bash_var("KS_STACK_VPN_DEPENDENTS", list(getattr(bundle, "vpn_dependents", []))),
            _bash_var("KS_STACK_MESH_SUBNET", getattr(bundle, "mesh_subnet", "") or ""),
            _bash_var("KS_STACK_MESH_VIA", getattr(bundle, "mesh_via", "") or ""),
            _bash_var("KS_STACK_HOSTS", ["%s %s" % (ip, n) for ip, n in getattr(bundle, "hosts", [])]),
            _bash_var("KS_STACK_NO_RESOLVED_STUB", bool(getattr(bundle, "no_resolved_stub", False))),
        ]
        for rel, (content, _mode) in sorted(bundle.files.items()):
            lines.append("KS_FILES[stack/%s]=%s" % (rel, _q(util.b64(content))))
        for name, text in sorted(bundle.units.items()):
            lines.append("KS_FILES[unit/%s]=%s" % (name, _q(util.b64(text))))
    lines += ["", "# ---- common library (roles/common/lib.sh) ------------------------------",
              lib_text.rstrip("\n"), "",
              "# ---- role: %s (roles/%s/apply.sh) --------------------------------" % (role.name, role.name),
              body.rstrip("\n"), "",
              "# ---- post script (fleet.yaml post_script) -----------------------------",
              "ks_post_script() {"]
    post = (host.cfg.get("post_script") or "").rstrip("\n")
    # pasted as written: indenting it would break a heredoc whose terminator must start the line
    lines += (post.splitlines() if post.strip() else ["    :"])
    lines += ["}", "", "ks_main \"$@\"", ""]
    return "\n".join(lines)
