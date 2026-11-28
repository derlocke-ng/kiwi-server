#!/usr/bin/env bash
# A person's computer as a client (docs/design/clients.md): the Debian GNOME desktop VM of
# desktop-clevis-vm.sh is added to FLEET as client NAME with a place on the VPN, and joins by the
# one-time command the control page shows. Its agent reports (`client list` shows its system, its
# box, its disk), applies its profile (the network's CA in the system's trust, the VPN as a
# NetworkManager connection that carries the mesh) and takes an order. A report signed with another
# key is refused, a join command works once. Afterwards the agent, the connection and the CA are
# gone from the VM, NAME from the fleet file and from the VPN. KIWI_ORDER=1: an update order too.
# KIWI_UNLOCK=1 (the default): its disk bound to the network's ring RING (home) with the drill's
# passphrase, and started with the drill's own rings down; `leave` takes the binding away again.
#
#   [VMHOST=sh3] tests/drills/client-agent-vm.sh FLEET [NAME]
#   e.g. tests/drills/client-agent-vm.sh ~/kiwi/fleet.yaml desk-vm
#
# Needs: the VM installed and bound by desktop-clevis-vm.sh (its folder ~/kiwi-vm/desktop/debian
# and its disk on VMHOST), FLEET's secret store open, FLEET joined to a control node of 2.330.0 or
# later. KIWI_SERVER: the kiwi-server to use (default: the installed one). One PASS or FAIL line
# per check.
set -u
fleet=${1:?the fleet file}; name=${2:-desk-vm}
here=$(cd "$(dirname "$0")/../.." && pwd)
KIWI=${KIWI_SERVER:-kiwi-server}
w=$HOME/kiwi-vm/desktop/debian
cd "$w" || { echo "no desktop VM folder at $w: run desktop-clevis-vm.sh first" >&2; exit 2; }
VMHOST=${VMHOST-sh3}
VD=${VD:-$( [ -n "$VMHOST" ] && echo /mnt/nvme_2tb/share/virt/kiwi-desktop/debian || echo "$w/vm" )}
PSSH=${PSSH:-18341}
KEY=$w/fleet/secrets/admin_ed25519
# shellcheck source=tests/drills/lib-desktop-vm.sh
. "$here/tests/drills/lib-desktop-vm.sh"
before=$(mktemp "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/kiwi-fleet.XXXXXX") || exit 2      # in memory, never on a disk
done_() {
    vssh "sudo sh -c $(printf '%q' "$(PYTHONPATH="$here/lib" python3 -c 'from kiwiserver import clientagent; print(clientagent.REMOVE)')")" 2>/dev/null
    vssh "sudo sh -c 'nmcli connection delete kiwi-home >/dev/null 2>&1; rm -f /usr/local/share/ca-certificates/kiwi-home.crt; update-ca-certificates >/dev/null 2>&1; true'" 2>/dev/null
    "$KIWI" client remove "$fleet" "$name" >/dev/null 2>&1        # still there when the drill stopped half-way: off the VPN too
    [ -s "$before" ] && ! cmp -s "$before" "$fleet" && cp "$before" "$fleet" && "$KIWI" control push "$fleet" >/dev/null 2>&1
    vssh "sudo sed -i '/ # kiwi-client-drill\$/d' /etc/hosts" 2>/dev/null
    rm -f "$before"; desk_cleanup
}
trap done_ EXIT
vh '[ -s bound ]' || { echo "the VM is not installed and bound yet: run desktop-clevis-vm.sh first" >&2; exit 2; }
admin=$(python3 -c "import yaml; print(yaml.safe_load(open('fleet/fleet.yaml'))['defaults']['admin']['user'])")

vh_prepare; rc=$?; check $rc "the VM's folder on ${VMHOST:-this computer}: $VD"; [ $rc = 0 ] || exit 1
tunnel_up 7611 7612 7613; check $? "the test rings reach ${VMHOST:-the VM}"
# a board id of its own (qemu gives none without it): the agent sums it into the box it runs on
rings health1 health2 auto; vm_start client1.log -uuid 6b1e4c2a-9f3d-4e7b-8a51-2c0d9e7f4a13
up_within 300; rc=$?; check $rc "the desktop VM is up (its disk opened by its rings)"; [ $rc = 0 ] || exit 1

"$KIWI" control pull "$fleet" >/dev/null 2>&1 || true
cp "$fleet" "$before"
"$KIWI" client add "$fleet" "$name" --vpn >/dev/null && "$KIWI" control push "$fleet" >/dev/null 2>&1
check $? "$name is a client of the fleet with a place on the VPN, and the control node has it"
url=$(PYTHONPATH="$here/lib" python3 -c '
import sys
from kiwiserver import config, machineagent, modules
f = config.Fleet.load(sys.argv[1]); print(machineagent.url(f, modules.discover()) or "")' "$fleet")
host=${url#https://}
# a VM behind its host's NAT asks its host's resolver (on sh3: v1's, which knows no .home names):
# the control node's LAN address goes into its /etc/hosts for the drill, and out again after
addr=$(PYTHONPATH="$here/lib" python3 -c '
import sys
from kiwiserver import config, machineagent
f = config.Fleet.load(sys.argv[1]); h = machineagent.control_host(f); print(((h.cfg.get("network") or {}).get("address") or "").split("/")[0])' "$fleet")
vssh "sudo sed -i '/ # kiwi-client-drill\$/d' /etc/hosts; echo '$addr $host # kiwi-client-drill' | sudo tee -a /etc/hosts >/dev/null"
say "        $host at $addr in the VM's /etc/hosts"
# the VM's clock as this computer's: a report more than five minutes off is refused (its own clock
# ran 23 minutes ahead, 28 Nov 2026)
vssh "sudo date -u -s @$(date +%s) >/dev/null"; check $? "the VM's clock set from this computer's"
command=$("$KIWI" client invite "$fleet" "$name" 2>/dev/null)
case $command in *"/api/client/join/"*"| sudo sh"*) rc=0 ;; *) rc=1 ;; esac; check $rc "the control node makes its one-time join command"
installed=$(date +%s)
vssh "bash -c $(printf '%q' "$command")" >/dev/null; check $? "the command run on the VM puts the agent on it"
vssh "bash -c $(printf '%q' "$command")" >/dev/null 2>&1; [ $? != 0 ]; check $? "the same command a second time does nothing"
vssh "sudo stat -c '%a %U' /etc/kiwi-client/key" | grep -qx "600 root"; check $? "its key is root's alone"

n=24; row=
while [ "$n" -gt 0 ]; do
    row=$("$KIWI" client list "$fleet" --porcelain 2>/dev/null | python3 -c '
import json, sys
r = next((x for x in json.load(sys.stdin)["clients"] if x["name"] == sys.argv[1]), None)
if r and r["state"] == "on" and (r.get("seen") or 0) >= int(sys.argv[2]) - 2:      # a report of this run, not one kept from before
    f = r["facts"]; print("%s|%s|%s|%s|%s" % (f.get("os", ""), f.get("box", ""), f.get("crypt", ""), f.get("clevis", ""), f.get("agent", "")))' "$name" "$installed")
    [ -n "$row" ] && break; sleep 5; n=$((n - 1))
done
[ -n "$row" ]; check $? "the control node lists $name as on, within two minutes"
[ -n "$row" ] || {
    say "        the VM's agent says:"; vssh "sudo journalctl -t kiwi-client-agent -u kiwi-client-agent --no-pager -o cat -n 15; sudo systemctl status kiwi-client-agent.timer --no-pager -n 0" 2>&1 | sed 's/^/          /' | cut -c1-200
    say "        once more by hand:"; vssh "sudo sh -c 'rm -f /run/kiwi-client-agent.last; /usr/local/sbin/kiwi-client-agent; cat /run/kiwi-client-agent.last'" 2>&1 | tail -n 3 | sed 's/^/          /' | cut -c1-200
}
IFS='|' read -r os box crypt clevis agent <<<"$row"
case $os in *Debian*) true ;; *) false ;; esac; check $? "its system: ${os:-?}"
[ ${#box} = 16 ]; check $? "the box it runs on, as a salted sum: ${box:-none}"
[ "${crypt:-0}" -ge 1 ] && [ "${clevis:-0}" -ge 1 ]; check $? "its disk: ${crypt:-?} encrypted, ${clevis:-?} bound to rings"
n=24; while [ "$n" -gt 0 ]; do vssh "sudo test -s /var/lib/kiwi-client/profile.sum" 2>/dev/null && break; sleep 5; n=$((n - 1)); done
[ "$n" -gt 0 ]; check $? "its profile is applied"
vssh "test -s /usr/local/share/ca-certificates/kiwi-home.crt || test -s /etc/pki/ca-trust/source/anchors/kiwi-home.pem"
check $? "the network's CA is in the VM's trust store"
vssh "nmcli -t -f NAME connection show --active | grep -qx kiwi-home"; rc=$?; check $rc "its VPN connection kiwi-home is up"
[ $rc = 0 ] || { vssh "nmcli -t -f NAME,TYPE,DEVICE connection show; sudo journalctl -t kiwi-client-agent --no-pager -o cat -n 8" 2>&1 | sed 's/^/          /' | cut -c1-200; }
mesh=$(PYTHONPATH="$here/lib" python3 -c '
import sys
from kiwiserver import config
f = config.Fleet.load(sys.argv[1]); m = [h for h in f.hosts.values() if h.role == "master"][0]
print((m.role_settings.get("vpn_ip") if m.role_settings else "") or (m.cfg.get("vpn_ip") or "10.9.0.1"))' "$fleet" 2>/dev/null)
vssh "ip -o route get ${mesh:-10.9.0.1}" | grep -q "dev kiwi-home"; check $? "the mesh (${mesh:-10.9.0.1}) goes through it"
vssh "ip -o route get 1.1.1.1" | grep -qv "dev kiwi-home"; check $? "the Internet does not (only the network through the VPN)"
"$KIWI" control order "$fleet" "$name" profile > "$before.log" 2>&1; rc=$?
check $rc "an order to apply its profile came back done$([ $rc = 0 ] || echo ": $(tail -n 1 "$before.log" | cut -c1-160)")"
if [ "${KIWI_ORDER:-0}" = 1 ]; then
    "$KIWI" control order "$fleet" "$name" update > "$before.log" 2>&1; check $? "an update order: $(tail -n 1 "$before.log" | cut -c1-120)"
fi
said() { vssh "sudo journalctl -t kiwi-client-agent --since '-10 min' --no-pager -o cat" 2>/dev/null; }
said | grep -q "report to $url: 200"; rc=$?; check $rc "the VM's journal says the control node took it"
[ $rc = 0 ] || said | tail -n 5 | sed 's/^/          /' | cut -c1-200

vssh "sudo sh -c 'cp /etc/kiwi-client/key /run/kiwi-key.keep; printf other > /etc/kiwi-client/key; rm -f /run/kiwi-client-agent.last; /usr/local/sbin/kiwi-client-agent; cp /run/kiwi-key.keep /etc/kiwi-client/key; rm -f /run/kiwi-key.keep'"
said | grep -q "report to $url: 403 .*not this client"; rc=$?; check $rc "a report signed with another key is refused (403, not this client's)"
[ $rc = 0 ] || said | tail -n 5 | sed 's/^/          /' | cut -c1-200

# ---- its disk: bound once with its passphrase to the network's ring RING (on its machine at home),
# then started with the drill's own rings all down: the network's ring alone opens it
if [ "${KIWI_UNLOCK:-1}" = 1 ]; then
    ring=${RING:-home}
    PYTHONPATH="$here/lib" python3 -c '
import sys
from kiwiserver import fleettext, util
path, name, ring = sys.argv[1:4]
new = fleettext.put(util.read_text(path), ["clients", name], {"unlock": {"rings": [ring]}})     # its block, written anew
if not new:
    sys.exit("the fleet file could not be changed in place")
util.write_text(path, new, 0o600)' "$fleet" "$name" "$ring" && "$KIWI" control push "$fleet" >/dev/null 2>&1
    check $? "$name's disk is to open by the network's ring $ring"
    "$KIWI" client policy "$fleet" "$name" | grep -q '"tang"'; check $? "kiwi-server client policy says how"
    "$KIWI" control order "$fleet" "$name" profile >/dev/null 2>&1
    n=24; while [ "$n" -gt 0 ]; do vssh "grep -qx 'unlock waits' /run/kiwi-client/status" 2>/dev/null && break; vssh "sudo kiwi-client-agent now" >/dev/null 2>&1; sleep 5; n=$((n - 1)); done
    [ "$n" -gt 0 ]; check $? "its agent says the disk waits to be bound (and binds nothing by itself)"
    before_slots=$(vssh "sudo clevis luks list -d \$(lsblk -rpno NAME,FSTYPE | awk '\$2 == \"crypto_LUKS\" {print \$1; exit}')" | wc -l)
    printf '%s' "${PASS_PHRASE:-kiwitest}" | vssh "sudo /usr/local/sbin/kiwi-client-agent bind --stdin" > "$before.log" 2>&1; rc=$?
    check $rc "bound with its passphrase on stdin, as the app does$([ $rc = 0 ] || echo ": $(tail -n 2 "$before.log" | tr '\n' ' ' | cut -c1-160)")"
    after_slots=$(vssh "sudo clevis luks list -d \$(lsblk -rpno NAME,FSTYPE | awk '\$2 == \"crypto_LUKS\" {print \$1; exit}')" | wc -l)
    [ "$after_slots" -gt "$before_slots" ]; check $? "a slot more on its disk ($before_slots -> $after_slots)"
    rings                                       # every ring of the drill down
    vm_start client2.log -uuid 6b1e4c2a-9f3d-4e7b-8a51-2c0d9e7f4a13
    t0=$(date +%s); up_within 300; check $? "it opens by the network's ring $ring alone ($(( $(date +%s) - t0 )) s)"
    vssh "sudo date -u -s @$(date +%s) >/dev/null"
fi

# ---- it leaves: everything the agent put there goes, the agent last
vssh "sudo /usr/local/sbin/kiwi-client-agent leave" > "$before.log" 2>&1; check $? "it leaves the network ($(tail -n 1 "$before.log" | cut -c1-80))"
vssh "! nmcli -t -f NAME connection show | grep -qx kiwi-home"; check $? "its VPN connection is gone"
vssh "! test -e /usr/local/share/ca-certificates/kiwi-home.crt"; check $? "the network's CA is no longer trusted"
if [ "${KIWI_UNLOCK:-1}" = 1 ]; then
    left=$(vssh "sudo clevis luks list -d \$(lsblk -rpno NAME,FSTYPE | awk '\$2 == \"crypto_LUKS\" {print \$1; exit}')" | wc -l)
    [ "$left" = "$before_slots" ]; check $? "its disk has its slots of before ($left)"
fi
vssh "! test -e /etc/kiwi-client && ! test -e /usr/local/sbin/kiwi-client-agent"; check $? "the agent is gone from the VM"
"$KIWI" client remove "$fleet" "$name" >/dev/null && "$KIWI" control push "$fleet" >/dev/null 2>&1; check $? "$name is out of the fleet file and off the VPN again"
cmp -s "$before" "$fleet"; check $? "the fleet file is as it was"
[ "$fails" = 0 ] && say "all passed" || say "$fails wrong"
exit $((fails > 0))
