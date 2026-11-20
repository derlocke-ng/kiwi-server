#!/usr/bin/env bash
# A part moves to another machine, on real machines (kiwi-server move, docs/backups.md): a marker
# is put into the part's data before, and must be on the new machine after; the part must run
# there and not on the old one, and its name must answer from the new machine.
#
#   tests/drills/part-move.sh FLEET PART FROM TO DATA_DIR [--back]
#
# DATA_DIR: a folder of the part's data that its backup holds (Forgejo: /home/user/docker/kn-git/data).
# --back: afterwards move it back and check again (two moves, and the machines are as before).
# One PASS or FAIL line per check.
set -u
fleet=${1:?the fleet file}; part=${2:?a part}; from=${3:?the machine it is on}; to=${4:?the machine it goes to}
data=${5:?a folder of its data}; back=${6:-}
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
dir=$(cd "$(dirname "$fleet")" && pwd); S=$(readlink -f "$dir/secrets")
read -r user domain < <(python3 -c '
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))["defaults"]
print(d["admin"]["user"], d.get("domain") or "home")' "$fleet")
on() { # MACHINE COMMAND: as root there
    ssh -i "$S/admin_ed25519" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes \
        -o UserKnownHostsFile="$S/known_hosts" -o ConnectTimeout=10 "$user@$1.$domain" "sudo sh -c $(printf '%q' "$2")"
}
one_move() { # FROM TO
    local a=$1 b=$2 mark name code ip
    mark="kiwi-move $(date +%s) $RANDOM"
    kiwi-server move "$fleet" "$part" "$a" "$b" > "plan-$a-$b.log" 2>&1; check $? "the plan for $part from $a to $b has nothing in its way"
    name=$(kiwi-server move "$fleet" "$part" "$a" "$b" --porcelain 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("name") or "")')
    on "$a" "echo '$mark' > '$data/.kiwi-move-marker'"; check $? "a marker is in $part's data on $a"
    kiwi-server move "$fleet" "$part" "$a" "$b" --yes > "move-$a-$b.log" 2>&1; check $? "kiwi-server move $part $a $b --yes ended well"
    [ "$(on "$b" "cat '$data/.kiwi-move-marker'" 2>/dev/null)" = "$mark" ]; check $? "the marker is in $part's data on $b: the data came along"
    on "$b" "docker ps --format '{{.Names}}' 2>/dev/null; podman ps --format '{{.Names}}' 2>/dev/null" | grep -q -- "-$part"; check $? "$part runs on $b"
    ! on "$a" "docker ps --format '{{.Names}}' 2>/dev/null; podman ps --format '{{.Names}}' 2>/dev/null" | grep -q -- "-$part\$"; check $? "$part no longer runs on $a"
    python3 -c '
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))["hosts"]
role = lambda h: d[h][d[h].get("role", "node")]
sys.exit(0 if sys.argv[2] in role(sys.argv[4])["modules"] and sys.argv[2] not in role(sys.argv[3])["modules"] else 1)' "$fleet" "$part" "$a" "$b"
    check $? "the fleet file has $part among $b's parts, not $a's"
    if [ -n "$name" ]; then
        ip=$(getent hosts "$b.$domain" | awk '{print $1; exit}')
        code=000
        for _ in 1 2 3 4 5 6; do
            code=$(curl -s -m 15 -o /dev/null -w '%{http_code}' --cacert "$S/ca/kiwiCA.pem" --resolve "$name:443:$ip" "https://$name/")
            case $code in 2*|3*) break ;; esac
            sleep 10
        done
        case $code in 2*|3*) true ;; *) false ;; esac; check $? "$name answers from $b ($code)"
    fi
}
# a network with a control node: its copy of the fleet first, and this one back to it afterwards
kiwi-server control pull "$fleet" > control-pull.log 2>&1 || say "note: nothing pulled from a control node (see control-pull.log)"
one_move "$from" "$to"
[ "$back" = --back ] && one_move "$to" "$from"
kiwi-server control push "$fleet" > control-push.log 2>&1 || say "note: nothing pushed to a control node (see control-push.log)"
say "$fails wrong"
exit $((fails > 0))
