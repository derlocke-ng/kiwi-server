#!/usr/bin/env bash
# An arm64 board, stood in for by the Debian 13 arm64 cloud image in QEMU (emulated, no KVM):
# a login `board` with its own key and sudo, as a board's own image would have. Then
# `kiwi-server adopt` takes it into FLEET as HOST and applies its role. HOST must be in FLEET
# already (target: debian, debian.arch: arm64, network.dhcp: true) and enrolled; the drill
# guard (drills.yaml next to the fleet) must allow it. The VM keeps running in WORKDIR
# (run.sh starts it again); stop it with: pkill -f "qemu-system-aarch64.*$WORKDIR".
#   tests/drills/arm64-vm.sh FLEET HOST [WORKDIR]      PORT=2260 (ssh on localhost)  MEM=3072
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
FLEET=$(realpath "$1"); HOST=$2
W=$(realpath -m "${3:-$HOME/kiwi-vm/$HOST}")
PORT=${PORT:-2260}
MEM=${MEM:-3072}
grep -qE "^allowed:.*\b$HOST\b" "$(dirname "$FLEET")/drills.yaml" 2>/dev/null || { echo "$HOST is not in $(dirname "$FLEET")/drills.yaml allowed:" >&2; exit 2; }
mkdir -p "$W/seed"
cd "$W"
img=debian-13-genericcloud-arm64.qcow2
base=https://cloud.debian.org/images/cloud/trixie/latest
if [ ! -f "$img" ]; then
    curl -fsSL -o SHA512SUMS "$base/SHA512SUMS"
    curl -fL -o "$img" "$base/$img"
    sha512sum -c --ignore-missing SHA512SUMS
fi
[ -f board_ed25519 ] || ssh-keygen -q -t ed25519 -N '' -C board-drill -f board_ed25519
printf 'instance-id: %s-1\nlocal-hostname: board\n' "$HOST" > seed/meta-data
cat > seed/user-data <<EOF
#cloud-config
users:
  - name: board
    groups: [sudo]
    shell: /bin/bash
    sudo: "ALL=(ALL) NOPASSWD:ALL"
    ssh_authorized_keys: ["$(cat board_ed25519.pub)"]
ssh_pwauth: false
EOF
if [ ! -f disk.qcow2 ]; then
    cp "$img" disk.qcow2 && qemu-img resize -q disk.qcow2 20G
    cp /usr/share/AAVMF/AAVMF_CODE.fd code.fd; cp /usr/share/AAVMF/AAVMF_VARS.fd vars.fd
fi
cat > run.sh <<EOF
#!/bin/bash
cd "\$(dirname "\$0")"
python3 -m http.server -d seed -b 127.0.0.1 8642 >/dev/null 2>&1 &
web=\$!
qemu-system-aarch64 -M virt -cpu max -smp 4 -m $MEM -nographic \\
  -drive if=pflash,format=raw,readonly=on,file=code.fd -drive if=pflash,format=raw,file=vars.fd \\
  -drive if=virtio,format=qcow2,file=disk.qcow2 \\
  -netdev user,id=n0,hostfwd=tcp:127.0.0.1:$PORT-:22 -device virtio-net-pci,netdev=n0 \\
  -smbios type=1,serial=ds='nocloud;s=http://10.0.2.2:8642/' > console.log 2>&1 </dev/null
kill \$web
EOF
chmod +x run.sh
pgrep -f "qemu-system-aarch64.*$W" >/dev/null || { setsid ./run.sh >/dev/null 2>&1 & }
echo "waiting for the board's sshd (an emulated boot takes a minute or two)"
for _ in $(seq 60); do
    ssh -i board_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=5 -p "$PORT" board@127.0.0.1 true 2>/dev/null && break
    sleep 10
done
eval "$(ssh-agent -s)" >/dev/null
trap 'ssh-agent -k >/dev/null' EXIT
ssh-add -q board_ed25519
"$ROOT/bin/kiwi-server" adopt "$FLEET" "$HOST" --as "ssh://board@127.0.0.1:$PORT" --new-host-key   # a new VM, a new key
"$ROOT/bin/kiwi-server" status "$FLEET" "$HOST" --ssh "ssh://$(python3 -c 'import sys,yaml; d=yaml.safe_load(open(sys.argv[1])); print(((d["hosts"][sys.argv[2]] or {}).get("admin") or {}).get("user") or d["defaults"]["admin"]["user"])' "$FLEET" "$HOST")@127.0.0.1:$PORT"
