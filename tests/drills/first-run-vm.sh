#!/usr/bin/env bash
# The first run of a machine, from nothing, on a VM: build its install media, install it on
# an empty disk, let `kiwi-server next` set it up, then restart it with its rings up, with
# one off and with all off. Prints one PASS or FAIL line per check.
#
#   tests/drills/first-run-vm.sh <fleet.yaml> <host> <vm-host> [ring-host:unit ...]
#
# <vm-host> is an ssh name with libvirt (virsh -c qemu:///system), a pool `iso` and a pool
# `default`; the VM is called like the host and boots its disk first, then the ISO.
# ring-host:unit: where a Tang ring of this host runs (address:systemd unit), to switch it
# off and on for the restart checks. The machine and its disk are WIPED.
set -u
fleet=$1; host=$2; vm=$3; shift 3
rings=("$@")
grep -qE "^allowed:.*\b$host\b" "$(dirname "$fleet")/drills.yaml" 2>/dev/null || { echo "$host is not in $(dirname "$fleet")/drills.yaml allowed:" >&2; exit 2; }
ks=${KIWI_SERVER:-kiwi-server}
dir=$(dirname "$fleet")
key=$dir/secrets/admin_ed25519
V="virsh -c qemu:///system"
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
fails=0

addr=$("$ks" list "$fleet" --porcelain | python3 -c "
import json, sys
h = next(x for x in json.load(sys.stdin)['hosts'] if x['name'] == '$host')
print((h['address'] or '').split('/')[0])")
[ -n "$addr" ] && [ "$addr" != dhcp ] || { say "FAIL $host has no fixed address in the fleet file"; exit 1; }
on() { timeout 20 ssh -o BatchMode=yes -o ConnectTimeout=6 -o IdentitiesOnly=yes -o IdentityAgent=none \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i "$key" "core@$1" "$2" 2>/dev/null; }
up() { on "$addr" true; }
wait_up() { local n=$1; while [ "$n" -gt 0 ]; do up && return 0; sleep 10; n=$((n - 1)); done; return 1; }
wait_down() { local n=30; while [ "$n" -gt 0 ]; do up || return 0; sleep 2; n=$((n - 1)); done; return 1; }

say "== $host at $addr: media"
[ -f "$dir/secrets/$host.$("$ks" list "$fleet" --porcelain | python3 -c "import json,sys; print(json.load(sys.stdin)['domain'])").conf" ] ||
    "$ks" enroll "$fleet" "$host" >/dev/null 2>&1
"$ks" build "$fleet" "$host" >/dev/null 2>&1; check $? "install media built"
iso=$dir/output/$host/$host.iso

say "== a fresh disk, and the install"
scp -q -o BatchMode=yes "$iso" "$vm:$host.iso" && ssh -o BatchMode=yes "$vm" "set -e
    $V destroy $host >/dev/null 2>&1 || true
    size=\$($V vol-info --pool default $host.qcow2 | awk '/Capacity/ {print \$2 \$3}' | sed 's/iB//; s/\\..*G/G/')
    $V vol-delete --pool iso $host.iso >/dev/null 2>&1 || true
    $V vol-create-as iso $host.iso \$(stat -c %s $host.iso) --format raw >/dev/null
    $V vol-upload --pool iso $host.iso $host.iso >/dev/null; rm -f $host.iso
    $V vol-delete --pool default $host.qcow2 >/dev/null
    $V vol-create-as default $host.qcow2 \${size:-40G} --format qcow2 >/dev/null
    $V start $host >/dev/null"
check $? "VM started from the install media on an empty disk"
sleep 90
wait_up 120; check $? "the machine installed itself and answers"

say "== the guide sets it up"
PYTHONUNBUFFERED=1 "$ks" next "$fleet" "$host" --yes > "/tmp/first-run-$host.log" 2>&1
check $? "kiwi-server next set it up (log: /tmp/first-run-$host.log)"
on "$addr" 'getent hosts ghcr.io >/dev/null'; check $? "it resolves names"
n=$(on "$addr" 'sudo podman ps -q 2>/dev/null | wc -l; sudo docker ps -q 2>/dev/null | wc -l' | sort -n | tail -1)
rc=1; [ "${n:-0}" -ge 1 ] && rc=0
check "$rc" "its containers run (${n:-0})"
crypt=$(on "$addr" 'lsblk -lno FSTYPE | grep -c crypto_LUKS')
say "encrypted partitions: ${crypt:-0}"

restart() { on "$addr" 'sudo systemctl reboot'; wait_down; }
ring() { on "${1%%:*}" "sudo systemctl $2 ${1#*:}" >/dev/null; }
if [ "${crypt:-0}" -ge 1 ] && [ "${#rings[@]}" -ge 1 ]; then
    say "== restarts"
    restart; wait_up 30; check $? "restart with every ring up: back by itself"
    if [ "${#rings[@]}" -ge 2 ]; then
        ring "${rings[0]}" stop; restart; wait_up 30; check $? "restart with one ring off: back by itself"
    fi
    for r in "${rings[@]}"; do ring "$r" stop; done
    restart
    if wait_up 9; then check 1 "restart with every ring off: it waits"; else check 0 "restart with every ring off: it waits"; fi
    "$ks" status "$fleet" "$host" 2>&1 | grep -q "disk is locked"; check $? "status says its disk is locked"
    ring "${rings[0]}" start; wait_up 30; check $? "one ring back: it opens by itself"
    for r in "${rings[@]}"; do ring "$r" start; done
fi
say "== $host: $fails failed"
exit "$fails"
