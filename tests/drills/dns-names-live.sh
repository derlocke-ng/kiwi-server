#!/usr/bin/env bash
# A new name restarts no Pi-hole (kiwi-modules 0.140.0, kiwi-stack 0.9.46). The fleet's names are a
# hosts file in a folder dnsmasq watches; an apply that brings a name rewrites the file, and the
# running Pi-hole reads it again. Until then every new or removed site restarted Pi-hole on every
# DNS machine. The drill adds a name to MACHINE's DNS (an extra record in the fleet file), applies,
# asks for it, takes it away again and applies once more. Pi-hole must answer for the name and then
# no longer, and run on from the same start through both applies. The fleet file is as it was after.
#
#   [KIWI_SERVER=bin/kiwi-server] tests/drills/dns-names-live.sh FLEET MACHINE
#   e.g. KIWI_SERVER=bin/kiwi-server tests/drills/dns-names-live.sh ~/kiwi/fleet.yaml lime
#
# MACHINE: one with the dns module, applied once with this release (its first apply restarts
# Pi-hole once, for its new settings), reached by `ssh MACHINE` (kiwi-server ssh-config --write).
# One PASS or FAIL line per check.
set -u
fleet=${1:?the fleet file}; m=${2:?a machine}
KIWI=${KIWI_SERVER:-kiwi-server}
here=$(cd "$(dirname "$0")/../.." && pwd)
name=kiwi-drill-live.home; addr=10.255.255.7
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
on() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$m" "sudo sh -c $(printf '%q' "$1")"; }
edit() {        # 1: with the name, 0: as it was
    PYTHONPATH="$here/lib" python3 -c '
import sys, yaml
from kiwiserver import fleettext, util
path, m, name, addr = sys.argv[1:5]
text = util.read_text(path)
dns = dict(yaml.safe_load(text)["hosts"][m]["node"].get("dns") or {})
recs = dict(dns.get("extra_records") or {}, **{name: addr})
new = fleettext.put(text, ("hosts", m, "node", "dns", "extra_records"), recs)
if not new:
    sys.exit("the fleet file could not be changed in place")
util.write_text(path, new, 0o600)' "$fleet" "$m" "$name" "$addr"
}
asks() { on "podman exec $p dig +short +time=2 +tries=1 @127.0.0.1 $name" 2>/dev/null | head -n 1; }

before=$(mktemp "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/kiwi-fleet.XXXXXX") || exit 2      # in memory, never on a disk
back() { [ -s "$before" ] && ! cmp -s "$before" "$fleet" && cp "$before" "$fleet"; rm -f "$before" "$before.log"; }
trap back EXIT
p=$(on "podman ps --format '{{.Names}}' | grep -E '^k[mn]-pihole\$' | head -n 1"); [ -n "$p" ] || { echo "$m runs no Pi-hole" >&2; exit 2; }
dd=$(on 'sed -n "s/^STACK_DIR=//p" /etc/kiwi-server/stack.env'); [ -n "$dd" ] || { echo "$m has no stack" >&2; exit 2; }
on "test -s $dd/$p/names/kiwi.hosts && ! podman exec $p printenv FTLCONF_dns_hosts >/dev/null"
check $? "$m's names are a file ($p/names/kiwi.hosts), not the variable: applied with this release" || exit 1
started=$(on "podman inspect -f '{{.State.StartedAt}}' $p")
[ -z "$(asks)" ]; check $? "before: $name is not known"

"$KIWI" control pull "$fleet" >/dev/null 2>&1 || true
cp "$fleet" "$before"
edit; check $? "the name is in the fleet file ($name at $addr, an extra record of $m's DNS)"
"$KIWI" apply "$fleet" "$m" > "$before.log" 2>&1; rc=$?
check $rc "$m applied$([ $rc = 0 ] || echo ": $(tail -n 3 "$before.log" | tr '\n' ' ' | cut -c1-240)")"
grep -q "^$addr $name\$" <(on "cat $dd/$p/names/kiwi.hosts"); check $? "the names file has it"
got=$(asks); [ "$got" = "$addr" ]; check $? "Pi-hole answers for it: ${got:-nothing}"
[ "$(on "podman inspect -f '{{.State.StartedAt}}' $p")" = "$started" ]; check $? "Pi-hole was not restarted for it"

cp "$before" "$fleet"; cmp -s "$before" "$fleet"; check $? "the fleet file is as it was"
"$KIWI" apply "$fleet" "$m" > "$before.log" 2>&1; rc=$?
check $rc "$m applied without the name"
[ -z "$(asks)" ]; check $? "the name is gone from Pi-hole"
[ "$(on "podman inspect -f '{{.State.StartedAt}}' $p")" = "$started" ]; check $? "Pi-hole still runs from the same start ($started)"
[ "$fails" = 0 ] && say "all passed" || say "$fails wrong"
exit $((fails > 0))
