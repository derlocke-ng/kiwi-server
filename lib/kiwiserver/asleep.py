"""A machine that sleeps by day and wakes for the night's backups (`asleep: {from, until}`).

From `from` to `until` (local time on the machine) it suspends to RAM, with its clock set to wake
it at `until`. It does not go to sleep while the network sets it up or a backup runs; then it
tries again a quarter of an hour later, inside the window. Wake-on-LAN (network.wol) wakes it
earlier. While asleep `status` says so instead of counting it as gone, so no message goes out.
"""
import datetime
import re

from . import remote, unitsh, util
from .util import KiwiError

TIME = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
SCRIPT_PATH = "/usr/local/sbin/kiwi-asleep"
UNIT = "kiwi-asleep"

# FROM and UNTIL are put in; the window may cross midnight. Busy: the network's setup, its own
# backup, or one coming in to its backup server.
SCRIPT = r'''#!/bin/sh
# kiwi-server: asleep from %(from)s until %(until)s (asleep in the fleet file). Run every quarter of an hour.
f=$(date -d "%(from)s" +%%s); u=$(date -d "%(until)s" +%%s); now=$(date +%%s)
if [ "$f" -le "$u" ]; then inside=$([ "$now" -ge "$f" ] && [ "$now" -lt "$u" ] && echo y)
else inside=$({ [ "$now" -ge "$f" ] || [ "$now" -lt "$u" ]; } && echo y); fi
[ -n "$inside" ] || exit 0
# running, not only "active": a one-shot that has finished (kiwi-role) stays active
for unit in kiwi-role kiwi-self-apply kiwi-stack-update; do
    case $(systemctl show -p SubState --value "$unit.service" 2>/dev/null) in
        running|start|start-pre|start-post) logger -t kiwi-asleep "not now: $unit runs"; exit 0 ;;
    esac
done
# its own backup (restic, in a container: the host sees the process)
if pgrep -x restic >/dev/null 2>&1; then logger -t kiwi-asleep "not now: a backup runs"; exit 0; fi
# a backup coming in: a connection open in the backup server's container
for rt in podman docker; do
    pid=$($rt inspect -f '{{.State.Pid}}' kn-backup-server 2>/dev/null) || continue
    if [ -n "$pid" ] && [ "$pid" != 0 ] && [ -n "$(nsenter -t "$pid" -n ss -Htn state established 2>/dev/null)" ]; then
        logger -t kiwi-asleep "not now: a backup comes in"; exit 0
    fi
done
[ "$u" -gt "$now" ] || u=$(date -d "tomorrow %(until)s" +%%s)
logger -t kiwi-asleep "asleep until %(until)s"
exec rtcwake -m mem -t "$u"
'''
SERVICE = ("[Unit]\nDescription=kiwi-server: asleep from %(from)s until %(until)s\n\n"
           "[Service]\nType=oneshot\nExecStart=%(path)s\n")
TIMER = ("[Unit]\nDescription=kiwi-server: asleep from %(from)s until %(until)s\n\n"
         "[Timer]\nOnCalendar=*-*-* %(from)s:00\nOnCalendar=*:0/15\nAccuracySec=1min\n\n[Install]\nWantedBy=timers.target\n")
INSTALL = "set -eu\n" + unitsh.PRELUDE + r'''
install -d -m 0755 /usr/local/sbin
cat > %(path)s.new <<'KIWI_EOF'
%(script)sKIWI_EOF
chmod 0755 %(path)s.new && mv %(path)s.new %(path)s
kiwi_unit %(unit)s.service <<'KIWI_EOF'
%(service)sKIWI_EOF
kiwi_unit %(unit)s.timer <<'KIWI_EOF'
%(timer)sKIWI_EOF
kiwi_reload
kiwi_enable --now %(unit)s.timer
'''
# (nothing of it there: nothing to do. `systemctl disable` makes systemd read all its units again even then)
REMOVE = ("[ -e /etc/systemd/system/%(unit)s.timer ] || [ -e %(path)s ] || exit 0; "
          "systemctl disable --now -q %(unit)s.timer 2>/dev/null || true; rm -f %(path)s /etc/systemd/system/%(unit)s.service "
          "/etc/systemd/system/%(unit)s.timer; systemctl daemon-reload" % {"unit": UNIT, "path": SCRIPT_PATH})


def window(host):
    """(from, until) as HH:MM, or None."""
    a = host.cfg.get("asleep") or {}
    if not a:
        return None
    return str(a.get("from") or ""), str(a.get("until") or "")


def problems(cfg):
    a = cfg.get("asleep")
    if not a:
        return []
    if not isinstance(a, dict) or set(a) - {"from", "until"}:
        return ["asleep is {from: HH:MM, until: HH:MM}: when it sleeps and when it wakes"]
    out = []
    for k in ("from", "until"):
        if not TIME.match(str(a.get(k) or "")):
            out.append("asleep.%s must be HH:MM (24h)" % k)
    if not out and a["from"] == a["until"]:
        out.append("asleep: from and until are the same time")
    return out


def now_asleep(host, now=None):
    """Is it inside its sleep window now (this computer's clock, the machine's time zone assumed
    the same)? Then not answering is what it should do."""
    w = window(host)
    if not w or not all(TIME.match(x) for x in w):
        return False
    now = now or datetime.datetime.now()
    t = now.hour * 60 + now.minute
    f, u = [int(x[:2]) * 60 + int(x[3:]) for x in w]
    return f <= t < u if f < u else (t >= f or t < u)


def install_script(host):
    f, u = window(host)
    v = {"from": f, "until": u, "path": SCRIPT_PATH, "unit": UNIT}
    return INSTALL % dict(v, script=SCRIPT % v, service=SERVICE % v, timer=TIMER % v)


def after_apply(host, tgt, runner=None):
    """Its sleep window on the machine as the fleet says it, or none."""
    run = runner or remote.run
    try:
        if window(host):
            run(tgt, install_script(host))
            util.say("%s: asleep from %s until %s (it wakes by its clock; Wake-on-LAN wakes it earlier)"
                     % ((host.name,) + window(host)))
        else:
            run(tgt, REMOVE)
    except KiwiError as e:
        util.warn("%s: its sleep window was not set: %s" % (host.name, e))
