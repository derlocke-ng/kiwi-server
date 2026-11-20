"""The agent on every machine (docs/design/scale.md; not agent.py, which is the app's own
kiwi-server that stays running on a person's computer): once a minute it tells the control node how
the machine is. It runs the same script the control node used to log in for (facts.SCRIPT) and
sends its output over the network's own HTTPS, signed with a key of the machine's own that
derives from the seed. The control node then knows a machine is up the moment it starts, and
looks at it without a login.

This is its first step: it reports. Taking orders and watching its neighbours come on top of it.
"""
import hashlib
import hmac
import os
import time

from . import facts, names as namesmod, remote, util
from .util import KiwiError

DIR = "/etc/kiwi-server/agent"
SCRIPT_PATH = "/usr/local/sbin/kiwi-agent"
UNIT = "kiwi-agent"
FRESH = 150         # seconds a report counts as how the machine is now (it comes every minute)
SKEW = 300          # seconds a report's own time may be off the control node's
LIMIT = 512 * 1024  # bytes of a report

SCRIPT = r'''#!/bin/bash
# kiwi-server: this machine's agent (docs/design/scale.md). Every minute it tells the control node
# how this machine is: what the control node used to log in for. Signed with a key of this
# machine's own, sent over the network's own HTTPS. It changes one thing here, when told: the
# containers of a part that went to its standby while this machine was gone stop (X-Kiwi-Hold),
# until the apply that takes the part away from here.
d=/etc/kiwi-server/agent
exec 9>/run/kiwi-agent.lock; flock -n 9 || exit 0
. "$d/conf" || exit 0
body=$(mktemp) || exit 0; head=$(mktemp) || exit 0; trap 'rm -f "$body" "$head"' EXIT
timeout 45 bash "$d/facts.sh" > "$body" 2>/dev/null
now=$(date +%s); boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)
sum=$(sha256sum "$body" | cut -d' ' -f1)
sign=$(printf '%s\n%s\n%s\n%s' "$NAME" "$now" "$boot" "$sum" | openssl dgst -sha256 -hmac "$(cat "$d/key")" -r 2>/dev/null | cut -d' ' -f1)
if [ -z "$sign" ]; then out="no signature (is openssl there?)"; else
out=$(curl -sS --max-time 20 --cacert "$d/ca.pem" -o /dev/null -D "$head" -w '%{http_code}' -X POST \
    -H "Content-Type: text/plain" -H "X-Kiwi-Host: $NAME" -H "X-Kiwi-Time: $now" -H "X-Kiwi-Boot: $boot" \
    -H "X-Kiwi-Sign: $sign" --data-binary @"$body" "$URL/api/report" 2>&1 | tail -1)
fi
[ "$out" = "$(cat /run/kiwi-agent.last 2>/dev/null)" ] || { logger -t kiwi-agent "report to $URL: $out"; echo "$out" > /run/kiwi-agent.last; }
if [ "$out" = 200 ]; then
    for c in $(sed -n 's/^[Xx]-[Kk]iwi-[Hh]old: *//p' "$head" | tr -d '\r'); do
        case $c in *[!a-z0-9-]*|"") continue ;; esac
        if systemctl is-active -q "$c.service" 2>/dev/null; then
            systemctl stop "$c.service" && logger -t kiwi-agent "stopped $c: its part runs on its standby now"
        elif command -v docker >/dev/null 2>&1 && [ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" = true ]; then
            docker stop "$c" >/dev/null && logger -t kiwi-agent "stopped $c: its part runs on its standby now"
        fi
    done
fi
'''
SERVICE = ("[Unit]\nDescription=kiwi-server: tell the control node how this machine is\nAfter=network-online.target\n\n"
           "[Service]\nType=oneshot\nExecStart=%s\n" % SCRIPT_PATH)
TIMER = ("[Unit]\nDescription=kiwi-server: report to the control node every minute\n\n[Timer]\nOnBootSec=10s\n"
         "OnUnitActiveSec=1min\nAccuracySec=5s\n\n[Install]\nWantedBy=timers.target\n")
INSTALL = r'''set -eu
install -d -m 0700 %(dir)s
key=$(cat)
umask 077
printf '%%s' "$key" > %(dir)s/key.new && mv %(dir)s/key.new %(dir)s/key
cat > %(dir)s/conf <<'KIWI_EOF'
URL=%(url)s
NAME=%(name)s
KIWI_EOF
cat > %(dir)s/ca.pem <<'KIWI_EOF'
%(ca)sKIWI_EOF
cat > %(dir)s/facts.sh <<'KIWI_FACTS_EOF'
%(facts)s
KIWI_FACTS_EOF
umask 022
cat > %(path)s.new <<'KIWI_EOF'
%(script)sKIWI_EOF
chmod 0755 %(path)s.new && mv %(path)s.new %(path)s
cat > /etc/systemd/system/%(unit)s.service <<'KIWI_EOF'
%(service)sKIWI_EOF
cat > /etc/systemd/system/%(unit)s.timer <<'KIWI_EOF'
%(timer)sKIWI_EOF
systemctl daemon-reload
systemctl enable -q %(unit)s.timer
systemctl restart %(unit)s.timer
systemctl start --no-block %(unit)s.service
'''
REMOVE = ("systemctl disable --now -q %s.timer 2>/dev/null || true; rm -rf %s %s /etc/systemd/system/%s.service "
          "/etc/systemd/system/%s.timer; systemctl daemon-reload" % (UNIT, DIR, SCRIPT_PATH, UNIT, UNIT))


def control_host(fleet):
    """The machine the control page runs on (the first that lists the part), or None."""
    for h in fleet.hosts.values():
        if "control" in ((h.role_settings_raw or {}).get("modules") or []) or "control" in (h.modules or []):
            return h
    return None


def url(fleet, ms):
    """Where the agents report: the control page's address on the network, or None without one."""
    c = control_host(fleet)
    if c is None:
        return None
    name = namesmod.name_of(fleet, c, "control", ms) if ms is not None else None
    return "https://%s" % (name or "control." + c.hostname)


def key(fleet, host):
    """The machine's own key for its reports: from the seed, by its name."""
    return fleet.derive_secret(host.hostname, "agent", "key")


def signature(key_text, name, when, boot, body):
    """What the agent signs: its name, its time, its boot and the fingerprint of what it sends."""
    text = "%s\n%s\n%s\n%s" % (name, when, boot, hashlib.sha256(body).hexdigest())
    return hmac.new(key_text.encode(), text.encode(), hashlib.sha256).hexdigest()


def check(key_text, name, when, boot, sign, body, now=None):
    """None when the report is this machine's and of now; else why not."""
    try:
        t = int(when)
    except (TypeError, ValueError):
        return "no time"
    if abs((now if now is not None else time.time()) - t) > SKEW:
        return "its clock and this one's are more than %d minutes apart" % (SKEW // 60)
    if len(body) > LIMIT:
        return "too big"
    if not hmac.compare_digest(signature(key_text, name, when, boot, body), str(sign or "").strip().lower()):
        return "the signature is not this machine's"
    return None


def fresh_report(folder, name, now=None):
    """The text of a machine's report if one came within FRESH seconds, else None."""
    got = fresh_report_at(folder, name, now)
    return got[0] if got else None


def fresh_report_at(folder, name, now=None):
    """(text, when it came) of a machine's report if one came within FRESH seconds, else None. When
    it came is what its time is measured against: its clock is not off by the minute it waited."""
    path = os.path.join(folder or "", name + ".txt")
    try:
        came = os.path.getmtime(path)
        if (now if now is not None else time.time()) - came > FRESH:
            return None
        return util.read_text(path), came
    except OSError:
        return None


def after_apply(fleet, host, tgt, ms, runner=None):
    """The machine's agent: installed with its key and where to report, when the network has a
    control node."""
    run = runner or remote.run
    try:
        where = url(fleet, ms)
        if not where:                   # a network without a control node: nobody to report to, nothing is asked
            return                      # of the machine (an agent left from before logs once that nobody answers)
        ca = util.read_text(fleet.resolve_path("secrets/ca/kiwiCA.pem"))
        script = INSTALL % {"dir": DIR, "url": where, "name": host.name, "ca": ca if ca.endswith("\n") else ca + "\n",
                            "facts": facts.SCRIPT, "path": SCRIPT_PATH, "script": SCRIPT, "unit": UNIT,
                            "service": SERVICE, "timer": TIMER}
        run(tgt, script, key(fleet, host).encode())
        util.say("%s: reports to %s every minute" % (host.name, where))
    except (KiwiError, OSError) as e:
        util.warn("%s: its agent was not set up: %s" % (host.name, e))
