#!/usr/bin/env bash
# A person's computer as a client (docs/design/clients.md, the first step): the Debian GNOME desktop
# VM of desktop-clevis-vm.sh joins FLEET as client NAME, its agent reports to the control node, and
# `kiwi-server client list` shows it with its system, its box and its disk. A report signed with
# another key is refused. Afterwards the agent is gone from the VM and NAME from the fleet file.
#
#   [VMHOST=sh3] tests/drills/client-agent-vm.sh FLEET [NAME]
#   e.g. tests/drills/client-agent-vm.sh ~/kiwi/fleet.yaml desk-vm
#
# Needs: the VM installed and bound by desktop-clevis-vm.sh (its folder ~/kiwi-vm/desktop/debian
# and its disk on VMHOST), FLEET's secret store open, FLEET joined to a control node of 2.329.0 or
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
    [ -s "$before" ] && ! cmp -s "$before" "$fleet" && cp "$before" "$fleet" && "$KIWI" control push "$fleet" >/dev/null 2>&1
    rm -f "$before"; desk_cleanup
}
trap done_ EXIT
vh '[ -s bound ]' || { echo "the VM is not installed and bound yet: run desktop-clevis-vm.sh first" >&2; exit 2; }
admin=$(python3 -c "import yaml; print(yaml.safe_load(open('fleet/fleet.yaml'))['defaults']['admin']['user'])")

vh_prepare; check $? "the VM's folder on ${VMHOST:-this computer}: $VD" || exit 1
tunnel_up 7611 7612 7613; check $? "the test rings reach ${VMHOST:-the VM}"
rings health1 health2 auto; vm_start client1.log
up_within 300; check $? "the desktop VM is up (its disk opened by its rings)" || exit 1

"$KIWI" control pull "$fleet" >/dev/null 2>&1 || true
cp "$fleet" "$before"
"$KIWI" client add "$fleet" "$name" >/dev/null && "$KIWI" control push "$fleet" >/dev/null 2>&1
check $? "$name is a client of the fleet, and the control node has it"
url=$(PYTHONPATH="$here/lib" python3 -c '
import sys
from kiwiserver import config, machineagent, modules
f = config.Fleet.load(sys.argv[1]); print(machineagent.url(f, modules.discover()) or "")' "$fleet")
host=${url#https://}
if ! vssh "getent hosts $host" >/dev/null; then
    # a VM behind its host's NAT asks the host's resolver, which may not know the network's names
    addr=$(PYTHONPATH="$here/lib" python3 -c '
import sys
from kiwiserver import config, machineagent
f = config.Fleet.load(sys.argv[1]); h = machineagent.control_host(f); print(((h.cfg.get("network") or {}).get("address") or "").split("/")[0])' "$fleet")
    vssh "echo '$addr $host' | sudo tee -a /etc/hosts >/dev/null"; say "        $host: not in the VM's DNS, written into its /etc/hosts ($addr)"
fi
script=$(PYTHONPATH="$here/lib" python3 -c '
import sys
from kiwiserver import clientagent, config, util
f = config.Fleet.load(sys.argv[1])
print(clientagent.install_script(f, sys.argv[2], sys.argv[3], util.read_text(f.resolve_path("secrets/ca/kiwiCA.pem"))))' "$fleet" "$name" "$url")
key=$(PYTHONPATH="$here/lib" python3 -c '
import sys
from kiwiserver import clientagent, config
print(clientagent.key(config.Fleet.load(sys.argv[1]), sys.argv[2]), end="")' "$fleet" "$name")
printf '%s' "$key" | vssh "sudo sh -c $(printf '%q' "$script")"; check $? "the agent is put on the VM"
vssh "sudo stat -c '%a %U' /etc/kiwi-client/key" | grep -qx "600 root"; check $? "its key is root's alone"

n=24; row=
while [ "$n" -gt 0 ]; do
    row=$("$KIWI" client list "$fleet" --porcelain 2>/dev/null | python3 -c '
import json, sys
r = next((x for x in json.load(sys.stdin)["clients"] if x["name"] == sys.argv[1]), None)
if r and r["state"] == "on":
    f = r["facts"]; print("%s|%s|%s|%s|%s" % (f.get("os", ""), f.get("box", ""), f.get("crypt", ""), f.get("clevis", ""), f.get("agent", "")))' "$name")
    [ -n "$row" ] && break; sleep 5; n=$((n - 1))
done
[ -n "$row" ]; check $? "the control node lists $name as on, within two minutes"
IFS='|' read -r os box crypt clevis agent <<<"$row"
case $os in *Debian*) true ;; *) false ;; esac; check $? "its system: ${os:-?}"
[ ${#box} = 16 ]; check $? "the box it runs on, as a salted sum: ${box:-none}"
[ "${crypt:-0}" -ge 1 ] && [ "${clevis:-0}" -ge 1 ]; check $? "its disk: ${crypt:-?} encrypted, ${clevis:-?} bound to rings"
vssh "journalctl -t kiwi-client-agent --no-pager -o cat | grep -q 'report to $url: 200'"; check $? "the VM's journal says the control node took it"

vssh "sudo sh -c 'cp /etc/kiwi-client/key /run/kiwi-key.keep; printf other > /etc/kiwi-client/key; rm -f /run/kiwi-client-agent.last; /usr/local/sbin/kiwi-client-agent; cp /run/kiwi-key.keep /etc/kiwi-client/key; rm -f /run/kiwi-key.keep'"
vssh "journalctl -t kiwi-client-agent --since '-2 min' --no-pager -o cat | grep -q 'report to $url: 403'"; check $? "a report signed with another key is refused (403)"

"$KIWI" client remove "$fleet" "$name" >/dev/null && "$KIWI" control push "$fleet" >/dev/null 2>&1; check $? "$name is out of the fleet file again"
vssh "sudo sh -c $(printf '%q' "$(PYTHONPATH="$here/lib" python3 -c 'from kiwiserver import clientagent; print(clientagent.REMOVE)')")"
vssh "! test -e /etc/kiwi-client && ! test -e /usr/local/sbin/kiwi-client-agent"; check $? "the agent is gone from the VM"
cmp -s "$before" "$fleet"; check $? "the fleet file is as it was"
[ "$fails" = 0 ] && say "all passed" || say "$fails wrong"
exit $((fails > 0))
