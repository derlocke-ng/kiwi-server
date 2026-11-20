#!/usr/bin/env bash
# netcup's rescue system for a rented machine, for real (kiwi-server vps rescue): the machine is
# restarted into it, the rescue system answers on port 22 with a host key of its own, its root
# password is kept in the fleet's secrets and not shown; then back: the machine starts its own
# system, its disk opens through its rings, its stack runs, the password file is gone.
# Nobody logs in to the rescue system here.
#
#   KIWI_NEVER="<live server id> <live address>" tests/drills/vps-rescue.sh FLEET HOST
set -u
fleet=${1:?the fleet file}; host=${2:?a rented machine of the fleet}
[ -n "${KIWI_NEVER:-}" ] || { echo "drill: set KIWI_NEVER to the servers that must never be touched" >&2; exit 2; }
here=$(cd "$(dirname "$0")/../.." && pwd)
ks=("$here/bin/kiwi-server")
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
dir=$(cd "$(dirname "$fleet")" && pwd); S=$(readlink -f "$dir/secrets")
read -r user port < <(python3 -c '
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
a = dict(d["defaults"].get("admin") or {}, **(d["hosts"][sys.argv[2]].get("admin") or {}))
print(a["user"], a.get("ssh_port") or 22)' "$fleet" "$host")
address=$(python3 -c '
import sys, yaml
h = yaml.safe_load(open(sys.argv[1]))["hosts"][sys.argv[2]]
print(next((b.get("vpn-server", {}).get("wg_host") for b in h.values() if isinstance(b, dict) and b.get("vpn-server")), "") or "")' "$fleet" "$host")
[ -n "$address" ] || { echo "drill: $host has no address on the Internet in the fleet file" >&2; exit 2; }
at() {
    ssh -i "$S/admin_ed25519" -p "$port" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes \
        -o UserKnownHostsFile="$S/known_hosts" -o ConnectTimeout=10 "$user@$address" "sudo sh -c $(printf '%q' "$1")"
}
own=$(ssh-keyscan -T 10 -p "$port" -t ed25519 "$address" 2>/dev/null | awk '!/^#/ {print $3}' | head -1)
[ -n "$own" ]; check $? "$host answers with its own host key on $port"
t0=$(date +%s)
"${ks[@]}" vps rescue "$fleet" "$host" --name on --yes > rescue-on.log 2>&1; check $? "into the rescue system ($(( $(date +%s) - t0 )) s)"
! grep -qiE "password[^ ]* *[:=] *[^ ]{6,}" rescue-on.log; check $? "its password is not on the screen"
pw=$(ls "$S"/rescue/*.password 2>/dev/null | head -1)
[ -n "$pw" ] && [ "$(stat -c %a "$pw")" = 600 ]; check $? "its password is kept in the fleet's secrets (0600)"
key22=""; n=600; while [ "$n" -gt 0 ]; do
    key22=$(ssh-keyscan -T 10 -p 22 -t ed25519 "$address" 2>/dev/null | awk '!/^#/ {print $3}' | head -1)
    [ -n "$key22" ] && [ "$key22" != "$own" ] && break; sleep 15; n=$((n - 15)); done
[ -n "$key22" ] && [ "$key22" != "$own" ]; check $? "the rescue system answers on 22 with a host key of its own ($(( $(date +%s) - t0 )) s)"
! at true 2>/dev/null; check $? "$host's own system is away meanwhile"
t0=$(date +%s)
"${ks[@]}" vps rescue "$fleet" "$host" --name off > rescue-off.log 2>&1; check $? "out of the rescue system"
[ ! -e "$pw" ]; check $? "its password went with it"
n=900; while [ "$n" -gt 0 ] && ! at true 2>/dev/null; do sleep 15; n=$((n - 15)); done
at true; check $? "$host answers again with its pinned host key ($(( $(date +%s) - t0 )) s)"
n=600; while [ "$n" -gt 0 ] && ! at 'systemctl is-active -q kiwi-stack.service' 2>/dev/null; do sleep 15; n=$((n - 15)); done
at 'systemctl is-active -q kiwi-stack.service'; check $? "its stack runs"
say "$fails wrong"
exit "$fails"
