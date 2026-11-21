#!/usr/bin/env bash
# An emergency (health.py, docs/design/authority.md step 3): a machine that runs a part the network
# needs loses its power. The control node must name it an emergency, "I have it" from this computer
# must end the repeats and say who, and the emergency must go once the machine is back.
#
#   VMHOST=sh3 tests/drills/emergency.sh FLEET MACHINE [LIMIT_MINUTES]
#
# MACHINE must be a virtual machine on VMHOST that runs a part with need: must (kiwi-server parts).
set -u
fleet=${1:?the fleet file}; on=${2:?the machine}; limit=${3:-15}
VMHOST=${VMHOST:?the ssh name of the machine that runs MACHINE as a VM}
KIWI=${KIWI_SERVER:-kiwi-server}
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
is() { if "$@"; then echo 0; else echo 1; fi; }
V="virsh -c qemu:///system"
emergency() {   # prints "by" of the machine's emergency ("-" when nobody has it); fails when there is none
    "$KIWI" status "$fleet" --porcelain 2>/dev/null | python3 -c '
import json, sys
key = "machine:" + sys.argv[1]
for e in json.load(sys.stdin).get("emergencies") or []:
    if e.get("key") == key:
        print(e.get("by") or "-")
        sys.exit(0)
sys.exit(1)' "$on"
}

"$KIWI" parts "$fleet" "$on" 2>/dev/null | grep -q "The network needs it"; check $? "$on runs a part the network needs"
ssh -o BatchMode=yes -o ConnectTimeout=8 "$VMHOST" "$V domstate $on" 2>/dev/null | grep -q running; check $? "$on is a running VM on $VMHOST"
if emergency >/dev/null; then check 1 "no emergency for $on before"; else check 0 "no emergency for $on before"; fi
ssh -o BatchMode=yes "$VMHOST" "$V destroy $on" >/dev/null; check $? "$on's power cut"
cut=$(date +%s)
named=""
while (( $(date +%s) - cut < limit * 60 )); do
    sleep 20
    if emergency >/dev/null; then named=$(( $(date +%s) - cut )); break; fi
done
check "$(is [ -n "$named" ])" "the control node names it an emergency (${named:-never} s after the cut)"
"$KIWI" control ack "$fleet" "machine:$on" > ack.log 2>&1; check $? "I have it, from this computer"
by=$(emergency)
check "$(is [ -n "$by" ] && [ "$by" != - ])" "the control node says who has it (${by:-nobody})"
"$KIWI" control activity "$fleet" 2>/dev/null | grep -q "has it: $on"; check $? "Activity says it"
ssh -o BatchMode=yes "$VMHOST" "$V start $on" >/dev/null; check $? "$on's power back"
over=""
for _ in $(seq 1 40); do
    sleep 20
    if ! emergency >/dev/null; then over=$(( $(date +%s) - cut )); break; fi
done
check "$(is [ -n "$over" ])" "the emergency is over once $on is back (${over:-never} s after the cut)"
say "$([ $fails = 0 ] && echo passed || echo "$fails failed")"
exit $(( fails > 0 ))
