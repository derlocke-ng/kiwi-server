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
from kiwimodules.resolver import (_mesh_address_from_wireguard, resolve_settings, service_user,  # noqa: F401
                                  stack_spec)

from . import util
from .util import KiwiError


class Role(_profiles.Profile):
    """A profile plus the install side: the targets it runs on and apply.sh."""

    def __init__(self, path, roles_root):
        super().__init__(path, roles_root)
        meta = yaml.safe_load(util.read_text(os.path.join(path, "role.yaml"))) or {}
        self.targets = [str(t) for t in (meta.get("targets") or ["coreos", "ucore", "debian"])]
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
        lines += stack_vars(host, bundle)
        lines.append("KS_FILES[kiwi-stack]=%s" % _q(util.b64(stack_cli())))
        lines.append("KS_FILES[kiwi-stack-lib]=%s" % _q(util.b64(lib_text)))
        for rel, (content, _mode) in sorted(bundle.files.items()):
            lines.append("KS_FILES[stack/%s]=%s" % (rel, _q(util.b64(content))))
        for name, text in sorted(bundle.units.items()):
            lines.append("KS_FILES[unit/%s]=%s" % (name, _q(util.b64(text))))
        for name, text in sorted(getattr(bundle, "quadlets", {}).items()):
            lines.append("KS_FILES[quadlet/%s]=%s" % (name, _q(util.b64(text))))
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
