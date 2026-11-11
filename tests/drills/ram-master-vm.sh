#!/usr/bin/env bash
# The coreos-ram target in a local QEMU VM with no disk at all; no machine of any fleet is
# touched. A throwaway fleet, admin key and SSH CA in WORKDIR: build, boot, `watch` sends the
# role, reboot, `watch` sends it again, and the master comes back as itself. Needs qemu-kvm,
# OVMF, the build tools or the toolchain image, and the internet for the container images.
# Takes a few minutes.
#   tests/drills/ram-master-vm.sh [WORKDIR]
#   MEM=2048 (MiB)  COPY_TO_RAM=false (true needs MEM=3072 or more)  PORT=2222 (ssh on localhost)
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
KS=$ROOT/bin/kiwi-server
W=$(realpath -m "${1:-$(mktemp -d -t kiwi-ram-drill-XXXX)}")
MEM=${MEM:-2048}
PORT=${PORT:-2222}
COPY_TO_RAM=${COPY_TO_RAM:-false}
OVMF=/usr/share/edk2/ovmf
TGT=ssh://core@127.0.0.1:$PORT
mkdir -p "$W/keys"
cd "$W"

say() { echo "$(date +%T) $*"; }
fail() { say "FAILED: $*"; exit 1; }

[[ -f keys/admin ]] || ssh-keygen -q -t ed25519 -N "" -C kiwi-ram-drill -f keys/admin
cat > fleet.yaml <<EOF
# a throwaway fleet for tests/drills/ram-master-vm.sh
defaults:
  target: coreos-ram
  role: bare
  domain: home
  admin: { user: core, ssh_keys: [keys/admin.pub] }
  backup: { hosts: [] }
  ram: { copy_to_ram: $COPY_TO_RAM }
hosts:
  trifle:
    role: master
    master:
      modules: [vpn-server, dns, reverse-proxy]
      vpn-server: { wg_host: 127.0.0.1 }
      dns: { mullvad_socks: false }
EOF

stop_vm() {
    [[ -s qemu.pid ]] || return 0
    kill "$(< qemu.pid)" 2>/dev/null || true
    rm -f qemu.pid
    sleep 2
}
trap stop_vm EXIT

boot() { # no disk: what it keeps, it keeps in RAM
    stop_vm
    [[ -f vars.fd ]] || cp "$OVMF/OVMF_VARS.fd" vars.fd
    qemu-system-x86_64 -enable-kvm -machine q35 -cpu host -smp 2 -m "$MEM" \
        -drive if=pflash,format=raw,readonly=on,file="$OVMF/OVMF_CODE.fd" -drive if=pflash,format=raw,file=vars.fd \
        -drive file=out/trifle/trifle.iso,media=cdrom,readonly=on,if=none,id=cd -device ide-cd,drive=cd,bootindex=0 \
        -netdev user,id=n0,hostfwd=tcp:127.0.0.1:"$PORT"-:22 -device virtio-net-pci,netdev=n0 \
        -display none -monitor none -daemonize -pidfile qemu.pid
}

vm() { # COMMAND as root on the VM; it must show the host key the image carries
    ssh -o BatchMode=yes -o ConnectTimeout=5 -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
        -o UserKnownHostsFile="$W/image_known_hosts" -i keys/admin -p "$PORT" core@127.0.0.1 "sudo sh -c '$1'"
}

up_after() { # START: until the master answers, then `watch --once` as the timer would, until it is ready
    local start=$1 i
    for ((i = 0; i < 60; i++)); do vm true 2>/dev/null && break; sleep 5; done
    vm true >/dev/null || fail "no ssh with the image's host key after five minutes"
    for ((i = 0; i < 30; i++)); do
        "$KS" watch fleet.yaml trifle --once --ssh "$TGT" >> watch.log 2>&1 || true
        if vm "test -f /var/lib/kiwi-server/role.done" 2>/dev/null; then echo $(( $(date +%s) - start )); return 0; fi
        sleep 10
    done
    fail "watch did not bring the master up: $W/watch.log"
}

wg_key() { vm "grep -o \"\\\"publicKey\\\": *\\\"[^\\\"]*\\\"\" /home/user/docker/km-vpn-server/wg0.json | head -1"; }

check_up() {
    vm "kiwi-stack verify" >/dev/null || fail "the stack is not up"
    [[ -z $(vm "systemctl --failed --no-legend") ]] || fail "failed units: $(vm "systemctl --failed --no-legend")"
    [[ -z $(vm "lsblk -dno NAME,TYPE | grep -w disk") ]] || fail "the VM has a disk"
}

[[ -f secrets/ssh_ca ]] || "$KS" sshca fleet.yaml init >/dev/null
say "build ($COPY_TO_RAM copy to RAM, $MEM MiB, no disk)"
rm -f secrets/known_hosts watch.log
"$KS" build fleet.yaml trifle --no-backup -o out >/dev/null
echo "[127.0.0.1]:$PORT $(cut -d' ' -f1,2 secrets/ram/trifle/ssh_host_ed25519_key.pub)" > image_known_hosts

say "boot 1"
boot
s=$(up_after "$(date +%s)")
check_up
vm "test -s /etc/ssh/ssh_host_ed25519_key-cert.pub" || fail "the image shows no host certificate"
"$KS" watch fleet.yaml trifle --once --ssh "$TGT" >> watch.log 2>&1      # a running master: nothing to send
key=$(wg_key)
[[ -s secrets/wg-easy/trifle.json ]] || fail "the fleet keeps no copy of the WireGuard identity"
say "boot 1: up $s s after boot (watch sent the role); $(vm "free -m" | awk '/^Mem/ {print $3 " MB used"}')"

say "boot 2: a reboot"
vm "touch /etc/kiwi-drill-marker"
vm "systemctl reboot" 2>/dev/null || true
t=$(date +%s)
sleep 15
s=$(up_after "$t")
check_up
vm "test ! -e /etc/kiwi-drill-marker" || fail "/etc kept a file across the reboot"
[[ $(wg_key) == "$key" ]] || fail "the WireGuard key changed"
say "boot 2: up $s s after the reboot; same host key, same WireGuard key, /etc fresh; $(vm "free -m" | awk '/^Mem/ {print $3 " MB used"}')"
say "passed ($W)"
