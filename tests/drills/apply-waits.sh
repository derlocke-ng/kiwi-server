#!/usr/bin/env bash
# An apply that did not reach a machine waits for it (2.259, docs/design/scale.md): restart a
# leaf machine, apply it through the control node while it is away, and watch the control node
# apply it by itself when it answers again. Nobody is asked anything.
#
#   tests/drills/apply-waits.sh FLEET MACHINE [WAIT_SECONDS]
#
# MACHINE: a leaf (not a master, not the control node); its disk must open by itself at boot.
# One PASS or FAIL line per check. The control node looks every ten minutes: the default wait
# is 25 minutes.
set -u
fleet=${1:?the fleet file}; m=${2:?a leaf machine}; patience=${3:-1500}
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
# the control node's activity since `since`: is there an entry of user $2, what $3, for this machine, with result $4?
seen() {
    kiwi-server control activity "$fleet" --porcelain 2>/dev/null | python3 -c '
import json, sys
since, m, user, what, result = sys.argv[1:6]
try:
    entries = json.load(sys.stdin).get("entries") or []
except ValueError:
    sys.exit(2)
ok = any(e.get("user") == user and e.get("what") == what and m in (e.get("hosts") or []) and e.get("result") == result
         and (e.get("time") or "")[:19] >= since for e in entries)
sys.exit(0 if ok else 1)' "$since" "$m" "$1" "$2" "$3"
}
since=$(date -u +%Y-%m-%dT%H:%M:%S)
me=$(kiwi-server control activity "$fleet" --porcelain 2>/dev/null | python3 -c '
import json, sys
e = [x for x in (json.load(sys.stdin).get("entries") or []) if x.get("what") == "pushed the fleet"]
print(e[0]["user"] if e else "")')
[ -n "$me" ]; check $? "the control node answers (this computer is ${me:-?} there)"
kiwi-server power "$fleet" "$m" restart --yes >/dev/null 2>&1; check $? "$m is told to restart"
sleep 10
kiwi-server control run "$fleet" apply "$m" > apply-waits.log 2>&1
[ $? != 0 ]; check $? "the apply does not reach it while it restarts"
seen "$me" apply failed; check $? "the control node has that apply as failed"
say "waiting for the control node to apply $m by itself (up to $((patience / 60)) minutes)"
n=$patience
while [ "$n" -gt 0 ] && ! seen control apply ok; do sleep 30; n=$((n - 30)); done
seen control apply ok; check $? "the control node applied $m by itself when it answered again ($(( (patience - n) / 60 )) min)"
say "$fails wrong"
exit $((fails > 0))
