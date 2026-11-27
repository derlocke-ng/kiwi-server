#!/usr/bin/env bash
# An upgrade, rehearsed: a throwaway machine is applied with an older release of kiwi-server, then
# with the one under test, and after each its services are asked whether they still answer and
# still hold their data. What it catches: a step of the new release that an older installation
# does not survive (a renamed file, a unit that is left behind, a container that does not come
# back), which the test machines never show because they take every release as it comes.
#
#   tests/drills/upgrade-rehearsal.sh FLEET MACHINE OLD_TAG [CHECK...]
#   e.g. tests/drills/upgrade-rehearsal.sh ~/kiwi/fleet.yaml mulberry v2.300.0 \
#            "https://cloud.mulberry.home/status.php 192.168.2.65 installed\":true" \
#            "https://vault.mulberry.home/alive 192.168.2.65 20"
#
# MACHINE: one of drills.yaml's `allowed`, whose settings the old release knows too (it is checked
# alone; the rest of the fleet may be newer). OLD_TAG: a release tag of kiwi-server; its code is
# fetched into a folder of its own and removed afterwards. CHECK: "URL ADDRESS TEXT", fetched with
# the fleet's CA at ADDRESS, passes when the answer holds TEXT. KIWI_SERVER: the release under
# test (default: the one installed). SOAK: seconds until the last look (150; 0: none). One PASS or
# FAIL line per check. The machine is left on the release under test.
set -u
fleet=${1:?the fleet file}; m=${2:?a machine}; old=${3:?an older release tag, like v2.300.0}; shift 3
checks=("$@")
dir=$(cd "$(dirname "$fleet")" && pwd)
grep -qE "^allowed:.*\b$m\b" "$dir/drills.yaml" 2>/dev/null || { echo "$m is not in $dir/drills.yaml allowed:" >&2; exit 2; }
new=${KIWI_SERVER:-kiwi-server}
repo=${KIWI_SERVER_REPO:-https://github.com/derlocke-ng/kiwi-server}
ca=$dir/secrets/ca/kiwiCA.pem
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
answers() {     # after WHAT: every CHECK
    local c url addr text host got
    for c in "${checks[@]}"; do
        read -r url addr text <<<"$c"
        host=${url#*://}; host=${host%%/*}
        got=$(curl -s -m 30 --cacert "$ca" --resolve "$host:443:$addr" "$url" 2>/dev/null)
        [[ $got == *"$text"* ]]; check $? "$1: $url answers with $text"
    done
}
ready() {       # the machine's containers are up again (an apply returns before every one is healthy)
    local n=30
    while [ "$n" -gt 0 ]; do
        "$new" status "$fleet" "$m" 2>/dev/null | grep -qiE "starting|unhealthy" || return 0
        sleep 10; n=$((n - 1))
    done
}

work=$(mktemp -d "${TMPDIR:-/tmp}/kiwi-upgrade.XXXXXX"); trap 'rm -rf "$work"' EXIT
git clone -q --depth 1 --branch "$old" "$repo" "$work/old" 2>/dev/null; check $? "the old release $old fetched"
[ -x "$work/old/bin/kiwi-server" ] || { say "$fails wrong"; exit 1; }
oldv=$("$work/old/bin/kiwi-server" --version 2>&1 | tail -1); newv=$("$new" --version 2>&1 | tail -1)
say "        from: $oldv   to: $newv"

answers "before"
say "== $m with the old release"
"$work/old/bin/kiwi-server" apply "$fleet" "$m" > "$work/old.log" 2>&1; rc=$?
check $rc "the old release applied $m$([ $rc = 0 ] || echo ": $(tail -n 2 "$work/old.log" | tr '\n' ' ' | cut -c1-200)")"
ready; answers "on the old release"

say "== $m with the release under test"
"$new" apply "$fleet" "$m" > "$work/new.log" 2>&1; rc=$?
check $rc "the release under test applied $m$([ $rc = 0 ] || echo ": $(tail -n 2 "$work/new.log" | tr '\n' ' ' | cut -c1-200)")"
grep -E "did not restart|did not start|is not up" "$work/new.log" | head -3 | sed 's/^/        /'
ready; answers "after the upgrade"
# and still a while later: some faults show only after minutes (the reverse proxy forgets a
# container's address after two, and a firewall reload once cut the containers' DNS)
soak=${SOAK:-150}
if [ "$soak" -gt 0 ]; then say "        waiting $soak s"; sleep "$soak"; answers "$soak s after the upgrade"; fi
"$new" plan "$fleet" "$m" 2>/dev/null | grep -q "nothing changes"; check $? "a second plan changes nothing"
[ "$fails" = 0 ] && say "all passed" || say "$fails wrong"
exit $((fails > 0))
