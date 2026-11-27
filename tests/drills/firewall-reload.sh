#!/usr/bin/env bash
# An apply, and a reload of firewalld, leave the containers their DNS (kiwi-stack 0.9.34). Until
# then every apply on a Podman machine with firewalld reloaded it, the stack network fell out of
# the trusted sources, and two minutes later the reverse proxy answered 502 for every site
# (lemonaide, 27 Nov 2026).
#
#   tests/drills/firewall-reload.sh FLEET MACHINE URL [WAIT_SECONDS]
#
# MACHINE: a Podman machine with firewalld and the reverse proxy; URL: a site its proxy serves,
# reachable from here. Applies the machine, reloads its firewalld by hand, and fetches URL after
# each, WAIT_SECONDS later (default 200: nginx forgets a name after about two minutes).
# One PASS or FAIL line per check. Nothing restarts when nothing of the machine changed.
set -u
fleet=${1:?the fleet file}; m=${2:?a machine}; url=${3:?a site its proxy serves}; wait=${4:-200}
ca=$(dirname "$fleet")/secrets/ca/kiwiCA.pem
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
on() { timeout 60 ssh -o ConnectTimeout=8 -o BatchMode=yes "$m" "$@" 2>&1; }
page() { curl -s -o /dev/null -m 20 -w '%{http_code}' ${ca:+--cacert "$ca"} "$url" 2>/dev/null; }
served() { case $(page) in 2??|3??|401|403) return 0 ;; *) return 1 ;; esac; }      # anything but the proxy's 502
# the proxy's container resolves another container of the stack (its own name it knows without asking)
names() { on 'n=$(sudo podman ps --format "{{.Names}}" | grep -m1 nginx) || exit 1
for o in $(sudo podman ps --format "{{.Names}}" | grep -vx "$n"); do
    sudo podman exec "$n" getent hosts "$o" >/dev/null 2>&1 && exit 0
done; exit 1'; }
subnets() { on 'for q in /etc/containers/systemd/*.network; do sudo sed -n "s/^Subnet=//p" "$q"; done'; }
trusted() { # $1: "" or --permanent — is every stack subnet a trusted source there?
    local s have; have=$(on "sudo firewall-cmd $1 --zone=trusted --list-sources")
    for s in $(subnets); do case " $have " in *" $s "*) ;; *) return 1 ;; esac; done
}

[ "$(on 'systemctl is-active firewalld')" = active ] || { say "$m: firewalld does not run there: nothing to drill"; exit 2; }
[ -f "$ca" ] || ca=
served; check $? "before: $url is served"

say "applying $m"
since=$(on 'date "+%Y-%m-%d %H:%M:%S"')
kiwi-server apply "$fleet" "$m" >/dev/null 2>&1; check $? "the apply went through"
n=$(on "sudo journalctl -u firewalld --since '$since' --no-pager -o cat | grep -ci 'reload'")
[ "${n:-1}" = 0 ]; check $? "the apply did not reload firewalld"
trusted ""; check $? "the stack network is a trusted source, running"
trusted --permanent; check $? "and in the permanent configuration"
names; check $? "a container resolves a name"
say "waiting $wait s"; sleep "$wait"
served; check $? "$wait s after the apply: $url is served"

say "reloading firewalld on $m by hand"
on 'sudo firewall-cmd -q --reload'
trusted ""; check $? "after the reload: the stack network is still a trusted source"
names; check $? "a container resolves a name"
say "waiting $wait s"; sleep "$wait"
served; check $? "$wait s after the reload: $url is served"
names; check $? "a container still resolves a name"

[ "$fails" = 0 ] && say "all passed" || say "$fails failed"
exit "$fails"
