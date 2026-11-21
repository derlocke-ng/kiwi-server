#!/usr/bin/env bash
# A dead machine's part comes from its newest backup onto another machine (kiwi-server move --gone,
# docs/backups.md "From a machine that is dead"), on real machines. FROM is a VM: it is switched
# off hard at the hypervisor, as a dead machine is.
#
#   VMHOST=sh3 [KIWI_SERVER=bin/kiwi-server] tests/drills/part-gone.sh FLEET PART FROM TO DATA_DIR [--back]
#
# A marker goes into the part's data and FROM makes a backup; a second marker after it must be lost.
# FROM off; the move; the first marker on TO, the second not; the part runs on TO and its name answers
# there. FROM on again: it runs its old copy until it is applied, then no more. --back: a plain move
# home again afterwards. One PASS or FAIL line per check.
set -u
fleet=${1:?the fleet file}; part=${2:?a part}; from=${3:?the machine it is on (a VM)}; to=${4:?the machine it goes to}
data=${5:?a folder of its data}; back=${6:-}
VMHOST=${VMHOST:?the ssh name of the hypervisor}
KIWI=${KIWI_SERVER:-kiwi-server}
V="virsh -c qemu:///system"
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
is() { if "$@"; then echo 0; else echo 1; fi; }
dir=$(cd "$(dirname "$fleet")" && pwd); S=$(readlink -f "$dir/secrets")
read -r user domain < <(python3 -c '
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))["defaults"]
print(d["admin"]["user"], d.get("domain") or "home")' "$fleet")
on() { # MACHINE COMMAND: as root there
    ssh -i "$S/admin_ed25519" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes \
        -o UserKnownHostsFile="$S/known_hosts" -o ConnectTimeout=10 "$user@$1.$domain" "sudo sh -c $(printf '%q' "$2")"
}
runs() { on "$1" "docker ps --format '{{.Names}}' 2>/dev/null; podman ps --format '{{.Names}}' 2>/dev/null" | grep -Eq -- "-$part(-|\$)"; }

ssh -o BatchMode=yes "$VMHOST" "$V domstate $from" 2>/dev/null | grep -q running; check $? "$from is a running VM on $VMHOST"
[ "$fails" = 0 ] || { say "stopped: $from is not a VM there"; exit 1; }
"$KIWI" control pull "$fleet" > control-pull.log 2>&1 || say "note: nothing pulled from a control node (see control-pull.log)"
"$KIWI" move "$fleet" "$part" "$from" "$to" --gone > plan-answers.log 2>&1
grep -q "$from answers: a move without --gone" plan-answers.log; check $? "--gone is refused while $from answers"
kept="kiwi-gone kept $(date +%s) $RANDOM"; lost="kiwi-gone lost $(date +%s) $RANDOM"
on "$from" "echo '$kept' > '$data/.kiwi-gone-kept'"; check $? "a marker is in $part's data on $from"
on "$from" "kiwi-stack backup" > backup.log 2>&1; check $? "$from made a backup"
on "$from" "echo '$lost' > '$data/.kiwi-gone-lost'"; check $? "a second marker after the backup"
ssh -o BatchMode=yes "$VMHOST" "$V destroy $from" >/dev/null; check $? "$from switched off hard"
sleep 20
"$KIWI" move "$fleet" "$part" "$from" "$to" > plan-plain.log 2>&1
grep -q "if it is dead, --gone" plan-plain.log; check $? "a plain move points at --gone while $from does not answer"
name=$("$KIWI" move "$fleet" "$part" "$from" "$to" --gone --porcelain 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("name") or "")')
began=$(date +%s)
"$KIWI" move "$fleet" "$part" "$from" "$to" --gone --yes > move-gone.log 2>&1; check $? "kiwi-server move $part $from $to --gone --yes ended well ($(( $(date +%s) - began )) s)"
got=$(on "$to" "cat '$data/.kiwi-gone-kept'" 2>/dev/null); check "$(is [ "$got" = "$kept" ])" "the marker of the backup is on $to"
! on "$to" "test -e '$data/.kiwi-gone-lost'" 2>/dev/null; check $? "the marker after the backup is not (it was lost, as said)"
runs "$to"; check $? "$part runs on $to"
if [ -n "$name" ]; then
    ip=$(getent hosts "$to.$domain" | awk '{print $1; exit}'); code=000
    for _ in 1 2 3 4 5 6; do
        code=$(curl -s -m 15 -o /dev/null -w '%{http_code}' --cacert "$S/ca/kiwiCA.pem" --resolve "$name:443:$ip" "https://$name/")
        case $code in 2*|3*) break ;; esac
        sleep 10
    done
    case $code in 2*|3*) true ;; *) false ;; esac; check $? "$name answers from $to ($code)"
fi
ssh -o BatchMode=yes "$VMHOST" "$V start $from" >/dev/null; check $? "$from switched on again"
up=""
for _ in $(seq 1 40); do
    sleep 10
    if on "$from" true 2>/dev/null; then up=1; break; fi
done
check "$(is [ -n "$up" ])" "$from answers again"
sleep 30
if runs "$from"; then say "        $from runs its old copy of $part until it is applied, as said"; fi
"$KIWI" apply "$fleet" "$from" > apply-back.log 2>&1; check $? "$from applied"
! runs "$from"; check $? "$part no longer runs on $from"
if [ "$back" = --back ]; then
    "$KIWI" move "$fleet" "$part" "$to" "$from" --yes > move-back.log 2>&1; check $? "a plain move of $part home to $from"
    got=$(on "$from" "cat '$data/.kiwi-gone-kept'" 2>/dev/null); check "$(is [ "$got" = "$kept" ])" "the marker came home too"
    runs "$from"; check $? "$part runs on $from again"
fi
"$KIWI" control push "$fleet" > control-push.log 2>&1 || say "note: nothing pushed to a control node (see control-push.log)"
say "$fails wrong"
exit $((fails > 0))
