#!/usr/bin/env bash
# DHCP from the two machines of a site (docs/design/basics-twice.md; cli.fill_vip and kiwi-modules'
# dhcp-relay `follow`): two Pi-holes with the settings kiwi-server gives a pair (the whole range on
# both, the moving address as router and DNS server), and a DHCP client (dhcpcd, which asks for its
# old address again as most devices do), on a podman network of their own: nothing outside this
# computer sees a lease. Only the holder of the moving address hands out leases; here its DHCP is
# turned on and off (on a machine, its relay starts and stops). Checked: a lease; the same address
# when the client asks again; the same address from the other one after a switch, after a switch
# back, and after the first one is gone altogether.
# Each Pi-hole also has podman's own network, for its first blocklist download (it waits for DNS
# before anything else); its DHCP answers only on the pair's network, where its range is.
#
#   HALVES=1 shows why not halves on both at once: each refuses the other's leases.
#   tests/drills/dhcp-pair.sh      (rootless podman; pulls pihole/pihole, builds a dhcpcd image)
# One PASS or FAIL line per check.
set -u
net=kiwi-dhcp-pair; sub=10.231.0; vip=$sub.10
PIHOLE=${PIHOLE:-docker.io/pihole/pihole:latest}; client_image=localhost/kiwi-dhcp-client:drill
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
is() { if "$@"; then echo 0; else echo 1; fi; }
gone() { podman rm -f -t 2 kiwi-dhcp-a kiwi-dhcp-b kiwi-dhcp-client >/dev/null 2>&1; podman network rm -f $net >/dev/null 2>&1; }
gone
work=$(mktemp -d)
trap 'gone; rm -rf "$work"' EXIT
printf 'FROM docker.io/library/alpine:3.22\nRUN apk add --no-cache dhcpcd\n' > "$work/Containerfile"
podman build -q -t $client_image -f "$work/Containerfile" "$work" >/dev/null; check $? "the client's image (dhcpcd)"
podman network create --internal --disable-dns --subnet $sub.0/24 $net >/dev/null; check $? "a network of its own ($sub.0/24, no way out)"
pw=$(head -c 12 /dev/urandom | base64 | tr -dc a-zA-Z0-9)
server() {  # NAME ADDRESS START END ACTIVE
    podman run -d --name "kiwi-dhcp-$1" --network "$net:ip=$2" --network podman --cap-add NET_ADMIN --cap-add NET_RAW \
        -e TZ=UTC -e FTLCONF_webserver_api_password="$pw" -e FTLCONF_dns_listeningMode=all \
        -e FTLCONF_dhcp_start="$3" -e FTLCONF_dhcp_end="$4" -e FTLCONF_dhcp_router=$vip -e FTLCONF_dhcp_leaseTime=2m \
        -e FTLCONF_misc_dnsmasq_lines="dhcp-option=option:dns-server,$vip" "$PIHOLE" >/dev/null || return 1
    up "$1" 53 || return 1
    dhcp "$1" "$5"
}
up() {  # NAME PORT: within two minutes
    local _
    for _ in $(seq 1 60); do podman exec "kiwi-dhcp-$1" sh -c "netstat -lun 2>/dev/null | grep -q ':$2 '" && return 0; sleep 2; done
    return 1
}
dhcp() {  # NAME true|false: as the holder's relay starting or stopping
    podman exec "kiwi-dhcp-$1" pihole-FTL --config dhcp.active "$2" >/dev/null || return 1
    if [ "$2" = true ]; then up "$1" 67; else sleep 2; fi
}
podman run -d --name kiwi-dhcp-client --network $net --cap-add NET_ADMIN --cap-add NET_RAW $client_image sleep 3600 >/dev/null
check $? "a client on the network"
ask() {  # prints "IP SERVER" of the lease, or nothing; NAKs counted into $work/naks
    local out
    out=$(podman exec kiwi-dhcp-client dhcpcd -1 -4 -A -t 20 -d eth0 2>&1)
    grep -c "NAK" <<<"$out" >> "$work/naks"
    sed -nE 's/.*acknowledged ([0-9.]+) from ([0-9.]+).*/\1 \2/p' <<<"$out" | tail -1
}
naks() { awk '{n += $1} END {print n + 0}' "$work/naks" 2>/dev/null; : > "$work/naks"; }
if [ "${HALVES:-0}" = 1 ]; then
    server a $sub.2 $sub.100 $sub.150 true; check $? "Pi-hole a, half $sub.100-$sub.150, hands out leases"
    server b $sub.3 $sub.151 $sub.200 true; check $? "Pi-hole b, half $sub.151-$sub.200, hands out leases"
    : > "$work/naks"; read -r ip _s < <(ask)
    seen=" $ip "
    for _ in $(seq 1 6); do read -r ip2 _s < <(ask); [[ $seen == *" $ip2 "* ]] || seen="$seen$ip2 "; done
    n=$(naks)
    say "        7 asks: addresses$seen; $n refusals"
    check "$(is [ "$(wc -w <<<"$seen")" -gt 1 ])" "halves on both: the client's address changes (why the pair does not do this)"
    say "$fails wrong"; exit $((fails > 0))
fi
say "        Pi-hole's DHCP lines: $(podman run --rm --entrypoint sh "$PIHOLE" -c 'strings /usr/bin/pihole-FTL | grep -c "^dhcp-authoritative"') x dhcp-authoritative in its config writer"
server a $sub.2 $sub.100 $sub.200 true; check $? "Pi-hole a at $sub.2, the whole range, hands out leases (holds the address)"
server b $sub.3 $sub.100 $sub.200 false; check $? "Pi-hole b at $sub.3, the whole range, waits"
: > "$work/naks"
read -r ip srv < <(ask)
check "$(is [ "${srv:-}" = $sub.2 ])" "a lease: ${ip:-none} from ${srv:-nobody}"
kept=0
for _ in $(seq 1 5); do read -r ip2 _s < <(ask); [ "${ip2:-}" = "${ip:-x}" ] && kept=$((kept + 1)); done
check "$(is [ "$kept" = 5 ])" "the client keeps $ip when it asks again ($kept of 5, $(naks) refusals)"
dhcp a false && dhcp b true; check $? "the address moves to b: a's DHCP off, b's on"
read -r ip3 srv3 < <(ask)
check "$(is [ "${ip3:-}" = "${ip:-x}" ] && [ "${srv3:-}" = $sub.3 ])" "from b the same address: ${ip3:-none} from ${srv3:-nobody} ($(naks) refusals)"
dhcp b false && dhcp a true; check $? "and back to a"
read -r ip4 srv4 < <(ask)
check "$(is [ "${ip4:-}" = "${ip:-x}" ] && [ "${srv4:-}" = $sub.2 ])" "from a the same address: ${ip4:-none} from ${srv4:-nobody} ($(naks) refusals)"
podman stop -t 2 kiwi-dhcp-a >/dev/null 2>&1; dhcp b true; check $? "a gone altogether, b hands out leases"
t0=$SECONDS
read -r ip5 srv5 < <(ask)
check "$(is [ "${ip5:-}" = "${ip:-x}" ] && [ "${srv5:-}" = $sub.3 ])" "still the same address: ${ip5:-none} from ${srv5:-nobody} ($(( SECONDS - t0 )) s, $(naks) refusals)"
say "$fails wrong"
exit $((fails > 0))
