#!/usr/bin/env bash
# Backups, snapshots and the emergency rebuild of a machine, on a VM: a marker goes into a
# module's data, is backed up twice, broken and brought back from the snapshot; then the
# machine is wiped and installed anew (first-run-vm.sh), and has to come back with the
# marker by itself, its history kept.
#
#   tests/drills/regenerate-vm.sh <fleet.yaml> <host> <vm-host> <module> <file-in-its-data>
#
# The host must be set up and carry the backup module and <module>; the file is a path on
# the machine inside what <module> backs up. The machine and its disk are WIPED.
set -u
fleet=$1; host=$2; vm=$3; module=$4; file=$5
grep -qE "^allowed:.*\b$host\b" "$(dirname "$fleet")/drills.yaml" 2>/dev/null || { echo "$host is not in $(dirname "$fleet")/drills.yaml allowed:" >&2; exit 2; }
ks=${KIWI_SERVER:-kiwi-server}
dir=$(dirname "$fleet")
key=$dir/secrets/admin_ed25519
here=$(dirname "$0")
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
addr=$("$ks" list "$fleet" --porcelain | python3 -c "
import json, sys
h = next(x for x in json.load(sys.stdin)['hosts'] if x['name'] == '$host')
print((h['address'] or '').split('/')[0])")
on() { timeout 60 ssh -o BatchMode=yes -o ConnectTimeout=6 -o IdentitiesOnly=yes -o IdentityAgent=none \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i "$key" "core@$addr" "$1" 2>/dev/null; }
marker() { on "sudo cat '$file'"; }
snapshots() { on "sudo kiwi-stack snapshots $module 2>/dev/null | grep -cE '^[0-9a-f]{8} '"; }
is() { rc=1; [ "$(marker)" = "$1" ] && rc=0; check "$rc" "$2 (the marker says: $(marker))"; }

say "== $host at $addr: backups and snapshots of $module"
on "echo first | sudo tee '$file' >/dev/null"
"$ks" backups "$fleet" "$host" --now >/dev/null 2>&1; check $? "a backup on request"
n1=$(snapshots)
on "echo second | sudo tee '$file' >/dev/null"
"$ks" backups "$fleet" "$host" --now >/dev/null 2>&1; check $? "a second backup"
n2=$(snapshots)
rc=1; [ "${n2:-0}" -gt "${n1:-0}" ] && rc=0
check "$rc" "each backup is a snapshot of its own (${n1:-0}, then ${n2:-0})"
on "echo broken | sudo tee '$file' >/dev/null"
"$ks" backups "$fleet" "$host" "$module" --restore >/dev/null 2>&1; check $? "restore of $module from the newest snapshot"
is second "the data is what the last backup held"

say "== the machine is lost: wiped and installed anew"
KIWI_SERVER=$ks bash "$here/first-run-vm.sh" "$fleet" "$host" "$vm" | sed 's/^/   /'
is second "the rebuilt machine brought its data back by itself"
n3=$(snapshots)
rc=1; [ "${n3:-0}" -ge "${n2:-0}" ] && rc=0
check "$rc" "its history is kept (${n3:-0} snapshots, ${n2:-0} before)"
"$ks" backups "$fleet" "$host" --now >/dev/null 2>&1; check $? "it backs up again"
n4=$(snapshots)
rc=1; [ "${n4:-0}" -gt "${n3:-0}" ] && rc=0
check "$rc" "on top of that history (${n4:-0} snapshots)"
say "== $host: $fails failed"
exit "$fails"
