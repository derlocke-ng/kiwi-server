#!/usr/bin/env bash
# A home's gateway node when the home's Internet connection changes under it (the router
# reconnects and comes back with another address). A client behind the gateway asks the
# Internet through it the whole time. Two things are checked, without restarting anything
# on the gateway:
#
#   1. The connection is gone for a while and comes back: the client must get nothing
#      while it is gone (nothing leaks around the VPN) and the Internet again soon after.
#   2. The old path is dead for good, as when a router keeps a stale mapping for the
#      tunnel after it got a new address: the master no longer hears the gateway from
#      where it was. The gateway has to notice and come back from somewhere new by itself.
#
#   tests/drills/uplink-change.sh <fleet.yaml> <gateway-host> <client-address> <master-ssh> [<master-ssh-port>]
#
# <client-address>: a machine on the gateway's LAN that this drill may give another default
# route for the test (it is put back). <master-ssh>: user@address of the gateway's master.
set -u
fleet=$1; gw=$2; client=$3; master=$4; mport=${5:-22}
grep -qE "^allowed:.*\b$gw\b" "$(dirname "$fleet")/drills.yaml" 2>/dev/null || { echo "$gw is not in $(dirname "$fleet")/drills.yaml allowed:" >&2; exit 2; }
ks=${KIWI_SERVER:-kiwi-server}
dir=$(dirname "$fleet")
key=$dir/secrets/admin_ed25519
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
info=$("$ks" list "$fleet" --porcelain | python3 -c "
import json, sys
d = json.load(sys.stdin)
h = next(x for x in d['hosts'] if x['name'] == '$gw')
print((h['address'] or '').split('/')[0], h['vpn_ip'])")
gwaddr=${info% *}; gwmesh=${info#* }
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=6 -o IdentitiesOnly=yes -o IdentityAgent=none
     -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i "$key")
ong() { timeout 30 "${SSH[@]}" "core@$gwaddr" "$1" 2>/dev/null; }
onc() { timeout 30 "${SSH[@]}" "core@$client" "$1" 2>/dev/null; }
onm() { timeout 30 "${SSH[@]}" -p "$mport" "$master" "$1" 2>/dev/null; }
probe() { onc 'ping -c1 -W2 9.9.9.9 >/dev/null 2>&1'; }
until_back() {  # seconds until the client has the Internet again, or 1 after $1 seconds
    local limit=$1 start; start=$(date +%s)
    while [ $(( $(date +%s) - start )) -lt "$limit" ]; do
        if probe; then echo $(( $(date +%s) - start )); return 0; fi
        sleep 3
    done
    return 1
}
mip=${master#*@}
was=$(onc "ip route show default | head -1")
cleanup() {
    ong 'sudo nft delete table inet kiwidrill 2>/dev/null'
    onm 'sudo nft delete table inet kiwidrill 2>/dev/null'
    [ -n "$was" ] && onc "sudo ip route replace $was"
}
trap cleanup EXIT

say "== $gw ($gwaddr, $gwmesh on the mesh), a client at $client behind it"
onc "sudo ip route replace default via $gwaddr"
sleep 3
probe; check $? "the client reaches the Internet through the gateway"
ong 'sudo systemctl stop kn-vpn-client.service'
sleep 5
if probe; then check 1 "with the gateway's VPN client stopped the client gets nothing"; else check 0 "with the gateway's VPN client stopped the client gets nothing"; fi
ong 'sudo systemctl start kn-vpn-client.service'
t=$(until_back 300); check $? "and has the Internet again once it runs (${t:-no} s)"

say "== 1. the home's connection is gone for 90 seconds"
ong "sudo nft add table inet kiwidrill; sudo nft add chain inet kiwidrill f '{ type filter hook forward priority -300; }'; sudo nft add chain inet kiwidrill o '{ type filter hook output priority -300; }'; sudo nft add rule inet kiwidrill f ip daddr $mip drop; sudo nft add rule inet kiwidrill o ip daddr $mip drop"
sleep 20
if probe; then check 1 "while it is gone the client gets nothing"; else check 0 "while it is gone the client gets nothing"; fi
sleep 70
ong 'sudo nft delete table inet kiwidrill'
t=$(until_back 300); check $? "it is back: the client has the Internet again by itself (${t:-no} s)"

say "== 2. the old path is dead for good (a stale mapping after a new address)"
rt=$(onm 'sudo sh -c "command -v podman || command -v docker"')
end=$(onm "sudo $rt exec km-vpn-server sh -c 'p=\$(wg show wg0 allowed-ips | grep -w $gwmesh/32 | cut -f1); wg show wg0 endpoints | grep \"\$p\" | cut -f2'")
say "the master hears the gateway from ${end:-nowhere}"
if [ -n "$end" ] && [ "$end" != "(none)" ]; then
    onm "sudo nft add table inet kiwidrill; sudo nft add chain inet kiwidrill p '{ type filter hook prerouting priority -350; }'; sudo nft add rule inet kiwidrill p ip saddr ${end%:*} udp sport ${end##*:} drop"
    sleep 30
    if probe; then check 1 "the tunnel is down from where it was"; else check 0 "the tunnel is down from where it was"; fi
    t=$(until_back 900); check $? "the gateway came back from somewhere new by itself (${t:-not within 900} s)"
    now=$(onm "sudo $rt exec km-vpn-server sh -c 'p=\$(wg show wg0 allowed-ips | grep -w $gwmesh/32 | cut -f1); wg show wg0 endpoints | grep \"\$p\" | cut -f2'")
    say "the master now hears it from ${now:-nowhere}"
    onm 'sudo nft delete table inet kiwidrill'
else
    check 1 "the master knows where the gateway is"
fi
say "== $gw: $fails failed"
exit "$fails"
