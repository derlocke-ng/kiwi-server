#!/usr/bin/env bash
# A part goes to its standby by itself (recovery.switch, watchdog.switches): the machine that runs
# it loses its power, and nobody does anything. The control node must notice, find that the
# standby does not reach that machine either, and switch the part there; the part's name must
# answer from the standby, with the data it had. Then the power comes back, and the control node
# must take the part off the machine that was gone (the apply that waited for it).
#
#   VMHOST=sh3 tests/drills/part-failover.sh FLEET PART ON STANDBY DATA_DIR [LIMIT_MINUTES]
#
# ON must be a virtual machine on VMHOST (its power is cut with virsh destroy, and given back with
# virsh start). The control node must run a release that switches by itself. One PASS or FAIL
# line per check.
set -u
fleet=${1:?the fleet file}; part=${2:?a part}; on=${3:?the machine it runs on}; sb=${4:?the machine that keeps it ready}
data=${5:?a folder of its data}; limit=${6:-25}
VMHOST=${VMHOST:?the ssh name of the machine that runs ON as a VM}
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
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
runs() {   # MACHINE: the part's container runs there (asked three times: one lost answer is no proof)
    local n out
    for n in 1 2 3; do
        if out=$(at "$1" "docker ps --format '{{.Names}}' 2>/dev/null; podman ps --format '{{.Names}}' 2>/dev/null; echo kiwi-asked"); then
            grep -q kiwi-asked <<<"$out" && { grep -q -- "-$part\$" <<<"$out"; return; }
        fi
        sleep 5
    done
    return 1
}
V="virsh -c qemu:///system"
ssh -o BatchMode=yes -o ConnectTimeout=8 "$VMHOST" "$V domstate $on" 2>/dev/null | grep -q running; check $? "$on is a running VM on $VMHOST"
runs "$on"; check $? "$part runs on $on"

# the link, on the control node too: it is the one that acts
kiwi-server control pull "$fleet" > control-pull.log 2>&1; check $? "the fleet from the control node"
python3 - "$fleet" "$part" "$on" "$sb" <<'PY'
import sys, yaml
path, part, on, sb = sys.argv[1:5]
text = open(path).read()
head = "".join(l for l in text.splitlines(True)[:5] if l.startswith("#"))
d = yaml.safe_load(text)
d["hosts"][on].setdefault("keep_ready", {})[part] = sb
open(path, "w").write(head + yaml.safe_dump(d, sort_keys=False, allow_unicode=True, width=1000))
PY
kiwi-server apply "$fleet" "$on" "$sb" > apply-link.log 2>&1; check $? "$on and $sb applied: $sb keeps $part ready"
grep -q "$sb: keeps ready: $part of $on" apply-link.log || kiwi-server apply "$fleet" "$sb" >> apply-link.log 2>&1
kiwi-server control push "$fleet" > control-push.log 2>&1; check $? "the control node has the link"
name=$(kiwi-server move "$fleet" "$part" "$on" "$sb" --porcelain 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("name") or "")' 2>/dev/null)
mark="kiwi-failover $(date +%s) $RANDOM"
at "$on" "echo '$mark' > '$data/.kiwi-standby-marker'"
n=180; while [ "$n" -gt 0 ]; do [ "$(at "$sb" "cat '$data/.kiwi-standby-marker'" 2>/dev/null)" = "$mark" ] && break; sleep 10; n=$((n - 10)); done
[ "$(at "$sb" "cat '$data/.kiwi-standby-marker'" 2>/dev/null)" = "$mark" ]; check $? "$sb has $part's data, a marker in it"

# the power goes out
t0=$(date +%s)
ssh -o BatchMode=yes -o ConnectTimeout=8 "$VMHOST" "$V destroy $on" >/dev/null 2>&1; check $? "$on lost its power"
sbip=$(lanof "$sb"); moved="" answered=""
end=$((t0 + limit * 60))
while [ "$(date +%s)" -lt "$end" ]; do
    if [ -z "$moved" ] && runs "$sb"; then moved=$(( $(date +%s) - t0 )); say "  $part runs on $sb, $((moved / 60)) min $((moved % 60)) s after the power went"; fi
    if [ -n "$moved" ] && [ -n "$name" ] && [ -z "$answered" ]; then
        c=$(curl -s -m 5 -o /dev/null -w '%{http_code}' --cacert "$S/ca/kiwiCA.pem" --resolve "$name:443:$sbip" "https://$name/")
        case $c in 2*|3*) answered=$(( $(date +%s) - t0 )) ;; esac
    fi
    [ -n "$moved" ] && { [ -z "$name" ] || [ -n "$answered" ]; } && break
    sleep 15
done
[ -n "$moved" ]; check $? "the control node switched $part to $sb by itself (${moved:-never} s after the power went)"
[ -z "$name" ] || { [ -n "$answered" ]; check $? "$name answers from $sb (${answered:-never} s)"; }
[ "$(at "$sb" "cat '$data/.kiwi-standby-marker'" 2>/dev/null)" = "$mark" ]; check $? "the data came along: the marker is there"
kiwi-server control activity "$fleet" 2>/dev/null | grep -i "switched $part" | head -1 | sed 's/^/    /'

# the power comes back: the part must leave the machine that was gone
ssh -o BatchMode=yes -o ConnectTimeout=8 "$VMHOST" "$V start $on" >/dev/null 2>&1; check $? "$on has its power again"
# given up: its stack no longer has the part at all (the apply that waited for it), not merely
# "not running yet" in the first seconds after a start
t1=$(date +%s); n=$((20 * 60))
kept() { at "$on" "grep '^STACK_SERVICES=' /etc/kiwi-server/stack.env" 2>/dev/null | grep -qE -- "-$part( |\")"; }    # one of its own parts
while [ "$n" -gt 0 ]; do
    if at "$on" true 2>/dev/null && ! kept && ! runs "$on"; then break; fi
    sleep 20; n=$((n - 20))
done
at "$on" true 2>/dev/null && ! kept && ! runs "$on"; check $? "$on is back and gave $part up ($(( ($(date +%s) - t1) / 60 )) min)"
# how long it ran the part beside its standby after the power came back: its agent's first report
# holds it back (X-Kiwi-Hold), before the apply that takes the part away
# (the units by name: journalctl's -u takes no pattern like k?-…, it matched nothing and said 0)
both=$(at "$on" "journalctl -b -u 'kn-$part.service' -u 'km-$part.service' -o short-unix --no-pager 2>/dev/null" | awk '
    / Started / && !s { s = $1 } / Stopped / && s && !e { e = $1 } END { if (!s) print 0; else if (!e) print -1; else printf "%d", e - s }')
[ "${both:--1}" -ge 0 ] && [ "${both:--1}" -le 60 ]; check $? "$on ran $part beside $sb for ${both:-?} s after it came back (60 at most)"
runs "$sb"; check $? "$part still runs on $sb"
at "$on" "grep '^STACK_READY=' /etc/kiwi-server/stack.env" 2>/dev/null | grep -qE -- "-$part( |\")"
check $? "$on keeps $part ready now: the two stay a pair"
# the control node may be in the middle of its jobs (the apply that waited): asked again for a few minutes
n=10; while [ "$n" -gt 0 ] && ! kiwi-server control pull "$fleet" > control-pull2.log 2>&1; do sleep 30; n=$((n - 1)); done
[ "$n" -gt 0 ]; check $? "this computer has the fleet the control node changed"
say "$fails wrong"
exit $((fails > 0))
