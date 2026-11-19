#!/usr/bin/env bash
# A desktop whose encrypted disk opens with rings (docs/design/unlock.md, desktops): a Debian
# desktop (GNOME) in a local QEMU VM, its root in LUKS, bound with clevis to four test rings that
# run here (podman, 127.0.0.1:7611-7614; the VM reaches them at 10.0.2.2):
#
#   health1, health2   machines that must be up: it boots only when everything runs
#   auto               its auto-unlock ring: up while auto-unlock is on for it
#   emergency          its "Unlock now" ring: up for a while after the button
#
# Policy: (health1 and health2 and auto) or emergency. The passphrase always works too.
# One PASS or FAIL line per check. Touches nothing but WORKDIR and its own containers.
#
#   tests/drills/desktop-clevis-vm.sh [WORKDIR]       (default ~/kiwi-vm/desktop/debian)
set -u
{
here=$(cd "$(dirname "$0")/../.." && pwd)
w=${1:-$HOME/kiwi-vm/desktop/debian}
mkdir -p "$w"; cd "$w" || exit 2
OVMF=/usr/share/edk2/ovmf
PSSH=${PSSH:-18341}
PASS_PHRASE=kiwitest          # a test value, typed at the VM's console
IMAGE=ghcr.io/derlocke-ng/tang:15-kiwi2
declare -A PORT=([health1]=7611 [health2]=7612 [auto]=7613 [emergency]=7614)
ks=("$here/bin/kiwi-server")
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
fails=0
wait_log() { local n=$3; while [ "$n" -gt 0 ]; do grep -qaE "$2" "$1" 2>/dev/null && return 0; sleep 5; n=$((n - 5)); done; return 1; }
stop() { [ -s qemu.pid ] && { kill "$(cat qemu.pid)" 2>/dev/null; sleep 3; kill -9 "$(cat qemu.pid)" 2>/dev/null; rm -f qemu.pid; }; true; }
ring() {   # NAME up|down
    local c=kiwi-desk-$1
    if [ "$2" = up ]; then
        if ! podman container exists "$c"; then
            mkdir -p "rings/$1"
            [ -n "$(ls -A "rings/$1")" ] || podman run --rm --security-opt label=disable -v "$w/rings/$1:/db" \
                --entrypoint /bin/sh "$IMAGE" -c "/usr/libexec/tangd-keygen /db && chown -R 0:0 /db" >/dev/null
            podman run -d --name "$c" --security-opt label=disable -p "127.0.0.1:${PORT[$1]}:7500" -v "$w/rings/$1:/db" \
                --entrypoint /usr/libexec/tangd "$IMAGE" -l -p 7500 /db >/dev/null
        else
            podman start "$c" >/dev/null
        fi
        for _ in $(seq 40); do curl -sf "http://127.0.0.1:${PORT[$1]}/adv" >/dev/null && return 0; sleep 0.25; done
        return 1
    fi
    podman container exists "$c" && podman stop -t 2 "$c" >/dev/null; true
}
rings() { local r; for r in health1 health2 auto emergency; do
              if [[ " $* " == *" $r "* ]]; then ring "$r" up; else ring "$r" down; fi; done; }
cleanup() { stop; local r; for r in health1 health2 auto emergency; do podman rm -f "kiwi-desk-$r" >/dev/null 2>&1; done; true; }
trap cleanup EXIT
vm() {     # LOG BOOT(cd|disk)
    stop
    local boot=()
    [ "$2" = cd ] && boot=(-drive if=none,id=cd,format=raw,readonly=on,file="$iso" -device ide-cd,drive=cd,bootindex=0)
    rm -f "$1" serial.sock
    qemu-system-x86_64 -enable-kvm -machine q35 -cpu host -smp 2 -m 4096 \
        -drive if=pflash,format=raw,readonly=on,file="$OVMF/OVMF_CODE.fd" -drive if=pflash,format=raw,file=vars.fd \
        "${boot[@]}" \
        -drive if=none,id=disk,format=qcow2,file=disk.qcow2 -device virtio-blk-pci,drive=disk,bootindex=1 \
        -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$PSSH-:22 \
        -display none -chardev socket,id=s0,path=serial.sock,server=on,wait=off,logfile="$1" -serial chardev:s0 \
        -daemonize -pidfile qemu.pid
}
type_at_console() { python3 - "$w/serial.sock" "$1" <<'EOF'
import socket, sys, time
s = socket.socket(socket.AF_UNIX); s.connect(sys.argv[1])
for ch in sys.argv[2] + "\r":
    s.send(ch.encode()); time.sleep(0.05)
time.sleep(1); s.close()
EOF
}
vssh() { ssh -q -p "$PSSH" -i fleet/secrets/admin_ed25519 -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 "$admin@127.0.0.1" "$@"; }
up_within() { local n=$1; while [ "$n" -gt 0 ]; do vssh true 2>/dev/null && return 0; sleep 5; n=$((n - 5)); done; return 1; }

# ---- the desktop's install media: kiwi-server's Debian, encrypted, with GNOME and clevis ------
if [ ! -s disk.qcow2.installed ]; then
    rm -rf fleet; mkdir -p fleet
    cat > answers.json <<'EOF'
{"domain": "home", "timezone": "UTC", "keyboard": "us", "locale": "en_US.UTF-8", "master_where": "none",
 "lan_subnet": "10.0.2.0/24", "lan_gateway": "10.0.2.2",
 "nodes": [{"name": "desk", "preset": "minimal", "address": "10.0.2.15", "target": "debian"}]}
EOF
    "${ks[@]}" init fleet/fleet.yaml --answers answers.json --no-store >init.log 2>&1; check $? "a network of one made (init)"
    python3 - fleet/fleet.yaml "$PASS_PHRASE" <<'EOF'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
h = d["hosts"]["desk"]
h["role"] = "bare"
h.pop("node", None)
h["network"] = {"dhcp": True}
h["install"] = {"on_done": "poweroff"}
h["debian"] = {"encrypt": True, "passphrase": sys.argv[2],
               "packages": ["task-gnome-desktop", "clevis", "clevis-luks", "clevis-initramfs"],
               "kernel_arguments": ["ip=dhcp", "console=tty0", "console=ttyS0,115200n8"]}
open(sys.argv[1], "w").write(yaml.safe_dump(d, sort_keys=False))
EOF
    "${ks[@]}" build fleet/fleet.yaml desk >build.log 2>&1; check $? "its install media built"
    iso=$(find fleet -name "desk*.iso" | head -1)
    [ -n "$iso" ] || { say "FAIL no ISO: see build.log"; exit 1; }
    rm -f disk.qcow2; qemu-img create -f qcow2 disk.qcow2 40G >/dev/null
    cp "$OVMF/OVMF_VARS.fd" vars.fd
    say "installing (Debian with GNOME over the net: up to an hour)"
    vm install.log cd
    n=5400; while [ -s qemu.pid ] && kill -0 "$(cat qemu.pid)" 2>/dev/null && [ "$n" -gt 0 ]; do sleep 15; n=$((n - 15)); done
    [ "$n" -gt 0 ]; check $? "installed and powered off by itself"
    rm -f qemu.pid
    touch disk.qcow2.installed
fi
admin=$(python3 -c "import yaml,sys; print(yaml.safe_load(open('fleet/fleet.yaml'))['defaults']['admin']['user'])")

# ---- first boot: the passphrase at its console, then clevis bound to the policy -----------------
rings health1 health2 auto
if [ ! -s bound ]; then
    vm boot1.log disk
    wait_log boot1.log "Please unlock disk|Enter passphrase" 300; check $? "it asks for its passphrase at boot (not bound yet)"
    type_at_console "$PASS_PHRASE"
    up_within 300; check $? "the passphrase opens it"
    dev=$(vssh "sudo blkid -t TYPE=crypto_LUKS -o device" | head -1)
    policy='{"t":1,"pins":{"sss":[{"t":3,"pins":{"tang":[{"url":"http://10.0.2.2:7611"},{"url":"http://10.0.2.2:7612"},{"url":"http://10.0.2.2:7613"}]}}],"tang":[{"url":"http://10.0.2.2:7614"}]}}'
    ring emergency up                      # binding asks every ring for its key once
    printf '%s' "$PASS_PHRASE" | vssh "sudo clevis luks bind -y -k - -d $dev sss '$policy'" >bind.log 2>&1
    check $? "bound: (health1 and health2 and auto) or emergency"
    vssh "sudo update-initramfs -u -k all" >>bind.log 2>&1; check $? "its boot system carries clevis"
    echo "$dev" > bound
    vssh "sudo systemctl poweroff" 2>/dev/null; sleep 20; stop
fi

boot() {   # NAME RINGS... ; the VM from its disk with exactly these rings up
    local name=$1; shift
    rings "$@"
    vm "$name.log" disk
}
# 1. everything runs and auto-unlock is on: it opens by itself
boot s1 health1 health2 auto
t0=$(date +%s); up_within 300; check $? "1. all health rings and auto up: opens by itself ($(( $(date +%s) - t0 )) s)"

# 2. auto-unlock off: it waits; Unlock now (the emergency ring) opens it
boot s2 health1 health2
wait_log s2.log "Please unlock disk|Enter passphrase" 300 && ! up_within 90; check $? "2. auto-unlock off: it waits at its prompt"
ring emergency up; t0=$(date +%s)
up_within 180; check $? "2. Unlock now (emergency ring up): opens a waiting desktop ($(( $(date +%s) - t0 )) s)"

# 3. a health ring is down: it waits even with auto on; Unlock now still opens it
boot s3 health1 auto
wait_log s3.log "Please unlock disk|Enter passphrase" 300 && ! up_within 90; check $? "3. health2 down: it waits although auto-unlock is on"
ring emergency up; t0=$(date +%s)
up_within 180; check $? "3. Unlock now opens it anyway ($(( $(date +%s) - t0 )) s)"

# 4. no ring at all: the passphrase opens it
boot s4
wait_log s4.log "Please unlock disk|Enter passphrase" 300 && ! up_within 60; check $? "4. no ring: it waits"
type_at_console "$PASS_PHRASE"
up_within 180; check $? "4. the passphrase still opens it"
vssh "systemctl is-active gdm" 2>/dev/null | grep -q active; check $? "4. it is a desktop (gdm runs)"

say "$fails wrong"
exit $((fails > 0))
} 2>&1
