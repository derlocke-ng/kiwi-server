#!/usr/bin/env bash
# The coreos-ram target in a local QEMU VM; no machine of any fleet is touched. A throwaway
# fleet and admin key in WORKDIR, build, boot on a blank disk, apply, reboot, then a new image
# with another setting on the same disk. Needs qemu-kvm, OVMF, the build tools or the toolchain
# image, and the internet for the container images. Takes a few minutes.
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
mkdir -p "$W/keys"
cd "$W"

say() { echo "$(date +%T) $*"; }
fail() { say "FAILED: $*"; exit 1; }

[[ -f keys/admin ]] || ssh-keygen -q -t ed25519 -N "" -C kiwi-ram-drill -f keys/admin
fleet() { # KEEP_LOGS
    cat > fleet.yaml <<EOF
# a throwaway fleet for tests/drills/ram-master-vm.sh
defaults:
  target: coreos-ram
  role: bare
  domain: home
  disk: /dev/vda
  admin: { user: core, ssh_keys: [keys/admin.pub] }
  backup: { hosts: [] }
  ram: { copy_to_ram: $COPY_TO_RAM, keep_logs: $1 }
hosts:
  trifle:
    role: master
    master:
      modules: [vpn-server, dns, reverse-proxy]
      vpn-server: { wg_host: 127.0.0.1 }
      dns: { mullvad_socks: false }
EOF
}

stop_vm() {
    [[ -s qemu.pid ]] || return 0
    kill "$(< qemu.pid)" 2>/dev/null || true
    rm -f qemu.pid
    sleep 2
}
trap stop_vm EXIT

boot() {
    stop_vm
    [[ -f vars.fd ]] || cp "$OVMF/OVMF_VARS.fd" vars.fd
    qemu-system-x86_64 -enable-kvm -machine q35 -cpu host -smp 2 -m "$MEM" \
        -drive if=pflash,format=raw,readonly=on,file="$OVMF/OVMF_CODE.fd" -drive if=pflash,format=raw,file=vars.fd \
        -drive file=out/trifle/trifle.iso,media=cdrom,readonly=on,if=none,id=cd -device ide-cd,drive=cd,bootindex=0 \
        -drive file=disk.qcow2,if=virtio,format=qcow2 \
        -netdev user,id=n0,hostfwd=tcp:127.0.0.1:"$PORT"-:22 -device virtio-net-pci,netdev=n0 \
        -display none -monitor none -daemonize -pidfile qemu.pid
}

vm() { # COMMAND: as root on the VM, its host key checked against the fleet's known_hosts
    ssh -o BatchMode=yes -o ConnectTimeout=5 -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
        -o UserKnownHostsFile="$W/secrets/known_hosts" -i keys/admin -p "$PORT" core@127.0.0.1 "sudo sh -c '$1'"
}

wait_role() { # until kiwi-role.service has run this boot: the seconds since START
    local start=$1 st i
    for ((i = 0; i < 120; i++)); do
        st=$(vm "systemctl is-active kiwi-role" 2>/dev/null) || st=
        if [[ $st == active ]]; then echo $(( $(date +%s) - start )); return 0; fi
        if [[ $st == failed ]]; then fail "kiwi-role failed: journalctl -u kiwi-role"; fi
        sleep 5
    done
    fail "kiwi-role did not run within ten minutes"
}

wg_key() { vm "grep -o \"\\\"publicKey\\\": *\\\"[^\\\"]*\\\"\" /home/user/docker/km-vpn-server/wg0.json | head -1"; }

check_up() {
    vm "kiwi-stack verify" >/dev/null || fail "the stack is not up"
    [[ -z $(vm "systemctl --failed --no-legend") ]] || fail "failed units: $(vm "systemctl --failed --no-legend")"
}

fleet false
say "build ($COPY_TO_RAM copy to RAM, $MEM MiB)"
"$KS" build fleet.yaml trifle --no-backup -o out >/dev/null
rm -f disk.qcow2 secrets/known_hosts
[[ -f secrets/ssh_ca ]] || "$KS" sshca fleet.yaml init >/dev/null
qemu-img create -q -f qcow2 disk.qcow2 10G

say "boot 1: a blank disk"
boot
t=$(date +%s)
for ((i = 0; i < 60; i++)); do vm true 2>/dev/null && break; sleep 5; done
vm true >/dev/null || fail "no ssh after five minutes"
say "boot 1: ssh after $(( $(date +%s) - t )) s; $(vm "bash /etc/kiwi-ram/state.sh status" | head -1)"
"$KS" apply fleet.yaml trifle --ssh "ssh://core@127.0.0.1:$PORT" > apply.log 2>&1 || fail "apply: $W/apply.log"
check_up
key=$(wg_key)
say "apply: $(grep -m1 "kiwi-state:" apply.log); $(vm "free -m" | awk '/^Mem/ {print $3 " MB used"}')"

say "boot 2: a reboot"
vm "touch /etc/kiwi-drill-marker"
vm "systemctl reboot" 2>/dev/null || true
sleep 20
t=$(( $(date +%s) - 20 ))
s=$(wait_role "$t")
check_up
vm "test ! -e /etc/kiwi-drill-marker" || fail "/etc kept a file across the reboot"
vm "test -s /etc/ssh/ssh_host_ed25519_key-cert.pub -a -s /etc/ssh/sshd_config.d/40-kiwi-ca.conf" ||
    fail "the SSH CA's host certificate did not survive the reboot"
[[ $(wg_key) == "$key" ]] || fail "the WireGuard key changed"
say "boot 2: stack up $s s after the reboot; same host key and certificate, same WireGuard key, /etc fresh; $(vm "free -m" | awk '/^Mem/ {print $3 " MB used"}')"

say "boot 3: a new image (keep_logs on) on the same disk"
vm "systemctl poweroff" 2>/dev/null || true
sleep 10
fleet true
"$KS" build fleet.yaml trifle --no-backup -o out >/dev/null
boot
t=$(date +%s)
sleep 20
s=$(wait_role "$t")
check_up
[[ $(wg_key) == "$key" ]] || fail "the WireGuard key changed with the image"
vm "mountpoint -q /var/log/journal" || fail "keep_logs: the journal is not on the partition"
say "boot 3: stack up $s s after boot; same keys; the journal on the partition"
say "passed ($W)"
