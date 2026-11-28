#!/usr/bin/env bash
# Install media for a person's computer (docs/design/clients.md, "Install media"): `kiwi-server
# client iso` makes a stick from Fedora's installer that installs SYSTEM (Bluefin by default) on a
# new VM, encrypted, and the computer joins FLEET at its first start. The drill types the disk's
# passphrase at the boot prompt (qemu's monitor, as a keyboard would) and waits until the control
# node lists the computer with its system. Afterwards NAME is out of the fleet file again; the VM
# and its disk stay on VMHOST for a look (FRESH=1 the next time installs anew).
#
#   [VMHOST=sh3] tests/drills/client-iso-vm.sh FLEET BASE_ISO [NAME [SYSTEM]]
#   e.g. tests/drills/client-iso-vm.sh ~/kiwi/fleet.yaml ~/kiwi-vm/desktop/iso/Fedora-Silverblue-45.iso
#
# Needs: FLEET's store open and joined to a control node of 2.332.0 or later; the install pulls
# SYSTEM's image from its registry (some gigabytes). The disk's passphrase is the drill's test
# value, on the stick. KIWI_SERVER: the kiwi-server to use. One PASS or FAIL line per check.
set -u
fleet=${1:?the fleet file}; base=${2:?a Fedora installer ISO}; name=${3:-desk-iso}; system=${4:-bluefin}
here=$(cd "$(dirname "$0")/../.." && pwd)
KIWI=${KIWI_SERVER:-kiwi-server}
w=$HOME/kiwi-vm/desktop/$name; mkdir -p "$w"; cd "$w" || exit 2
VMHOST=${VMHOST-sh3}
VD=${VD:-$( [ -n "$VMHOST" ] && echo "/mnt/nvme_2tb/share/virt/kiwi-desktop/$name" || echo "$w/vm" )}
PSSH=${PSSH:-18351}          # (no login into it: what it says comes through the control node)
PASS_PHRASE=kiwitest          # a test value: on the stick, typed at the VM's boot prompt
# shellcheck source=tests/drills/lib-desktop-vm.sh
. "$here/tests/drills/lib-desktop-vm.sh"
before=$(mktemp "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/kiwi-fleet.XXXXXX") || exit 2
secret=$(mktemp "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/kiwi-pass.XXXXXX") || exit 2
done_() {
    "$KIWI" client remove "$fleet" "$name" >/dev/null 2>&1
    [ -s "$before" ] && ! cmp -s "$before" "$fleet" && cp "$before" "$fleet" && "$KIWI" control push "$fleet" >/dev/null 2>&1
    rm -f "$before" "$secret"; vm_stop 2>/dev/null; true
}
trap done_ EXIT
keys() {        # TEXT: typed on the VM's screen, as a keyboard (qemu's monitor), then Enter
    vh "python3 -c 'import socket,sys,time
s=socket.socket(socket.AF_UNIX); s.connect(\"monitor.sock\"); time.sleep(0.3); s.recv(4096)
for ch in sys.argv[1]: s.send((\"sendkey \" + ch + \"\\n\").encode()); time.sleep(0.15)
s.send(b\"sendkey ret\\n\"); time.sleep(0.5); s.close()' '$1'"
}
listed() {      # the control node's row of NAME, as "state|os", once a report of this run came
    "$KIWI" client list "$fleet" --porcelain 2>/dev/null | python3 -c '
import json, sys
r = next((x for x in json.load(sys.stdin)["clients"] if x["name"] == sys.argv[1]), None)
if r and r["state"] == "on":
    print("%s|%s" % (r["state"], r["facts"].get("os", "")))' "$name"
}

vh_prepare; rc=$?; check $rc "the VM's folder on ${VMHOST:-this computer}: $VD"; [ $rc = 0 ] || exit 1
"$KIWI" control pull "$fleet" >/dev/null 2>&1 || true
cp "$fleet" "$before"
"$KIWI" client add "$fleet" "$name" >/dev/null && "$KIWI" control push "$fleet" >/dev/null 2>&1
check $? "$name is a client of the fleet"
printf '%s' "$PASS_PHRASE" > "$secret"
"$KIWI" client iso "$fleet" "$name" --system "$system" --base "$base" --output "$w/$name.iso" --keymap us \
    --passphrase-file "$secret" --days 1 > "$w/iso.log" 2>&1; rc=$?
check $rc "kiwi-server client iso makes the stick$([ $rc = 0 ] || echo ": $(tail -n 2 "$w/iso.log" | tr '\n' ' ' | cut -c1-200)")"
[ $rc = 0 ] || exit 1
xorriso -osirrox on -indev "$w/$name.iso" -extract /ks.cfg "$w/ks.cfg" >/dev/null 2>&1
grep -q "ostreecontainer --url=" "$w/ks.cfg"; check $? "its kickstart installs $system from its registry"

vh_put "$w/$name.iso" install.iso; check $? "the stick on ${VMHOST:-this computer}"
vh "rm -f disk.qcow2; qemu-img create -f qcow2 disk.qcow2 64G >/dev/null && cp ${OVMF_PAIR##*:} vars.fd"
say "installing $system from its registry (gigabytes: a while)"
t0=$(date +%s)
vm_start install.log -drive if=none,id=cd,format=raw,readonly=on,media=cdrom,file=install.iso \
    -device ide-cd,drive=cd,bootindex=0
# the installer reboots when it is done (reboot --eject): the disk asks for its passphrase then
n=5400; while [ "$n" -gt 0 ]; do
    vm_running || break
    if vh "python3 -c 'import socket,time
s=socket.socket(socket.AF_UNIX); s.connect(\"monitor.sock\"); time.sleep(0.3); s.recv(4096)
s.send(b\"info block\\n\"); time.sleep(0.5); print(s.recv(8192).decode(errors=\"replace\"))'" 2>/dev/null | grep -q "cd.*not inserted\|cd.*\[not inserted\]"; then break; fi
    sleep 30; n=$((n - 30))
done
[ "$n" -gt 0 ] && vm_running; check $? "installed, the stick ejected ($(( ($(date +%s) - t0) / 60 )) minutes)"
sleep 60
keys "$PASS_PHRASE"; say "        the passphrase typed at its boot prompt"
n=60; got=
while [ "$n" -gt 0 ]; do got=$(listed); [ -n "$got" ] && break; sleep 10; n=$((n - 1)); done
[ -n "$got" ]; check $? "it joined at its first start: the control node lists $name as on"
case ${got#*|} in *[Bb]luefin*|*Fedora*|*Silverblue*) rc=0 ;; *) rc=1 ;; esac
check $rc "its system: ${got#*|}"
[ "$fails" = 0 ] && say "all passed" || say "$fails wrong"
exit $((fails > 0))
