"""Target backends: how a host's base system is installed and configured.

coreos / ucore  -> Butane -> Ignition -> coreos-installer iso customize
debian          -> preseed.cfg + /kiwi-server files -> netinst ISO rebuilt with xorriso

Both hand the role script to a `kiwi-role.service` that runs it on first boot.
"""
ROLE_DIR = "/var/lib/kiwi-server"
ROLE_SCRIPT = ROLE_DIR + "/role.sh"
ROLE_MARKER = ROLE_DIR + "/role.done"


def on_calendar(days, time_hhmm):
    """systemd OnCalendar= for 'these weekdays at HH:MM' (local time)."""
    days = [d for d in (days or []) if d]
    prefix = ",".join(days) + " " if days else ""
    return "%s*-*-* %s:00" % (prefix, time_hhmm)


def role_unit(host, extra_conditions=(), after=("network-online.target",)):
    """The first-boot unit. It stays enabled: the marker file is the only
    thing that stops it, so a failed run is retried on the next boot and
    shows up on the console instead of disappearing."""
    lines = [
        "[Unit]",
        "Description=Kiwi Server first-boot role setup (%s on %s)" % (host.role, host.hostname),
        "Documentation=https://github.com/derlocke-ng/kiwi-server",
        "After=%s" % " ".join(after),
        "Wants=network-online.target",
        "ConditionPathExists=!%s" % ROLE_MARKER,
    ]
    for c in extra_conditions:
        lines.append("ConditionPathExists=%s" % c)
    lines += [
        "",
        "[Service]",
        "Type=oneshot",
        "RemainAfterExit=yes",
        "ExecStart=/usr/bin/bash %s" % ROLE_SCRIPT,
        "StandardOutput=journal+console",
        "StandardError=journal+console",
        "TimeoutStartSec=0",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ]
    return "\n".join(lines)
