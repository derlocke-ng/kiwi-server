#!/usr/bin/env bash
# A node at another site (docs/network.md, "A machine at another site"): a Debian node in a local
# QEMU VM behind QEMU's own NAT (10.0.2.0/24, as behind a friend's router), joined to a real network
# through its master. Nothing at home reaches its LAN; everything goes over the mesh. Checks: it
# joins the mesh, this computer and the control node reach it there, the home's DNS answers its
# mesh address and not a LAN one, its proxy answers over the mesh. Then it leaves the network again
# (KEEP=1 keeps it, and so does a failed check, for a look). Uses this checkout's kiwi-server; touches the fleet only by adding and
# removing this one machine; runs nothing on this computer but QEMU. One PASS or FAIL line per check.
#
#   tests/drills/offsite-vm.sh FLEET CONTROL_ADDR HOME_DNS [NAME] [WORKDIR]
#       CONTROL_ADDR: the control node's LAN address; HOME_DNS: a DNS server at home (the gateway's)
#       NAME: kumquat by default; WORKDIR: ~/kiwi-vm/offsite         (~30 to 60 minutes, most of it Debian's install)
#   After a failed check the machine stays: kiwi-server remove FLEET NAME --yes takes it out again
#   OVER_VPN=1: install.over_vpn: its media bring a one-time join peer, it comes onto the VPN by itself,
#       and its first setup is `kiwi-server next` from home over the mesh (no computer at its site);
#       afterwards the join tunnel and peer are gone and it answers at its own mesh address
#   ENCRYPT=RING: its disk encrypted (LUKS), opened at boot by RING, a ring at home that the master relays
#       over the mesh (it keeps no key): the master is applied so that it relays, the first boot gets its
#       one-time key through the initramfs's SSH from a computer at its site, the first apply binds the
#       ring through the master, a restart must open the disk by itself; at the end the master again
set -u
{
here=$(cd "$(dirname "$0")/../.." && pwd)
F=$(readlink -f "$1"); CONTROL=$2; HOMEDNS=$3; N=${4:-kumquat}; w=${5:-$HOME/kiwi-vm/offsite}
mkdir -p "$w"; cd "$w" || exit 2
fd=$(dirname "$F")
OVMF=/usr/share/edk2/ovmf
ks=("$here/bin/kiwi-server")
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
stop() { [ -s qemu.pid ] && { kill "$(cat qemu.pid)" 2>/dev/null; sleep 2; kill -9 "$(cat qemu.pid)" 2>/dev/null; rm -f qemu.pid; }; true; }
ssh_to() {      # ADDR CMD: the fleet's own key and known hosts
    ssh -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=10 -o UserKnownHostsFile="$fd/secrets/known_hosts" \
        -i "$fd/secrets/admin_ed25519" "$1" "$2"
}
vm() {      # LOG BOOT(cd|disk)
    stop
    local boot=()
    [ "$2" = cd ] && boot=(-drive if=none,id=cd,format=raw,readonly=on,file="$iso" -device ide-cd,drive=cd,bootindex=0)
    qemu-system-x86_64 -enable-kvm -machine q35 -cpu host -smp 2 -m 2048 \
        -drive if=pflash,format=raw,readonly=on,file="$OVMF/OVMF_CODE.fd" -drive if=pflash,format=raw,file=vars.fd \
        "${boot[@]}" -drive if=none,id=disk,format=qcow2,file=disk.qcow2 -device virtio-blk-pci,drive=disk,bootindex=1 \
        -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:${P_SSH:-2290}-:22,hostfwd=tcp:127.0.0.1:${P_DROP:-2292}-:2222 \
        -display none -serial file:"$1" -daemonize -pidfile qemu.pid
}
# what the machine itself says, through QEMU's forwarded port (only this computer reaches it)
inside() { ssh -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
               -i "$fd/secrets/admin_ed25519" -p "${P_SSH:-2290}" core@127.0.0.1 "$1" 2>/dev/null; }
trap 'if [ "${KEEP:-0}" != 1 ] && [ "$fails" = 0 ]; then stop; fi' EXIT

# ---- the machine, at a site of its own --------------------------------------------------------
"${ks[@]}" add "$F" node "$N" --preset minimal --target debian >add.log 2>&1; check $? "$N added to the fleet (a node, Debian)"
python3 "$HOME/kiwi-dev/tools/fleet-edit.py" "$F" "$N" 'network.dhcp=true' 'network.site="away"' 'disk="/dev/vda"' \
    'install.on_done="poweroff"' >/dev/null; check $? "$N stands at the site away, its address from the router there"
if [ "${OVER_VPN:-0}" = 1 ]; then
    python3 "$HOME/kiwi-dev/tools/fleet-edit.py" "$F" "$N" 'install.over_vpn=true' >/dev/null
    check $? "its first setup comes over the VPN (install.over_vpn)"
fi
if [ -n "${ENCRYPT:-}" ]; then
    python3 "$HOME/kiwi-dev/tools/fleet-edit.py" "$F" "$N" 'debian.encrypt=true' \
        "unlock={\"rings\": [\"$ENCRYPT\"], \"threshold\": 1, \"ssh\": true}" >/dev/null
    check $? "its disk encrypted, opened at boot by the ring $ENCRYPT"
fi
"${ks[@]}" enroll "$F" "$N" >enroll.log 2>&1; check $? "$N enrolled: its place on the VPN"
master=$("${ks[@]}" list "$F" --porcelain 2>/dev/null | python3 -c "import json,sys; print(next(h['name'] for h in json.load(sys.stdin)['hosts'] if h['role']=='master' and not h.get('standby_of')))")
if [ -n "${ENCRYPT:-}" ]; then
    # the master relays the ring at home for it: applied now, so the port is there before the first bind
    python3 "$here/tests/drills/nginx-check.py" "$F" "$master" >nginx-check.log 2>&1 && \
        "${ks[@]}" apply "$F" "$master" >master-apply.log 2>&1
    check $? "the master $master relays the ring $ENCRYPT at home (applied)"
fi
mesh=$("${ks[@]}" list "$F" --porcelain 2>/dev/null | python3 -c "import json,sys; print(next(h['vpn_ip'] for h in json.load(sys.stdin)['hosts'] if h['name']=='$N'))")
say "its mesh address: $mesh"
"${ks[@]}" build "$F" "$N" >build.log 2>&1; check $? "its install media built"
iso="$fd/output/$N/$N.iso"
[ -f "$iso" ]; check $? "the ISO is there"
if [ "${OVER_VPN:-0}" = 1 ]; then
    jconf=$(ls "$fd"/secrets/join/"$N".*.conf 2>/dev/null | head -1)
    join=$(sed -n 's/^Address *= *\([0-9.]*\).*/\1/p' "$jconf" 2>/dev/null)
    [ -n "$join" ]; check $? "its one-time join peer is made (${join:-none})"
fi

# ---- installed behind QEMU's NAT: nobody at home reaches 10.0.2.15 --------------------------------
rm -f disk.qcow2; qemu-img create -q -f qcow2 disk.qcow2 20G
cp "$OVMF/OVMF_VARS.fd" vars.fd
vm install.log cd; check $? "the VM starts from the media"
for _ in $(seq 1 180); do [ -s qemu.pid ] && kill -0 "$(cat qemu.pid)" 2>/dev/null || break; sleep 10; done
! { [ -s qemu.pid ] && kill -0 "$(cat qemu.pid)" 2>/dev/null; }; check $? "the install ended and switched it off"
rm -f qemu.pid
vm boot.log disk; check $? "it starts from its disk"
# for later: after this computer restarts, ./run.sh in the VM's folder starts the machine again
cat > run.sh <<RUN
#!/bin/bash
# $N, the offsite test node (tests/drills/offsite-vm.sh): start it again after this computer restarts.
cd "\$(dirname "\$0")" || exit 1
if [ -f qemu.pid ] && kill -0 "\$(cat qemu.pid)" 2>/dev/null; then echo "$N runs already"; exit 0; fi
exec qemu-system-x86_64 -enable-kvm -machine q35 -cpu host -smp 2 -m 2048 \\
    -drive if=pflash,format=raw,readonly=on,file=$OVMF/OVMF_CODE.fd -drive if=pflash,format=raw,file=vars.fd \\
    -drive if=none,id=disk,format=qcow2,file=disk.qcow2 -device virtio-blk-pci,drive=disk,bootindex=1 \\
    -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:${P_SSH:-2290}-:22,hostfwd=tcp:127.0.0.1:${P_DROP:-2292}-:2222 \\
    -display none -serial file:boot-run.log -daemonize -pidfile qemu.pid
RUN
chmod +x run.sh

if [ -n "${ENCRYPT:-}" ]; then
    # nothing is bound yet: the one-time key, from a computer at its site (QEMU's port stands for it)
    "${ks[@]}" unlock "$F" "$N" --manual --address 127.0.0.1 --port "${P_DROP:-2292}" --timeout 900 >unlock-first.log 2>&1
    check $? "its first boot: the one-time key through the initramfs's SSH"
fi
if [ "${OVER_VPN:-0}" = 1 ]; then
    # ---- it comes onto the VPN by itself; home sets it up over the mesh, nobody at its site -----------
    seen=1
    for _ in $(seq 1 90); do timeout 3 bash -c "echo > /dev/tcp/$join/22" 2>/dev/null && { seen=0; break; }; sleep 10; done
    check $seen "the bare system is on the VPN at its join address $join"
    "${ks[@]}" next "$F" "$N" </dev/null >first-apply.log 2>&1
    check $? "its first setup, by kiwi-server next from home over the mesh"
    sleep 20
    [ ! -e "$jconf" ]; check $? "its one-time join peer is gone from the fleet's secrets"
    inside 'ip link show kiwi-join' >/dev/null 2>&1; [ $? != 0 ]; check $? "its join tunnel is gone on the machine"
else
# ---- its first apply comes from a computer at its site: home cannot reach it before it is on the VPN ----
# (here QEMU's forwarded port stands for a laptop on that LAN)
for _ in $(seq 1 60); do inside true && break; sleep 10; done
"${ks[@]}" apply "$F" "$N" --ssh "ssh://core@127.0.0.1:${P_SSH:-2290}" --new-host-key >first-apply.log 2>&1
check $? "its first apply, from a computer at its site"
fi

# ---- it joins the mesh through the master, from behind someone else's router ----------------------
up=1
# (a node answers no ping: its SSH over the mesh is the question)
for _ in $(seq 1 240); do ssh_to "core@$mesh" true >/dev/null 2>&1 && { up=0; break; }; sleep 10; done
check $up "it answers at its mesh address $mesh (its tunnel to the master is up)"
if [ "$up" != 0 ]; then         # why not: its first-boot role and its tunnel, as it says itself
    inside 'sudo systemctl is-active kiwi-role.service; sudo journalctl -u kiwi-role.service -n 25 --no-pager -o cat; sudo podman ps --format "{{.Names}} {{.Status}}"' >inside.log
    say "its own words are in $w/inside.log"
fi
ok=1
for _ in $(seq 1 30); do
    "${ks[@]}" status "$F" "$N" --porcelain >status.json 2>/dev/null
    python3 -c "import json,sys; sys.exit(0 if json.load(open('status.json'))['hosts'][0]['ok'] else 1)" 2>/dev/null && { ok=0; break; }
    sleep 10
done
check $ok "kiwi-server reaches it over the mesh (status)"
python3 -c "import json; h=json.load(open('status.json'))['hosts'][0]; print(h['state'], [p['text'] for p in h['problems']])" 2>/dev/null
ssh_to "core@$mesh" 'ip -4 route show default' 2>/dev/null | grep -q "via 10.0.2.2"; check $? "its way out is the other site's router (10.0.2.2), not home"

# ---- home learns its name with the next apply of the DNS machines, and never a LAN address ---------
pending=$("${ks[@]}" list "$F" --porcelain 2>/dev/null | python3 -c "import json,sys; print(' '.join(json.load(sys.stdin).get('changed') or []))")
say "machines whose DNS learns $N with their next apply: ${pending:-none}"
got=$(dig +short "@$HOMEDNS" "$N.home" 2>/dev/null | head -1)
case "$got" in
    "$mesh") check 0 "the home's DNS ($HOMEDNS) answers $N.home with its mesh address" ;;
    "") say "the home's DNS does not know $N.home yet (apply: $pending)" ;;
    *) check 1 "the home's DNS answers $N.home with $got, not its mesh address" ;;
esac
code=$(curl -sk -o /dev/null -w '%{http_code}' -m 15 --resolve "$N.home:443:$mesh" "https://$N.home/" 2>/dev/null)
[ -n "$code" ] && [ "$code" != 000 ]; check $? "its proxy answers over the mesh (https $code)"

# ---- an encrypted one: the ring at home opens it after a restart, through the master, nobody types --
if [ -n "${ENCRYPT:-}" ]; then
    dev=$(ssh_to "core@$mesh" "sudo blkid -t TYPE=crypto_LUKS -o device | head -1" 2>/dev/null)
    [ -n "$dev" ]; check $? "its disk is LUKS ($dev)"
    ssh_to "core@$mesh" "sudo clevis luks list -d $dev" >clevis.txt 2>&1
    grep -q tang clevis.txt; check $? "bound to a ring: $(tr '\n' ' ' <clevis.txt | cut -c1-160)"
    ssh_to "core@$mesh" "sudo systemctl reboot" >/dev/null 2>&1
    sleep 20
    back=1
    for _ in $(seq 1 60); do ssh_to "core@$mesh" true >/dev/null 2>&1 && { back=0; break; }; sleep 10; done
    check $back "after a restart it opened its disk by itself and is on the mesh again (the ring $ENCRYPT, relayed)"
    ssh_to "core@$mesh" "sudo journalctl -b -o cat --no-pager | grep -i -m3 -E 'clevis|tang'" >reboot-unlock.txt 2>/dev/null
fi

# ---- the control node at home reaches it too ----------------------------------------------------
"${ks[@]}" control push "$F" >push.log 2>&1; check $? "the control node has the fleet with $N"
ssh_to "core@$CONTROL" "sudo podman exec kn-control kiwi-server status /fleet/fleet.yaml $N --porcelain" >control-status.json 2>control.err
python3 -c "import json,sys; sys.exit(0 if json.load(open('control-status.json'))['hosts'][0]['ok'] else 1)" 2>/dev/null
check $? "the control node reaches it over the mesh"

# ---- and it leaves again ----------------------------------------------------------------------
if [ "${KEEP:-0}" != 1 ] && [ "$fails" = 0 ]; then
    stop
    "${ks[@]}" remove "$F" "$N" --yes >remove.log 2>&1; check $? "$N left the network (off the VPN, out of the fleet file)"
    "${ks[@]}" control push "$F" >>push.log 2>&1
    if [ -n "${ENCRYPT:-}" ]; then          # nobody needs the relay any more: the master closes it
        "${ks[@]}" apply "$F" "$master" >master-apply-after.log 2>&1; check $? "the master no longer relays the ring"
    fi
fi
say "$fails wrong"
exit $((fails > 0))
}
