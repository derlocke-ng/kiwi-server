#!/usr/bin/env bash
# The other permission drills (docs/design/authority.md §2), beside the power cut (permissions.sh):
#
#   VMHOST=sh3 tests/drills/permissions-more.sh FLEET PART ON STANDBY WITNESS network
#       ON's network is cut while it runs (its link down at the hypervisor): ON stops the part by
#       itself within three minutes, STANDBY takes it, and the part never runs on both. The link
#       comes back: ON gets no permission, the part stays on STANDBY.
#   tests/drills/permissions-more.sh FLEET PART ON STANDBY WITNESS witness-away
#       WITNESS stops voting for ten minutes: nothing changes, ON keeps the part (STANDBY's
#       permission is enough), STANDBY takes nothing.
#   CONTROL=papaya VMHOST=sh3 tests/drills/permissions.sh ... with CONTROL_AWAY=1: see there.
#
# One PASS or FAIL line per check.
set -u
fleet=${1:?the fleet file}; part=${2:?a part}; on=${3:?the machine it runs on}; sb=${4:?its standby}; wit=${5:?its witness}
mode=${6:?network or witness-away}
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
runs() {   # MACHINE: 0 runs, 1 does not, 2 does not answer
    local out
    out=$(at "$1" "podman ps --format '{{.Names}}' 2>/dev/null; docker ps --format '{{.Names}}' 2>/dev/null; echo kiwi-asked") || return 2
    grep -q kiwi-asked <<<"$out" || return 2
    grep -Eq -- "-$part(-|\$)" <<<"$out"
}
left() { at "$1" "/usr/local/sbin/kiwi-lease show" 2>/dev/null | awk -v p="$part" '$1 == p {print $2}'; }

runs "$on"; check $? "$part runs on $on"
check "$(is [ "$(left "$on")" -gt 0 ])" "$on holds a permission for $part"

if [ "$mode" = network ]; then
    VMHOST=${VMHOST:?the ssh name of the hypervisor}
    V="virsh -c qemu:///system"
    nic=$(ssh -o BatchMode=yes "$VMHOST" "$V domiflist $on" | awk 'NR > 2 && $1 != "" {print $1; exit}')
    check "$(is [ -n "$nic" ])" "$on's network card at $VMHOST: ${nic:-none}"
    ssh -o BatchMode=yes "$VMHOST" "$V domif-setlink $on $nic down" >/dev/null; check $? "$on's link cut"
    cut=$(date +%s); took=""; twice=0
    while (( $(date +%s) - cut < 9 * 60 )); do
        sleep 15
        runs "$sb"; s=$?
        if [ $s = 0 ]; then
            [ -z "$took" ] && took=$(( $(date +%s) - cut ))
        fi
        if (( $(date +%s) - cut > 360 )) && [ -n "$took" ]; then break; fi
    done
    check "$(is [ -n "$took" ])" "$sb runs $part, ${took:-never} s after the link was cut"
    # on its own console the machine stopped the part: asked through the hypervisor's view of it is not
    # possible without its network, so the next look is after the link is back, before any vote reaches it
    ssh -o BatchMode=yes "$VMHOST" "$V domif-setlink $on $nic up" >/dev/null; check $? "$on's link back"
    back=""
    for _ in $(seq 1 20); do
        sleep 5
        runs "$on"; r=$?
        if [ $r != 2 ]; then back=1; break; fi
    done
    check "$(is [ -n "$back" ])" "$on answers again"
    check "$(is [ "$r" = 1 ])" "$part was stopped on $on when it came back (it stopped it itself, cut off)"
    at "$on" "journalctl -t kiwi-lease --since '-15 min' --no-pager -o cat | tail -3" | sed 's/^/        /'
    for _ in $(seq 1 12); do
        if runs "$on"; then twice=$((twice + 1)); fi
        sleep 15
    done
    check "$(is [ "$twice" = 0 ])" "$part did not start on $on again (seen running $twice of 12 times in 3 minutes)"
    runs "$sb"; check $? "$part still runs on $sb"
    say "        to write the take down and turn the pair round: the control node does, or kiwi-server switch $fleet $part $on --taken --yes"
fi

if [ "$mode" = witness-away ]; then
    at "$wit" "systemctl stop kiwi-vote.timer"; check $? "$wit stops voting"
    away=$(date +%s); moved=0; stopped=0
    while (( $(date +%s) - away < 10 * 60 )); do
        sleep 30
        runs "$on" || stopped=$((stopped + 1))
        runs "$sb" && moved=$((moved + 1))
    done
    at "$wit" "systemctl start kiwi-vote.timer"; check $? "$wit votes again"
    check "$(is [ "$stopped" = 0 ])" "$part ran on $on throughout the ten minutes (not running $stopped of 20 looks)"
    check "$(is [ "$moved" = 0 ])" "$sb took nothing (running there $moved of 20 looks)"
    by=$(at "$on" "cat /etc/kiwi-server/lease/$part.by-$sb 2>/dev/null | cut -d' ' -f2")
    check "$(is [ -n "$by" ] && [ $(( $(date +%s) - by )) -lt 120 ])" "$sb gave $on its permission all along"
fi
say "$([ $fails = 0 ] && echo passed || echo "$fails failed")"
exit $(( fails > 0 ))
