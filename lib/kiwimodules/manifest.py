"""What the machine side reads: bash variables for the role script and for
the bundle's stack.sh (the same list, from one place)."""
import os

from . import resolver


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


def stack_vars(host, bundle):
    """The KS_STACK_* variables: what roles/common/lib.sh needs to put a rendered
    stack on a machine. The same list goes into the role script and into the
    bundle's stack.sh (kiwi-stack apply DIR)."""
    rs = host.role_settings
    lines = [
            _bash_var("KS_STACK_DIR", rs.get("docker_dir") or "/home/user/docker"),
            _bash_var("KS_STACK_USER", resolver.service_user(host)),
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
            _bash_var("KS_STACK_RUNTIME", getattr(bundle, "runtime", "podman") or "podman"),
            _bash_var("KS_STACK_QUADLETS", sorted(getattr(bundle, "quadlets", {}))),
            _bash_var("KS_STACK_NO_RESOLVED_STUB", bool(getattr(bundle, "no_resolved_stub", False))),
            _bash_var("KS_STACK_IMAGES", list(getattr(bundle, "images", []))),
            _bash_var("KS_STACK_DNS_IP", getattr(bundle, "dns_ip", "") or ""),
            _bash_var("KS_STACK_AUTO_UPDATE", getattr(bundle, "auto_update", "registry") or "registry"),
            _bash_var("KS_SEED_ID", host.secrets.seed_id() if host.secrets.has_seed() else ""),
        ]
    return lines


def stack_manifest(host, profile, version, bundle):
    """stack.sh: a bash-sourceable manifest of the bundle directory, with the
    few base facts the machine side reads (host name, admin user, time zone,
    the stack's own role settings) and every KS_STACK_* variable."""
    rs = host.role_settings or {}
    lines = ["# kiwi stack bundle for %s — sourced by `kiwi-stack apply DIR`; generated, do not edit" % host.hostname,
             "# shellcheck disable=SC2034",
             "KS_VERSION=%s" % _q(version),
             _bash_var("KS_HOST", host.name),
             _bash_var("KS_HOSTNAME", host.hostname),
             _bash_var("KS_DOMAIN", host.domain),
             _bash_var("KS_TARGET", host.target),
             _bash_var("KS_ROLE", profile.name),
             _bash_var("KS_ADMIN_USER", host.admin["user"]),
             _bash_var("KS_TIMEZONE", host.cfg["timezone"]),
             _bash_var("KS_ROLE_PRESET", rs.get("preset") or ""),
             _bash_var("KS_ROLE_DOCKER_SOURCE", rs.get("docker_source") or ""),
             _bash_var("KS_ROLE_DAILY_VPN_RESTART", rs.get("daily_vpn_restart") or ""),
             _bash_var("KS_ROLE_WEEKLY_UPDATE", rs.get("weekly_update") or ""),
             "KS_STACK=1"]
    return "\n".join(lines + stack_vars(host, bundle)) + "\n"


