#!/usr/bin/env bash
# A machine says when it goes down (machineagent.py, docs/design/authority.md step 3): kiwi-server
# power restarts it; the control node must have its last report in Activity with the reason, and
# after the restart the machine must run its part again.
#
#   tests/drills/last-report.sh FLEET MACHINE [PART]
#
# One PASS or FAIL line per check.
set -u
fleet=${1:?the fleet file}; on=${2:?the machine}; part=${3:-}
KIWI=${KIWI_SERVER:-kiwi-server}
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
is() { if "$@"; then echo 0; else echo 1; fi; }
dir=$(cd "$(dirname "$fleet")" && pwd); S=$(readlink -f "$dir/secrets")
user=$(python3 -c '
import sys, yaml
print(yaml.safe_load(open(sys.argv[1]))["defaults"]["admin"]["user"])' "$fleet")
lanof() { python3 -c '
import sys, yaml
h = yaml.safe_load(open(sys.argv[1]))["hosts"][sys.argv[2]]
print(str((h.get("network") or {}).get("address") or "").split("/")[0])' "$fleet" "$1"; }
at() {
    ssh -i "$S/admin_ed25519" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes \
        -o UserKnownHostsFile="$S/known_hosts" -o ConnectTimeout=8 "$user@$(lanof "$1")" "sudo sh -c $(printf '%q' "$2")"
}

at "$on" "systemctl is-active -q kiwi-agent-last.service"; check $? "$on has the unit for its last report"
"$KIWI" power "$fleet" "$on" restart --yes > power.log 2>&1; check $? "$on told to restart"
cut=$(date +%s)
heard=""
for _ in $(seq 1 20); do
    sleep 6
    if "$KIWI" control activity "$fleet" 2>/dev/null | grep -q "$on goes down: it restarts (restarted by kiwi-server power)"; then
        heard=$(( $(date +%s) - cut )); break
    fi
done
check "$(is [ -n "$heard" ])" "the control node has its last report with the reason (${heard:-never} s)"
back=""
for _ in $(seq 1 40); do
    sleep 10
    if at "$on" "true" 2>/dev/null; then back=$(( $(date +%s) - cut )); break; fi
done
check "$(is [ -n "$back" ])" "$on answers again (${back:-never} s after)"
if [ -n "$part" ]; then
    runs=""
    for _ in $(seq 1 24); do
        sleep 10
        if at "$on" "podman ps --format '{{.Names}}' 2>/dev/null; docker ps --format '{{.Names}}' 2>/dev/null" | grep -Eq -- "-$part(-|\$)"; then
            runs=$(( $(date +%s) - cut )); break
        fi
    done
    check "$(is [ -n "$runs" ])" "$part runs on $on again (${runs:-never} s after)"
fi
say "$([ $fails = 0 ] && echo passed || echo "$fails failed")"
exit $(( fails > 0 ))
