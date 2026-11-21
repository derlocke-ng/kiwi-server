#!/usr/bin/env bash
# A device behind the site's moving address (the vip module, docs/design/basics-twice.md): a
# throwaway VM on VMHOST (Debian's cloud image, cloud-init) at DEVICE/PREFIX with VIP as its gateway
# and DNS server. It fetches a page through VIP every second while FIRST's keepalived stops and
# starts again; the misses are counted. The VM and its volumes are removed afterwards.
#
#   VMHOST=sh3 tests/drills/vip-gateway.sh FLEET FIRST VIP DEVICE/PREFIX
#   e.g. lime 192.168.2.70 192.168.2.71/16
# The device's SSH host key is made here and given to it, so its key is known before it is asked.
# One PASS or FAIL line per check.
set -u
fleet=${1:?the fleet file}; first=${2:?the machine that holds the address}; vip=${3:?the moving address}
dev=${4:?the address of the device with its prefix}
VMHOST=${VMHOST:?the ssh name of the hypervisor}
V="virsh -c qemu:///system"
name=kiwi-device
image_url=${IMAGE_URL:-https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2}
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
is() { if "$@"; then echo 0; else echo 1; fi; }
dir=$(cd "$(dirname "$fleet")" && pwd); S=$(readlink -f "$dir/secrets")
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
user=$(python3 -c '
import sys, yaml
print(yaml.safe_load(open(sys.argv[1]))["defaults"]["admin"]["user"])' "$fleet")
lanof() { python3 -c '
import sys, yaml
h = yaml.safe_load(open(sys.argv[1]))["hosts"][sys.argv[2]]
print(str((h.get("network") or {}).get("address") or "").split("/")[0])' "$fleet" "$1"; }
on() {
    ssh -i "$S/admin_ed25519" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes \
        -o UserKnownHostsFile="$S/known_hosts" -o ConnectTimeout=8 "$user@$(lanof "$1")" "sudo sh -c $(printf '%q' "$2")"
}
addr=${dev%/*}
device() {
    ssh -i "$S/admin_ed25519" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes \
        -o UserKnownHostsFile="$work/known_hosts" -o ConnectTimeout=6 "kiwi@$addr" "$@"
}
gone() { ssh -o BatchMode=yes "$VMHOST" "$V destroy $name >/dev/null 2>&1; $V undefine $name >/dev/null 2>&1
    $V vol-delete $name.qcow2 default >/dev/null 2>&1; $V vol-delete $name-seed.iso default >/dev/null 2>&1; rm -rf /tmp/$name"; }

# the device: its own host key, the fleet's admin key to log in, a fixed address behind the moving one
ssh-keygen -q -t ed25519 -N '' -C "$name" -f "$work/host" >/dev/null
echo "$addr $(cut -d' ' -f1,2 "$work/host.pub")" > "$work/known_hosts"
python3 - "$work" "$(cat "$S/admin_ed25519.pub")" "$dev" "$vip" <<'EOF'
import os, sys
work, key, dev, vip = sys.argv[1:]
host = open(os.path.join(work, "host")).read()
pub = open(os.path.join(work, "host.pub")).read().strip()
ind = lambda text, n: "\n".join(" " * n + l for l in text.strip("\n").split("\n"))
open(os.path.join(work, "user-data"), "w").write("""#cloud-config
hostname: kiwi-device
users:
  - name: kiwi
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys: [%s]
ssh_deletekeys: true
ssh_keys:
  ed25519_private: |
%s
  ed25519_public: %s
""" % (key, ind(host, 4), pub))
open(os.path.join(work, "network-config"), "w").write("""version: 2
ethernets:
  lan:
    match: {name: "en*"}
    addresses: [%s]
    routes: [{to: default, via: %s}]
    nameservers: {addresses: [%s]}
""" % (dev, vip, vip))
open(os.path.join(work, "meta-data"), "w").write("instance-id: kiwi-device-1\nlocal-hostname: kiwi-device\n")
EOF
gone
ssh -o BatchMode=yes "$VMHOST" "mkdir -p /tmp/$name" && scp -q -o BatchMode=yes "$work/user-data" "$work/meta-data" "$work/network-config" "$VMHOST:/tmp/$name/"
check $? "the device's seed on $VMHOST"
ssh -o BatchMode=yes "$VMHOST" "set -e; cd /tmp/$name
  [ -s base.qcow2 ] || curl -fsSL -o base.qcow2 '$image_url'
  xorriso -as mkisofs -quiet -o seed.iso -V cidata -J -r user-data meta-data network-config
  $V vol-create-as default $name.qcow2 \$(stat -c %s base.qcow2) --format qcow2 >/dev/null
  $V vol-upload $name.qcow2 base.qcow2 --pool default
  $V vol-create-as default $name-seed.iso \$(stat -c %s seed.iso) --format raw >/dev/null
  $V vol-upload $name-seed.iso seed.iso --pool default
  virt-install --connect qemu:///system --name $name --memory 1024 --vcpus 1 --import --osinfo debian12 \
    --disk vol=default/$name.qcow2,bus=virtio --disk vol=default/$name-seed.iso,device=cdrom \
    --network bridge=br0,model=virtio --graphics none --noautoconsole >/dev/null"
check $? "the device runs on $VMHOST"
up=""
for _ in $(seq 1 40); do sleep 5; device true 2>/dev/null && { up=1; break; }; done
check "$(is [ -n "$up" ])" "the device answers at $addr (its host key as made here)"
[ -n "$up" ] || { gone; say "$fails wrong"; exit 1; }
device "ip route show default" | grep -q "via $vip"; check $? "its gateway is $vip"
device "getent hosts deb.debian.org >/dev/null"; check $? "it resolves a name through $vip"
device "curl -fsS -m 8 -o /dev/null https://deb.debian.org/debian/dists/stable/Release"; check $? "it reaches the Internet through $vip"
# a page every second through the moving address while it moves
device "nohup sh -c 'i=0; while [ \$i -lt 60 ]; do if curl -fsS -m 2 -o /dev/null https://deb.debian.org/debian/dists/stable/Release; then echo ok; else echo miss; fi; i=\$((i+1)); sleep 1; done' > /tmp/probe 2>&1 &"
sleep 8
on "$first" "systemctl stop kn-vip.service 2>/dev/null || docker stop kn-vip >/dev/null"; check $? "$first's moving address stopped"
sleep 20
on "$first" "systemctl start kn-vip.service 2>/dev/null || docker start kn-vip >/dev/null"; check $? "$first's moving address back"
sleep 35
probe=$(device "cat /tmp/probe")
oks=$(grep -c '^ok' <<<"$probe"); misses=$(grep -c '^miss' <<<"$probe")
say "        $oks of $((oks + misses)) fetches went through, $misses missed"
check "$(is [ "$misses" -le 6 ])" "the device lost at most a few seconds over two switches ($misses misses)"
gone; check $? "the device and its volumes are gone"
say "$fails wrong"
exit $((fails > 0))
