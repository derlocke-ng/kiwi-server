#!/usr/bin/env bash
# A Fedora Silverblue desktop whose encrypted disk opens with rings: the same policy and the same
# scenarios as desktop-clevis-vm.sh (Debian), on Fedora's image-based desktop. Installed from the
# Silverblue installer ISO with a kickstart (LUKS2 on btrfs); clevis layered with rpm-ostree and
# its dracut parts put into the boot system (rpm-ostree initramfs). One PASS or FAIL line per check.
#
#   VMHOST=sh3 tests/drills/desktop-silverblue-vm.sh ISO [WORKDIR]   (default ~/kiwi-vm/desktop/silverblue)
set -u
{
here=$(cd "$(dirname "$0")/../.." && pwd)
iso=$(realpath "${1:?the Silverblue installer ISO}")
w=${2:-$HOME/kiwi-vm/desktop/silverblue}
mkdir -p "$w"; cd "$w" || exit 2
VMHOST=${VMHOST-sh3}
VD=${VD:-$( [ -n "$VMHOST" ] && echo /mnt/nvme_2tb/share/virt/kiwi-desktop/silverblue || echo "$w/vm" )}
PSSH=${PSSH:-18342}
KSPORT=7620
PASS_PHRASE=kiwitest
KEY=$w/admin_ed25519
admin=kiwi
# shellcheck source=tests/drills/lib-desktop-vm.sh
. "$here/tests/drills/lib-desktop-vm.sh"
cleanup() { desk_cleanup; [ -n "${KS:-}" ] && kill "$KS" 2>/dev/null; true; }
trap cleanup EXIT

vh_prepare; check $? "the VM's folder on ${VMHOST:-this computer}: $VD"
tunnel_up 7611 7612 7613 7614 $KSPORT; check $? "the test rings and the kickstart reach ${VMHOST:-the VM}"

[ -z "${FRESH-}" ] || vh 'rm -f disk.qcow2.installed bound'    # FRESH=1: install anew
if ! vh '[ -e disk.qcow2.installed ]'; then
    vh 'rm -f bound'    # a new disk is not bound yet
    [ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N "" -C kiwi-desktop-drill -f "$KEY"
    label=$(xorriso -indev "$iso" -pvd_info 2>/dev/null | sed -n 's/^Volume Id *: //p')
    mkdir -p ks
    cat > ks/ks.cfg <<EOF
text
lang en_US.UTF-8
keyboard us
timezone UTC --utc
network --bootproto=dhcp --device=link --activate
rootpw --lock
user --name=$admin --groups=wheel --password=$PASS_PHRASE --plaintext
sshkey --username=$admin "$(cat "$KEY.pub")"
zerombr
clearpart --all --initlabel --disklabel=gpt
autopart --type=btrfs --encrypted --passphrase=$PASS_PHRASE --luks-version=luks2
bootloader --append="console=tty0 console=ttyS0,115200n8 rd.neednet=1 ip=dhcp"
ostreesetup --osname=fedora --url=file:///ostree/repo --ref=fedora/45/x86_64/silverblue --remote=fedora --nogpg
services --enabled=sshd
poweroff
%post
# Silverblue has sshd (GNOME's Remote Login switches it); services --enabled did not take on an ostree install
systemctl enable sshd.service
echo "$admin ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/$admin
chmod 0440 /etc/sudoers.d/$admin
%end
EOF
    (cd ks && exec python3 -m http.server "$KSPORT" --bind 127.0.0.1 >/dev/null 2>&1) &
    KS=$!
    mkdir -p boot
    xorriso -osirrox on -indev "$iso" -extract /images/pxeboot/vmlinuz boot/vmlinuz -extract /images/pxeboot/initrd.img boot/initrd.img >/dev/null 2>&1
    vh_put "$iso" install.iso && vh_put boot/vmlinuz vmlinuz && vh_put boot/initrd.img initrd.img; check $? "the ISO and its kernel on ${VMHOST:-this computer}"
    vh "rm -f disk.qcow2; qemu-img create -f qcow2 disk.qcow2 40G >/dev/null && cp ${OVMF_PAIR##*:} vars.fd"
    say "installing Silverblue (from the ISO, no network needed: some minutes)"
    vm_start install.log -drive if=none,id=cd,format=raw,readonly=on,file=install.iso -device ide-cd,drive=cd \
        -kernel vmlinuz -initrd initrd.img \
        -append "'inst.stage2=hd:LABEL=$label inst.ks=http://10.0.2.2:$KSPORT/ks.cfg inst.text console=ttyS0,115200'"
    n=3600; while vm_running && [ "$n" -gt 0 ]; do sleep 30; n=$((n - 30)); done
    [ "$n" -gt 0 ]; check $? "installed and powered off by itself"
    kill "$KS" 2>/dev/null; KS=
    vh 'rm -f qemu.pid; touch disk.qcow2.installed'
fi

if ! vh '[ -s bound ]'; then
    rings health1 health2 auto emergency
    vm_start boot1.log
    vm_wait_log boot1.log "$PROMPT" 300; check $? "it asks for its passphrase at boot"
    vm_type "$PASS_PHRASE"
    up_within 300; rc=$?; check $rc "the passphrase opens it, and ssh answers"
    [ $rc = 0 ] || { say "$fails wrong (stopped: no way in)"; exit 1; }
    vssh "rpm -q clevis clevis-luks clevis-dracut" > clevis.log 2>&1; have=$?
    say "clevis in the image: $( [ $have = 0 ] && echo yes || echo "no: layered with rpm-ostree" )"
    if [ $have != 0 ]; then
        vssh "sudo rpm-ostree install -y --idempotent clevis clevis-luks clevis-dracut" >>clevis.log 2>&1; check $? "clevis layered"
    fi
    vssh "sudo rpm-ostree initramfs --enable --arg=-a --arg=clevis --arg=-a --arg=clevis-pin-tang --arg=-a --arg=clevis-pin-sss" >>clevis.log 2>&1
    check $? "the boot system made here, with clevis and its tang and sss pins"
    vssh "sudo systemctl poweroff" 2>/dev/null
    n=180; while vm_running && [ "$n" -gt 0 ]; do sleep 5; n=$((n - 5)); done
    vm_start boot2.log
    vm_wait_log boot2.log "$PROMPT" 300; check $? "it asks again, from the new deployment"
    vm_type "$PASS_PHRASE"
    up_within 300; check $? "after the reboot into the new deployment the passphrase opens it"
    dev=$(vssh "sudo blkid -t TYPE=crypto_LUKS -o device" | head -1)
    printf '%s' "$PASS_PHRASE" | vssh "sudo clevis luks bind -y -k - -d $dev sss '$POLICY'" >bind.log 2>&1
    rc=$?; check $rc "bound: (health1 and health2 and auto) or emergency"
    [ $rc = 0 ] || { say "$fails wrong (stopped: not bound)"; exit 1; }
    vh "echo $dev > bound"
    vssh "sudo systemctl poweroff" 2>/dev/null; sleep 20
fi

scenarios "$PASS_PHRASE"
vssh "systemctl is-active gdm" 2>/dev/null | grep -q active; check $? "it is a desktop (gdm runs)"
say "$fails wrong"
exit $((fails > 0))
} 2>&1
