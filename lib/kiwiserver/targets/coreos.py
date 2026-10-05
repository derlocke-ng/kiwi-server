"""Fedora CoreOS and uCore: a Butane config per host, converted to Ignition.

uCore is plain Fedora CoreOS at install time. The Ignition config carries the
two autorebase units from the uCore project (unsigned image first, then the
signed one), and the role unit waits for the second rebase to finish before it
runs — so the role script always runs on the final image.
"""
import yaml

from .. import util
from ..config import DAYS
from . import ROLE_DIR, ROLE_SCRIPT, on_calendar, role_unit

BUTANE_VARIANT = "fcos"
BUTANE_VERSION = "1.6.0"

# simple names people type in coreos.services -> the unit to enable
SERVICE_MAP = {
    "docker": "docker.socket", "podman": "podman.socket", "cockpit": "cockpit.socket",
    "tailscale": "tailscaled.service", "nfs": "nfs-server.service", "samba": "smb.service",
    "libvirtd": "libvirtd.socket", "sshd": "sshd.service",
}

UCORE_SIGNED_MARKER = "/etc/ucore-autorebase/signed"


def _str_presenter(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(str, _str_presenter)


def to_yaml(obj):
    return yaml.dump(obj, Dumper=_Dumper, default_flow_style=False, sort_keys=False,
                     width=1000, allow_unicode=True)


def zincati_config(host):
    """Reboots only inside the configured window. Zincati fetches and stages
    updates continuously; `periodic` only decides when it may finalize."""
    u = host.updates()
    if not u["enabled"]:
        return "[updates]\nenabled = false\n"
    # an empty days list means every day — still only inside the window
    days = ", ".join('"%s"' % d for d in (u["days"] or list(DAYS)))
    return (
        "[updates]\nstrategy = \"periodic\"\n\n"
        "[updates.periodic]\ntime_zone = \"local\"\n\n"
        "[[updates.periodic.window]]\ndays = [ %s ]\nstart_time = \"%s\"\nlength_minutes = %d\n"
        % (days, u["time"], int(u["length_minutes"]))
    )


def nm_keyfile(host):
    net = host.static_network()
    if not net:
        return None
    iface = net["interface"]
    lines = ["[connection]", "id=kiwi-static", "type=ethernet"]
    if iface:
        lines.append("interface-name=%s" % iface)
    lines += ["autoconnect=true", "", "[ipv4]", "method=manual",
              "address1=%s,%s" % (net["address"], net["gateway"] or "")]
    if net["dns"]:
        lines.append("dns=%s;" % ";".join(net["dns"]))
    lines += ["may-fail=false", "", "[ipv6]", "method=auto", ""]
    return "\n".join(lines)


def ucore_units(image):
    """Verbatim from ublue-os/ucore examples/ucore-autorebase.butane."""
    def unit(name, desc, conds, cmd, marker):
        return {"name": name, "enabled": True, "contents": (
            "[Unit]\nDescription=%s\n%s"
            "After=network-online.target\nWants=network-online.target\n\n"
            "[Service]\nType=oneshot\nStandardOutput=journal+console\n"
            "ExecStart=/usr/bin/rpm-ostree rebase --bypass-driver %s\n"
            "ExecStart=/usr/bin/touch %s\n"
            "ExecStart=/usr/bin/systemctl disable %s\n"
            "ExecStart=/usr/bin/systemctl reboot\n\n"
            "[Install]\nWantedBy=multi-user.target\n"
            % (desc, "".join("ConditionPathExists=%s\n" % c for c in conds), cmd, marker, name))}
    return [
        unit("ucore-unsigned-autorebase.service", "uCore autorebase to unsigned OCI and reboot",
             ["!/etc/ucore-autorebase/unverified", "!/etc/ucore-autorebase/signed"],
             "ostree-unverified-registry:%s" % image, "/etc/ucore-autorebase/unverified"),
        unit("ucore-signed-autorebase.service", "uCore autorebase to signed OCI and reboot",
             ["/etc/ucore-autorebase/unverified", "!/etc/ucore-autorebase/signed"],
             "ostree-image-signed:docker://%s" % image, "/etc/ucore-autorebase/signed"),
    ]


STAGED_REBOOT_SCRIPT = ROLE_DIR + "/staged-reboot.sh"
STAGED_REBOOT_SH = """#!/usr/bin/env bash
# kiwi-server: reboot into a staged rpm-ostree deployment, if there is one.
# Run by kiwi-staged-reboot.timer inside the fleet's update window.
set -euo pipefail
if rpm-ostree status --json | grep -Eq '"staged" *: *true'; then
    echo "staged update found, rebooting"
    systemctl reboot
else
    echo "no staged update"
fi
"""


def staged_reboot_units(host):
    """uCore disables Zincati and stages updates with rpm-ostreed-automatic
    (AutomaticUpdatePolicy=stage). Nothing reboots into them by itself, so
    this timer does, in the fleet's window, when a deployment is staged."""
    u = host.updates()
    svc = ("[Unit]\nDescription=Kiwi Server: reboot into a staged rpm-ostree update\n\n"
           "[Service]\nType=oneshot\nExecStart=/usr/bin/bash %s\n" % STAGED_REBOOT_SCRIPT)
    timer = ("[Unit]\nDescription=Kiwi Server: update reboot window\n\n"
             "[Timer]\nOnCalendar=%s\nPersistent=false\nRandomizedDelaySec=5min\n\n"
             "[Install]\nWantedBy=timers.target\n" % on_calendar(u["days"], u["time"]))
    return [
        {"name": "kiwi-staged-reboot.service", "contents": svc},
        {"name": "kiwi-staged-reboot.timer", "enabled": bool(u["enabled"]), "contents": timer},
    ]


def _file(path, contents, mode=0o644, overwrite=True):
    return {"path": path, "mode": mode, "overwrite": overwrite, "contents": {"inline": contents}}


def build_butane(host, role_script):
    c = host.cfg
    ucore = host.target == "ucore"
    files, dirs, links, units = [], [], [], []

    # ---- admin user ---------------------------------------------------------
    a = host.admin
    user = {"name": a["user"]}
    keys = host.ssh_keys()
    if keys:
        user["ssh_authorized_keys"] = keys
    ph = host.password_hash()
    if ph:
        user["password_hash"] = ph
    groups = list(a.get("groups") or [])
    if a["user"] != "core":
        groups = ["wheel", "sudo"] + [g for g in groups if g not in ("wheel", "sudo")]
    if groups:
        user["groups"] = groups

    # ---- base system ----------------------------------------------------------
    files.append(_file("/etc/hostname", host.hostname + "\n"))
    if c.get("motd"):
        files.append(_file("/etc/motd", str(c["motd"]).rstrip("\n") + "\n"))
    tz = c.get("timezone") or "UTC"
    if tz != "UTC":
        links.append({"path": "/etc/localtime", "target": "../usr/share/zoneinfo/%s" % tz,
                      "overwrite": True})
    km = nm_keyfile(host)
    if km:
        files.append(_file("/etc/NetworkManager/system-connections/kiwi-static.nmconnection",
                           km, mode=0o600))
    if a.get("ssh_password_auth"):
        files.append(_file("/etc/ssh/sshd_config.d/20-kiwi-passwords.conf",
                           "PasswordAuthentication yes\n"))

    # ---- updates ----------------------------------------------------------------
    u = host.updates()
    if ucore:
        if not u["enabled"]:
            units.append({"name": "rpm-ostreed-automatic.timer", "enabled": False})
        files.append(_file(STAGED_REBOOT_SCRIPT, STAGED_REBOOT_SH, mode=0o755))
        units += staged_reboot_units(host)
    else:
        files.append(_file("/etc/zincati/config.d/55-updates-strategy.toml", zincati_config(host)))

    # ---- uCore autorebase ------------------------------------------------------
    if ucore:
        dirs.append({"path": "/etc/ucore-autorebase", "mode": 0o754})
        units += ucore_units(c["ucore"]["image"])

    # ---- the role -----------------------------------------------------------------
    dirs.append({"path": ROLE_DIR, "mode": 0o700})
    files.append(_file(ROLE_SCRIPT, role_script, mode=0o700))
    units.append({"name": "kiwi-role.service", "enabled": True,
                  "contents": role_unit(host, extra_conditions=[UCORE_SIGNED_MARKER] if ucore else [])})

    # ---- advanced passthrough (plain Butane fields) --------------------------
    co = c["coreos"]
    for svc in co.get("services") or []:
        units.append({"name": SERVICE_MAP.get(str(svc), str(svc)), "enabled": True})
    for unit in co.get("units") or []:
        units.append(dict(unit))
    for f in co.get("files") or []:
        f = dict(f)
        contents = f.pop("contents", "")
        entry = {"path": f.pop("path"), "mode": int(f.pop("mode", 0o644)),
                 "overwrite": bool(f.pop("overwrite", True))}
        entry["contents"] = contents if isinstance(contents, dict) else {"inline": str(contents)}
        entry.update(f)
        files.append(entry)
    for d in co.get("directories") or []:
        dirs.append({"path": d["path"], "mode": int(d.get("mode", 0o755))})

    # ---- assemble ---------------------------------------------------------------------
    bu = {"variant": BUTANE_VARIANT, "version": BUTANE_VERSION,
          "passwd": {"users": [user]}}
    storage = {}
    if dirs:
        storage["directories"] = dirs
    if files:
        storage["files"] = files
    if links:
        storage["links"] = links
    if co.get("luks"):
        storage["luks"] = co["luks"] if isinstance(co["luks"], list) else [co["luks"]]
    bu["storage"] = storage
    bu["systemd"] = {"units": units}
    kargs = [str(k) for k in (co.get("kernel_arguments") or [])]
    if kargs:
        bu["kernel_arguments"] = {"should_exist": kargs}
    if co.get("boot_device"):
        bu["boot_device"] = co["boot_device"]
    return bu


def render(host, role_script):
    return to_yaml(build_butane(host, role_script))


def to_ignition(tc, bu_path, ign_path):
    """butane --strict: an unknown key is an error, never a silent no-op."""
    tc.run(["butane", "--strict", "--pretty", "-o", ign_path, bu_path],
           mounts=[util.parent(bu_path), util.parent(ign_path)])
