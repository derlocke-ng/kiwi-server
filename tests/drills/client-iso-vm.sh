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
# phoenix (clevis in its boot system): KIWI_UNLOCK=1 (the default) binds its disk to the ring RING
# (home) and starts it again with nobody at its prompt. KIWI_SIGN_SWITCH=image: the stick leaves the
# switch to the signed origin to the image (phoenix-desktop's own service) instead of its install.
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
    [ -s "$before" ] && ! cmp -s "$before" "$fleet" && cp "$before" "$fleet"
    "$KIWI" control push "$fleet" >/dev/null 2>&1      # the control node forgets NAME too (it listed it after a run)
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
try:
    r = next((x for x in json.load(sys.stdin)["clients"] if x["name"] == sys.argv[1]), None)
except ValueError:      # no answer this time (a locked store says so in words): asked again
    r = None
if r and r["state"] == "on":
    print("%s|%s" % (r["state"], r["facts"].get("os", "")))' "$name"
}

vh_prepare; rc=$?; check $rc "the VM's folder on ${VMHOST:-this computer}: $VD"; [ $rc = 0 ] || exit 1
"$KIWI" control pull "$fleet" >/dev/null 2>&1 || true
cp "$fleet" "$before"
"$KIWI" client add "$fleet" "$name" >/dev/null && "$KIWI" control push "$fleet" >/dev/null 2>&1
check $? "$name is a client of the fleet"
printf '%s' "$PASS_PHRASE" > "$secret"
# the IT department's way in (--admin-key): the drill logs in through it after the install
KEY=$w/admin_ed25519; [ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N "" -C kiwi-client-iso-drill -f "$KEY"
"$KIWI" client iso "$fleet" "$name" --system "$system" --base "$base" --output "$w/$name.iso" --keymap us \
    --passphrase-file "$secret" --days 1 --admin-key "$KEY.pub" > "$w/iso.log" 2>&1; rc=$?
check $rc "kiwi-server client iso makes the stick$([ $rc = 0 ] || echo ": $(tail -n 2 "$w/iso.log" | tr '\n' ' ' | cut -c1-200)")"
[ $rc = 0 ] || exit 1
xorriso -osirrox on -indev "$w/$name.iso" -extract /ks.cfg "$w/ks.cfg" >/dev/null 2>&1
grep -q "ostreecontainer --url=" "$w/ks.cfg"; check $? "its kickstart installs $system from its registry"
# KIWI_SIGN_SWITCH=image: the stick leaves the switch to the signed origin to the image itself
# (phoenix-desktop's phoenix-signed-updates at the first start), as another installer would
if [ "${KIWI_SIGN_SWITCH:-install}" = image ]; then
    sed -i "/updates checked against the image's signature/d; /^bootc switch --mutate-in-place/d" "$w/ks.cfg"
    xorriso -indev "$w/$name.iso" -outdev "$w/$name.noswitch.iso" -boot_image any replay -map "$w/ks.cfg" /ks.cfg -end >/dev/null 2>&1 \
        && mv -f "$w/$name.noswitch.iso" "$w/$name.iso" && ! grep -q "bootc switch" "$w/ks.cfg"
    check $? "the stick leaves the switch to the signed origin to the image"
fi

vh_put "$w/$name.iso" install.iso; check $? "the stick on ${VMHOST:-this computer}"
vh "rm -f disk.qcow2; qemu-img create -f qcow2 disk.qcow2 64G >/dev/null && cp ${OVMF_PAIR##*:} vars.fd"
say "installing $system from its registry (gigabytes: a while)"
t0=$(date +%s)
vm_start install.log -drive if=none,id=cd,format=raw,readonly=on,media=cdrom,file=install.iso \
    -device ide-cd,drive=cd,bootindex=0
# the installer powers off when it is done: the stick comes out, the computer starts from its disk
n=5400; size=; still=0
while [ "$n" -gt 0 ]; do
    vm_running || break
    # the secret store locks itself after 30 idle minutes: kept in use while the install runs
    [ $((n % 300)) = 0 ] && "$KIWI" client list "$fleet" >/dev/null 2>&1
    # an installer that stopped on an error waits for Enter: the disk no longer grows (28 Nov 2026)
    now=$(vh 'stat -c %s disk.qcow2' 2>/dev/null)
    if [ -n "$now" ] && [ "$now" = "$size" ]; then still=$((still + 30)); else still=0; size=$now; fi
    if [ "$still" -ge 1200 ]; then
        vh "python3 -c 'import socket,time
s=socket.socket(socket.AF_UNIX); s.connect(\"monitor.sock\"); time.sleep(0.3); s.recv(4096)
s.send(b\"screendump $VD/stalled.ppm\\n\"); time.sleep(1.5); s.close()'"
        say "        the disk has not grown for 20 minutes: its screen is $VD/stalled.ppm on ${VMHOST:-this computer}"
        n=0; break
    fi
    sleep 30; n=$((n - 30))
done
[ "$n" -gt 0 ]; check $? "installed, and switched off by itself ($(( ($(date +%s) - t0) / 60 )) minutes)"
[ "$n" -gt 0 ] || exit 1
vh 'rm -f qemu.pid'
vm_start first.log
sleep 60
keys "$PASS_PHRASE"; say "        the passphrase typed at its boot prompt"
admin=kiwi
up_within 300; check $? "the IT department's key logs in (the local admin kiwi)"
vssh "sudo -n true"; check $? "and has sudo without a password"
case $system in phoenix*)      # Bluefin with clevis in its boot system (github.com/derlocke-ng/phoenix-desktop)
    vssh "grep -qw rd.neednet=1 /proc/cmdline"; check $? "$system: the network at boot (rd.neednet=1, the image's kargs.d)"
    vssh "{ sudo lsinitrd -m; sudo lsinitrd -m /usr/lib/modules/\$(uname -r)/initramfs.img; } 2>/dev/null | grep -qx clevis"
    check $? "$system: clevis in its boot system"
    ;;
esac
case $system in bluefin*|phoenix*)     # switched to the signed origin at the end of the install
    n=1; [ "${KIWI_SIGN_SWITCH:-install}" = image ] && n=18     # the image's own service, at the first start
    while [ "$n" -gt 0 ]; do vssh "rpm-ostree status 2>/dev/null | grep -q 'ostree-image-signed:'" && break; n=$((n - 1)); [ "$n" -gt 0 ] && sleep 10; done
    [ "$n" -gt 0 ]; check $? "$system: its updates are checked against the image's signature (rpm-ostree status)"
    if [ "${KIWI_SIGN_SWITCH:-install}" = image ]; then
        vssh "sudo journalctl -b --no-pager -u phoenix-signed-updates" > "$w/signed.log" 2>&1
        grep -q "now checked" "$w/signed.log"; check $? "the image switched itself: $(grep -v '^--' "$w/signed.log" | tail -n 1 | cut -c1-120)"
    fi ;;
esac
# a VM behind its host's NAT asks its host's resolver (on sh3: v1's, which knows no .home names)
url=$(sed -n 's|.*\(https://[^/]*\)/api/client/join/.*|\1|p' "$w/ks.cfg" | head -n 1); host=${url#https://}
addr=$(PYTHONPATH="$here/lib" python3 -c '
import sys
from kiwiserver import config, machineagent
f = config.Fleet.load(sys.argv[1]); h = machineagent.control_host(f); print(((h.cfg.get("network") or {}).get("address") or "").split("/")[0])' "$fleet")
# (v1's resolver answers "::" for them, so a name that "resolves" is not enough: the address must be the control node's)
vssh "[ \"\$(getent ahostsv4 $host | awk 'NR==1{print \$1}')\" = '$addr' ] || { echo '$addr $host' | sudo tee -a /etc/hosts >/dev/null; sudo systemctl restart kiwi-join-once.service; }"
n=60; got=
while [ "$n" -gt 0 ]; do got=$(listed); [ -n "$got" ] && break; sleep 10; n=$((n - 1)); done
[ -n "$got" ]; check $? "it joined at its first start: the control node lists $name as on"
if [ -z "$got" ]; then      # what the computer says, before the VM goes off
    vssh "getent ahostsv4 $host | head -n 1; sudo journalctl -b --no-pager -u kiwi-join-once -u kiwi-client-agent | tail -n 25" > "$w/join.log" 2>&1
    say "        its join's journal: $w/join.log"; sed 's/^/        | /' "$w/join.log" | tail -n 12
fi
case ${got#*|} in *[Bb]luefin*|*Fedora*|*Silverblue*) rc=0 ;; *) rc=1 ;; esac
check $rc "its system: ${got#*|}"

# ---- phoenix: its disk bound once with its passphrase to the network's ring RING (home: on lime),
# then started without anyone at its prompt: the ring opens it (clevis is in the image's boot system)
case $system in phoenix*) if [ "${KIWI_UNLOCK:-1}" = 1 ] && [ -n "$got" ]; then
    ring=${RING:-home}
    PYTHONPATH="$here/lib" python3 -c '
import sys
from kiwiserver import fleettext, util
path, name, ring = sys.argv[1:4]
new = fleettext.put(util.read_text(path), ["clients", name], {"unlock": {"rings": [ring]}})
if not new:
    sys.exit("the fleet file could not be changed in place")
util.write_text(path, new, 0o600)' "$fleet" "$name" "$ring" && "$KIWI" control push "$fleet" >/dev/null 2>&1
    check $? "$name's disk is to open by the network's ring $ring"
    # the computer got its agent from the control node, which may run an older kiwi-server than this
    # checkout: KIWI_AGENT=checkout (the default) puts this checkout's agent and facts on it first
    if [ "${KIWI_AGENT:-checkout}" = checkout ]; then
        PYTHONPATH="$here/lib" python3 -c 'from kiwiserver import clientagent as c; print(c.SCRIPT % {"dir": c.DIR, "version": c.VERSION}, end="")' \
            | vssh "sudo install -m 0755 /dev/stdin /usr/local/sbin/kiwi-client-agent" \
        && PYTHONPATH="$here/lib" python3 -c 'from kiwiserver import clientagent as c; print(c.FACTS % {"version": c.VERSION}, end="")' \
            | vssh "sudo install -m 0600 /dev/stdin /etc/kiwi-client/facts.sh"
        check $? "this checkout's agent on it"
    fi
    "$KIWI" control order "$fleet" "$name" profile >/dev/null 2>&1
    n=24; while [ "$n" -gt 0 ]; do vssh "grep -qx 'unlock waits' /run/kiwi-client/status" 2>/dev/null && break; vssh "sudo kiwi-client-agent now" >/dev/null 2>&1; sleep 5; n=$((n - 1)); done
    [ "$n" -gt 0 ]; check $? "its agent says the disk waits to be bound"
    printf '%s' "$PASS_PHRASE" | vssh "sudo /usr/local/sbin/kiwi-client-agent bind --stdin" > "$w/bind.log" 2>&1; rc=$?
    check $rc "bound with its passphrase$([ $rc = 0 ] || echo ": $(tail -n 2 "$w/bind.log" | tr '\n' ' ' | cut -c1-160)")"
    grep -q "already" "$w/bind.log"; check $? "nothing layered: clevis was in the image ($(head -n 1 "$w/bind.log" | cut -c1-80))"
    vssh "sudo systemctl poweroff" >/dev/null 2>&1
    n=60; while [ "$n" -gt 0 ] && vm_running; do sleep 2; n=$((n - 1)); done
    vm_stop; vh 'rm -f qemu.pid'
    vm_start ring.log
    t0=$(date +%s); up_within 300; rc=$?
    check $rc "it opens by the network's ring $ring, nobody at its prompt ($(( $(date +%s) - t0 )) s)"
    [ $rc = 0 ] || say "        its console: $(vh 'tail -c 2000 ring.log' | tr -d '\033' | tr '\r' '\n' | grep -v '^\s*$' | tail -n 3 | tr '\n' ' ' | cut -c1-200)"
fi ;; esac
[ "$fails" = 0 ] && say "all passed" || say "$fails wrong"
exit $((fails > 0))
