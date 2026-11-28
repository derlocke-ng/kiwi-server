#!/usr/bin/env bash
# Orders for a machine's agent, end to end (docs/control.md, "Orders for a machine's agent"): from
# this computer through the control node's API, to the machine's agent with the answer to its next
# report, and what came of it back with the one after. Restarts SERVICE on MACHINE once.
#
#   tests/drills/agent-orders.sh FLEET MACHINE SERVICE
#   e.g. tests/drills/agent-orders.sh ~/kiwi/fleet.yaml quince kn-vault
#
# FLEET joined to a control node that knows orders (2.326.0 and later), MACHINE with an agent of
# the same release, reached by `ssh MACHINE` (kiwi-server ssh-config --write). KIWI_SERVER: the
# kiwi-server to use (default: the installed one). One PASS or FAIL line per check.
set -u
fleet=${1:?the fleet file}; m=${2:?a machine}; svc=${3:?one of its services, like kn-vault}
KIWI=${KIWI_SERVER:-kiwi-server}
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
on() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$m" "sudo sh -c $(printf '%q' "$1")"; }
log=$(mktemp "${XDG_RUNTIME_DIR:-/tmp}/kiwi-orders.XXXXXX"); trap 'rm -f "$log"' EXIT

on "grep -q X-Kiwi-Orders /usr/local/sbin/kiwi-agent"; check $? "$m's agent takes orders"
before=$(on "systemctl show -p ActiveEnterTimestampMonotonic --value $svc.service")
began=$(date +%s)
"$KIWI" control order "$fleet" "$m" restart "$svc" > "$log" 2>&1; rc=$?
check $rc "the order to restart $svc came back done$([ $rc = 0 ] || echo ": $(tail -n 2 "$log" | tr '\n' ' ' | cut -c1-200)")"
say "        $(( $(date +%s) - began )) s from the order to its answer"
after=$(on "systemctl show -p ActiveEnterTimestampMonotonic --value $svc.service")
[ -n "$after" ] && [ "$after" != "$before" ]; check $? "$svc was started anew on $m"
on "journalctl -t kiwi-agent --since '-5 min' --no-pager -o cat | grep -q 'from the control node: restart $svc: exit 0'"
check $? "$m's journal says the agent did it"
"$KIWI" control order "$fleet" "$m" restart sshd > "$log" 2>&1; rc=$?
[ "$rc" != 0 ] && grep -q "no such service here: sshd" "$log"; check $? "an order for a service outside the stack is refused on the machine"
"$KIWI" control order "$fleet" "$m" rm "$svc" > "$log" 2>&1; rc=$?
[ "$rc" != 0 ] && grep -q "takes restart, start, stop, update" "$log"; check $? "an order of another kind is refused by the control node"
"$KIWI" control orders "$fleet" "$m" 2>/dev/null | grep -q " ok .*restart *$svc"; check $? "the orders list has it, done"
[ "$fails" = 0 ] && say "all passed" || say "$fails wrong"
exit $((fails > 0))
