#!/usr/bin/env bash
# A new network whose master is installed at a provider, the way a beginner gets there: the
# wizard's answers, then `kiwi-server next` until it says that everything runs. Then the
# checks: the master answers, its names resolve, a phone gets a place on the VPN.
#
#   KIWI_NEVER="<id or address> ..." tests/drills/master-vps.sh <new-dir> <provider> <server-id> <address> <token-file>
#
# THE SERVER IS WIPED. KIWI_NEVER names servers this must never touch (a network in use);
# the drill refuses to run without it, and refuses a server named in it.
set -u
dir=$1; provider=$2; sid=$3; addr=$4; token=$5
ks=${KIWI_SERVER:-kiwi-server}
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
[ -n "${KIWI_NEVER:-}" ] || { say "FAIL KIWI_NEVER is not set: say which servers are in use"; exit 1; }
for never in $KIWI_NEVER; do
    if [ "$never" = "$sid" ] || [ "$never" = "$addr" ]; then say "FAIL $never is in use: not touched"; exit 1; fi
done
[ ! -e "$dir/fleet.yaml" ] || { say "FAIL $dir/fleet.yaml exists"; exit 1; }
mkdir -p "$dir" && chmod 700 "$dir"
fleet=$dir/fleet.yaml
tmp=$(mktemp -d "${XDG_RUNTIME_DIR:-/dev/shm}/kiwi-drill.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
cp "$token" "$tmp/token"
python3 - "$tmp" "$provider" "$sid" "$addr" <<'PY'
import json, sys
tmp, provider, sid, addr = sys.argv[1:]
json.dump({"master_where": provider, "master_address": addr, "provider_server": int(sid),
           "provider_token": tmp + "/token", "exit": "direct", "nodes": []}, open(tmp + "/answers.json", "w"))
PY
"$ks" init "$fleet" --answers "$tmp/answers.json" >/dev/null 2>"$tmp/init.err"      # (the recovery key is not kept)
check $? "the wizard's answers made a network in $dir"
master=$("$ks" list "$fleet" --porcelain | python3 -c "import json,sys; print(json.load(sys.stdin)['hosts'][0]['name'])")
say "the master is $master at $addr (server $sid at $provider)"

done_=1
for round in 1 2 3 4 5 6 7 8; do
    say "== next, round $round"
    out=$(PYTHONUNBUFFERED=1 "$ks" next "$fleet" --yes 2>&1); rc=$?
    printf '%s\n' "$out" | grep -E '^:: |^error' | grep -v -E 'Setting up|Unpacking|Get:|Selecting' | tail -6 | cut -c1-200
    if printf '%s' "$out" | grep -q "every machine is set up"; then done_=0; break; fi
    [ "$rc" = 0 ] || sleep 60
    sleep 30
done
check "$done_" "kiwi-server next brought the master up by itself"
"$ks" status "$fleet" "$master" >"$tmp/status" 2>&1
grep -q "running" "$tmp/status"; check $? "status: the master answers and its services run"
grep -E 'running|problem|locked' "$tmp/status" | head -3 | cut -c1-160
"$ks" check "$fleet" >"$tmp/check" 2>&1; check $? "check: its names and its exit"
grep -c '^ok' "$tmp/check" | sed 's/^/   checks ok: /'
"$ks" enroll "$fleet" phone >/dev/null 2>&1; check $? "a phone got its place on the VPN"
find "$dir/secrets/" -name 'phone*' 2>/dev/null | grep -q .; check $? "its config is in the network's keys"
say "== $master: $fails failed"
exit "$fails"
