#!/usr/bin/env bash
# Install media left in a machine do not install it again (iso.py, the guard): tried in a VM
# whose firmware starts from the media first, as a PC does with the stick left in.
#
# A throwaway network of one, its media built from this tree, a VM on VMHOST (an ssh name; empty:
# here) with the media as its first boot device and an empty disk as its second:
#   1. it installs and switches itself off, and the media's id is on the installed system's /boot
#   2. started from its disk, a marker file is written (what a reinstall would erase)
#   3. started again with the media first: nothing is installed. UEFI: the machine starts from
#      its disk by itself. BIOS: it says so and switches itself off; from its disk it is as before
#   4. media made again (a new id) do install: the marker file is gone
#
#   VMHOST=sh3 tests/drills/install-guard-vm.sh [coreos|debian] [uefi|bios] [WORKDIR]
#
# Touches no fleet but its own (WORKDIR/fleet, keys as plain files: a test) and no machine but
# its VM (VMHOST:/mnt/nvme_2tb/share/virt/kiwi-guard-TARGET-FW, or WORKDIR/vm here). About 15
# minutes for coreos, 40 for debian. One PASS or FAIL line per check.
set -u
{
here=$(cd "$(dirname "$0")/../.." && pwd)
target=${1:-coreos}; fw=${2:-uefi}
w=${3:-$HOME/kiwi-vm/guard-$target-$fw}
VMHOST=${VMHOST:-}
VD=${VD:-$([ -n "$VMHOST" ] && echo "/mnt/nvme_2tb/share/virt/kiwi-guard-$target-$fw" || echo "$w/vm")}
PSSH=${PSSH:-18341}
mkdir -p "$w"; cd "$w" || exit 2
ks=("$here/bin/kiwi-server")
admin=core; KEY=$w/fleet/secrets/admin_ed25519
# shellcheck source=tests/drills/lib-desktop-vm.sh
. "$here/tests/drills/lib-desktop-vm.sh"
trap 'vm_stop' EXIT
vh_prepare || { say "FAIL no UEFI firmware on ${VMHOST:-this computer}"; exit 1; }
start() {   # LOG media|disk: with the media it is the first boot device, as a stick left in a PC is
    local log=$1 cd="" firm
    [ "$2" = media ] && cd="-drive if=none,id=cd,format=raw,readonly=on,file=media.iso -device ide-cd,drive=cd,bootindex=0"
    if [ "$fw" = uefi ]; then
        firm="-drive if=pflash,format=raw,readonly=on,file=${OVMF_PAIR%%:*} -drive if=pflash,format=raw,file=vars.fd"
    else
        firm=""
    fi
    vm_stop
    vh "rm -f '$log' serial.sock; qemu-system-x86_64 -enable-kvm -machine q35 -cpu host -smp 2 -m 4096 $firm $cd \
        -drive if=none,id=disk,format=qcow2,file=disk.qcow2 -device virtio-blk-pci,drive=disk,bootindex=1 \
        -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$PSSH-:22 \
        -display none -chardev socket,id=s0,path=serial.sock,server=on,wait=off,logfile='$log' -serial chardev:s0 \
        -monitor unix:monitor.sock,server=on,wait=off -daemonize -pidfile qemu.pid"
}
# A uCore machine restarts twice by itself in its first minutes (its image is changed over): a
# question asked in such a moment gets no answer. Asked again until one comes.
ask() {     # COMMAND: its output; fails only when no answer came within two minutes
    local out rc n=24
    while [ "$n" -gt 0 ]; do
        out=$(vssh "$1" 2>/dev/null); rc=$?
        [ "$rc" != 255 ] && { printf '%s' "$out"; return 0; }
        sleep 5; n=$((n - 1))
    done
    return 1
}
settled() { # the first start's own restarts are over: up for two minutes in one piece
    local n=120 up
    while [ "$n" -gt 0 ]; do
        up=$(vssh 'cut -d. -f1 /proc/uptime' 2>/dev/null)
        [ "${up:-0}" -ge 120 ] && return 0
        sleep 10; n=$((n - 1))
    done
    return 1
}
off_within() { local n=$1; while [ "$n" -gt 0 ] && vm_running; do sleep 10; n=$((n - 10)); done; ! vm_running; }
build() {   # the media, from the fleet as it is: a new id each time
    "${ks[@]}" build fleet/fleet.yaml kumquat >build.log 2>&1 || return 1
    iso=$(find fleet -name "kumquat*.iso" | head -1)
    [ -s "$iso" ] && vh_put "$iso" media.iso
}

# ---- a network of one, its media ------------------------------------------------------------
rm -rf fleet; mkdir -p fleet
cat > answers.json <<EOF
{"domain": "home", "timezone": "UTC", "keyboard": "us", "locale": "en_US.UTF-8", "master_where": "none",
 "lan_subnet": "10.0.2.0/24", "lan_gateway": "10.0.2.2",
 "nodes": [{"name": "kumquat", "preset": "minimal", "address": "10.0.2.15", "target": "$target"}]}
EOF
"${ks[@]}" init fleet/fleet.yaml --answers answers.json --no-store >init.log 2>&1; check $? "a network of one made (init)"
python3 - fleet/fleet.yaml "$target" <<'EOF'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
h = d["hosts"]["kumquat"]
h["install"] = {"on_done": "poweroff"}
h["encryption"] = False                     # the guard is what is tried here: no key, no TPM
h["disk"] = "auto"
if sys.argv[2] == "coreos":
    h.setdefault("coreos", {})["console"] = "ttyS0,115200n8"
open(sys.argv[1], "w").write(yaml.safe_dump(d, sort_keys=False))
EOF
admin=$(python3 -c 'import sys, yaml; print(yaml.safe_load(open(sys.argv[1]))["defaults"]["admin"]["user"])' fleet/fleet.yaml)
build; check $? "its install media built and on ${VMHOST:-this computer}"
[ -s "${iso:-}" ] || { say "FAIL no media ($(tail -3 build.log))"; exit 1; }

# ---- 1. the install ----------------------------------------------------------------------------
vh "rm -f disk.qcow2 vars.fd; qemu-img create -q -f qcow2 disk.qcow2 20G; [ '$fw' != uefi ] || cp '${OVMF_PAIR##*:}' vars.fd"
t0=$(date +%s)
start install.log media
off_within 2400; check $? "installed, and switched itself off ($(( $(date +%s) - t0 )) s)"

# ---- 2. from its disk: the mark is there, a marker file is written ---------------------------
start first.log disk
up_within 900 && settled; check $? "it starts from its disk and answers"
mark=$(ask 'sudo cat /boot/kiwi-installed')
grep -q "^kumquat [0-9a-f]\{16\} 20" <<< "$mark"; check $? "the media's id is on its /boot (${mark:-nothing})"
ask 'echo kept | sudo tee /var/kiwi-drill-marker >/dev/null; sync' >/dev/null
[ "$(ask 'sudo cat /var/kiwi-drill-marker')" = kept ]; check $? "a marker file is on its disk"
vssh 'sudo systemctl poweroff' 2>/dev/null; off_within 120 || vm_stop

# ---- 3. started again with the media left in --------------------------------------------------
t0=$(date +%s)
start again.log media
if [ "$fw" = uefi ] && [ "$target" = coreos ]; then
    up_within 900; check $? "with the media first it starts from its disk by itself ($(( $(date +%s) - t0 )) s)"
    vh "grep -qa 'is installed already' again.log"; check $? "the media said why nothing is installed"
    vh "grep -a 'kiwi-server:' again.log | tail -3" | sed 's/^/    /'
else
    off_within 900; check $? "with the media first nothing is installed: it switches itself off ($(( $(date +%s) - t0 )) s)"
    if [ "$target" = coreos ]; then
        vh "grep -qa 'is installed already' again.log"; check $? "the media said why"
    fi
    start after.log disk
    up_within 900; check $? "from its disk it starts as before"
fi
[ "$(ask 'sudo cat /var/kiwi-drill-marker')" = kept ]; check $? "what was on its disk is still there"
[ "$(ask 'sudo cat /boot/kiwi-installed')" = "$mark" ]; check $? "it was not installed a second time (the same mark)"
vssh 'sudo systemctl poweroff' 2>/dev/null; off_within 120 || vm_stop

# ---- 4. media made again install: that is how a machine is installed again on purpose ---------
build; check $? "the media made again"
t0=$(date +%s)
start reinstall.log media
off_within 2400; check $? "the new media install, and the machine switches itself off ($(( $(date +%s) - t0 )) s)"
start second.log disk
up_within 900 && settled; check $? "it starts from its disk"
[ "$(ask 'sudo test -e /var/kiwi-drill-marker && echo there || echo gone')" = gone ]; check $? "the disk was erased: the marker file is gone"
new=$(ask 'sudo cat /boot/kiwi-installed')
[ -n "$new" ] && [ "$new" != "$mark" ]; check $? "and it carries the new media's id"
vm_stop
say "$([ "$fails" = 0 ] && echo "all passed" || echo "$fails failed")"
exit "$fails"
}
