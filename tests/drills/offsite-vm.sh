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
        -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:${P_SSH:-2290}-:22 -display none -serial file:"$1" \
        -daemonize -pidfile qemu.pid
}
# what the machine itself says, through QEMU's forwarded port (only this computer reaches it)
inside() { ssh -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
               -i "$fd/secrets/admin_ed25519" -p "${P_SSH:-2290}" core@127.0.0.1 "$1" 2>/dev/null; }
trap 'if [ "${KEEP:-0}" != 1 ] && [ "$fails" = 0 ]; then stop; fi' EXIT

# ---- the machine, at a site of its own --------------------------------------------------------
"${ks[@]}" add "$F" node "$N" --preset minimal --target debian >add.log 2>&1; check $? "$N added to the fleet (a node, Debian)"
python3 "$HOME/kiwi-dev/tools/fleet-edit.py" "$F" "$N" 'network.dhcp=true' 'network.site="away"' 'disk="/dev/vda"' \
    'install.on_done="poweroff"' >/dev/null; check $? "$N stands at the site away, its address from the router there"
"${ks[@]}" enroll "$F" "$N" >enroll.log 2>&1; check $? "$N enrolled: its place on the VPN"
mesh=$("${ks[@]}" list "$F" --porcelain 2>/dev/null | python3 -c "import json,sys; print(next(h['vpn_ip'] for h in json.load(sys.stdin)['hosts'] if h['name']=='$N'))")
say "its mesh address: $mesh"
"${ks[@]}" build "$F" "$N" >build.log 2>&1; check $? "its install media built"
iso="$fd/output/$N/$N.iso"
[ -f "$iso" ]; check $? "the ISO is there"

# ---- installed behind QEMU's NAT: nobody at home reaches 10.0.2.15 --------------------------------
rm -f disk.qcow2; qemu-img create -q -f qcow2 disk.qcow2 20G
cp "$OVMF/OVMF_VARS.fd" vars.fd
vm install.log cd; check $? "the VM starts from the media"
for _ in $(seq 1 180); do [ -s qemu.pid ] && kill -0 "$(cat qemu.pid)" 2>/dev/null || break; sleep 10; done
! { [ -s qemu.pid ] && kill -0 "$(cat qemu.pid)" 2>/dev/null; }; check $? "the install ended and switched it off"
rm -f qemu.pid
vm boot.log disk; check $? "it starts from its disk"

# ---- its first apply comes from a computer at its site: home cannot reach it before it is on the VPN ----
# (here QEMU's forwarded port stands for a laptop on that LAN; later the join token makes this unneeded)
for _ in $(seq 1 60); do inside true && break; sleep 10; done
"${ks[@]}" apply "$F" "$N" --ssh "ssh://core@127.0.0.1:${P_SSH:-2290}" --new-host-key >first-apply.log 2>&1
check $? "its first apply, from a computer at its site"

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
fi
say "$fails wrong"
exit $((fails > 0))
}
