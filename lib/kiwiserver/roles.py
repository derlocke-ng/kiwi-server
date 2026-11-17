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

from kiwimodules import manifest as _manifest, profiles as _profiles, resolver as _resolver
from kiwimodules.manifest import _bash_var, _q, stack_manifest, stack_vars  # noqa: F401  (re-exported)
from kiwimodules.resolver import (_mesh_address_from_wireguard, service_user,  # noqa: F401
                                  stack_spec)

from . import riseup, util
from .util import KiwiError


def resolve_settings(role, host, modset=None):
    """kiwimodules' resolver, after what kiwi-server gives a block itself: Riseup as a VPN
    client's exit becomes the custom OpenVPN config it fetched (riseup.py)."""
    riseup.settle(host)
    return _resolver.resolve_settings(role, host, modset)


class Role(_profiles.Profile):
    """A profile plus the install side: the targets it runs on and apply.sh."""

    def __init__(self, path, roles_root):
        super().__init__(path, roles_root)
        meta = yaml.safe_load(util.read_text(os.path.join(path, "role.yaml"))) or {}
        self.targets = [str(t) for t in (meta.get("targets") or ["coreos", "ucore", "debian"])]
        # coreos-ram is CoreOS live, for masters (docs/design/ram-master.md): a master or a bare
        # machine that runs on coreos runs there too. Decided here, the profiles are kiwi-modules'.
        if "coreos" in self.targets and (self.node_type == "master" or not self.stack) and "coreos-ram" not in self.targets:
            self.targets.append("coreos-ram")
        self.apply_path = os.path.join(path, "apply.sh")
        if not os.path.isfile(self.apply_path):
            raise KiwiError("%s: apply.sh is missing" % self.name)

    def as_dict(self, modset=None):
        d = super().as_dict(modset)
        d["targets"] = self.targets
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

def stack_cli(path=None):
    p = os.path.join(path or roles_dir(), "common", "kiwi-stack")
    if not os.path.isfile(p):
        raise KiwiError("roles/common/kiwi-stack is missing")
    return util.read_text(p)


def mount_lines(mounts):
    """The role script's part for the data disks (fleet.yaml `mounts`): each in /etc/fstab
    between two marks, so a disk taken out of the fleet file leaves the file again, and
    mounted before the role's own steps. A disk that is not there, or has no filesystem
    where none may be made, ends the script with what to do: services writing into an empty
    folder on the system disk would look like lost data later."""
    out = ["# ---- data disks (fleet.yaml mounts) --------------------------------------",
           "ks_mounts() {",
           "    local dev at fmt real fs",
           "    [[ -f /etc/fstab ]] || install -m 0644 /dev/null /etc/fstab      # CoreOS comes without one",
           "    sed -i '/^# kiwi-server mounts: begin/,/^# kiwi-server mounts: end/d' /etc/fstab",
           "    echo '# kiwi-server mounts: begin (from the fleet file: change them there)' >> /etc/fstab",
           "    while read -r dev at fmt; do",
           "        [[ -n $dev ]] || continue",
           "        at=$(realpath -m \"$at\")          # /mnt is /var/mnt on CoreOS: the path as the system has it",
           "        real=$(findfs \"$dev\" 2>/dev/null || true)",
           "        [[ $dev == /dev/* && -b $dev ]] && real=$dev",
           "        [[ -n $real ]] || ks_die \"the data disk $dev is not there: plug it in, or take it out of mounts in the fleet file\"",
           "        fs=$(blkid -o value -s TYPE \"$real\" 2>/dev/null || true)",
           "        if [[ -z $fs ]]; then",
           "            [[ -n $fmt ]] || ks_die \"the data disk $dev has no filesystem: format: xfs in its mounts entry makes one\"",
           "            [[ -z $(wipefs -n \"$real\" 2>/dev/null) ]] || ks_die \"the data disk $dev is not empty and has no filesystem kiwi-server knows: look at it first\"",
           "            ks_say \"making a filesystem ($fmt) on the empty disk $dev\"",
           "            \"mkfs.$fmt\" -q \"$real\"",
           "        fi",
           "        echo \"$dev $at auto nofail,x-systemd.device-timeout=30s 0 2\" >> /etc/fstab",
           "        install -d \"$at\"",
           "        systemctl daemon-reload",
           "        findmnt -rn \"$at\" >/dev/null || mount \"$at\" || ks_die \"the data disk $dev does not mount at $at\"",
           "        ks_say \"data disk $dev is at $at\"",
           "    done <<'KS_MOUNTS'"]
    out += ["%s %s %s" % m for m in mounts]
    out += ["KS_MOUNTS",
            "    echo '# kiwi-server mounts: end' >> /etc/fstab",
            "}", ""]
    return out


def watchdog_lines():
    """The role script's part for the hardware watchdog. systemd feeds it while it lives; a
    machine that hangs is reset after a minute, one that hangs in a restart or shutdown
    after ten (seen: a machine that hung in its update reboot for 39 minutes, until someone
    reset it). Where there is no such device, or `watchdog: false`, nothing is set."""
    conf = "/etc/systemd/system.conf.d/50-kiwi-watchdog.conf"
    return ["# ---- hardware watchdog (fleet.yaml watchdog) --------------------------------",
            "ks_watchdog() {",
            "    if [[ $KS_WATCHDOG == 1 && -e /dev/watchdog ]]; then",
            "        install -d /etc/systemd/system.conf.d",
            "        if ! grep -qs '^RebootWatchdogSec=10min' %s; then" % conf,
            "            printf '[Manager]\\nRuntimeWatchdogSec=60s\\nRebootWatchdogSec=10min\\n' > %s" % conf,
            "            systemctl daemon-reexec || true",
            "            ks_say \"hardware watchdog: a hang resets this machine after a minute, a hung restart after ten\"",
            "        fi",
            "    elif [[ -e %s ]]; then" % conf,
            "        rm -f %s" % conf,
            "        systemctl daemon-reexec || true",
            "    fi",
            "}", ""]


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
        _bash_var("KS_WATCHDOG", bool(host.cfg.get("watchdog", True))),
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
        lines += stack_vars(host, bundle)
        lines.append("KS_FILES[kiwi-stack]=%s" % _q(util.b64(stack_cli())))
        lines.append("KS_FILES[kiwi-stack-lib]=%s" % _q(util.b64(lib_text)))
        for rel, (content, _mode) in sorted(bundle.files.items()):
            lines.append("KS_FILES[stack/%s]=%s" % (rel, _q(util.b64(content))))
        for name, text in sorted(bundle.units.items()):
            lines.append("KS_FILES[unit/%s]=%s" % (name, _q(util.b64(text))))
        for name, text in sorted(getattr(bundle, "quadlets", {}).items()):
            lines.append("KS_FILES[quadlet/%s]=%s" % (name, _q(util.b64(text))))
    from . import config
    mounts = config.mounts_of(host.cfg)
    # the role's own steps come after the machine's: the watchdog, and the data disks its services need
    body = body.replace("ks_role_apply ()", "ks_role_apply()", 1).replace("ks_role_apply()", "ks_role_apply_role()", 1)
    lines += ["", "# ---- common library (roles/common/lib.sh) ------------------------------",
              lib_text.rstrip("\n"), "",
              "# ---- role: %s (roles/%s/apply.sh) --------------------------------" % (role.name, role.name),
              body.rstrip("\n"), ""]
    lines += watchdog_lines()
    if mounts:
        lines += mount_lines(mounts)
    lines += ["ks_role_apply() {", "    ks_watchdog"] + (["    ks_mounts"] if mounts else []) + [
        "    ks_role_apply_role", "}", ""]
    lines += [
              "# ---- post script (fleet.yaml post_script) -----------------------------",
              "ks_post_script() {"]
    post = (host.cfg.get("post_script") or "").rstrip("\n")
    # pasted as written: indenting it would break a heredoc whose terminator must start the line
    lines += (post.splitlines() if post.strip() else ["    :"])
    lines += ["}", "", "ks_main \"$@\"", ""]
    return "\n".join(lines)
