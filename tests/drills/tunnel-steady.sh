#!/usr/bin/env bash
# A node's tunnel to its master stays up while the Internet behind the master is away. The VPN
# client's own check (gluetun) asks names on the Internet through the tunnel; until kiwi-modules
# 0.134.0 a failed check set the tunnel up anew every six seconds, and the mesh was cut each time.
# The drill cuts the way to the Internet inside MACHINE's tunnel for four minutes (a rule in the
# VPN client's own firewall; the mesh's addresses stay open), asks the master's mesh address once
# a second from inside the tunnel, and counts how often the tunnel was set up anew.
#
#   tests/drills/tunnel-steady.sh MACHINE [MESH_SUBNET [MASTER_MESH_ADDRESS]]
#   e.g. tests/drills/tunnel-steady.sh lychee 10.9.0.0/24 10.9.0.1
#
# MACHINE: a node with the vpn-client module, reached by `ssh MACHINE` (kiwi-server ssh-config
# --write). What goes out to the Internet through this machine's tunnel is cut for the four
# minutes; its services at home and on the mesh go on. One PASS or FAIL line per check.
set -u
m=${1:?a machine}; mesh=${2:-10.9.0.0/24}; master=${3:-10.9.0.1}; secs=${SECONDS_CUT:-230}
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
on() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$m" "sudo sh -c $(printf '%q' "$1")"; }
c=$(on "podman ps --format '{{.Names}}' | grep -E '^k[nm]-vpn-client\$' | head -n 1"); [ -n "$c" ] || { echo "$m runs no VPN client" >&2; exit 2; }
rule="OUTPUT -o tun0 ! -d $mesh -j DROP"
open() { on "podman exec $c iptables -D $rule 2>/dev/null; true"; }
trap open EXIT
on "podman exec $c ping -c 3 -W 2 $master >/dev/null"; check $? "before: the master's mesh address answers through the tunnel"
since=$(on "date '+%Y-%m-%d %H:%M:%S'")
on "podman exec $c iptables -I ${rule/OUTPUT/OUTPUT 1}"; check $? "the way to the Internet is cut inside the tunnel"
got=$(on "podman exec $c ping -c $secs -i 1 -W 1 $master 2>&1 | grep -o '[0-9]* packets received' | cut -d' ' -f1")
open; trap - EXIT
anew=$(on "journalctl -u $c.service --since '$since' --no-pager -o cat | grep -c 'restarting VPN'")
said=$(on "journalctl -u $c.service --since '$since' --no-pager -o cat | grep -c 'healthcheck failed'")
[ "${said:-0}" -gt 0 ]; check $? "the VPN client's check noticed it ($said times)"
[ "${anew:-1}" = 0 ]; check $? "the tunnel was not set up anew (${anew:-?} times)"
[ "${got:-0}" -ge $((secs - 2)) ]; check $? "the mesh went on: ${got:-0} of $secs answers from $master"
on "podman exec $c ping -c 3 -W 2 $master >/dev/null"; check $? "after: the master's mesh address answers"
[ "$fails" = 0 ] && say "all passed" || say "$fails wrong"
exit $((fails > 0))
