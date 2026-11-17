#!/usr/bin/env bash
# kiwi-server's own install media for a CoreOS machine encrypt its disk (docs/design/encryption.md,
# step 2): a throwaway network of one machine made by `init`, its media built by `build`, installed
# in a local QEMU VM (UEFI, KVM, 4 GB). With an emulated TPM 2.0 the root comes up in LUKS bound to
# the TPM, by itself; without one the install stops and says what to do. Touches no fleet but its
# own (WORKDIR/fleet, keys as plain files: a test) and no machine. One PASS or FAIL line per check.
#
#   tests/drills/iso-encrypt-vm.sh [WORKDIR]       (default ~/kiwi-vm/isoenc)
set -u
{
here=$(cd "$(dirname "$0")/../.." && pwd)
w=${1:-$HOME/kiwi-vm/isoenc}
mkdir -p "$w"; cd "$w" || exit 2
OVMF=/usr/share/edk2/ovmf
PSSH=${PSSH:-18322}
ks=("$here/bin/kiwi-server")
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
fails=0
wait_log() { local n=$3; while [ "$n" -gt 0 ]; do grep -qaE "$2" "$1" 2>/dev/null && return 0; sleep 5; n=$((n - 5)); done; return 1; }
stop() { [ -s qemu.pid ] && { kill "$(cat qemu.pid)" 2>/dev/null; sleep 2; kill -9 "$(cat qemu.pid)" 2>/dev/null; rm -f qemu.pid; }; true; }
tpm_up() { mkdir -p tpm; rm -f tpm/sock; swtpm socket --tpmstate dir="$w/tpm" --ctrl type=unixio,path="$w/tpm/sock" --tpm2 --daemon
           for _ in $(seq 20); do [ -S tpm/sock ] && break; sleep 0.2; done; }
cleanup() { stop; pkill -f "swtpm socket --tpmstate dir=$w/tp[m]" 2>/dev/null; true; }
trap cleanup EXIT
vm() {     # LOG TPM(0|1) BOOT(cd|disk)
    stop
    local tpm=() boot=()
    [ "$2" = 1 ] && { pgrep -f "swtpm socket --tpmstate dir=$w/tp[m]" >/dev/null || tpm_up
                      tpm=(-chardev socket,id=chrtpm,path="$w/tpm/sock" -tpmdev emulator,id=tpm0,chardev=chrtpm -device tpm-tis,tpmdev=tpm0); }
    if [ "$3" = cd ]; then boot=(-drive if=none,id=cd,format=raw,readonly=on,file="$iso" -device ide-cd,drive=cd,bootindex=0); fi
    rm -f "$1" serial.sock
    qemu-system-x86_64 -enable-kvm -machine q35 -cpu host -smp 2 -m 4096 \
        -drive if=pflash,format=raw,readonly=on,file="$OVMF/OVMF_CODE.fd" -drive if=pflash,format=raw,file=vars.fd \
        "${tpm[@]}" "${boot[@]}" \
        -drive if=none,id=disk,format=qcow2,file=disk.qcow2 -device virtio-blk-pci,drive=disk,bootindex=1 \
        -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$PSSH-:22 \
        -display none -chardev socket,id=s0,path=serial.sock,server=on,wait=off,logfile="$1" -serial chardev:s0 \
        -daemonize -pidfile qemu.pid
}
vssh() { ssh -q -p "$PSSH" -i fleet/secrets/admin_ed25519 -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 core@127.0.0.1 "$@"; }

# ---- a network of one, its media ------------------------------------------------------------
rm -rf fleet; mkdir -p fleet
cat > answers.json <<'EOF'
{"domain": "home", "timezone": "UTC", "keyboard": "us", "locale": "en_US.UTF-8", "master_where": "none",
 "lan_subnet": "10.0.2.0/24", "lan_gateway": "10.0.2.2",
 "nodes": [{"name": "kumquat", "preset": "minimal", "address": "10.0.2.15", "target": "coreos"}]}
EOF
"${ks[@]}" init fleet/fleet.yaml --answers answers.json --no-store >init.log 2>&1; check $? "a network of one made (init)"
python3 - fleet/fleet.yaml <<'EOF'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
h = d["hosts"]["kumquat"]
h["install"] = {"on_done": "poweroff"}
h.setdefault("coreos", {})["console"] = "ttyS0,115200n8"
open(sys.argv[1], "w").write(yaml.safe_dump(d, sort_keys=False))
EOF
"${ks[@]}" build fleet/fleet.yaml kumquat >build.log 2>&1; check $? "its install media built"
iso=$(find fleet -name "kumquat*.iso" | head -1)
[ -s "$iso" ] || { say "FAIL no ISO ($(tail -3 build.log))"; exit 1; }
grep -q "tpm_version_major" "$(find fleet -name 'kumquat.pre-install.sh' | head -1)" 2>/dev/null
check $? "the install checks for a TPM first"

# ---- with a TPM: encrypted, by itself ---------------------------------------------------------
rm -rf disk.qcow2 vars.fd tpm; qemu-img create -q -f qcow2 disk.qcow2 20G; cp "$OVMF/OVMF_VARS.fd" vars.fd
t0=$(date +%s)
vm install.log 1 cd
for _ in $(seq 180); do kill -0 "$(cat qemu.pid 2>/dev/null)" 2>/dev/null || break; sleep 5; done
! kill -0 "$(cat qemu.pid 2>/dev/null)" 2>/dev/null; check $? "installed and powered off ($(( $(date +%s) - t0 )) s)"
vm first.log 1 disk
t0=$(date +%s); n=900; while [ "$n" -gt 0 ] && ! vssh true 2>/dev/null; do sleep 5; n=$((n - 5)); done
vssh true; check $? "it starts by itself and answers ($(( $(date +%s) - t0 )) s)"
out=$(vssh 'sudo cryptsetup status root | sed -n "s/^ *type: *//p"; sudo clevis luks list -d /dev/disk/by-partlabel/root')
printf '%s\n' "$out" | sed 's/^/    /'
grep -q '^LUKS2' <<< "$out" && grep -q tpm2 <<< "$out"; check $? "its root is encrypted, the TPM standing in until the first apply"

# ---- without a TPM: no install, and it says why ------------------------------------------------
stop; rm -f disk2.qcow2; qemu-img create -q -f qcow2 disk2.qcow2 20G
mv disk.qcow2 disk.tpm.qcow2; mv disk2.qcow2 disk.qcow2
vm notpm.log 0 cd
wait_log notpm.log "is not installed: its disk cannot be encrypted here" 600
check $? "without a TPM it does not install, and says what to do"
grep -a "cannot be encrypted here" notpm.log | head -1 | sed 's/^/    /'
stop
say "$([ "$fails" = 0 ] && echo "all passed" || echo "$fails failed")"
exit "$fails"
}
