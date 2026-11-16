#!/usr/bin/env bash
# The starter image in a local QEMU VM (UEFI, KVM, 4 GB, user-mode network with loopback port
# forwards). Touches no fleet and no machine. Prints one PASS or FAIL line per check.
#
#   tests/drills/starter-vm.sh [WORKDIR] build|install|again|two-disks|small|windows|typed|first-node|stop|all
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
#   code       the VM of `install`, still running: this checkout's code goes into the VM and over the
#              control image's (a drop-in for the kn-control unit), for trying code that is not released
#   first-node the VM of `install`, still running: a new network from its first-run page with "This
#              machine" as the first one at home; the master's install media are made on the machine,
#              fetched and installed on a rented server (netcup; THE SERVER IS WIPED); then "Set it up"
#              for this machine: it becomes the node, the starter's things go, the control page moves
#              to https://control.<name>.<domain>. Needs KIWI_NEVER (servers never to touch),
#              FN_SERVER (the server's id), FN_ADDRESS (its address) and FN_TOKEN (a netcup token file)
#   alone      the VM of `install`, still running: a new network with "No master yet" from its
#              first-run page (docs/design/no-master.md), "This machine" the only one; "Set it up"
#              makes it the node off the VPN: nginx, Pi-hole and the control part, no VPN client;
#              the names answer with its home address, the control page opens from the home network;
#              then a hard reset. Touches no server
#   up         the VM of `install`, still running: the page and screen checks again (after `code`;
#              LOG=serial-again.log for the VM of `again`)
#   handshake  the VM of `install`, still running: taken in as a network would, with a wrong code,
#              the right one and once more; the answer's proof and host keys checked
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
P8740=${P8740:-18740}; P80=${P80:-18080}; P8743=${P8743:-18743}
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
    # a TPM 2.0 (swtpm), as most PCs have one: the stick encrypts with it. TPM=0: a machine without
    local tpm=()
    if [ "${TPM:-1}" = 1 ]; then
        mkdir -p tpm
        if ! { [ -S tpm/sock ] && pgrep -f "swtpm socket --tpmstate dir=$w/tp[m]" >/dev/null; }; then
            rm -f tpm/sock
            swtpm socket --tpmstate dir="$w/tpm" --ctrl type=unixio,path="$w/tpm/sock" --tpm2 --daemon
            for _ in $(seq 20); do [ -S tpm/sock ] && break; sleep 0.2; done
        fi
        tpm=(-chardev socket,id=chrtpm,path="$w/tpm/sock" -tpmdev emulator,id=tpm0,chardev=chrtpm -device tpm-tis,tpmdev=tpm0)
    fi
    qemu-system-x86_64 -enable-kvm -machine q35 -cpu host -smp 2 -m 4096 \
        -drive if=pflash,format=raw,readonly=on,file="$OVMF/OVMF_CODE.fd" -drive if=pflash,format=raw,file=vars.fd \
        "${tpm[@]}" \
        -device qemu-xhci,id=xhci \
        -drive if=none,id=stick,format=raw,readonly=on,file="$iso" -device usb-storage,bus=xhci.0,drive=stick,bootindex=$stick_idx \
        -drive if=none,id=usbdisk,format=raw,file=usb-blank.img -device usb-storage,bus=xhci.0,drive=usbdisk \
        "${disks[@]}" \
        -netdev user,id=n0,hostfwd=tcp:127.0.0.1:"$P8740"-:8740,hostfwd=tcp:127.0.0.1:"$P80"-:80,hostfwd=tcp:127.0.0.1:"$P8743"-:8743 -device virtio-net-pci,netdev=n0 \
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

page() {    # page PORT: the first-run page over HTTPS (its own certificate), after its redirects
    curl -sk -L -o "page-$1.html" -w '%{http_code} %{url_effective}' --max-time 20 "https://127.0.0.1:$1/"
}

sends_on() {    # PORT: plain HTTP there only sends the browser to HTTPS
    curl -s -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 20 "http://127.0.0.1:$1/setup"
}

up_checks() {   # LOG: the installed system came up with its page and screen
    local log=$1 r
    wait_log "$log" 'Setup code: [0-9a-f]{4}-[0-9a-f]{4}' 1200; check $? "the console shows a setup code ($(grep -aoE 'Setup code: [0-9a-f-]+' "$log" | tail -1))"
    grep -aqE 'Open https://kiwi.local or https://[0-9.]+' "$log"; check $? "the console shows the address ($(grep -aoE 'or https://[0-9.]+' "$log" | tail -1))"
    grep -aqE 'It is the right one when it begins [0-9A-F]{2}(:[0-9A-F]{2}){7}' "$log"; check $? "the console shows the certificate's fingerprint"
    grep -aq $'▀' "$log"; check $? "the console shows the QR code"
    r=$(page "$P8743"); say "8743: $r"
    [[ $r == "200 "*/setup ]] && grep -q 'action="/setup"' "page-$P8743.html"; check $? "HTTPS (8743) ends at the first-run page with the questions"
    fp=$(echo | openssl s_client -connect 127.0.0.1:$P8743 2>/dev/null | openssl x509 -noout -fingerprint -sha256 | cut -d= -f2 | cut -d: -f1-8)
    grep -aq "begins $fp" "$log"; check $? "the page's certificate is the one the console names ($fp)"
    r=$(sends_on "$P8740"); say "8740: $r"
    [[ $r == "308 https://127.0.0.1:8743/setup" ]]; check $? "plain HTTP on 8740 only sends the browser to HTTPS"
    r=$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 20 -H 'Host: kiwi.local' "http://127.0.0.1:$P80/setup")
    say "80: $r"
    [[ $r == "308 https://kiwi.local/setup" ]]; check $? "http://kiwi.local only sends the browser to https://kiwi.local (443)"
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

# ---- first-node: the page's forms, posted the way its buttons post them ----
base=https://127.0.0.1:$P8743
jar=$w/cookies.txt
csrf() { curl -sk -b "$jar" --max-time 20 "$base/" | grep -o 'name="csrf" value="[^"]*"' | head -1 | cut -d'"' -f4; }
job() {     # job ACTION HOST SECONDS: start it as the page's button does, wait for its end, say how it ended
    local loc id n=$3 state=running off=0 r
    loc=$(curl -s -b "$jar" -o /dev/null -w '%{redirect_url}' --max-time 30 --data-urlencode "csrf=$(csrf)" \
        --data-urlencode "action=$1" --data-urlencode "host=$2" "$base/run")
    id=${loc##*/jobs/}
    [ -n "$id" ] && [ "$id" != "$loc" ] || { say "    no task was started for $1 $2 ($loc)"; return 1; }
    : > "job-$1-$2.log"
    while [ "$n" -gt 0 ]; do
        r=$(curl -sk -b "$jar" --max-time 20 "$base/api/jobs/$id/log?offset=$off") || r=
        state=$(python3 -c 'import json,sys
try:
    d = json.loads(sys.argv[1]); open(sys.argv[2], "a").write(d["text"]); print(d["state"], d["offset"])
except Exception:
    print("lost", sys.argv[3])' "$r" "job-$1-$2.log" "$off")
        off=${state#* }; state=${state% *}
        case $state in ok) return 0 ;; failed) tail -5 "job-$1-$2.log" | sed 's/^/    /'; return 1 ;; esac
        sleep 5; n=$((n - 5))
    done
    say "    $1 $2 did not end in $3 s (it is $state)"; tail -3 "job-$1-$2.log" | sed 's/^/    /'
    return 1
}
phase() {   # phase HOST: what the page's list says about it
    curl -sk -b "$jar" --max-time 20 "$base/" > page-home.html
    python3 - "$1" <<'EOF2'
import re, sys
html = open("page-home.html").read()
m = re.search(r'<div class="card"><div class="row"><strong>%s</strong>.*?</div>(.*?)</div>' % re.escape(sys.argv[1]), html, re.S)
print("to set up: " + re.sub(r"<[^>]+>", " ", m.group(1)).split(".")[0].strip()[:90] if m else "set up")
EOF2
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
code)
    port=${PCODE:-18999}
    tar -C "$here" -czf code.tgz --exclude=__pycache__ bin data kiwi.manifest lib modules roles
    python3 -m http.server "$port" --bind 127.0.0.1 --directory "$w" >/dev/null 2>&1 &
    srv=$!
    sleep 1
    out=$(sh_vm "rm -rf /var/kiwi-dev && mkdir -p /var/kiwi-dev /etc/containers/systemd/kn-control.container.d &&
        curl -s --max-time 60 http://10.0.2.2:$port/code.tgz | tar -xz -C /var/kiwi-dev &&
        printf '[Container]\nVolume=/var/kiwi-dev:/opt/kiwi-server:ro,Z\n' > /etc/containers/systemd/kn-control.container.d/dev.conf &&
        systemctl daemon-reload && systemctl restart kn-control.service && sleep 8 &&
        podman exec kn-control python3 -c 'import kiwiserver; print(kiwiserver.VERSION, kiwiserver.__file__)'")
    kill "$srv" 2>/dev/null; rm -f code.tgz
    say "$out"
    [[ $out == "$(sed -n 's/^VERSION=//p' "$here/kiwi.manifest") /opt/kiwi-server/"* ]]; check $? "the VM's control part runs this checkout's code"
    ;;
up)     # the VM of `install`, still running (after `code`, say): its page and screen checked again
    up_checks "${LOG:-serial-install.log}"
    ;;
handshake)  # the VM of `install`, still running: taken in as a network would (Control.join_as_starter)
    log=${LOG:-serial-install.log}
    code=$(grep -aoE 'Setup code: [0-9a-f]{4}-[0-9a-f]{4}' "$log" | tail -1 | cut -d' ' -f3)
    rm -f hs-key hs-key.pub; ssh-keygen -q -t ed25519 -N '' -C kiwi-drill -f hs-key
    ours=$(sh_vm 'cat /etc/ssh/ssh_host_*_key.pub' | awk '/^(ssh-|ecdsa-)/ { print $1" "$2 }' | LC_ALL=C sort)
    out=$(python3 - "https://127.0.0.1:$P8743/api/join-me" "$code" "$(cat hs-key.pub)" <<'EOF'
import hashlib, hmac, json, os, ssl, sys, urllib.error, urllib.request
url, code, key = sys.argv[1:4]
mac = lambda c, *p: hmac.new(c.encode(), "\x1f".join(p).encode(), hashlib.sha256).hexdigest()
ctx = ssl._create_unverified_context()
def ask(c):
    nonce = os.urandom(16).hex()
    body = {"nonce": nonce, "key": key, "network": "drill", "mac": mac(c, "kiwi-join-1", nonce, "drill", key)}
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=60) as r:
            return nonce, r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return nonce, e.code, json.load(e)
_, st, a = ask("0000-0000")
print("WRONG", st, a.get("error", ""))
nonce, st, a = ask(code)
good = st == 200 and hmac.compare_digest(a.get("mac", ""), mac(code, "kiwi-join-2", nonce, "\n".join(a.get("host_keys", [])), a.get("hostname", "")))
print("RIGHT", st, "proof-ok" if good else "proof-BAD", a.get("hostname", ""))
for k in sorted(a.get("host_keys", []), key=lambda x: x.encode()):
    print("KEY", k)
_, st, a = ask(code)
print("AGAIN", st, a.get("error", ""))
EOF
)
    printf '%s\n' "$out" | grep -v '^KEY' | sed 's/^/    /'
    grep -q '^WRONG 403 that is not the code' <<< "$out"; check $? "a wrong code is refused, and nothing comes back"
    grep -q '^RIGHT 200 proof-ok' <<< "$out"; check $? "the right code: the answer carries the stick's proof of the code"
    [ -n "$ours" ] && [ "$(sed -n 's/^KEY //p' <<< "$out")" = "$ours" ]; check $? "the host keys it sent are the VM's own ($(wc -l <<< "$ours"))"
    sh_vm 'cat /home/core/.ssh/authorized_keys' | grep -q 'kiwi-drill$'; check $? "the network's key is let in"
    grep -q '^AGAIN 403 .*already' <<< "$out"; check $? "a second take-in is refused"
    ;;
first-node)
    sid=${FN_SERVER:-}; addr=${FN_ADDRESS:-}; token=${FN_TOKEN:-}
    [ -n "$sid" ] && [ -n "$addr" ] && [ -s "$token" ] || { say "FAIL FN_SERVER, FN_ADDRESS and FN_TOKEN say which server becomes the master"; exit 1; }
    [ -n "${KIWI_NEVER:-}" ] || { say "FAIL KIWI_NEVER is not set: say which servers are in use"; exit 1; }
    for never in $KIWI_NEVER; do
        if [ "$never" = "$sid" ] || [ "$never" = "$addr" ]; then say "FAIL $never is in use: not touched"; exit 1; fi
    done
    log=serial-install.log; master=trifle; node=mango
    code=$(grep -aoE 'Setup code: [0-9a-f-]+' "$log" | tail -1 | cut -d' ' -f3)
    [ -n "$code" ] && kill -0 "$(cat qemu.pid 2>/dev/null)" 2>/dev/null || { say "FAIL run install first: its VM must be running"; exit 1; }
    t0=$(date +%s); rm -f "$jar"
    # a throwaway network: its page password is kept beside the disks, for a look by hand afterwards
    (umask 077; head -c 18 /dev/urandom | base64 | tr -d '/+=' > page-password)
    # 1. the questions
    curl -sk -o page-setup.html --max-time 30 "$base/setup"
    this=$(grep -o 'name="nodes.0.address" value="[0-9.]*"' page-setup.html | cut -d'"' -f4)
    grep -q '<strong>This machine</strong>' page-setup.html && [ "$this" = 10.0.2.15 ]; check $? "the questions list this machine as the first at home, at its address ($this)"
    made=$(curl -s -c "$jar" --max-time 120 --data-urlencode "code=$code" --data-urlencode "password@page-password" \
        --data-urlencode "again@page-password" --data-urlencode "master_where=vps" --data-urlencode "master_address=$addr" \
        --data-urlencode "master_name=$master" --data-urlencode "exit=direct" --data-urlencode "lan_subnet=10.0.2.0/24" \
        --data-urlencode "lan_gateway=10.0.2.2" --data-urlencode "nodes.0.preset=minimal" --data-urlencode "nodes.0.address=$this" \
        --data-urlencode "nodes.0.name=$node" "$base/setup" | grep -c 'AGE-SECRET-KEY-')     # the recovery key is not kept
    [ "$made" -ge 1 ] && grep -q kiwi_session "$jar"; check $? "the answers made a network (its recovery key was shown)"
    out=$(sh_vm 'ls /var/home/user/docker/kn-control')
    grep -q 'fleet.yaml.age' <<< "$out" && grep -q 'secrets.age' <<< "$out"; check $? "it rests encrypted where a node's control part keeps it"
    say "$master: $(phase $master)"; say "$node: $(phase $node)"
    # 2. the master: its install media are made on the machine, fetched, and installed at the provider
    job build $master 1800; check $? "the machine made the master's install media ($(( $(date +%s) - t0 )) s)"
    curl -sk -b "$jar" -o master.iso --max-time 600 "$base/hosts/$master/media"
    [ "$(stat -c %s master.iso 2>/dev/null || echo 0)" -gt 100000000 ]; check $? "the install media were fetched from the page ($(( $(stat -c %s master.iso) >> 20 )) MB)"
    python3 - "$here/lib" "$token" "$sid" "$w/master.iso" <<'EOF2'
import sys, time
sys.path.insert(0, sys.argv[1])
from kiwiserver import netcup
client = netcup.Netcup(sys.argv[2])
netcup.install(client, client.server(int(sys.argv[3])), sys.argv[4], "kiwi-drill-%d.iso" % time.time(),
               lambda m: print("%s     netcup: %s" % (time.strftime("%H:%M:%S"), m), flush=True))
EOF2
    check $? "the master is installed on server $sid"
    rm -f master.iso
    n=60; while [ "$n" -gt 0 ] && ! timeout 5 bash -c "exec 3<>/dev/tcp/$addr/22" 2>/dev/null; do sleep 10; n=$((n - 1)); done
    [ "$n" -gt 0 ]; check $? "the master answers at $addr"
    job setup $master 2400; check $? "the page set the master up ($(( $(date +%s) - t0 )) s)"
    # 3. this machine: its place on the VPN, then its role, which takes the starter's things away
    job setup $node 600; check $? "this machine has its place on the VPN"
    say "$node: $(phase $node)"
    grep -q 'It is the machine this page runs on' page-home.html && ! grep -q 'Make its install media' page-home.html
    check $? "its step is 'Set it up', with no install media"
    job setup $node 900; check $? "'Set it up' took the machine over and started its role"
    grep -q 'this page moves' "job-setup-$node.log"; check $? "the task says that the page moves"
    n=240; state=
    while [ "$n" -gt 0 ]; do
        state=$(sh_vm 'systemctl is-active kiwi-self-apply.service; test -e /usr/local/bin/kiwi-starter-done && echo starter || echo node' | tr '\n' ' ')
        case $state in *inactive*|*failed*) break ;; esac      # ended: with the starter's things gone, or not
        sleep 15; n=$((n - 1))
    done
    say "the role ended: $state ($(( $(date +%s) - t0 )) s)"
    sh_vm 'journalctl -u kiwi-self-apply --no-pager -o cat | tail -6' | sed 's/^/    /'
    [[ $state == *inactive*node* ]]; check $? "the role is in and the starter took its things away"
    out=$(sh_vm 'hostname; cat /home/core/.ssh/authorized_keys /home/core/.ssh/authorized_keys.d/* | grep -c kiwi-starter;
        grep -c "^# kiwi-server stack" /etc/containers/systemd/kn-control.container; nft list tables | grep -c kiwi_starter;
        ls /var/lib/kiwi-starter /etc/kiwi-starter 2>&1 | grep -c "No such"; test -e /var/lib/kiwi-server/role.done && echo role-done;
        podman ps --format "{{.Names}}" | sort | tr "\n" " "')
    printf '%s\n' "$out" | sed 's/^/    /'
    grep -q "^$node\." <<< "$out"; check $? "its name is the network's ($node)"
    [ "$(sed -n 2p <<< "$out")" = 0 ] && [ "$(sed -n 4p <<< "$out")" = 0 ] && [ "$(sed -n 5p <<< "$out")" = 2 ]
    check $? "the starter key, its port 80 and its folders are gone"
    [ "$(sed -n 3p <<< "$out")" = 1 ] && grep -q role-done <<< "$out"; check $? "the control part is the stack's, the role is recorded on the machine"
    for c in kn-control kn-nginx kn-vpn-client; do grep -qw "$c" <<< "$out" || { say "    $c does not run"; false; }; done
    check $? "its containers run (control, nginx, the VPN client)"
    node_checks() {     # the link to the master, and the control page at its new place
        local out n=12
        while [ "$n" -gt 0 ]; do        # the tunnel needs a moment after its container started
            out=$(sh_vm 'podman exec kn-vpn-client ping -c 1 -W 3 10.8.0.1 2>&1 | grep -c "1 packets received"')
            [ "${out:-0}" = 1 ] && break; sleep 5; n=$((n - 1))
        done
        [ "${out:-0}" = 1 ]; check $? "it reaches the master over the VPN"
        # the proxy serves the page's name to the VPN only: the machine's own LAN side is refused
        out=$(sh_vm "curl -sk -o /dev/null -w '%{http_code}' --max-time 20 --resolve control.$node.home:443:127.0.0.1 https://control.$node.home/login")
        [ "$out" = 403 ]; check $? "the proxy has control.$node.home and refuses who is not on the VPN ($out)"
        out=$(sh_vm "podman exec kn-control python3 -c 'import os, urllib.request; print(urllib.request.urlopen(\"http://127.0.0.1:8740/login\", timeout=10).read().decode().count(\"keys are locked\"), os.environ.get(\"KIWI_CONTROL_HOST\"), os.environ.get(\"KIWI_STARTER\"))'")
        [ "$out" = "1 $node None" ]; check $? "the control part is the node's own, its keys locked until someone logs in ($out)"
    }
    node_checks
    r=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 10 "$base/login"); [ "$r" = 000 ]
    check $? "the starter's page on 8740 is gone ($r)"
    # a power cut right after: the machine is reset hard and must come back as the node. Nothing is
    # typed while it starts (keys on the serial ports would stop its boot menu)
    before=$(grep -ac 'login:' "$log")
    python3 - "$w/monitor.sock" <<'EOF2'
import socket, sys, time
s = socket.socket(socket.AF_UNIX); s.connect(sys.argv[1]); time.sleep(0.5); s.sendall(b"system_reset\n"); time.sleep(1)
EOF2
    n=60; while [ "$n" -gt 0 ] && [ "$(grep -ac 'login:' "$log")" -le "$before" ]; do sleep 5; n=$((n - 1)); done
    tail -c 400 "$log" | grep -aq "^$node login:"; check $? "after a hard reset its console shows the node's login, not the starter's message"
    n=30; while [ "$n" -gt 0 ] && [ "$(sh_vm 'podman ps --format "{{.Names}}" | wc -l' | tail -1)" != 3 ]; do sleep 10; n=$((n - 1)); done
    out=$(sh_vm 'hostname; nmcli -t -f NAME,DEVICE con show --active | head -1; ip -4 -o addr show scope global | awk "{print \$4}" | head -1;
        systemctl list-unit-files "kiwi-starter*" --no-legend | wc -l; systemctl is-active kiwi-role.service kiwi-stack.service | tr "\n" " "; echo;
        curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1/')
    printf '%s\n' "$out" | sed 's/^/    /'
    [ "$(sed -n 1p <<< "$out")" = "$node.home" ] && [[ $(sed -n 2p <<< "$out") == kiwi-static:* ]] && [ "$(sed -n 3p <<< "$out")" = 10.0.2.15/24 ]
    check $? "it has its name and its fixed address"
    [ "$(sed -n 4p <<< "$out")" = 0 ] && [[ $(sed -n 5p <<< "$out") == "inactive active "* ]] && [ "$(sed -n 6p <<< "$out")" = 301 ]
    check $? "no starter unit is left, the role did not run again, port 80 is the proxy's"
    node_checks
    say "done after $(( $(date +%s) - t0 )) s; the VM keeps running (stop ends it); the master on server $sid keeps running"
    ;;
alone)
    log=serial-install.log; node=mango
    code=$(grep -aoE 'Setup code: [0-9a-f-]+' "$log" | tail -1 | cut -d' ' -f3)
    [ -n "$code" ] && kill -0 "$(cat qemu.pid 2>/dev/null)" 2>/dev/null || { say "FAIL run install first: its VM must be running"; exit 1; }
    t0=$(date +%s); rm -f "$jar"
    (umask 077; head -c 18 /dev/urandom | base64 | tr -d '/+=' > page-password)
    curl -sk -o page-setup.html --max-time 30 "$base/setup"
    this=$(grep -o 'name="nodes.0.address" value="[0-9.]*"' page-setup.html | cut -d'"' -f4)
    grep -q 'value="none"' page-setup.html && [ "$this" = 10.0.2.15 ]; check $? "the questions offer 'No master yet' and list this machine ($this)"
    made=$(curl -s -c "$jar" --max-time 120 --data-urlencode "code=$code" --data-urlencode "password@page-password" \
        --data-urlencode "again@page-password" --data-urlencode "master_where=none" --data-urlencode "lan_subnet=10.0.2.0/24" \
        --data-urlencode "lan_gateway=10.0.2.2" --data-urlencode "nodes.0.preset=minimal" --data-urlencode "nodes.0.address=$this" \
        --data-urlencode "nodes.0.name=$node" "$base/setup" | grep -c 'AGE-SECRET-KEY-')
    [ "$made" -ge 1 ] && grep -q kiwi_session "$jar"; check $? "the answers made a network without a master"
    say "$node: $(phase $node)"
    # "Set it up" until the machine takes its role (no place on a VPN to get first)
    ok=1
    for i in 1 2; do
        job setup $node 900 || { ok=1; break; }
        ok=0
        sh_vm 'systemctl is-active kiwi-self-apply.service' | grep -qxE 'active|activating' && break
        say "    after step $i: $(phase $node)"
    done
    check $ok "'Set it up' took the machine over"
    n=240; state=
    while [ "$n" -gt 0 ]; do
        state=$(sh_vm 'systemctl is-active kiwi-self-apply.service; test -e /usr/local/bin/kiwi-starter-done && echo starter || echo node' | tr '\n' ' ')
        case $state in *inactive*|*failed*) break ;; esac
        sleep 15; n=$((n - 1))
    done
    say "the role ended: $state ($(( $(date +%s) - t0 )) s)"
    sh_vm 'journalctl -u kiwi-self-apply --no-pager -o cat | tail -6' | sed 's/^/    /'
    [[ $state == *inactive*node* ]]; check $? "the role is in and the starter took its things away"
    alone_checks() {
        local out n=24
        while [ "$n" -gt 0 ]; do
            out=$(sh_vm 'podman ps --format "{{.Names}}" | sort | tr "\n" " "')
            grep -qw kn-pihole <<< "$out" && grep -qw kn-nginx <<< "$out" && grep -qw kn-control <<< "$out" && break
            sleep 5; n=$((n - 1))
        done
        say "    containers: $out"
        grep -qw kn-pihole <<< "$out" && grep -qw kn-nginx <<< "$out" && grep -qw kn-control <<< "$out" && ! grep -qw kn-vpn-client <<< "$out"
        check $? "Pi-hole, nginx and the control part run; no VPN client"
        # what a device at home is told (the machine itself has its own names on 127.0.0.1)
        out=$(sh_vm "podman exec kn-pihole dig +short +time=3 @127.0.0.1 control.$node.home | tail -1")
        [ "$out" = 10.0.2.15 ]; check $? "its Pi-hole answers control.$node.home with its home address ($out)"
        out=$(sh_vm "curl -sk -o /dev/null -w '%{http_code}' --max-time 20 --resolve control.$node.home:443:10.0.2.15 https://control.$node.home/login")
        [ "$out" = 200 ]; check $? "the control page opens from the home network ($out)"
    }
    alone_checks
    before=$(grep -ac 'login:' "$log")
    python3 - "$w/monitor.sock" <<'EOF2'
import socket, sys, time
s = socket.socket(socket.AF_UNIX); s.connect(sys.argv[1]); time.sleep(0.5); s.sendall(b"system_reset\n"); time.sleep(1)
EOF2
    n=60; while [ "$n" -gt 0 ] && [ "$(grep -ac 'login:' "$log")" -le "$before" ]; do sleep 5; n=$((n - 1)); done
    tail -c 400 "$log" | grep -aq "^$node login:"; check $? "after a hard reset its console shows the node's login"
    alone_checks
    say "done after $(( $(date +%s) - t0 )) s; the VM keeps running (stop ends it)"
    ;;
stop) stop ;;
all)
    for c in build install again two-disks small windows typed; do bash "$self" "$w" "$c" || fails=$((fails + 1)); done
    ;;
*) echo "unknown: $cmd" >&2; exit 2 ;;
esac
exit "$fails"
