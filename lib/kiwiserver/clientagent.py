"""The agent on a person's computer (docs/design/clients.md): a desktop, a laptop, a pentest system
on a portable SSD. Once a minute it tells the control node how the computer is: the box it runs on
(a sum of the board's id, salted for this network), its system, its VPN, its disk, its updates.
Signed with a key of its own that derives from the seed by the client's name, sent over the
network's own HTTPS. Nothing listens on the computer. A client is the installed system, not the
box: the same SSD booted on two machines is one client on two boxes.

This is its first step: it reports. Its profile (CA, VPN, DNS mode) and its orders come next.
"""
import hashlib
import hmac
import os
import re
import time

from . import unitsh, util
from .util import KiwiError

DIR = "/etc/kiwi-client"
SCRIPT_PATH = "/usr/local/sbin/kiwi-client-agent"
UNIT = "kiwi-client-agent"
NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
FRESH = 150         # seconds a report counts as how the computer is now
SKEW = 300
LIMIT = 64 * 1024
VERSION = 1

FACTS = r'''
# what the control node keeps of this computer, one fact a line
# a board without an id of its own (all zeros or all F: many cheap ones, a VM without one) has no box
uuid=$(cat /sys/class/dmi/id/product_uuid 2>/dev/null | tr -d ' \n' | grep -viE '^[0-]*$|^[f-]*$')
[ -n "$uuid" ] && echo "box $(printf '%%s\n%%s' "$(cat "$d/salt")" "$uuid" | sha256sum | cut -c1-16)"
if [ -r /etc/os-release ]; then . /etc/os-release; echo "os ${PRETTY_NAME:-$ID}"; echo "os_id ${ID:-} ${VERSION_ID:-}"; fi
echo "kernel $(uname -r)"
echo "uptime $(cut -d. -f1 /proc/uptime)"
echo "hostname $(cat /proc/sys/kernel/hostname)"
for i in $(ip -o link show type wireguard 2>/dev/null | awk -F': ' '{print $2}' | cut -d@ -f1); do
    echo "vpn $i $(ip -o -4 addr show dev "$i" 2>/dev/null | awk '{print $4}' | head -n 1)"
done
r=$(ip -o route get 1.1.1.1 2>/dev/null | grep -o 'dev [^ ]*' | cut -d' ' -f2); [ -n "$r" ] && echo "route $r"
echo "crypt $(lsblk -rno TYPE 2>/dev/null | grep -c '^crypt$')"
if command -v clevis >/dev/null 2>&1; then
    n=0; for dev in $(lsblk -rpno NAME,FSTYPE 2>/dev/null | awk '$2 == "crypto_LUKS" {print $1}'); do
        clevis luks list -d "$dev" 2>/dev/null | grep -q . && n=$((n + 1))
    done; echo "clevis $n"
fi
if command -v rpm-ostree >/dev/null 2>&1; then
    rpm-ostree status --json 2>/dev/null | tr -d ' \n' | grep -q '"staged":true' && echo "updates staged" || echo "updates none-staged"
elif command -v apt-get >/dev/null 2>&1; then
    echo "updates $(apt-get -s -o Debug::NoLocking=1 upgrade 2>/dev/null | grep -c '^Inst ')"
fi
echo "agent %(version)s"
'''

SCRIPT = r'''#!/bin/bash
# kiwi-server: this computer's agent (docs/design/clients.md). Every minute it tells the control
# node how this computer is. Signed with a key of its own, sent over the network's own HTTPS. Away
# from the network it says nothing and tries again a minute later.
d=%(dir)s
. "$d/conf" || exit 0
exec 9>/run/kiwi-client-agent.lock; flock -n 9 || exit 0
body=$(mktemp) || exit 0; resp=$(mktemp) || exit 0; trap 'rm -f "$body" "$resp"' EXIT
d="$d" timeout 45 bash "$d/facts.sh" > "$body" 2>/dev/null
now=$(date +%%s)
sum=$(sha256sum "$body" | cut -d' ' -f1)
sign=$(printf 'client\n%%s\n%%s\n%%s' "$NAME" "$now" "$sum" | openssl dgst -sha256 -hmac "$(cat "$d/key")" -r 2>/dev/null | cut -d' ' -f1)
if [ -z "$sign" ]; then out="no signature (is openssl there?)"; else
out=$(curl -sS --max-time 20 --cacert "$d/ca.pem" -o "$resp" -w '%%{http_code}' -X POST \
    -H "Content-Type: text/plain" -H "X-Kiwi-Client: $NAME" -H "X-Kiwi-Time: $now" -H "X-Kiwi-Sign: $sign" \
    --data-binary @"$body" "$URL/api/client-report" 2>&1 | tail -n 1)
# refused: the control node's reason with it (a clock that is off, a key that is not this client's)
case $out in 200|000) ;; *) out="$out $(head -c 160 "$resp" | tr -d '\n')" ;; esac
fi
[ "$out" = "$(cat /run/kiwi-client-agent.last 2>/dev/null)" ] || { logger -t kiwi-client-agent "report to $URL: $out"; echo "$out" > /run/kiwi-client-agent.last; }
'''
SERVICE = ("[Unit]\nDescription=kiwi-server: tell the network's control node how this computer is\n"
           "After=network-online.target\n\n[Service]\nType=oneshot\nExecStart=%s\n" % SCRIPT_PATH)
TIMER = ("[Unit]\nDescription=kiwi-server: report to the control node every minute\n\n[Timer]\nOnBootSec=30s\n"
         "OnUnitActiveSec=1min\nAccuracySec=5s\n\n[Install]\nWantedBy=timers.target\n")
# the key comes on stdin: it is never part of a command line, nor of a file on the computer that installs it
INSTALL = "set -eu\n" + unitsh.PRELUDE + r'''
for t in curl openssl sha256sum flock ip lsblk; do command -v "$t" >/dev/null || { echo "this computer has no $t" >&2; exit 3; }; done
install -d -m 0700 %(dir)s
key=$(cat)
umask 077
printf '%%s' "$key" > %(dir)s/key.new && mv %(dir)s/key.new %(dir)s/key
cat > %(dir)s/conf <<'KIWI_EOF'
URL=%(url)s
NAME=%(name)s
KIWI_EOF
cat > %(dir)s/salt <<'KIWI_EOF'
%(salt)s
KIWI_EOF
cat > %(dir)s/ca.pem <<'KIWI_EOF'
%(ca)sKIWI_EOF
cat > %(dir)s/facts.sh <<'KIWI_FACTS_EOF'
%(facts)s
KIWI_FACTS_EOF
umask 022
mkdir -p "$(dirname %(path)s)"
cat > %(path)s.new <<'KIWI_EOF'
%(script)sKIWI_EOF
chmod 0755 %(path)s.new && mv %(path)s.new %(path)s
kiwi_unit %(unit)s.service <<'KIWI_EOF'
%(service)sKIWI_EOF
kiwi_unit %(unit)s.timer <<'KIWI_EOF'
%(timer)sKIWI_EOF
kiwi_reload
kiwi_enable %(unit)s.timer
systemctl restart %(unit)s.timer
systemctl start --no-block %(unit)s.service
'''
REMOVE = ("systemctl disable --now -q %s.timer 2>/dev/null || true; rm -rf %s %s /etc/systemd/system/%s.service "
          "/etc/systemd/system/%s.timer; systemctl daemon-reload" % (UNIT, DIR, SCRIPT_PATH, UNIT, UNIT))


def names(fleet):
    """The fleet's clients: {name: its block} from `clients:` at the top of the fleet file."""
    raw = (getattr(fleet, "raw", None) or {}).get("clients") or {}
    if not isinstance(raw, dict):
        raise KiwiError("clients: must be a mapping of a name to its settings")
    out = {}
    for n, block in raw.items():
        if not NAME.match(str(n)):
            raise KiwiError("clients: %r is not a plain name (lowercase letters, digits and dashes)" % n)
        if str(n) in fleet.hosts:
            raise KiwiError("clients: %s is a machine of the fleet already" % n)
        out[str(n)] = block if isinstance(block, dict) else {}
    return out


def key(fleet, name):
    """The client's own key for its reports: from the seed, by its name."""
    return fleet.derive_secret("client", name, "key")


def salt(fleet):
    """What the board's id is summed with: one for the network, so the sum means nothing outside it."""
    return fleet.derive_secret("box", "salt", length=16)


def signature(key_text, name, when, body):
    """'client' first: a client's report can never pass for a machine's, nor the other way round."""
    text = "client\n%s\n%s\n%s" % (name, when, hashlib.sha256(body).hexdigest())
    return hmac.new(key_text.encode(), text.encode(), hashlib.sha256).hexdigest()


def check(key_text, name, when, sign, body, now=None):
    """None when the report is this client's and of now; else why not."""
    try:
        t = int(when)
    except (TypeError, ValueError):
        return "no time"
    if abs((now if now is not None else time.time()) - t) > SKEW:
        return "its clock and this one's are more than %d minutes apart" % (SKEW // 60)
    if len(body) > LIMIT:
        return "too big"
    if not hmac.compare_digest(signature(key_text, name, when, body), str(sign or "").strip().lower()):
        return "the signature is not this client's"
    return None


def install_script(fleet, name, url, ca):
    """The script that puts the agent on the computer, run there as root; the key goes on its stdin."""
    if not url:
        raise KiwiError("the network has no control node: a client reports to one")
    if not ca.strip():
        raise KiwiError("the network's CA is not there (secrets/ca/kiwiCA.pem): render or apply once first")
    facts = FACTS % {"version": VERSION}
    script = SCRIPT % {"dir": DIR}
    return INSTALL % {"dir": DIR, "url": url, "name": name, "salt": salt(fleet), "ca": ca if ca.endswith("\n") else ca + "\n",
                      "facts": facts, "path": SCRIPT_PATH, "script": script, "unit": UNIT, "service": SERVICE, "timer": TIMER}


def parse(text):
    """A report's facts: {box, os, os_id, kernel, uptime, hostname, vpn: [(link, address)], route,
    crypt, clevis, updates, agent}."""
    out = {"vpn": []}
    for line in (text or "").splitlines():
        k, _sp, v = line.partition(" ")
        v = v.strip()
        if k == "vpn":
            link, _sp, addr = v.partition(" ")
            out["vpn"].append({"link": link[:32], "address": addr[:64]})
        elif k in ("uptime", "crypt", "clevis") and v.isdigit():
            out[k] = int(v)
        elif k in ("box", "os", "os_id", "kernel", "hostname", "route", "updates", "agent"):
            out[k] = v[:200]
    return out


def report_path(folder, name):
    return os.path.join(folder, "clients", name + ".txt")


def keep(folder, name, body):
    """The report, kept as the client's state: the last one only."""
    path = report_path(folder, name)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    util.write_bytes(path, body, 0o600)


def rows(folder, clients, boxes=None, now=None):
    """[{name, state, seen, facts, box_of}] for the page and `kiwi-server clients`: its last report,
    whether it is of now, and the machine of the fleet whose box it runs on (`boxes`: {sum: name})."""
    now = now if now is not None else time.time()
    out = []
    for name in sorted(clients):
        path = report_path(folder, name)
        try:
            came = os.path.getmtime(path)
            facts = parse(util.read_text(path))
        except OSError:
            out.append({"name": name, "state": "never", "seen": None, "facts": {}, "box_of": None})
            continue
        out.append({"name": name, "state": "on" if now - came <= FRESH else "away", "seen": int(came), "facts": facts,
                    "box_of": (boxes or {}).get(facts.get("box"))})
    return out
