#!/usr/bin/env bash
# An Enshrouded server of an older setup, taken over by the games module. v1 ran
# mornedhels/enshrouded-server with one folder mounted at /opt/enshrouded: the game in it, and
# the world under server/savegame. The games module keeps the world apart (kn-games/<server>/
# savegame, backed up) from the game's files (kn-games-files/<server>/game, not backed up). The
# drill makes a server the old way on MACHINE, lets it write its world, stops it, moves the
# world and the game's files to where the module keeps them, adds the server to the fleet file
# and applies. The new server must go on with the old world, and must not download the game again.
# Afterwards the fleet file is as it was, the server and its folders are gone.
#
#   [KIWI_SERVER=bin/kiwi-server] tests/drills/enshrouded-takeover.sh FLEET MACHINE
#
# MACHINE: a podman machine with the vpn-client module, 16 gigabytes of memory and 20 of disk to
# spare, reached by `ssh MACHINE` (kiwi-server ssh-config --write). The first start downloads the
# game (about 8 gigabytes): ten minutes on a fast line. REUSE=1 takes the game a run before left
# in /var/tmp/kiwi-v1ens (a run that was cut off) and makes only the world anew. One PASS or FAIL
# line per check.
set -u
fleet=${1:?the fleet file}; m=${2:?a machine}
KIWI=${KIWI_SERVER:-kiwi-server}
here=$(cd "$(dirname "$0")/../.." && pwd)
name=takeover; image=docker.io/mornedhels/enshrouded-server:latest
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
on() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$m" "sudo sh -c $(printf '%q' "$1")"; }
up() {          # LOG TRIES: the server says it is online, in its own log; asked every 20 s
    local n=$2
    while [ "$n" -gt 0 ]; do on "grep -q \"'HostOnline' (up)\" $1 2>/dev/null" && return 0; sleep 20; n=$((n - 1)); done
    return 1
}
edit() {
    PYTHONPATH="$here/lib" python3 -c '
import sys, yaml
from kiwiserver import fleettext, util
path, m, name = sys.argv[1:4]
text = util.read_text(path)
node = yaml.safe_load(text)["hosts"][m]["node"]
new = fleettext.put(text, ("hosts", m, "node", "modules"), list(node["modules"]) + ["games"])
new = new and fleettext.put(new, ("hosts", m, "node", "games"), {"servers": {name: {"game": "enshrouded", "name": "kiwi stand-in"}}})
if not new:
    sys.exit("the fleet file could not be changed in place")
util.write_text(path, new, 0o600)' "$fleet" "$m" "$name"
}

before=$(mktemp "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/kiwi-fleet.XXXXXX") || exit 2      # in memory, never on a disk
cp "$fleet" "$before"
back() { [ -s "$before" ] && ! cmp -s "$before" "$fleet" && cp "$before" "$fleet"; rm -f "$before" "$before.log"; }
trap back EXIT
grep -qE "^\s+modules:.*\bgames\b" <(sed -n "/^  $m:/,/^  [a-z]/p" "$fleet") && { echo "$m already has the games module: take another machine" >&2; exit 2; }
dd=$(on 'sed -n "s/^STACK_DIR=//p" /etc/kiwi-server/stack.env'); [ -n "$dd" ] || { echo "$m has no stack" >&2; exit 2; }
old=/var/tmp/kiwi-v1ens

say "== a server the old way on $m: one folder at /opt/enshrouded"
on "podman rm -f kiwi-v1ens >/dev/null 2>&1; true"
if [ "${REUSE:-0}" = 1 ] && on "test -f $old/current_version"; then
    on "rm -rf $old/server/savegame $old/server/logs"; say "        the game's files of a run before are taken"
else
    on "rm -rf $old; mkdir -p $old && chown 1000:1000 $old"
fi
on "podman run -d --name kiwi-v1ens -e SERVER_NAME='kiwi stand-in' -e PUID=1000 -e PGID=1000 -v $old:/opt/enshrouded:z $image >/dev/null"
check $? "the old server's container started"
up "$old/server/logs/enshrouded_server.log" 150; check $? "the old server is online (its first start downloads the game)"
on "! grep -q \"Found file 'world0.ksd'\" $old/server/logs/enshrouded_server.log"; check $? "it started without a world, and made one"
# it writes its world when it stops
on "podman stop -t 120 kiwi-v1ens >/dev/null; podman rm kiwi-v1ens >/dev/null"
on "test -s $old/server/savegame/world0.ksd"; check $? "stopped; it left its world (server/savegame/world0.ksd)"
size=$(on "du -sm $old | cut -f1"); say "        the folder holds $size MB"
version=$(on "cat $old/current_version 2>/dev/null")

say "== taken over: the world and the game's files moved, the server in the fleet file, $m applied"
# (its log goes: the new server's log must be the one that says it is online)
on "mkdir -p $dd/kn-games/$name $dd/kn-games-files/$name && mv $old/server/savegame $dd/kn-games/$name/savegame && rm -rf $old/server/logs && mv $old $dd/kn-games-files/$name/game"
check $? "server/savegame -> kn-games/$name/savegame, the rest -> kn-games-files/$name/game"
"$KIWI" control pull "$fleet" >/dev/null 2>&1 || true
cp "$fleet" "$before"
edit; check $? "the server is in the fleet file"
began=$(date +%s)
"$KIWI" apply "$fleet" "$m" > "$before.log" 2>&1; rc=$?
check $rc "$m applied$([ $rc = 0 ] || echo ": $(tail -n 3 "$before.log" | tr '\n' ' ' | cut -c1-240)")"
c=kn-game-$name
log=$dd/kn-games-files/$name/game/server/logs/enshrouded_server.log
up "$log" 60; check $? "the games module's server is online"
took=$(( $(date +%s) - began )); say "        $took s from the apply to online"
[ "$(on "cat $dd/kn-games-files/$name/game/current_version 2>/dev/null")" = "$version" ] && [ "$took" -lt 600 ]
check $? "it did not download the game again (the same version, online within ten minutes)"
on "grep -q \"Found file 'world0.ksd'\" $log"; check $? "it found the old world (its log: Found file 'world0.ksd')"
on "systemctl stop $c.service"; n=$(on "ls $dd/kn-games/$name/savegame/backup | wc -l")
[ "${n:-0}" -ge 2 ]; check $? "stopped, it saved into the same folder again ($n copies of the world kept beside it)"
on "ls -A $dd/kn-games-files/$name/game/server/savegame 2>/dev/null | wc -l" | grep -qx 0; check $? "and nothing was written into the game's own folder instead"

say "== away again"
cp "$before" "$fleet"; cmp -s "$before" "$fleet"; check $? "the fleet file is as it was"
"$KIWI" apply "$fleet" "$m" > "$before.log" 2>&1; check $? "$m applied without the server"
on "! podman container exists $c"; check $? "its container is gone"
on "rm -rf $dd/kn-games/$name $dd/kn-games-files/$name $old; rmdir $dd/kn-games $dd/kn-games-files 2>/dev/null; true"
[ "$fails" = 0 ] && say "all passed" || say "$fails wrong"
exit $((fails > 0))
