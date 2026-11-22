#!/usr/bin/env bash
# A device behind the site's moving address (the vip module, docs/design/basics-twice.md): on HOST, a
# test machine on the same LAN, a network namespace with an address of its own (a macvlan card, its
# own MAC) whose gateway and DNS server are VIP. It fetches a page through VIP every second while
# FIRST's keepalived stops and starts again; the misses are counted. HOST's own network is not
# touched; the namespace and its card are removed afterwards.
# (22 Nov 2026: first written with a VM on sh3, whose static address never came up and whose console
# could not be read; a namespace on a machine of the fleet needs neither.)
#
#   tests/drills/vip-gateway.sh FLEET HOST FIRST VIP DEVICE/PREFIX [OTHER_FLEET...]
#   e.g. ~/kiwi/fleet.yaml medlar lime 192.168.2.70 192.168.2.72/16 ~/kiwi-2/fleet.yaml
# DEVICE must be an address no machine of the fleets has, and nothing on the LAN may answer it.
# One PASS or FAIL line per check.
set -u
fleet=${1:?the fleet file}; host=${2:?a test machine on the LAN}; first=${3:?the machine that holds the address}
vip=${4:?the moving address}; dev=${5:?the address of the device with its prefix}; shift 5
addr=${dev%/*}
ns=kiwi-device
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
is() { if "$@"; then echo 0; else echo 1; fi; }
dir=$(cd "$(dirname "$fleet")" && pwd); S=$(readlink -f "$dir/secrets")
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
in_ns() { on "$host" "ip netns exec $ns $1"; }
gone() { on "$host" "ip netns del $ns 2>/dev/null; rm -rf /etc/netns/$ns; true"; }

# the address: no machine of the fleets has it, and nothing on the LAN answers it
taken=$(python3 - "$addr" "$fleet" "$@" <<'EOF'
import sys, yaml
addr, found = sys.argv[1], []
for path in sys.argv[2:]:
    for n, h in (yaml.safe_load(open(path)).get("hosts") or {}).items():
        h = h or {}
        block = h.get(h.get("role", "node")) or {}
        for a in (str((h.get("network") or {}).get("address") or ""), str((block.get("vip") or {}).get("address") or "")):
            if a.split("/")[0] == addr:
                found.append("%s (%s)" % (n, path))
print(", ".join(found))
EOF
)
check "$(is [ -z "$taken" ])" "$addr is no machine's of the fleets${taken:+: $taken}"
[ -z "$taken" ] || { say "$fails wrong"; exit 1; }
! ping -c 2 -W 1 "$addr" >/dev/null 2>&1; check $? "nothing on the LAN answers $addr"
[ "$fails" = 0 ] || { say "stopped: nothing was set up"; say "$fails wrong"; exit 1; }

gone
lan=$(on "$host" "ip -o -4 route get $vip | sed -nE 's/.* dev ([^ ]+).*/\1/p'")
# the same card every run (locally administered, from the address): with a new one each time, the
# machines of the LAN keep the last run's card for that address until it times out, and the device's
# first half minute goes nowhere (22 Nov 2026); a real device keeps its card
mac=$(awk -F. '{printf "02:6b:69:%02x:%02x:%02x", $2, $3, $4}' <<<"$addr")
check "$(is [ -n "$lan" ])" "$host reaches $vip on ${lan:-nothing}"
on "$host" "set -e
  ip netns add $ns
  ip link add $ns link $lan address $mac type macvlan mode bridge
  ip link set $ns netns $ns
  ip netns exec $ns ip link set lo up
  ip netns exec $ns ip addr add $dev dev $ns
  ip netns exec $ns ip link set $ns up
  ip netns exec $ns ip route add default via $vip
  mkdir -p /etc/netns/$ns && echo 'nameserver $vip' > /etc/netns/$ns/resolv.conf"
check $? "the device on $host: $dev, its gateway and DNS server $vip"
trap gone EXIT
sleep 3
in_ns "ip route show default" | grep -q "via $vip"; check $? "its gateway is $vip"
in_ns "getent hosts deb.debian.org" >/dev/null; check $? "it resolves a name through $vip"
in_ns "curl -fsS -m 8 -o /dev/null https://deb.debian.org/debian/dists/stable/Release"; check $? "it reaches the Internet through $vip"
holder_ip=$(in_ns "curl -fsS -m 8 https://am.i.mullvad.net/ip" 2>/dev/null)
say "        it leaves the Internet at ${holder_ip:-?}"
# a page every second through the moving address while it moves
in_ns "sh -c 'i=0; while [ \$i -lt 70 ]; do if curl -fsS -m 2 -o /dev/null https://deb.debian.org/debian/dists/stable/Release; then echo ok; else echo miss; fi; i=\$((i+1)); sleep 1; done' > /run/$ns-probe 2>&1 &"
sleep 8
on "$first" "systemctl stop kn-vip.service 2>/dev/null || docker stop kn-vip >/dev/null"; check $? "$first's moving address stopped"
sleep 25
in_ns "curl -fsS -m 8 -o /dev/null https://deb.debian.org/debian/dists/stable/Release"; check $? "the device still reaches the Internet, through the other machine"
on "$first" "systemctl start kn-vip.service 2>/dev/null || docker start kn-vip >/dev/null"; check $? "$first's moving address back"
sleep 40
probe=$(on "$host" "cat /run/$ns-probe; rm -f /run/$ns-probe")
oks=$(grep -c '^ok' <<<"$probe"); misses=$(grep -c '^miss' <<<"$probe")
say "        $oks of $((oks + misses)) fetches went through, $misses missed"
check "$(is [ "$misses" -le 8 ])" "the device lost only seconds over two switches ($misses misses)"
gone; trap - EXIT
! on "$host" "ip netns list | grep -qw $ns"; check $? "the device is gone from $host"
say "$fails wrong"
exit $((fails > 0))
