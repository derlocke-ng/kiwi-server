#!/usr/bin/env bash
# A standby for a part, on real machines (keep_ready, kiwi-server standby / switch, docs/backups.md):
# the standby machine fetches the part's data every minute with a key that can do nothing else,
# and takes the part over with a switch.
#
#   tests/drills/part-standby.sh FLEET PART ON STANDBY DATA_DIR [--down]
#
# ON runs the part; STANDBY will keep it ready and then take it over. DATA_DIR: a folder of the
# part's data (Vaultwarden: /home/user/docker/kn-vault). One PASS or FAIL line per check.
# --down: ON is restarted right before the switch, so the switch happens while it does not answer
# (its disk must open by itself at boot); afterwards the control node must take the part off it.
set -u
fleet=${1:?the fleet file}; part=${2:?a part}; on=${3:?the machine it runs on}; sb=${4:?the machine that keeps it ready}
data=${5:?a folder of its data}; down=${6:-}
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
dir=$(cd "$(dirname "$fleet")" && pwd); S=$(readlink -f "$dir/secrets")
read -r user domain < <(python3 -c '
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))["defaults"]
print(d["admin"]["user"], d.get("domain") or "home")' "$fleet")
at() { # MACHINE COMMAND: as root there
    ssh -i "$S/admin_ed25519" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes \
        -o UserKnownHostsFile="$S/known_hosts" -o ConnectTimeout=10 "$user@$1.$domain" "sudo sh -c $(printf '%q' "$2")"
}
age() { kiwi-server standby "$fleet" --porcelain 2>/dev/null | python3 -c '
import json, sys
r = [x for x in json.load(sys.stdin)["standby"] if x["part"] == sys.argv[1]]
print(r[0]["age"] if r and r[0]["age"] is not None else -1)' "$part"; }
kiwi-server control pull "$fleet" > control-pull.log 2>&1 || say "note: nothing pulled from a control node"
# the fleet file: STANDBY keeps the part ready
python3 - "$fleet" "$part" "$on" "$sb" <<'PY'
import sys, yaml
path, part, on, sb = sys.argv[1:5]
text = open(path).read()
head = "".join(l for l in text.splitlines(True)[:5] if l.startswith("#"))
d = yaml.safe_load(text)
d["hosts"][on].setdefault("keep_ready", {})[part] = sb
open(path, "w").write(head + yaml.safe_dump(d, sort_keys=False, allow_unicode=True, width=1000))
PY
check $? "the fleet file: $sb keeps $part of $on ready"
kiwi-server apply "$fleet" "$on" "$sb" > apply-link.log 2>&1; check $? "$on and $sb applied"
grep -q "kept ready on $sb" apply-link.log; check $? "$on gives its folders to $sb"
if ! grep -q "$sb: keeps ready: $part of $on" apply-link.log; then      # the host key came with ON's apply: once more for the standby
    kiwi-server apply "$fleet" "$sb" >> apply-link.log 2>&1
fi
grep -q "$sb: keeps ready: $part of $on" apply-link.log; check $? "$sb fetches them"
# the part itself is made there and not started (kiwi-stack ready): a switch starts it at once
ready=$(at "$sb" "kiwi-stack ready list" 2>/dev/null | grep -- "-$part")
[ -n "$ready" ] && ! grep -qE ' (active|running)$' <<<"$ready"; check $? "$sb has $part made and not started (${ready:-nothing kept ready})"
n=240; while [ "$n" -gt 0 ]; do a=$(age); [ "$a" -ge 0 ] && [ "$a" -lt 90 ] && break; sleep 10; n=$((n - 10)); done
a=$(age); [ "$a" -ge 0 ] && [ "$a" -lt 90 ]; check $? "kiwi-server standby: its copy is current (${a} s)"
mark="kiwi-standby $(date +%s) $RANDOM"
at "$on" "echo '$mark' > '$data/.kiwi-standby-marker'"; check $? "a marker is in $part's data on $on"
n=180; while [ "$n" -gt 0 ]; do [ "$(at "$sb" "cat '$data/.kiwi-standby-marker'" 2>/dev/null)" = "$mark" ] && break; sleep 10; n=$((n - 10)); done
[ "$(at "$sb" "cat '$data/.kiwi-standby-marker'" 2>/dev/null)" = "$mark" ]; check $? "the marker reached $sb within $((180 - n)) s: its data is fetched"
at "$on" "docker ps --format '{{.Names}} {{.Status}}' 2>/dev/null; podman ps --format '{{.Names}} {{.Status}}' 2>/dev/null" | grep -- "-$part" | grep -qv -i paused
check $? "$part runs on $on meanwhile (not left paused)"
# the standby's key: reading the folders, and nothing else
k="ssh -i /etc/kiwi-server/standby/key -o UserKnownHostsFile=/etc/kiwi-server/standby/known_hosts -o StrictHostKeyChecking=yes -o BatchMode=yes -o IdentitiesOnly=yes root@$on.$domain"
! at "$sb" "$k id" >/dev/null 2>&1; check $? "the standby's key gets no shell on $on"
! at "$sb" "$k cat /etc/shadow" >/dev/null 2>&1; check $? "nor a file outside the folders"
! at "$sb" "rsync -a -e '${k% root@*}' /etc/hostname root@$on.$domain:$data/planted" >/dev/null 2>&1; check $? "nor can it write into the part's data there"
! at "$on" "test -e '$data/planted'"; check $? "nothing was planted on $on"
# the switch
name=$(kiwi-server move "$fleet" "$part" "$on" "$sb" --porcelain 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("name") or "")' 2>/dev/null)
kiwi-server switch "$fleet" "$part" > switch-plan.log 2>&1; check $? "the plan of the switch has nothing in its way"
mark2="kiwi-switch $(date +%s) $RANDOM"
at "$on" "echo '$mark2' > '$data/.kiwi-standby-marker'"
if [ "$down" = --down ]; then
    n=180; while [ "$n" -gt 0 ]; do [ "$(at "$sb" "cat '$data/.kiwi-standby-marker'" 2>/dev/null)" = "$mark2" ] && break; sleep 10; n=$((n - 10)); done
    kiwi-server power "$fleet" "$on" restart --yes >/dev/null 2>&1; check $? "$on is told to restart: the switch happens while it is away"
    sleep 8
fi
t0=$(date +%s)
kiwi-server switch "$fleet" "$part" --yes > switch.log 2>&1 &
sw=$!
# meanwhile: when does its name answer from the standby (its proxy, at its address)?
sbip=$(getent hosts "$sb.$domain" | awk '{print $1; exit}'); answered=""
while kill -0 "$sw" 2>/dev/null; do
    if [ -n "$name" ] && [ -z "$answered" ] && [ -n "$sbip" ]; then
        c=$(curl -s -m 3 -o /dev/null -w '%{http_code}' --cacert "$S/ca/kiwiCA.pem" --resolve "${name#https://}:443:$sbip" "https://${name#https://}/")
        case $c in 2*|3*) answered=$(( $(date +%s) - t0 )) ;; esac
    fi
    sleep 2
done
wait "$sw"; check $? "kiwi-server switch $part --yes ended well ($(( $(date +%s) - t0 )) s)"
[ -n "$answered" ]; check $? "its name answered from $sb ${answered:-?} s after the switch began, before the switch ended"
grep -q "it was kept ready" switch.log; check $? "$part started from what was kept ready ($(grep -o '[0-9]* seconds\? after the switch began' switch.log | head -1))"
grep -E "it was away for|after the switch began" switch.log | sed 's/^/    /'
if [ "$down" = --down ]; then
    grep -q "does not answer" switch.log; check $? "the switch went on without $on"
    [ "$(at "$sb" "cat '$data/.kiwi-standby-marker'" 2>/dev/null)" = "$mark2" ]; check $? "$sb runs it with the copy it had"
else
    [ "$(at "$sb" "cat '$data/.kiwi-standby-marker'" 2>/dev/null)" = "$mark2" ]; check $? "what was written a moment before the switch is on $sb: nothing was lost"
fi
at "$sb" "docker ps --format '{{.Names}}' 2>/dev/null; podman ps --format '{{.Names}}' 2>/dev/null" | grep -q -- "-$part"; check $? "$part runs on $sb"
gone() { local out; out=$(at "$on" "docker ps --format '{{.Names}}' 2>/dev/null; podman ps --format '{{.Names}}' 2>/dev/null" 2>/dev/null) || return 1; ! grep -q -- "-$part\$" <<<"$out"; }
if [ "$down" = --down ]; then        # it comes back, and the control node gives it the change that waited
    n=1200; while [ "$n" -gt 0 ] && ! gone; do sleep 20; n=$((n - 20)); done
    gone; check $? "$on is back and no longer runs $part: the control node applied what waited ($(( (1200 - n) / 60 )) min)"
else
    gone; check $? "$part no longer runs on $on"
fi
! at "$sb" "grep -q '^$part ' /etc/kiwi-server/standby/pull.conf 2>/dev/null"; check $? "$sb no longer fetches over the part it now runs"
if [ -n "$name" ]; then
    ip=$(getent hosts "$sb.$domain" | awk '{print $1; exit}'); code=000
    case $name in
        *."$domain")
            for _ in 1 2 3 4 5 6; do
                code=$(curl -s -m 15 -o /dev/null -w '%{http_code}' --cacert "$S/ca/kiwiCA.pem" --resolve "$name:443:$ip" "https://$name/")
                case $code in 2*|3*) break ;; esac
                sleep 10
            done ;;
        *)  # a public name: as a stranger reaches it, through the master, with a certificate the world knows.
            # The new machine asks Let's Encrypt for it once the master hands the name on: a minute or two
            for _ in $(seq 1 18); do
                code=$(curl -s -m 15 -o /dev/null -w '%{http_code}' "https://$name/")
                case $code in 2*|3*) break ;; esac
                sleep 10
            done
            at "$sb" "ls /home/*/docker/k?-nginx/acme/$name/fullchain.pem >/dev/null 2>&1"; check $? "$sb holds the certificate of $name" ;;
    esac
    case $code in 2*|3*) true ;; *) false ;; esac; check $? "$name answers from $sb ($code)"
fi
kiwi-server control push "$fleet" > control-push.log 2>&1 || say "note: nothing pushed to a control node"
say "$fails wrong"
exit $((fails > 0))
