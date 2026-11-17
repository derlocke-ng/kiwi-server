#!/usr/bin/env bash
# An encrypted master from kiwi-server's own media (docs/design/encryption.md, step 3): a throwaway
# network made by `init` with its master on this computer's loopback (wg_host 127.0.0.1), its
# Debian media built, installed in a local QEMU VM (UEFI, KVM, 2 GB, as a small VPS). Its first
# start waits for the one-time key at SSH in its initramfs; `kiwi-server next` sees that and gives
# it the key; the system comes up with its root encrypted. Touches no fleet but its own
# (WORKDIR/fleet, keys as plain files: a test) and no machine. One PASS or FAIL line per check.
#
#   tests/drills/master-encrypt-vm.sh [WORKDIR]       (default ~/kiwi-vm/masterenc; ~20 minutes)
set -u
{
here=$(cd "$(dirname "$0")/../.." && pwd)
w=${1:-$HOME/kiwi-vm/masterenc}
mkdir -p "$w"; cd "$w" || exit 2
OVMF=/usr/share/edk2/ovmf
P_INIT=${P_INIT:-2222}; P_SSH=${P_SSH:-2201}
ks=("$here/bin/kiwi-server")
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
fails=0
stop() { [ -s qemu.pid ] && { kill "$(cat qemu.pid)" 2>/dev/null; sleep 2; kill -9 "$(cat qemu.pid)" 2>/dev/null; rm -f qemu.pid; }; true; }
trap stop EXIT
answers() { (exec 3<>/dev/tcp/127.0.0.1/"$1") 2>/dev/null; }
vm() {     # LOG BOOT(cd|disk)
    stop
    local boot=()
    [ "$2" = cd ] && boot=(-drive if=none,id=cd,format=raw,readonly=on,file="$iso" -device ide-cd,drive=cd,bootindex=0)
    rm -f "$1" serial.sock
    qemu-system-x86_64 -enable-kvm -machine q35 -cpu host -smp 2 -m 2048 \
        -drive if=pflash,format=raw,readonly=on,file="$OVMF/OVMF_CODE.fd" -drive if=pflash,format=raw,file=vars.fd \
        "${boot[@]}" -drive if=none,id=disk,format=qcow2,file=disk.qcow2 -device virtio-blk-pci,drive=disk,bootindex=1 \
        -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$P_INIT-:2222,hostfwd=tcp:127.0.0.1:$P_SSH-:$P_SSH \
        -display none -chardev socket,id=s0,path=serial.sock,server=on,wait=off,logfile="$1" -serial chardev:s0 \
        -daemonize -pidfile qemu.pid
}

# ---- a network whose master is this VM -----------------------------------------------------------
rm -rf fleet; mkdir -p fleet
cat > answers.json <<EOF
{"domain": "home", "timezone": "UTC", "keyboard": "us", "locale": "en_US.UTF-8", "master_where": "elsewhere",
 "master_name": "tart", "master_address": "203.0.113.7", "exit": "direct", "lan_subnet": "10.0.2.0/24", "lan_gateway": "10.0.2.2",
 "nodes": []}
EOF
"${ks[@]}" init fleet/fleet.yaml --answers answers.json --no-store >init.log 2>&1; check $? "a network made (init)"
python3 - fleet/fleet.yaml "$P_SSH" <<'EOF'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
h = d["hosts"]["tart"]
assert h["debian"] == {"encrypt": True} and h["unlock"] == {"ssh": True}, (h.get("debian"), h.get("unlock"))
h["admin"] = {"ssh_port": int(sys.argv[2])}
h["install"] = {"on_done": "poweroff"}
h["master"]["vpn-server"]["wg_host"] = "127.0.0.1"   # this VM, through QEMU's forwarded ports
h["disk"] = "/dev/vda"
open(sys.argv[1], "w").write(yaml.safe_dump(d, sort_keys=False))
EOF
check $? "the master is written encrypted, its key given over SSH in its initramfs"
"${ks[@]}" build fleet/fleet.yaml tart >build.log 2>&1; check $? "its install media built"
iso=$(find fleet -name "tart*.iso" | head -1)
[ -s "$iso" ] || { say "FAIL no ISO ($(tail -3 build.log))"; exit 1; }

# ---- install, then the first start waits -----------------------------------------------------------
rm -f disk.qcow2 vars.fd; qemu-img create -q -f qcow2 disk.qcow2 20G; cp "$OVMF/OVMF_VARS.fd" vars.fd
t0=$(date +%s)
vm install.log cd
for _ in $(seq 360); do kill -0 "$(cat qemu.pid 2>/dev/null)" 2>/dev/null || break; sleep 5; done
! kill -0 "$(cat qemu.pid 2>/dev/null)" 2>/dev/null; check $? "installed and powered off ($(( $(date +%s) - t0 )) s)"
vm first.log disk
n=300; while [ "$n" -gt 0 ] && ! answers "$P_INIT"; do sleep 5; n=$((n - 5)); done
answers "$P_INIT" && ! answers "$P_SSH"; check $? "its first start waits: its initramfs answers, its system not"

# ---- next gives it its key -------------------------------------------------------------------------
"${ks[@]}" next fleet/fleet.yaml --yes >next.log 2>&1
grep -E "one-time key|starts now" next.log | sed 's/^/    /'
grep -q "starts now" next.log; check $? "next sees it waiting and gives it the one-time key"
n=300; while [ "$n" -gt 0 ] && ! answers "$P_SSH"; do sleep 5; n=$((n - 5)); done
answers "$P_SSH"; check $? "its system starts"
out=$(ssh -q -p "$P_SSH" -i fleet/secrets/admin_ed25519 -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 core@127.0.0.1 'lsblk -rno TYPE,MOUNTPOINT | grep -c "^crypt"' 2>&1)
[ "${out:-0}" -ge 1 ] 2>/dev/null; check $? "its root is encrypted (crypt devices: ${out:-none})"
say "$([ "$fails" = 0 ] && echo "all passed" || echo "$fails failed")"
exit "$fails"
}
