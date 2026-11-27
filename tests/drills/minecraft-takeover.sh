#!/usr/bin/env bash
# A modded Minecraft world of an older setup, taken over by the games module. v1 ran
# itzg/minecraft-server with a mod loader and a server pack mounted at /modpacks, one folder per
# server with `data` and `modpacks` in it. The drill makes such a server on MACHINE the old way
# (a plain container, NeoForge, a small stand-in pack), marks its world, stops it, moves the two
# folders to where the games module keeps them, adds the server to the fleet file and applies.
# Then it asks the new server for the marks. Afterwards the fleet file is as it was, the server
# and its folders are gone.
#
#   [KIWI_SERVER=bin/kiwi-server] tests/drills/minecraft-takeover.sh FLEET MACHINE
#
# MACHINE: a podman machine with the vpn-client module and four gigabytes of memory to spare,
# reached by `ssh MACHINE` (kiwi-server ssh-config --write). It downloads Minecraft and NeoForge
# (some hundred megabytes) and takes ten to fifteen minutes. One PASS or FAIL line per check.
set -u
fleet=${1:?the fleet file}; m=${2:?a machine}
KIWI=${KIWI_SERVER:-kiwi-server}
here=$(cd "$(dirname "$0")/../.." && pwd)
MC=${MC_VERSION:-1.21.1}; NF=${NEOFORGE_VERSION:-21.1.214}; port=${MC_PORT:-25590}; name=takeover
image=docker.io/itzg/minecraft-server:latest
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
on() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$m" "sudo sh -c $(printf '%q' "$1")"; }
healthy() {     # CONTAINER TRIES: the image's own check, every 10 s
    local n=$2
    while [ "$n" -gt 0 ]; do on "podman exec $1 mc-health >/dev/null 2>&1" && return 0; sleep 10; n=$((n - 1)); done
    return 1
}
edit() {        # on|off: the server in the fleet file, the games module on the machine
    PYTHONPATH="$here/lib" python3 -c '
import sys, yaml
from kiwiserver import fleettext, util
path, m, name, mc, nf, port = sys.argv[1:7]
text = util.read_text(path)
node = yaml.safe_load(text)["hosts"][m]["node"]
new = fleettext.put(text, ("hosts", m, "node", "modules"), list(node["modules"]) + ["games"])
new = new and fleettext.put(new, ("hosts", m, "node", "games"), {"eula": True, "servers": {name: {
    "game": "minecraft", "port": int(port), "memory": "3G",
    "env": {"TYPE": "NEOFORGE", "VERSION": mc, "NEOFORGE_VERSION": nf, "GENERIC_PACK": "/modpacks/pack.zip"}}}})
if not new:
    sys.exit("the fleet file could not be changed in place")
util.write_text(path, new, 0o600)' "$fleet" "$m" "$name" "$MC" "$NF" "$port"
}

before=$(mktemp "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/kiwi-fleet.XXXXXX") || exit 2      # in memory, never on a disk
cp "$fleet" "$before"
back() { [ -s "$before" ] && ! cmp -s "$before" "$fleet" && cp "$before" "$fleet"; rm -f "$before"; }
trap back EXIT
grep -qE "^\s+modules:.*\bgames\b" <(sed -n "/^  $m:/,/^  [a-z]/p" "$fleet") && { echo "$m already has the games module: take another machine" >&2; exit 2; }
dd=$(on 'sed -n "s/^STACK_DIR=//p" /etc/kiwi-server/stack.env'); [ -n "$dd" ] || { echo "$m has no stack" >&2; exit 2; }
old=/var/tmp/kiwi-v1mc

say "== a server the old way on $m: NeoForge $NF for Minecraft $MC with a server pack"
on "podman rm -f kiwi-v1mc >/dev/null 2>&1; rm -rf $old; mkdir -p $old/data $old/modpacks && chown -R 1000:1000 $old"
# the stand-in pack: an archive that is unpacked into the server's folder, as a real pack is
python3 - <<'EOF' | on "cat > $old/modpacks/pack.zip && chown 1000:1000 $old/modpacks/pack.zip"
import io, sys, zipfile
b = io.BytesIO()
with zipfile.ZipFile(b, "w") as z:
    z.writestr("config/kiwi-standin.txt", "from the server pack\n")
    z.writestr("kiwipacks/README.txt", "a stand-in for a server pack\n")
sys.stdout.buffer.write(b.getvalue())
EOF
on "podman run -d --name kiwi-v1mc -e EULA=TRUE -e TYPE=NEOFORGE -e VERSION=$MC -e NEOFORGE_VERSION=$NF -e GENERIC_PACK=/modpacks/pack.zip \
    -e MEMORY=3G -e SEED=4242 -e UID=1000 -e GID=1000 -v $old/data:/data:z -v $old/modpacks:/modpacks:z $image >/dev/null"
check $? "the old server's container started"
healthy kiwi-v1mc 90; check $? "the old server answers (its first start downloads the game)"
on "podman exec kiwi-v1mc rcon-cli 'scoreboard objectives add kiwi_v1 dummy' >/dev/null; podman exec kiwi-v1mc rcon-cli save-all flush >/dev/null"
seed=$(on "podman exec kiwi-v1mc rcon-cli seed" | tr -dc '0-9-')
[ -n "$seed" ]; check $? "its world is marked and has a seed"
on "podman stop -t 90 kiwi-v1mc >/dev/null; podman rm kiwi-v1mc >/dev/null; test -f $old/data/world/level.dat && test -f $old/data/config/kiwi-standin.txt && test -f $old/data/.generic_pack.sum"
check $? "stopped; its folder has the world, the pack's files and the pack's mark"

say "== taken over: the folders moved, the server in the fleet file, $m applied"
on "mkdir -p $dd/kn-games/$name $dd/kn-games-files/$name && mv $old/data $dd/kn-games/$name/data && mv $old/modpacks $dd/kn-games-files/$name/modpacks"
check $? "data -> kn-games/$name/data, modpacks -> kn-games-files/$name/modpacks"
"$KIWI" control pull "$fleet" >/dev/null 2>&1 || true
cp "$fleet" "$before"
edit; check $? "the server is in the fleet file"
"$KIWI" apply "$fleet" "$m" > "$before.log" 2>&1; rc=$?
check $rc "$m applied$([ $rc = 0 ] || echo ": $(tail -n 3 "$before.log" | tr '\n' ' ' | cut -c1-240)")"
c=kn-game-$name
healthy $c 60; check $? "the games module's server answers"
[ "$(on "podman exec $c rcon-cli seed" | tr -dc '0-9-')" = "$seed" ]; check $? "its world has the old seed"
on "podman exec $c rcon-cli 'scoreboard objectives list'" | grep -q kiwi_v1; check $? "and the mark set under the old server"
on "grep -q -- '$NF' $dd/kn-games/$name/data/logs/latest.log"; check $? "it runs NeoForge $NF (the server's own log names it)"
on "podman logs $c 2>&1" | grep -i "generic pack" | head -2 | cut -c1-160 | sed 's/^/        /'
on "test -f $dd/kn-games/$name/data/config/kiwi-standin.txt"; check $? "the pack's files are still in place"

say "== away again"
cp "$before" "$fleet"; cmp -s "$before" "$fleet"; check $? "the fleet file is as it was"
"$KIWI" apply "$fleet" "$m" > "$before.log" 2>&1; check $? "$m applied without the server"
on "! podman container exists $c"; check $? "its container is gone"
on "rm -rf $dd/kn-games/$name $dd/kn-games-files/$name $old; rmdir $dd/kn-games $dd/kn-games-files 2>/dev/null; true"
rm -f "$before.log"
[ "$fails" = 0 ] && say "all passed" || say "$fails wrong"
exit $((fails > 0))
