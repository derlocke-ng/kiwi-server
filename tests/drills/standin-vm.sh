#!/usr/bin/env bash
# Can a CoreOS root be encrypted before any ring exists (docs/design/encryption.md)? A local QEMU
# VM (UEFI, KVM, 4 GB, an emulated TPM 2.0): CoreOS installed with its root in LUKS bound to the
# machine's TPM (the stand-in), then what a first apply would do (unlock.bind: a console key from the bound slot, a
# ring bound, the stand-in removed, rd.neednet), a boot that opens with the ring, and a boot with
# the ring off where the console key is typed. Touches no fleet and no machine. One PASS or FAIL
# line per check.
#
#   tests/drills/standin-vm.sh [WORKDIR]       (default ~/kiwi-vm/standin; stop: kill $(cat WORKDIR/qemu.pid))
#
# STANDIN=tpm (the default): the stand-in is the machine's TPM.
# STANDIN=dead: for a machine without a TPM, a ring whose keys are thrown away at once. The first
#   start opens the root with Ignition's own key; the take-in is to use that key (where Ignition
#   left it) to add the console key and bind the rings.
#
# Tried first, 16 November 2026: Clevis's `null` pin as the stand-in. CoreOS's initramfs has only
# the sss, tang and tpm2 pins (`clevis encrypt null` is invalid there), so Ignition fails.
# Base ISO: $KIWI_STARTER_BASE, else the newest Fedora CoreOS live ISO in ~/.cache/kiwi-server.
# Ports on 127.0.0.1: $PSSH (18222) to the VM's SSH, $PTANG (18000) the ring (a Tang container).
set -u
# one compound command: bash reads all of it before it runs any, so a change to this file
# during a run cannot make it run a VM's lines here
{
here=$(cd "$(dirname "$0")/../.." && pwd)
w=${1:-$HOME/kiwi-vm/standin}
mkdir -p "$w"; cd "$w" || exit 2
OVMF=/usr/share/edk2/ovmf
PSSH=${PSSH:-18222}; PTANG=${PTANG:-18000}
base=${KIWI_STARTER_BASE:-$(ls -1 "$HOME"/.cache/kiwi-server/fedora-coreos-*-live-iso.x86_64.iso 2>/dev/null | sort -V | tail -1)}
tools=localhost/kiwi-server-toolchain:latest
tang_image=ghcr.io/derlocke-ng/tang:15-kiwi3
tang=kiwi-drill-tang-standin
standin=${STANDIN:-tpm}
control_image=ghcr.io/derlocke-ng/kiwi-server-control:2

say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
fails=0
wait_log() { local n=$3; while [ "$n" -gt 0 ]; do grep -qaE "$2" "$1" 2>/dev/null && return 0; sleep 5; n=$((n - 5)); done; return 1; }
vssh() { ssh -q -p "$PSSH" -i drill-key -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 core@127.0.0.1 "$@"; }
wait_ssh() { local n=$1; while [ "$n" -gt 0 ]; do vssh true 2>/dev/null && return 0; sleep 5; n=$((n - 5)); done; return 1; }
tpm() {    # the emulated TPM 2.0: its state stays in tpm/ across starts, as a chip's would
    mkdir -p tpm
    [ -S tpm/sock ] && pgrep -f "swtpm socket --tpmstate dir=$w/tpm" >/dev/null && return 0
    rm -f tpm/sock
    swtpm socket --tpmstate dir="$w/tpm" --ctrl type=unixio,path="$w/tpm/sock" --tpm2 --daemon
    for _ in $(seq 20); do [ -S tpm/sock ] && break; sleep 0.2; done
}
stop() {
    [ -s qemu.pid ] || return 0
    local pid; pid=$(< qemu.pid)
    kill "$pid" 2>/dev/null
    for _ in $(seq 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
    kill -9 "$pid" 2>/dev/null; rm -f qemu.pid
}
vm() {   # LOG: start the VM (disk first, the install ISO second)
    stop
    tpm
    rm -f "$1" serial.sock
    qemu-system-x86_64 -enable-kvm -machine q35 -cpu host -smp 2 -m 4096 \
        -drive if=pflash,format=raw,readonly=on,file="$OVMF/OVMF_CODE.fd" -drive if=pflash,format=raw,file=vars.fd \
        -drive if=none,id=disk,format=qcow2,file=disk.qcow2 -device virtio-blk-pci,drive=disk,bootindex=0 \
        -drive if=none,id=cd,format=raw,readonly=on,file=auto.iso -device ide-cd,drive=cd,bootindex=1 \
        -chardev socket,id=chrtpm,path="$w/tpm/sock" -tpmdev emulator,id=tpm0,chardev=chrtpm -device tpm-tis,tpmdev=tpm0 \
        -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$PSSH-:22 \
        -display none -chardev socket,id=s0,path=serial.sock,server=on,wait=off,logfile="$1" -serial chardev:s0 \
        -daemonize -pidfile qemu.pid
}
type_serial() {     # TEXT: typed on the serial console, Enter at the end
    python3 - "$w/serial.sock" "$1" <<'EOF'
import socket, sys, time
s = socket.socket(socket.AF_UNIX); s.connect(sys.argv[1])
for ch in sys.argv[2] + "\r":
    s.sendall(ch.encode()); time.sleep(0.05)
EOF
}
cleanup() { stop; pkill -f "swtpm socket --tpmstate dir=$w/tpm" 2>/dev/null; podman rm -f "$tang" "$tang-dead" >/dev/null 2>&1; }
trap cleanup EXIT

# ---- the install media: root in LUKS, bound to the stand-in only ----------------------------
rm -f drill-key drill-key.pub; ssh-keygen -q -t ed25519 -N '' -C kiwi-drill -f drill-key
if [ "$standin" = dead ]; then
    # a ring's advertisement whose keys are gone before the machine is installed: it binds, nobody opens
    rm -rf dead-db; mkdir -m 0700 dead-db
    podman run --rm --security-opt label=disable -v "$w/dead-db":/db --entrypoint /bin/sh "$tang_image" \
        -c "/usr/libexec/tangd-keygen /db && chown -R 0:0 /db"
    podman rm -f "$tang-dead" >/dev/null 2>&1
    podman run -d --name "$tang-dead" --security-opt label=disable -v "$w/dead-db":/db -p 127.0.0.1:$((PTANG + 1)):7500 \
        --entrypoint /usr/libexec/tangd "$tang_image" -l -p 7500 /db >/dev/null
    rm -f dead.adv
    for _ in $(seq 20); do curl -sf "http://127.0.0.1:$((PTANG + 1))/adv" -o dead.adv && break; sleep 1; done
    thp=$(podman run --rm --security-opt label=disable -v "$w/dead-db":/db:ro --entrypoint /bin/sh "$control_image" -c \
        'for k in /db/*.jwk; do [ "$(jose fmt -j "$k" -g alg -u-)" = ES512 ] && jose jwk thp -i "$k"; done')
    podman rm -f "$tang-dead" >/dev/null; rm -rf dead-db
    [ -s dead.adv ] && [ -n "$thp" ]; check $? "a ring made and its keys thrown away (thumbprint $thp)"
    luks="    tang:
      - url: http://10.0.2.2:9
        thumbprint: $thp
        advertisement: '$(cat dead.adv)'
storage:
  disks:   # Ignition sizes the root: growing it at the first start would need the ring
    - device: /dev/disk/by-id/coreos-boot-disk
      partitions:
        - label: root
          number: 4
          size_mib: 0
          resize: true"
else
    luks="    tpm2: true"
fi
printf '%s\n' "variant: fcos" "version: 1.5.0" "passwd:" "  users:" "    - name: core" \
    "      ssh_authorized_keys: [\"$(cat drill-key.pub)\"]" "boot_device:" "  luks:" "$luks" > config.bu
rm -f config.ign auto.iso
podman run --rm -v "$w":/w:z -w /w --entrypoint butane "$tools" --strict -o config.ign config.bu
check $? "butane takes a root bound to the stand-in ($standin)"
podman run --rm -v "$w":/w:z -v "$(dirname "$base")":/b:ro,z -w /w --entrypoint coreos-installer "$tools" \
    iso customize --dest-device /dev/vda --dest-ignition config.ign --dest-console ttyS0,115200n8 \
    --live-karg-append console=ttyS0,115200n8 -o auto.iso "/b/$(basename "$base")"
check $? "install media made from $(basename "$base")"
[ -s auto.iso ] || exit 1

# ---- install and first boot ---------------------------------------------------------------
rm -rf disk.qcow2 vars.fd tpm; qemu-img create -q -f qcow2 disk.qcow2 12G; cp "$OVMF/OVMF_VARS.fd" vars.fd
t0=$(date +%s)
vm serial-1.log
wait_ssh 900; check $? "installed and up, nobody at it ($(( $(date +%s) - t0 )) s)"
dev=/dev/disk/by-partlabel/root
out=$(vssh "sudo cryptsetup status root | sed -n 's/^ *type: *//p'; sudo clevis luks list -d $dev; sudo ls -l /etc/luks 2>&1 | tail -n +2")
say "$out"
if [ "$standin" = dead ]; then
    grep -q '^LUKS2' <<< "$out" && grep -q 'tang' <<< "$out" && ! grep -q tpm2 <<< "$out"; check $? "the root is LUKS2, bound to the dead ring only"
else
    grep -q '^LUKS2' <<< "$out" && grep -q 'tpm2' <<< "$out" && ! grep -q tang <<< "$out"; check $? "the root is LUKS2, bound to the TPM only"
fi

# ---- the ring, and what a first apply does --------------------------------------------------
podman rm -f "$tang" >/dev/null 2>&1
rm -rf tang-db; mkdir -m 0700 tang-db     # as the operator ring makes its keys (unlock.OperatorRing)
podman run --rm --security-opt label=disable -v "$w/tang-db":/db --entrypoint /bin/sh "$tang_image" \
    -c "/usr/libexec/tangd-keygen /db && chown -R 0:0 /db"
podman run -d --name "$tang" --security-opt label=disable -v "$w/tang-db":/db -p 127.0.0.1:$PTANG:7500 \
    --entrypoint /usr/libexec/tangd "$tang_image" -l -p 7500 /db >/dev/null
for _ in $(seq 20); do curl -sf "http://127.0.0.1:$PTANG/adv" >/dev/null && break; sleep 1; done
curl -sf "http://127.0.0.1:$PTANG/adv" >/dev/null; check $? "the ring answers on $PTANG"
head -c 18 /dev/urandom | base64 | tr '+/' 'xy' > console.key      # a test value of this VM only
out=$(vssh "sudo bash -s" <<EOF
set -e
d=\$(mktemp -d); trap 'rm -rf \$d' EXIT
printf %s '$(cat console.key)' > \$d/new
slot=\$(clevis luks list -d $dev | sed -n 's/^\([0-9]*\):.*/\1/p' | head -1)
if [ -s /etc/luks/root ]; then cp /etc/luks/root \$d/old; echo "the key: Ignition's, in /etc/luks/root"
else clevis luks pass -d $dev -s \$slot > \$d/old; echo "the key: from the stand-in's slot"; fi
cryptsetup luksAddKey --batch-mode --key-file=\$d/old $dev \$d/new
clevis luks bind -y -k \$d/new -d $dev sss '{"t":1,"pins":{"tang":[{"url":"http://10.0.2.2:$PTANG"}]}}'
clevis luks unbind -f -d $dev -s \$slot </dev/null    # stdin is this script: unbind would read it as a key
rpm-ostree kargs --append=rd.neednet=1 >/dev/null </dev/null
echo bound; clevis luks list -d $dev
EOF
)
say "$out"
grep -q '^bound' <<< "$out" && grep -q "10.0.2.2:$PTANG" <<< "$out" && ! grep -qE 'tpm2|10.0.2.2:9"' <<< "$out"
check $? "rebound: the ring and the console key, the stand-in gone"

# ---- a boot with the ring ----------------------------------------------------------------
boot=$(vssh cat /proc/sys/kernel/random/boot_id)
vssh "sudo systemctl reboot" 2>/dev/null
t0=$(date +%s); n=600; now=""
while [ "$n" -gt 0 ]; do now=$(vssh cat /proc/sys/kernel/random/boot_id 2>/dev/null); [ -n "$now" ] && [ "$now" != "$boot" ] && break; sleep 5; n=$((n - 5)); done
[ -n "$now" ] && [ "$now" != "$boot" ]; check $? "it started again and opened with the ring ($(( $(date +%s) - t0 )) s)"
podman logs "$tang" 2>&1 | grep -c 'POST /rec' | xargs -I{} say "the ring was asked {} time(s)"

# ---- a boot with the ring off: the console key ---------------------------------------------
podman stop -t 2 "$tang" >/dev/null
vm serial-3.log      # a cold start (the VM is started again), the ring away
wait_log serial-3.log '[Pp]assphrase|[Pp]assword for' 600; check $? "without the ring it asks at the console"
grep -aiE 'passphrase|password for' serial-3.log | tail -1 | sed 's/^/    /'
type_serial "$(cat console.key)"
t0=$(date +%s)
wait_ssh 300; check $? "the console key opens it ($(( $(date +%s) - t0 )) s)"

say "$([ "$fails" = 0 ] && echo "all passed" || echo "$fails failed")"
exit "$fails"
}
