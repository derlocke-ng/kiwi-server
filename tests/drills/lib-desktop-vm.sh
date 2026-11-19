# shellcheck shell=bash
# shellcheck disable=SC2154,SC2034   # w, admin, VD, PSSH, KEY are set by the drill that sources this; POLICY is used there
# Shared by the desktop drills (desktop-clevis-vm.sh, desktop-silverblue-vm.sh): the VM runs on
# VMHOST (an ssh name: sh3, the owner's choice, disks under /mnt/nvme_2tb/share/virt) or here;
# the test rings run here in podman and reach the VM host through an ssh tunnel. The VM reaches
# its host's 127.0.0.1 at 10.0.2.2, so a ring at 127.0.0.1:7611 here is 10.0.2.2:7611 in the VM.
#
# The caller sets: w (a local folder), VMHOST (empty: here), VD (the VM's folder on VMHOST),
# PSSH (the VM's ssh port on VMHOST's loopback), admin, KEY (the admin key, a local file).
IMAGE=ghcr.io/derlocke-ng/tang:15-kiwi2
declare -A PORT=([health1]=7611 [health2]=7612 [auto]=7613 [emergency]=7614)
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
fails=0

vh() {      # a command in VD, on VMHOST or here
    if [ -n "$VMHOST" ]; then ssh -o BatchMode=yes "$VMHOST" "cd '$VD' && $*"; else (cd "$VD" && bash -c "$*"); fi
}
vh_put() {  # LOCAL NAME: a file into VD
    if [ -n "$VMHOST" ]; then scp -q "$1" "$VMHOST:$VD/$2"; else cp "$1" "$VD/$2"; fi
}
vh_prepare() {  # the folder, and the UEFI firmware this host has: OVMF_CODE / OVMF_VARS
    if [ -n "$VMHOST" ]; then ssh -o BatchMode=yes "$VMHOST" "mkdir -p '$VD'" || return 1; else mkdir -p "$VD" || return 1; fi
    OVMF_PAIR=$(vh 'for p in /usr/share/edk2/ovmf/OVMF_CODE.fd:/usr/share/edk2/ovmf/OVMF_VARS.fd \
        /usr/share/OVMF/OVMF_CODE_4M.fd:/usr/share/OVMF/OVMF_VARS_4M.fd /usr/share/OVMF/OVMF_CODE.fd:/usr/share/OVMF/OVMF_VARS.fd; do
        [ -f "${p%%:*}" ] && [ -f "${p##*:}" ] && { echo "$p"; break; }; done')
    [ -n "$OVMF_PAIR" ]
}
vm_stop() { vh '[ -s qemu.pid ] && { kill $(cat qemu.pid) 2>/dev/null; sleep 3; kill -9 $(cat qemu.pid) 2>/dev/null; rm -f qemu.pid; }; true'; }
vm_start() {    # LOG [EXTRA QEMU ARGS...]: from disk.qcow2, with vars.fd, the serial console in LOG
    local log=$1; shift
    vm_stop
    vh "rm -f '$log' serial.sock; qemu-system-x86_64 -enable-kvm -machine q35 -cpu host -smp 2 -m 4096 \
        -drive if=pflash,format=raw,readonly=on,file=${OVMF_PAIR%%:*} -drive if=pflash,format=raw,file=vars.fd \
        $* \
        -drive if=none,id=disk,format=qcow2,file=disk.qcow2 -device virtio-blk-pci,drive=disk,bootindex=1 \
        -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$PSSH-:22 \
        -display none -chardev socket,id=s0,path=serial.sock,server=on,wait=off,logfile='$log' -serial chardev:s0 \
        -daemonize -pidfile qemu.pid"
}
vm_running() { vh '[ -s qemu.pid ] && kill -0 $(cat qemu.pid) 2>/dev/null'; }
vm_wait_log() { local n=$3; while [ "$n" -gt 0 ]; do vh "grep -qaE '$2' '$1'" 2>/dev/null && return 0; sleep 5; n=$((n - 5)); done; return 1; }
vm_type() {     # TEXT, then Enter, at the VM's serial console
    vh "python3 -c 'import socket,sys,time
s=socket.socket(socket.AF_UNIX); s.connect(\"serial.sock\")
for ch in sys.argv[1]+\"\\r\": s.send(ch.encode()); time.sleep(0.05)
time.sleep(1)' '$1'"
}
vssh() {
    local jump=(); [ -n "$VMHOST" ] && jump=(-J "$VMHOST")
    ssh -q "${jump[@]}" -p "$PSSH" -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 "$admin@127.0.0.1" "$@"
}
up_within() { local n=$1; while [ "$n" -gt 0 ]; do vssh true 2>/dev/null && return 0; sleep 5; n=$((n - 5)); done; return 1; }

# ---- the test rings, here, and the tunnel that takes them to the VM host -----------------------
ring() {   # NAME up|down
    local c=kiwi-desk-$1
    if [ "$2" = up ]; then
        if ! podman container exists "$c"; then
            mkdir -p "$w/rings/$1"
            [ -n "$(ls -A "$w/rings/$1")" ] || podman run --rm --security-opt label=disable -v "$w/rings/$1:/db" \
                --entrypoint /bin/sh "$IMAGE" -c "/usr/libexec/tangd-keygen /db && chown -R 0:0 /db" >/dev/null
            podman run -d --name "$c" --security-opt label=disable -p "127.0.0.1:${PORT[$1]}:7500" -v "$w/rings/$1:/db" \
                --entrypoint /usr/libexec/tangd "$IMAGE" -l -p 7500 /db >/dev/null
        else
            podman start "$c" >/dev/null
        fi
        for _ in $(seq 40); do curl -sf "http://127.0.0.1:${PORT[$1]}/adv" >/dev/null && return 0; sleep 0.25; done
        return 1
    fi
    if podman container exists "$c"; then podman stop -t 2 "$c" >/dev/null; fi
    true
}
rings() { local r; for r in health1 health2 auto emergency; do
              if [[ " $* " == *" $r "* ]]; then ring "$r" up; else ring "$r" down; fi; done; }
tunnel_up() {   # PORTS...: each local 127.0.0.1 port on VMHOST's 127.0.0.1 too
    [ -n "$VMHOST" ] || return 0
    local f=() p
    for p in "$@"; do f+=(-R "127.0.0.1:$p:127.0.0.1:$p"); done
    ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=20 "${f[@]}" "$VMHOST" &
    TUNNEL=$!
    sleep 3; kill -0 "$TUNNEL" 2>/dev/null
}
desk_cleanup() {
    vm_stop 2>/dev/null
    [ -n "${TUNNEL:-}" ] && kill "$TUNNEL" 2>/dev/null
    local r; for r in health1 health2 auto emergency; do podman rm -f "kiwi-desk-$r" >/dev/null 2>&1; done
    true
}

# ---- the policy and its scenarios (the same for every desktop) -------------------------------
POLICY='{"t":1,"pins":{"sss":[{"t":3,"pins":{"tang":[{"url":"http://10.0.2.2:7611"},{"url":"http://10.0.2.2:7612"},{"url":"http://10.0.2.2:7613"}]}}],"tang":[{"url":"http://10.0.2.2:7614"}]}}'
PROMPT='Please unlock disk|Enter passphrase|Please enter passphrase'
scenarios() {   # PASSPHRASE; the VM's disk bound with POLICY
    local t0
    rings health1 health2 auto; vm_start s1.log
    t0=$(date +%s); up_within 300; check $? "1. all health rings and auto up: opens by itself ($(( $(date +%s) - t0 )) s)"

    rings health1 health2; vm_start s2.log
    vm_wait_log s2.log "$PROMPT" 300 && ! up_within 90; check $? "2. auto-unlock off: it waits at its prompt"
    ring emergency up; t0=$(date +%s)
    up_within 180; check $? "2. Unlock now (the emergency ring up): opens the waiting desktop ($(( $(date +%s) - t0 )) s)"

    rings health1 auto; vm_start s3.log
    vm_wait_log s3.log "$PROMPT" 300 && ! up_within 90; check $? "3. health2 down: it waits although auto-unlock is on"
    ring emergency up; t0=$(date +%s)
    up_within 180; check $? "3. Unlock now opens it anyway ($(( $(date +%s) - t0 )) s)"

    rings; vm_start s4.log
    vm_wait_log s4.log "$PROMPT" 300 && ! up_within 60; check $? "4. no ring: it waits"
    vm_type "$1"
    up_within 180; check $? "4. the passphrase still opens it"
}
