"""Settings resolution for one machine, and the spec the renderer takes.

The machine is any object with: name, hostname, target, cfg (timezone),
role_settings_raw, admin, and a `secrets` with derive_secret(*parts),
resolve_path(p), seed_id() — kiwi-server's Host (its fleet is the secrets
source) or the standalone Machine."""
import os
import re

from . import modules as modmod
from .schema import coerce, is_empty
from . import util
from .util import KiwiError, deep_merge


def _resolve_block(settings, block, host, where, files, file_prefix):
    values = {}
    for s in settings:
        if s.targets and host.target not in s.targets:
            values[s.key] = coerce(s, s.default, where)
            continue
        given = block.get(s.key)
        v = coerce(s, s.default if given is None else given, where)
        if is_empty(v) and getattr(s, "generate", False):
            # hostname, module, key — never the fleet's host key or the role name, so a
            # renamed role (node-cloud -> node) or a standalone render keeps the same secrets
            v = host.secrets.derive_secret(host.hostname, file_prefix.rstrip("/") or "role", s.key)
        if is_empty(v) and getattr(s, "fallback_file", ""):
            cand = s.fallback_file.format(hostname=host.hostname, name=host.name)
            if os.path.isfile(host.secrets.resolve_path(cand)):
                v = cand
        if s.required and is_empty(v):
            raise KiwiError("%s.%s is required" % (where, s.key))
        if s.type == "file" and not is_empty(v):
            p = host.secrets.resolve_path(v)
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
            wanted = role.default_modules(host.role_settings)
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
            block = deep_merge(role.defaults_for(name, host.role_settings), block)
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


def stack_spec(host, role, dns_records=None, master_ip="", ca_cert=None):
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
        domain=host.domain, runtime=rs.get("runtime") or "podman", ca_cert=ca_cert,
        auto_update=rs.get("auto_update") or "registry")




def service_user(host):
    """Who owns the stack's data directories: the stack setting, or the admin
    user — uid 1000 on a fresh CoreOS or Debian install, which is what the
    containers that drop privileges (PUID, sftp_uid) default to."""
    return (host.role_settings or {}).get("service_user") or host.admin["user"]
