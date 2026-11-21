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
# machine's own, sent over the network's own HTTPS. It changes nothing here.
d=/etc/kiwi-server/agent
. "$d/conf" || exit 0
body=$(mktemp) || exit 0; head=$(mktemp) || exit 0; trap 'rm -f "$body" "$head"' EXIT
if [ "${1:-}" = last ]; then
    # its last report, when the machine goes down (kiwi-agent-last.service stops): why, so that a
    # restart and a crash are no longer the same silence (docs/design/authority.md, step 3). Only
    # while the whole machine stops: a unit restarted by an apply says nothing.
    [ "$(systemctl is-system-running 2>/dev/null)" = stopping ] || exit 0
    jobs=$(systemctl list-jobs --no-legend 2>/dev/null)
    case $jobs in
        *reboot.target*|*kexec.target*) reason=reboot ;;
        *poweroff.target*) reason=poweroff ;;
        *halt.target*) reason=halt ;;
        *) reason=stop ;;
    esac
    why=$(head -c 200 /run/kiwi-server/going-down 2>/dev/null | tr '\n' ' ')
    printf 'kiwi-last %s %s\n' "$reason" "$why" > "$body"
    wait=8
else
    exec 9>/run/kiwi-agent.lock; flock -n 9 || exit 0
    timeout 45 bash "$d/facts.sh" > "$body" 2>/dev/null
    wait=20
fi
now=$(date +%s); boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)
sum=$(sha256sum "$body" | cut -d' ' -f1)
sign=$(printf '%s\n%s\n%s\n%s' "$NAME" "$now" "$boot" "$sum" | openssl dgst -sha256 -hmac "$(cat "$d/key")" -r 2>/dev/null | cut -d' ' -f1)
if [ -z "$sign" ]; then out="no signature (is openssl there?)"; else
out=$(curl -sS --max-time "$wait" --cacert "$d/ca.pem" -o /dev/null -D "$head" -w '%{http_code}' -X POST \
    -H "Content-Type: text/plain" -H "X-Kiwi-Host: $NAME" -H "X-Kiwi-Time: $now" -H "X-Kiwi-Boot: $boot" \
    -H "X-Kiwi-Sign: $sign" --data-binary @"$body" "$URL/api/report" 2>&1 | tail -1)
fi
[ "${1:-}" = last ] && { logger -t kiwi-agent "last report to $URL ($(cut -d' ' -f2- "$body")): $out"; exit 0; }
[ "$out" = "$(cat /run/kiwi-agent.last 2>/dev/null)" ] || { logger -t kiwi-agent "report to $URL: $out"; echo "$out" > /run/kiwi-agent.last; }
'''
SERVICE = ("[Unit]\nDescription=kiwi-server: tell the control node how this machine is\nAfter=network-online.target\n\n"
           "[Service]\nType=oneshot\nExecStart=%s\n" % SCRIPT_PATH)
# its last report: a unit that does nothing while the machine runs and reports when it stops. After the
# network and the stack, so it stops before them (the VPN to the control node is in the stack)
LAST_SERVICE = ("[Unit]\nDescription=kiwi-server: tell the control node when this machine goes down\n"
                "After=network-online.target kiwi-stack.service\nWants=network-online.target\n\n"
                "[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/bin/true\nExecStop=%s last\nTimeoutStopSec=15\n\n"
                "[Install]\nWantedBy=multi-user.target\n" % SCRIPT_PATH)
TIMER = ("[Unit]\nDescription=kiwi-server: report to the control node every minute\n\n[Timer]\nOnBootSec=10s\nOnBootSec=45s\n"
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
cat > /etc/systemd/system/%(unit)s-last.service <<'KIWI_EOF'
%(last)sKIWI_EOF
systemctl daemon-reload
systemctl enable -q %(unit)s-last.service
systemctl start %(unit)s-last.service
systemctl enable -q %(unit)s.timer
systemctl restart %(unit)s.timer
systemctl start --no-block %(unit)s.service
'''
REMOVE = ("systemctl disable -q %s-last.service 2>/dev/null || true; "
          "systemctl disable --now -q %s.timer 2>/dev/null || true; rm -rf %s %s /etc/systemd/system/%s.service "
          "/etc/systemd/system/%s.timer /etc/systemd/system/%s-last.service; systemctl daemon-reload"
          % (UNIT, UNIT, DIR, SCRIPT_PATH, UNIT, UNIT, UNIT))


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
                            "service": SERVICE, "timer": TIMER, "last": LAST_SERVICE}
        run(tgt, script, key(fleet, host).encode())
        util.say("%s: reports to %s every minute" % (host.name, where))
    except (KiwiError, OSError) as e:
        util.warn("%s: its agent was not set up: %s" % (host.name, e))
