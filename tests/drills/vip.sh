#!/usr/bin/env bash
# The site's moving address on two real machines (the vip module, docs/design/basics-twice.md):
# both get `vip` with ADDRESS and are applied; the first by name holds it and a name is answered
# there. Its moving address stops on the first: the second holds it within seconds and the name is
# still answered; back, the first holds it again. Then the first's Pi-hole stops: its check gives
# the address up the same way. The setting stays in the fleet file afterwards.
#
#   [KIWI_SERVER=bin/kiwi-server] tests/drills/vip.sh FLEET FIRST SECOND ADDRESS/PREFIX
#   e.g. lime papaya 192.168.2.70/16   (this computer must be on that LAN to ask the address)
# One PASS or FAIL line per check.
set -u
fleet=${1:?the fleet file}; a=${2:?the first machine (by name)}; b=${3:?the second}; vip=${4:?the moving address with its prefix}
KIWI=${KIWI_SERVER:-kiwi-server}
here=$(cd "$(dirname "$0")/../.." && pwd)
addr=${vip%/*}
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
is() { if "$@"; then echo 0; else echo 1; fi; }
dir=$(cd "$(dirname "$fleet")" && pwd); S=$(readlink -f "$dir/secrets")
read -r user domain < <(python3 -c '
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))["defaults"]
print(d["admin"]["user"], d.get("domain") or "home")' "$fleet")
lanof() { python3 -c '
import sys, yaml
h = yaml.safe_load(open(sys.argv[1]))["hosts"][sys.argv[2]]
print(str((h.get("network") or {}).get("address") or "").split("/")[0])' "$fleet" "$1"; }
on() {
    ssh -i "$S/admin_ed25519" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes \
        -o UserKnownHostsFile="$S/known_hosts" -o ConnectTimeout=8 "$user@$(lanof "$1")" "sudo sh -c $(printf '%q' "$2")"
}
holds() { on "$1" "ip -4 -o addr show" 2>/dev/null | grep -q " $addr/"; }
answers() {   # a name, asked of the moving address (a plain DNS question, nothing installed)
    python3 - "$addr" "$1" <<'PY'
import random, socket, struct, sys
server, name = sys.argv[1], sys.argv[2]
q = struct.pack(">HHHHHH", random.randrange(65536), 0x0100, 1, 0, 0, 0) + b"".join(
    bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0" + struct.pack(">HH", 1, 1)
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(2)
try:
    s.sendto(q, (server, 53)); r = s.recv(512)
except OSError:
    sys.exit(1)
sys.exit(0 if (struct.unpack(">H", r[6:8])[0] > 0) else 1)
PY
}
moved_to() {  # MACHINE: within 20 s
    local t0=$SECONDS
    while (( SECONDS - t0 < 20 )); do holds "$1" && { echo $(( SECONDS - t0 )); return 0; }; sleep 1; done
    return 1
}
"$KIWI" control pull "$fleet" > control-pull.log 2>&1 || say "note: nothing pulled from a control node"
PYTHONPATH="$here/lib" python3 -c '
import sys, yaml
from kiwiserver import fleettext, util
path, vip = sys.argv[1], sys.argv[2]
for m in sys.argv[3:]:
    text = util.read_text(path)
    h = yaml.safe_load(text)["hosts"][m]
    role = h.get("role", "node")
    mods = list((h.get(role) or {}).get("modules") or [])
    if "vip" not in mods:
        text = fleettext.put(text, ("hosts", m, role, "modules"), mods + ["vip"])
    text = fleettext.put(text, ("hosts", m, role, "vip"), {"address": vip}) if text else None
    if not text:
        sys.exit("%s: the fleet file could not be changed in its text" % m)
    util.write_text(path, text, 0o600)' "$fleet" "$vip" "$a" "$b"; check $? "vip $vip on $a and $b in the fleet file"
for m in "$a" "$b"; do
    "$KIWI" apply "$fleet" "$m" > "apply-$m.log" 2>&1; check $? "$m applied"
done
sleep 8
check "$(is holds "$a")" "$a holds $addr"
check "$(is answers "$a.$domain")" "$addr answers a name"
on "$a" "systemctl stop kn-vip.service 2>/dev/null || docker stop kn-vip >/dev/null"; check $? "$a's moving address stopped"
took=$(moved_to "$b"); check $? "$b holds $addr (${took:-never} s)"
check "$(is answers "$a.$domain")" "$addr still answers a name"
on "$a" "systemctl start kn-vip.service 2>/dev/null || docker start kn-vip >/dev/null"; check $? "$a's moving address back"
took=$(moved_to "$a"); check $? "$a holds $addr again (${took:-never} s)"
if [ "${PIHOLE_STEP:-0}" = 1 ]; then      # off unless asked: a Pi-hole that devices use directly is away for it
    on "$a" "systemctl stop kn-pihole.service 2>/dev/null || docker stop kn-pihole >/dev/null"; check $? "$a's Pi-hole stopped"
    took=$(moved_to "$b"); check $? "its check gave the address to $b (${took:-never} s)"
    check "$(is answers "$a.$domain")" "$addr answers a name from $b"
    on "$a" "systemctl start kn-pihole.service 2>/dev/null || docker start kn-pihole >/dev/null"; check $? "$a's Pi-hole back"
    took=$(moved_to "$a"); check $? "$a holds $addr again (${took:-never} s)"
fi
"$KIWI" control push "$fleet" > control-push.log 2>&1 || say "note: nothing pushed to a control node"
say "$fails wrong"
exit $((fails > 0))
