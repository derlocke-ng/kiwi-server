#!/usr/bin/env bash
# The starter image in a local QEMU VM (UEFI, KVM, 4 GB, user-mode network with loopback port
# forwards). Touches no fleet and no machine. Prints one PASS or FAIL line per check.
#
#   tests/drills/starter-vm.sh [WORKDIR] build|install|again|two-disks|small|stop|all
#
#   build      the starter image with --debug-shell (a root shell on the VM's second serial
#              port, for these checks only) into WORKDIR/starter-debug.iso
#   install    a blank 20 GB disk, the ISO as a USB stick and a blank USB disk: it installs on
#              the 20 GB disk, starts the installed system, the first-run page answers on 8740
#              and 80, the console shows the address, the code and the QR code; the VM keeps running
#   again      the same disk, the stick first in the boot order: no new install, the same system
#   two-disks  two blank disks: nothing installed, the message names both
#   small      one 8 GB disk: nothing installed, it says the disk is too small
#   windows    one disk with a GPT, a Microsoft reserved and an NTFS partition: no install for
#              90 s, the console names Windows, port 80 shows the page; the page's button
#              (posted as its form would) installs, and the first-run page comes up
#   typed      the same disk: "erase" typed on the serial console does nothing, "ERASE" installs
#   stop       kill the VM this script started (by its pid)
#
# WORKDIR (default ~/kiwi-vm/starter) keeps the ISO, the disks, serial-*.log and qemu.pid.
# Base ISO: $KIWI_STARTER_BASE, else the newest Fedora CoreOS live ISO in ~/.cache/kiwi-server.
set -u
here=$(cd "$(dirname "$0")/../.." && pwd)
self=$here/tests/drills/$(basename "$0")
w=$HOME/kiwi-vm/starter
if [ $# -ge 2 ]; then w=$1; shift; fi
cmd=${1:-all}
mkdir -p "$w"; cd "$w" || exit 2
w=$PWD
OVMF=/usr/share/edk2/ovmf
P8740=${P8740:-18740}; P80=${P80:-18080}
iso=$w/starter-debug.iso
image=ghcr.io/derlocke-ng/kiwi-server-control:2
# kiwi.local asked by another process (a container) on the VM's network, as a phone would ask
MDNS_PROBE='
import socket, struct, sys
name, src = sys.argv[1], sys.argv[2]
q = struct.pack(">HHHHHH", 0x4b57, 0, 1, 0, 0, 0) + b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0" + struct.pack(">HH", 1, 1)
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(src))
s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
s.bind((src, 0)); s.settimeout(3)
s.sendto(q, ("224.0.0.251", 5353))
try:
    while True:
        d, a = s.recvfrom(4096)
        if struct.unpack(">H", d[6:8])[0] and d[-6:-4] == b"\x00\x04":
            print("mdns: %s is %s (answered from %s)" % (name, socket.inet_ntoa(d[-4:]), a[0])); break
except socket.timeout:
    print("mdns: no answer")
'
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
fails=0

stop() {
    [ -s qemu.pid ] || return 0
    local pid; pid=$(< qemu.pid)
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid"
        for _ in $(seq 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
        kill -9 "$pid" 2>/dev/null
    fi
    rm -f qemu.pid
}

# vm LOG STICK-FIRST DISK... : start the VM in the background
vm() {
    local log=$1 first=$2; shift 2
    local disks=() i=0 d stick_idx=1 disk_idx=0
    [ "$first" = stick ] && { stick_idx=0; disk_idx=1; }
    for d in "$@"; do
        disks+=(-drive "if=none,id=d$i,format=$([[ $d == *.img ]] && echo raw || echo qcow2),file=$d" -device "virtio-blk-pci,drive=d$i,bootindex=$((disk_idx + i * 2))")
        i=$((i + 1))
    done
    stop
    [ -f vars.fd ] || cp "$OVMF/OVMF_VARS.fd" vars.fd
    rm -f "$log" debug.sock serial0.sock
    qemu-system-x86_64 -enable-kvm -machine q35 -cpu host -smp 2 -m 4096 \
        -drive if=pflash,format=raw,readonly=on,file="$OVMF/OVMF_CODE.fd" -drive if=pflash,format=raw,file=vars.fd \
        -device qemu-xhci,id=xhci \
        -drive if=none,id=stick,format=raw,readonly=on,file="$iso" -device usb-storage,bus=xhci.0,drive=stick,bootindex=$stick_idx \
        -drive if=none,id=usbdisk,format=raw,file=usb-blank.img -device usb-storage,bus=xhci.0,drive=usbdisk \
        "${disks[@]}" \
        -netdev user,id=n0,hostfwd=tcp:127.0.0.1:"$P8740"-:8740,hostfwd=tcp:127.0.0.1:"$P80"-:80 -device virtio-net-pci,netdev=n0 \
        -display none -chardev socket,id=s0,path=serial0.sock,server=on,wait=off,logfile="$log" -serial chardev:s0 -chardev socket,id=dbg,path=debug.sock,server=on,wait=off -serial chardev:dbg \
        -monitor unix:monitor.sock,server=on,wait=off -daemonize -pidfile qemu.pid
}

# wait_log LOG REGEX SECONDS
wait_log() {
    local n=$3
    while [ "$n" -gt 0 ]; do grep -qaE "$2" "$1" 2>/dev/null && return 0; sleep 5; n=$((n - 5)); done
    return 1
}

# sh COMMAND: run it in the installed system's debug shell (ttyS1), print its output
sh_vm() {
    python3 - "$w/debug.sock" "$1" <<'EOF'
import re, socket, sys, time, uuid
s = socket.socket(socket.AF_UNIX); s.connect(sys.argv[1]); s.settimeout(1)
tag = uuid.uuid4().hex[:8]
s.sendall(b"\nstty -echo; PS1=; bind 'set enable-bracketed-paste off' 2>/dev/null\n")
end = time.time() + 2
while time.time() < end:       # what the shell says before the command is not its output
    try:
        s.recv(65536)
    except socket.timeout:
        pass
s.sendall(("echo B%s; %s; echo E%s\n" % (tag, sys.argv[2], tag)).encode())
buf, end = b"", time.time() + 60
while time.time() < end and ("E" + tag).encode() not in buf.split(("B" + tag).encode(), 1)[-1]:
    try:
        buf += s.recv(65536)
    except socket.timeout:
        pass
out = buf.decode(errors="replace").split("B" + tag, 1)[-1].split("E" + tag, 1)[0]
print(re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", out).replace("\r", "").strip("\n"))
EOF
}

mdns() {
    sh_vm "echo $(base64 -w0 <<< "$MDNS_PROBE") | base64 -d > /run/mdns-probe.py
        a=\$(ip -4 -o route get 192.0.2.1 | sed -n 's/.* src \([0-9.]*\).*/\1/p')
        podman run --rm --network host -v /run/mdns-probe.py:/probe.py:ro,z --entrypoint python3 $image /probe.py kiwi.local \$a"
}

page() {    # page PORT: the first-run page, after its redirects
    curl -s -L -o "page-$1.html" -w '%{http_code} %{url_effective}' --max-time 20 "http://127.0.0.1:$1/"
}

up_checks() {   # LOG: the installed system came up with its page and screen
    local log=$1 r
    wait_log "$log" 'Setup code: [0-9a-f]{4}-[0-9a-f]{4}' 1200; check $? "the console shows a setup code ($(grep -aoE 'Setup code: [0-9a-f-]+' "$log" | tail -1))"
    grep -aqE 'Open http://kiwi.local or http://[0-9.]+' "$log"; check $? "the console shows the address ($(grep -aoE 'or http://[0-9.]+' "$log" | tail -1))"
    grep -aq $'▀' "$log"; check $? "the console shows the QR code"
    r=$(page "$P8740"); say "8740: $r"
    [[ $r == "200 "*/setup ]] && grep -q 'action="/setup"' "page-$P8740.html"; check $? "port 8740 ends at the first-run page with the questions"
    r=$(page "$P80"); say "80: $r"
    [[ $r == "200 "*/setup ]] && grep -q 'action="/setup"' "page-$P80.html"; check $? "port 80 ends at the same page"
}

inspect() {     # LOG: no password, mDNS, a new code after the container restarts
    local log=$1 out r old new n issue
    out=$(sh_vm 'getent shadow root core | cut -d: -f1,2; resolvectl mdns | tail -1')
    printf '%s\n' "$out" | sed 's/^/    /'
    [ "$(printf '%s\n' "$out" | grep -cE '^(root|core):[*!]+$')" = 2 ]; check $? "no password for root and core"
    r=$(mdns); say "$r"
    [[ $r == "mdns: kiwi.local is 10.0.2.15"* ]]; check $? "kiwi.local answers by mDNS on the VM's network"
    old=$(grep -aoE 'Setup code: [0-9a-f-]+' "$log" | tail -1)
    sh_vm 'systemctl restart kn-control.service' >/dev/null
    n=60; new=$old
    while [ "$n" -gt 0 ] && [ "$new" = "$old" ]; do
        sleep 5; n=$((n - 1)); new=$(grep -aoE 'Setup code: [0-9a-f-]+' "$log" | tail -1)
    done
    issue=$(sh_vm 'cat /etc/issue.d/50-kiwi.issue')
    [ "$new" != "$old" ] && grep -qF "$new" <<< "$issue" && ! grep -qF "$old" <<< "$issue"
    check $? "after a restart of the container the screen shows its new code only ($old -> $new)"
}

windows_disk() {    # NAME: 20 GB, GPT, a Microsoft reserved partition and a 1 GB NTFS one, as Windows leaves them
    local start
    rm -f "$1" ntfs.part
    truncate -s 20G "$1"
    sgdisk -n 1:2048:+16M -t 1:0c01 -c 1:"Microsoft reserved partition" \
        -n 2:0:+1G -t 2:0700 -c 2:"Basic data partition" "$1" >/dev/null || return 1
    start=$(sgdisk -i 2 "$1" | awk '/^First sector/ { print $3 }')
    [ -n "$start" ] || return 1
    truncate -s 1G ntfs.part
    mkfs.ntfs -q -F -Q -L Windows ntfs.part >/dev/null 2>&1 || return 1
    dd if=ntfs.part of="$1" bs=512 seek="$start" conv=notrunc,sparse status=none
    rm -f ntfs.part
}

type_serial() {     # TEXT: typed on the VM's first serial console, Enter at the end
    python3 - "$w/serial0.sock" "$1" <<'EOF'
import socket, sys, time
s = socket.socket(socket.AF_UNIX); s.connect(sys.argv[1])
for ch in sys.argv[2] + "\r":
    s.sendall(ch.encode()); time.sleep(0.05)
time.sleep(1); s.close()
EOF
}

asks() {    # LOG: the stick asks before it erases the disk, and does nothing for 90 s
    local log=$1 t0=$2 r
    wait_log "$log" 'the one disk here is not empty' 300; check $? "it asks: the one disk here is not empty ($(( $(date +%s) - t0 )) s)"
    grep -aqE 'vda  21.5 GB .*: it holds Windows\.' "$log"; check $? "it names what it found: $(grep -aoE 'vda  21.5 GB .*: it holds [A-Za-z ]+' "$log" | head -1)"
    grep -aq 'To erase this disk and install, type ERASE and press Enter.' "$log"; check $? "it says how to answer at the machine"
    while [ $(( $(date +%s) - t0 )) -lt 90 ]; do sleep 5; done
    ! grep -aq 'kiwi: installing on' "$log"; check $? "nothing installed after $(( $(date +%s) - t0 )) s"
}

blank() {   # blank NAME SIZE
    rm -f "$1"; qemu-img create -q -f qcow2 "$1" "$2"
}

[ -f usb-blank.img ] || truncate -s 32G usb-blank.img

case $cmd in
build)
    base=${KIWI_STARTER_BASE:-$(ls -1 "$HOME"/.cache/kiwi-server/fedora-coreos-*-live-iso.x86_64.iso 2>/dev/null | sort -V | tail -1)}
    "$here/bin/kiwi-server" build --starter --debug-shell --base "$base" --output "$iso"; check $? "starter image built from $(basename "$base")"
    ;;
install)
    blank disk.qcow2 20G; rm -f vars.fd
    t0=$(date +%s)
    vm serial-install.log disk disk.qcow2
    wait_log serial-install.log 'kiwi: installing on vda' 300; check $? "the live system installs on vda, not on the stick or the USB disk"
    wait_log serial-install.log 'kiwi: installed: take the stick out' 600; check $? "it says: installed: take the stick out ($(( $(date +%s) - t0 )) s)"
    up_checks serial-install.log
    say "up after $(( $(date +%s) - t0 )) s"
    out=$(sh_vm 'cat /home/core/.ssh/authorized_keys; ls -lZ /var/lib/kiwi-starter/id_ed25519')
    printf '%s\n' "$out" | sed 's/^/    /'
    [ "$(printf '%s\n' "$out" | grep -c '^ssh-')" = 1 ] && printf '%s\n' "$out" | grep -q '^ssh-ed25519 .* kiwi-starter$'
    check $? "authorized_keys holds the one kiwi-starter key"
    printf '%s\n' "$out" | grep -qE '^-rw-------\.? [0-9]+ root root .*container_file_t:s0:c[0-9]+,c[0-9]+ '; check $? "the private half is root 0600, labelled for the container"
    sh_vm 'stat -c %i /var/lib/kiwi-starter/id_ed25519; cat /var/lib/kiwi-starter/id_ed25519.pub' > first-boot.txt
    inspect serial-install.log
    ;;
inspect)    # the checks on a running installed system, again
    inspect "${2:-serial-install.log}"
    ;;
again)
    [ -f disk.qcow2 ] && [ -s first-boot.txt ] || { say "FAIL run install first"; exit 1; }
    sh_vm 'systemctl poweroff' >/dev/null 2>&1; sleep 15; stop
    t0=$(date +%s)
    vm serial-again.log stick disk.qcow2
    wait_log serial-again.log 'kiwi: this machine is installed already: take the stick out' 300
    check $? "the live system says: installed already ($(( $(date +%s) - t0 )) s)"
    ! grep -aq 'kiwi: installing on' serial-again.log; check $? "it did not install again"
    up_checks serial-again.log
    sh_vm 'stat -c %i /var/lib/kiwi-starter/id_ed25519; cat /var/lib/kiwi-starter/id_ed25519.pub' > second-boot.txt
    cmp -s first-boot.txt second-boot.txt; check $? "the same system: the starter key file has the same inode and key ($(head -1 second-boot.txt))"
    ;;
two-disks)
    stop; blank two-a.qcow2 20G; blank two-b.qcow2 20G; rm -f vars.fd
    vm serial-two.log disk two-a.qcow2 two-b.qcow2
    wait_log serial-two.log 'kiwi: 2 disks found, so nothing was installed' 300; check $? "it says: 2 disks found, nothing installed"
    grep -aqE '^ *vda +21.5 GB' serial-two.log && grep -aqE '^ *vdb +21.5 GB' serial-two.log; check $? "the message names vda and vdb"
    sleep 40
    ! grep -aq 'kiwi: installing on' serial-two.log && [ "$(grep -ac '2 disks found' serial-two.log)" -ge 2 ]; check $? "nothing installed, it keeps saying it"
    grep -a -A6 '2 disks found' serial-two.log | head -8 | sed 's/^/    /'
    stop
    ;;
small)
    stop; blank small.qcow2 8G; rm -f vars.fd
    vm serial-small.log disk small.qcow2
    wait_log serial-small.log 'kiwi: nothing was installed: the disk is too small' 300; check $? "it says: the disk is too small"
    grep -aqE '^ *vda +8.6 GB .*too small' serial-small.log; check $? "the message names vda as too small"
    grep -a -A4 'too small' serial-small.log | head -5 | sed 's/^/    /'
    stop
    ;;
windows)
    stop; windows_disk win.img || { say "FAIL could not make the Windows disk"; exit 1; }; rm -f vars.fd
    t0=$(date +%s)
    vm serial-windows.log disk win.img
    asks serial-windows.log "$t0"
    r=$(curl -s -o page-live.html -w '%{http_code}' --max-time 10 "http://127.0.0.1:$P80/"); say "port 80: $r"
    n=$(grep -o 'name=n value=[0-9]*' page-live.html | grep -o '[0-9]*$')
    [ "$r" = 200 ] && grep -q 'Erase this disk and install' page-live.html && grep -q 'it holds Windows' page-live.html && [ -n "$n" ]
    check $? "port 80 shows the page with its button"
    r=$(curl -s -o page-wrong.html -w '%{http_code}' --max-time 10 --data n=1 "http://127.0.0.1:$P80/erase"); say "a wrong number: $r"
    sleep 3; [ "$r" = 403 ] && ! grep -aq 'kiwi: installing on' serial-windows.log; check $? "a POST with another number erases nothing"
    r=$(curl -s -o page-yes.html -w '%{http_code}' --max-time 10 --data "n=$n" "http://127.0.0.1:$P80/erase"); say "the button: $r"
    [ "$r" = 200 ] && grep -q 'Installing. This page changes when the machine has started again.' page-yes.html; check $? "the button's POST: the page says it installs"
    wait_log serial-windows.log 'kiwi: installed: take the stick out' 600; check $? "it installed ($(( $(date +%s) - t0 )) s)"
    up_checks serial-windows.log
    say "up after $(( $(date +%s) - t0 )) s"
    stop
    ;;
typed)
    stop; windows_disk win.img || { say "FAIL could not make the Windows disk"; exit 1; }; rm -f vars.fd
    t0=$(date +%s)
    vm serial-typed.log disk win.img
    asks serial-typed.log "$t0"
    type_serial erase; sleep 5
    grep -aq 'that was not ERASE, nothing was erased' serial-typed.log && ! grep -aq 'kiwi: installing on' serial-typed.log
    check $? "\"erase\" in lower case: nothing erased, it says so"
    type_serial ERASE
    wait_log serial-typed.log 'kiwi: installing on vda' 60; check $? "\"ERASE\": it installs"
    wait_log serial-typed.log 'kiwi: installed: take the stick out' 600; check $? "it installed ($(( $(date +%s) - t0 )) s)"
    wait_log serial-typed.log 'Setup code: [0-9a-f]{4}-[0-9a-f]{4}' 900; check $? "the installed system shows its setup code"
    stop
    ;;
stop) stop ;;
all)
    for c in build install again two-disks small windows typed; do bash "$self" "$w" "$c" || fails=$((fails + 1)); done
    ;;
*) echo "unknown: $cmd" >&2; exit 2 ;;
esac
exit "$fails"
