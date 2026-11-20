#!/usr/bin/env bash
# A Debian desktop (GNOME) whose encrypted disk opens with rings (docs/design/unlock.md,
# desktops). Its root is in LUKS, bound with clevis to four test rings that run here in podman:
#
#   health1, health2   machines that must be up: it boots only when everything runs
#   auto               its auto-unlock ring: up while auto-unlock is on for it
#   emergency          its "Unlock now" ring: up for a while after the button
#
# Policy: (health1 and health2 and auto) or emergency. The passphrase always works too.
# The VM runs on VMHOST (default sh3, disks under /mnt/nvme_2tb/share/virt; VMHOST= runs it
# here); tests/drills/lib-desktop-vm.sh has the parts. One PASS or FAIL line per check.
#
#   VMHOST=sh3 tests/drills/desktop-clevis-vm.sh [WORKDIR]     (default ~/kiwi-vm/desktop/debian)
set -u
{
here=$(cd "$(dirname "$0")/../.." && pwd)
w=${1:-$HOME/kiwi-vm/desktop/debian}
mkdir -p "$w"; cd "$w" || exit 2
VMHOST=${VMHOST-sh3}
VD=${VD:-$( [ -n "$VMHOST" ] && echo /mnt/nvme_2tb/share/virt/kiwi-desktop/debian || echo "$w/vm" )}
PSSH=${PSSH:-18341}
PASS_PHRASE=kiwitest          # a test value, typed at the VM's console
KEY=$w/fleet/secrets/admin_ed25519
ks=("$here/bin/kiwi-server")
# shellcheck source=tests/drills/lib-desktop-vm.sh
. "$here/tests/drills/lib-desktop-vm.sh"
trap desk_cleanup EXIT

vh_prepare; check $? "the VM's folder on ${VMHOST:-this computer}: $VD"
tunnel_up 7611 7612 7613 7614; check $? "the test rings reach ${VMHOST:-the VM}"

# ---- the desktop's install media: kiwi-server's Debian, encrypted, with GNOME and clevis ------
if ! vh '[ -s disk.qcow2.installed ]'; then
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
               "packages": ["clevis", "clevis-luks", "clevis-initramfs"],
               "kernel_arguments": ["ip=::::::dhcp", "console=tty0", "console=ttyS0,115200n8"]}
open(sys.argv[1], "w").write(yaml.safe_dump(d, sort_keys=False))
EOF
    "${ks[@]}" build fleet/fleet.yaml desk >build.log 2>&1; check $? "its install media built"
    iso=$(find fleet -name "desk*.iso" | head -1)
    [ -n "$iso" ] || { say "FAIL no ISO: see build.log"; exit 1; }
    vh_put "$iso" install.iso; check $? "the ISO on ${VMHOST:-this computer}"
    vh "rm -f disk.qcow2; qemu-img create -f qcow2 disk.qcow2 40G >/dev/null && cp ${OVMF_PAIR##*:} vars.fd"
    say "installing Debian (encrypted, over the net: about 15 minutes)"
    vm_start install.log -drive if=none,id=cd,format=raw,readonly=on,file=install.iso -device ide-cd,drive=cd,bootindex=0
    n=2400; while vm_running && [ "$n" -gt 0 ]; do sleep 30; n=$((n - 30)); done
    [ "$n" -gt 0 ]; check $? "installed and powered off by itself"
    vh 'rm -f qemu.pid; touch disk.qcow2.installed'
fi
admin=$(python3 -c "import yaml; print(yaml.safe_load(open('fleet/fleet.yaml'))['defaults']['admin']['user'])")

# ip=::::::dhcp, not ip=dhcp: clevis-initramfs (20) takes field 6 of ip= as the device to wait
# for, and `cut -d: -f6` of "dhcp" is "dhcp" (it waited for a device of that name, 20 Nov 2026)
# ---- first boot: the passphrase at its console, then clevis bound to the policy -----------------
if ! vh '[ -s bound ]'; then
    rings health1 health2 auto emergency          # binding asks every ring for its key once
    vm_start boot1.log
    vm_wait_log boot1.log "$PROMPT" 300; check $? "it asks for its passphrase at boot (not bound yet)"
    vm_type "$PASS_PHRASE"
    up_within 300; rc=$?; check $rc "the passphrase opens it"
    [ $rc = 0 ] || { say "$fails wrong (stopped: no way in)"; exit 1; }
    # the desktop itself, after the first start: in the installer one of GNOME's packages asks a
    # question no preseed answers, and the install waits for ever at "Preconfiguring packages"
    say "installing GNOME (about 20 minutes)"
    vssh "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q task-gnome-desktop" >gnome.log 2>&1; check $? "GNOME installed"
    dev=$(vssh "sudo blkid -t TYPE=crypto_LUKS -o device" | head -1)
    printf '%s' "$PASS_PHRASE" | vssh "sudo clevis luks bind -y -k - -d $dev sss '$POLICY'" >bind.log 2>&1
    check $? "bound: (health1 and health2 and auto) or emergency"
    vssh "sudo update-initramfs -u -k all" >>bind.log 2>&1; check $? "its boot system carries clevis"
    vh "echo $dev > bound"
    vssh "sudo systemctl poweroff" 2>/dev/null; sleep 20
fi

scenarios "$PASS_PHRASE"
vssh "systemctl is-active gdm" 2>/dev/null | grep -q active; check $? "it is a desktop (gdm runs)"
say "$fails wrong"
exit $((fails > 0))
} 2>&1
