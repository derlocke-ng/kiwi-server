#!/usr/bin/env bash
# Permissions that run out (lease.py, docs/design/authority.md step 2), on real machines: the
# machine that runs a part loses its power. Its standby must take the part by itself once the
# machine's permission has run out and the witness agrees, without the control node; the control
# node must then write it down as a switch. When the machine is back, the part must not run there.
#
#   VMHOST=sh3 tests/drills/permissions.sh FLEET PART ON STANDBY WITNESS [LIMIT_MINUTES]
#
# ON must be a virtual machine on VMHOST (virsh destroy, virsh start). The machines must have been
# applied with a release that has permissions, in the order WITNESS, STANDBY, ON. One PASS or FAIL
# line per check; the times in between are what the drill is for.
set -u
fleet=${1:?the fleet file}; part=${2:?a part}; on=${3:?the machine it runs on}; sb=${4:?its standby}; wit=${5:?its witness}
limit=${6:-12}
VMHOST=${VMHOST:?the ssh name of the machine that runs ON as a VM}
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
at() { # MACHINE COMMAND: as root there, at its address at home
    ssh -i "$S/admin_ed25519" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes \
        -o UserKnownHostsFile="$S/known_hosts" -o ConnectTimeout=8 "$user@$(lanof "$1")" "sudo sh -c $(printf '%q' "$2")"
}
runs() {   # MACHINE: one of the part's containers runs there; 2: it does not answer
    local out
    out=$(at "$1" "podman ps --format '{{.Names}}' 2>/dev/null; docker ps --format '{{.Names}}' 2>/dev/null; echo kiwi-asked") || return 2
    grep -q kiwi-asked <<<"$out" || return 2
    grep -Eq -- "-$part(-|\$)" <<<"$out"
}
V="virsh -c qemu:///system"

# before
ssh -o BatchMode=yes -o ConnectTimeout=8 "$VMHOST" "$V domstate $on" 2>/dev/null | grep -q running; check $? "$on is a running VM on $VMHOST"
runs "$on"; check $? "$part runs on $on"
shown=$(at "$on" "/usr/local/sbin/kiwi-lease show")
left=$(awk -v p="$part" '$1 == p {print $2}' <<<"$shown")
check "$(is [ "${left:-0}" -gt 0 ])" "$on holds a permission for $part (${left:-none} s left)"
at "$sb" "grep -q '^$part $on .* standby on ' /etc/kiwi-server/lease/vote.conf"; check $? "$sb is its standby, and takes it by itself"
at "$wit" "grep -q '^$part $on .* witness' /etc/kiwi-server/lease/vote.conf"; check $? "$wit is its witness"
by=$(at "$on" "cat /etc/kiwi-server/lease/$part.by-* 2>/dev/null | cut -d' ' -f1 | sort -u | tr '\n' ' '")
say "        given by: ${by:-nobody yet}"

# the power cut
ssh -o BatchMode=yes "$VMHOST" "$V destroy $on" >/dev/null; check $? "$on's power cut"
cut=$(date +%s)
took=""
while (( $(date +%s) - cut < limit * 60 )); do
    sleep 15
    if runs "$sb"; then took=$(( $(date +%s) - cut )); break; fi
done
check "$(is [ -n "$took" ])" "$sb runs $part, ${took:-never} s after the power cut (the permission lasts 180 s, a voter waits 60 more)"
at "$wit" "ls /etc/kiwi-server/lease/state/$part@$on.refused" >/dev/null 2>&1; check $? "$wit said gone, and gives $on nothing more for $part"
at "$sb" "journalctl -t kiwi-vote --since '-15 min' --no-pager -o cat | tail -5" | sed 's/^/        /'

# written down by the control node
written=""
for _ in $(seq 1 20); do
    sleep 30
    "$KIWI" control pull "$fleet" >/dev/null 2>&1
    if python3 -c '
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
h = d["hosts"]
sys.exit(0 if (h[sys.argv[3]].get("keep_ready") or {}).get(sys.argv[2]) == sys.argv[4] else 1)' "$fleet" "$part" "$sb" "$on"; then
        written=$(( $(date +%s) - cut )); break
    fi
done
check "$(is [ -n "$written" ])" "the control node wrote it down: $part on $sb, kept ready on $on (${written:-never} s after the cut)"

# back
ssh -o BatchMode=yes "$VMHOST" "$V start $on" >/dev/null; check $? "$on's power back"
back=""
for _ in $(seq 1 40); do
    sleep 15
    runs "$on"; rc=$?
    if [ $rc != 2 ]; then back=$(( $(date +%s) - cut )); break; fi
done
check "$(is [ -n "$back" ])" "$on answers again (${back:-never} s after the cut)"
twice=0
for _ in $(seq 1 12); do
    if runs "$on"; then twice=$((twice + 1)); fi
    sleep 15
done
check "$(is [ "$twice" = 0 ])" "$part did not run on $on after it came back (seen running ${twice} of 12 times in 3 minutes)"
runs "$sb"; check $? "$part still runs on $sb"
say "$([ $fails = 0 ] && echo passed || echo "$fails failed")"
exit $(( fails > 0 ))
