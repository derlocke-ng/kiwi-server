"""Switched rings (docs/design/unlock.md): a ring that is stopped answers nobody, so a machine
bound to "its rings AND its own ring" waits at boot while its own ring is stopped.

    kiwi-server ring FLEET RING off|on       stopped or running, kept so (tang.stopped)
    kiwi-server ring FLEET RING window       running for unlock.window minutes, then stopped again

On the ring's machine a marker file per stopped ring, and a condition on the ring's unit, keep it
stopped across a restart and an apply; a window is a transient systemd timer there, so the ring
stops again even when nobody is left to stop it.
"""
import shlex

from . import fleettext, remote, util
from .util import KiwiError

MARKS = "/etc/kiwi/rings-stopped"

# stdin: one line per ring: "RING running|stopped CONTAINER". Each ring's unit gets the condition
# (a podman machine: quadlet units); a docker machine's container is stopped and started by name
STATE_SCRIPT = r'''
set -u
mkdir -p %(marks)s
lines=$(cat)
reload=
for unit in $(printf '%%s\n' "$lines" | awk 'NF == 3 {print $3 ".service"}'); do
    ring=$(printf '%%s\n' "$lines" | awk -v u="$unit" 'NF == 3 && $3 ".service" == u {print $1}')
    systemctl cat "$unit" >/dev/null 2>&1 || continue
    d="/etc/systemd/system/$unit.d"
    want="[Unit]
ConditionPathExists=!%(marks)s/$ring"
    if [ "$(cat "$d/kiwi-switch.conf" 2>/dev/null)" != "$want" ]; then
        mkdir -p "$d" && printf '%%s\n' "$want" > "$d/kiwi-switch.conf" && reload=1
    fi
done
[ -n "$reload" ] && systemctl daemon-reload
printf '%%s\n' "$lines" | while read -r ring state name; do
    [ -n "$ring" ] || continue
    if [ "$state" = stopped ]; then
        touch "%(marks)s/$ring"
        systemctl stop "kiwi-ring-window-$ring.timer" 2>/dev/null
        if systemctl cat "$name.service" >/dev/null 2>&1; then
            systemctl stop "$name.service"; systemctl reset-failed "$name.service" 2>/dev/null    # stopped on purpose, not failed
        else (podman stop "$name" || docker stop "$name") >/dev/null 2>&1; fi
    else
        rm -f "%(marks)s/$ring"
        if systemctl cat "$name.service" >/dev/null 2>&1; then systemctl start "$name.service"
        else (podman start "$name" || docker start "$name") >/dev/null 2>&1; fi
    fi
    echo "ring $ring $state"
done
''' % {"marks": MARKS}

# argv: RING CONTAINER MINUTES. Running now; stopped again by a transient timer after MINUTES
WINDOW_SCRIPT = r'''
set -eu
ring=$1; name=$2; min=$3
systemctl stop "kiwi-ring-window-$ring.timer" 2>/dev/null || true
rm -f %(marks)s/"$ring"
if systemctl cat "$name.service" >/dev/null 2>&1; then systemctl start "$name.service"
else podman start "$name" >/dev/null 2>&1 || docker start "$name" >/dev/null; fi
systemd-run --quiet --collect --on-active="${min}m" --unit="kiwi-ring-window-$ring" /bin/sh -c \
    "touch %(marks)s/$ring; { systemctl stop $name.service && systemctl reset-failed $name.service; } 2>/dev/null || podman stop $name 2>/dev/null || docker stop $name"
echo "ring $ring running for $min minutes"
''' % {"marks": MARKS}


def container(server, ring):
    """The ring's container on its machine (the tang module's name for it)."""
    from kiwimodules.modules import PREFIX
    return "%s-tang-%s" % (PREFIX["master" if server.role == "master" else "node"], ring)


def stopped(server):
    return [str(r) for r in ((server.module_settings.get("tang") or {}).get("stopped") or [])]


def after_apply(server, tgt):
    """Each ring of this machine as tang.stopped says: stopped, or running; the condition on its unit."""
    rings = list(((server.module_settings.get("tang") or {}).get("rings") or {}).keys())
    if not rings:
        return
    off = set(stopped(server))
    lines = "".join("%s %s %s\n" % (r, "stopped" if r in off else "running", container(server, r)) for r in rings)
    remote.run(tgt, STATE_SCRIPT, lines.encode())
    if off:
        util.say("%s: stopped ring%s: %s (switched off)" % (server.name, "s" if len(off) > 1 else "", ", ".join(sorted(off))))


def users(fleet, ring):
    """The machines whose own ring this is (unlock.switch)."""
    return sorted(h.name for h in fleet.hosts.values() if str((h.cfg.get("unlock") or {}).get("switch") or "") == ring)


def write_stopped(fleet_path, server, now_stopped):
    """tang.stopped of the ring's machine, in the fleet file."""
    text = util.read_text(fleet_path)
    import yaml
    raw = (yaml.safe_load(text).get("hosts") or {}).get(server.name) or {}
    block = dict((raw.get(server.role) or {}).get("tang") or {})
    block["stopped"] = sorted(now_stopped)
    new = fleettext.put(text, ("hosts", server.name, server.role, "tang"), block)
    if not new:
        raise KiwiError("%s: tang.stopped could not be written into the fleet file" % server.name)
    util.write_text(fleet_path, new, 0o600)


def switch(fleet_path, server, ring, state, tgt):
    """off or on: into the fleet file, and done on the ring's machine now."""
    off = set(stopped(server))
    off = (off | {ring}) if state == "off" else (off - {ring})
    write_stopped(fleet_path, server, off)
    out = remote.run(tgt, STATE_SCRIPT, ("%s %s %s\n" % (ring, "stopped" if state == "off" else "running",
                                                          container(server, ring))).encode()).decode(errors="replace")
    return out.strip()


def window(server, ring, minutes, tgt):
    argv = " ".join(shlex.quote(str(x)) for x in (ring, container(server, ring), int(minutes)))
    return remote.run(tgt, "set -- %s\n%s" % (argv, WINDOW_SCRIPT)).decode(errors="replace").strip()
