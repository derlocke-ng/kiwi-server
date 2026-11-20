#!/usr/bin/env bash
# netcup's snapshots of a rented machine, for real (kiwi-server vps snapshot / snapshots / revert):
# a marker file is written, a snapshot taken while the machine runs, the marker changed, the
# machine put back to the snapshot. Afterwards it must answer again by itself (an encrypted disk
# opens through its rings), with the marker as it was, and the snapshot is removed.
#
#   KIWI_NEVER="<live server id> <live address>" tests/drills/vps-snapshot.sh FLEET HOST
#
# KIWI_NEVER must be set: the drill refuses to run without a list of servers never to touch.
# One PASS or FAIL line per check.
set -u
fleet=${1:?the fleet file}; host=${2:?a rented machine of the fleet}
[ -n "${KIWI_NEVER:-}" ] || { echo "drill: set KIWI_NEVER to the servers that must never be touched" >&2; exit 2; }
here=$(cd "$(dirname "$0")/../.." && pwd)
ks=("$here/bin/kiwi-server")
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
dir=$(cd "$(dirname "$fleet")" && pwd); S=$(readlink -f "$dir/secrets")
read -r user domain port < <(python3 -c '
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
a = dict(d["defaults"].get("admin") or {}, **(d["hosts"][sys.argv[2]].get("admin") or {}))
print(a["user"], d["defaults"].get("domain") or "home", a.get("ssh_port") or 22)' "$fleet" "$host")
address=$(python3 -c '
import sys, yaml
h = yaml.safe_load(open(sys.argv[1]))["hosts"][sys.argv[2]]
print(next((b.get("vpn-server", {}).get("wg_host") for b in h.values() if isinstance(b, dict) and b.get("vpn-server")), "") or "")' "$fleet" "$host")
at() {
    ssh -i "$S/admin_ed25519" -p "$port" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes \
        -o UserKnownHostsFile="$S/known_hosts" -o ConnectTimeout=10 "$user@${address:-$host.$domain}" "sudo sh -c $(printf '%q' "$1")"
}
name="kiwi-drill-$(date +%H%M%S)"
at 'echo before > /var/kiwi-snapshot-marker; sync'; check $? "a marker is on $host"
t0=$(date +%s)
"${ks[@]}" vps snapshot "$fleet" "$host" --name "$name" > snapshot.log 2>&1; check $? "a snapshot was taken while it runs ($(( $(date +%s) - t0 )) s)"
"${ks[@]}" vps snapshots "$fleet" "$host" 2>/dev/null | grep -q "^$name "; check $? "it is listed"
[ "$(at 'cat /var/kiwi-snapshot-marker' 2>/dev/null)" = before ] && at 'systemctl is-active -q kiwi-stack.service'
check $? "$host kept running meanwhile"
at 'echo after > /var/kiwi-snapshot-marker; sync'
t0=$(date +%s)
"${ks[@]}" vps revert "$fleet" "$host" --name "$name" --yes > revert.log 2>&1; check $? "back to the snapshot ($(( $(date +%s) - t0 )) s)"
n=900; while [ "$n" -gt 0 ] && ! at true 2>/dev/null; do sleep 15; n=$((n - 15)); done
at true; check $? "$host answers again by itself ($(( $(date +%s) - t0 )) s after the revert began)"
[ "$(at 'cat /var/kiwi-snapshot-marker' 2>/dev/null)" = before ]; check $? "the marker is as it was at the snapshot"
n=600; while [ "$n" -gt 0 ] && ! at 'systemctl is-active -q kiwi-stack.service' 2>/dev/null; do sleep 15; n=$((n - 15)); done
at 'systemctl is-active -q kiwi-stack.service'; check $? "its stack runs"
"${ks[@]}" vps snapshots "$fleet" "$host" --delete "$name" > delete.log 2>&1; check $? "the snapshot is removed"
! "${ks[@]}" vps snapshots "$fleet" "$host" 2>/dev/null | grep -q "^$name "; check $? "and no longer listed"
at 'rm -f /var/kiwi-snapshot-marker'
say "$fails wrong"
exit "$fails"
