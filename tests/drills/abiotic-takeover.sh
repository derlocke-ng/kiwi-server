#!/usr/bin/env bash
# An Abiotic Factor server of an older setup, taken over by the games module. v1 ran
# ghcr.io/pleut/abiotic-factor-linux-docker with two folders: `gamefiles` at /server (the game)
# and `data` at /server/AbioticFactor/Saved (the world under SaveGames/Server/Worlds/<name>, the
# settings, the logs). The games module keeps them as kn-games-files/<server>/game and
# kn-games/<server>/saves. The drill makes a server the old way on MACHINE, lets it write its
# world, stops it, moves the two folders, adds the server to the fleet file and applies. The new
# server must save the same world again, and must not download the game again. (The game writes
# its world five minutes after it is up, and every five minutes: a world nobody played in is
# written with the same content, a new world never is. That is the check.) Afterwards the fleet
# file is as it was, the server and its folders are gone.
#
#   [KIWI_SERVER=bin/kiwi-server] tests/drills/abiotic-takeover.sh FLEET MACHINE
#
# MACHINE: a podman machine with the vpn-client module, 8 gigabytes of memory and 8 of disk to
# spare, reached by `ssh MACHINE` (kiwi-server ssh-config --write). The first start downloads the
# game (about 3 gigabytes); with the two waits for a save it takes twenty minutes. REUSE=1 takes
# the game a run before left in /var/tmp/kiwi-v1abf.
# One PASS or FAIL line per check.
set -u
fleet=${1:?the fleet file}; m=${2:?a machine}
KIWI=${KIWI_SERVER:-kiwi-server}
here=$(cd "$(dirname "$0")/../.." && pwd)
name=takeover; image=ghcr.io/pleut/abiotic-factor-linux-docker:latest; world=Cascade
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
on() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$m" "sudo sh -c $(printf '%q' "$1")"; }
up() {          # LOG TRIES: the server has its session, in the game's own log; asked every 20 s
    local n=$2
    while [ "$n" -gt 0 ]; do on "grep -q 'Session short code' $1 2>/dev/null" && return 0; sleep 20; n=$((n - 1)); done
    return 1
}
file() { echo "$1/SaveGames/Server/Worlds/$world/WorldSave_Facility.sav"; }
sum() { on "sha256sum < $(file "$1") 2>/dev/null" | cut -d' ' -f1; }
saved() {       # FOLDER SINCE TRIES: the world's file was written after SINCE (seconds); asked every 20 s
    local n=$3 t
    while [ "$n" -gt 0 ]; do t=$(on "stat -c %Y $(file "$1") 2>/dev/null"); [ "${t:-0}" -gt "$2" ] && return 0; sleep 20; n=$((n - 1)); done
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
new = new and fleettext.put(new, ("hosts", m, "node", "games"), {"servers": {name: {"game": "abiotic-factor", "name": "kiwi stand-in"}}})
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
old=/var/tmp/kiwi-v1abf

say "== a server the old way on $m: gamefiles at /server, data at /server/AbioticFactor/Saved"
on "podman rm -f kiwi-v1abf >/dev/null 2>&1; true"
if [ "${REUSE:-0}" = 1 ] && on "test -d $old/gamefiles/AbioticFactor"; then
    on "rm -rf $old/data && mkdir -p $old/data"; say "        the game's files of a run before are taken"
else
    on "rm -rf $old; mkdir -p $old/gamefiles $old/data"
fi
# (its first start ends without the game, the second downloads it: started again as docker's restart did)
on "podman run -d --restart on-failure:5 --name kiwi-v1abf -e MaxServerPlayers=6 -e Port=7777 -e QueryPort=27015 -e SteamServerName='kiwi stand-in' \
    -e WorldSaveName=$world -e AutoUpdate=true -v $old/gamefiles:/server:z -v $old/data:/server/AbioticFactor/Saved:z $image >/dev/null"
check $? "the old server's container started"
up "$old/data/Logs/AbioticFactor.log" 90; check $? "the old server has its session (its first start downloads the game)"
saved "$old/data" 0 30; check $? "it wrote its world (its first save, five minutes later)"
# it does not end on a polite word: stopped the hard way, its world is written by then
on "podman stop -t 10 kiwi-v1abf >/dev/null 2>&1; podman rm kiwi-v1abf >/dev/null"
was=$(sum "$old/data"); [ -n "$was" ]; check $? "stopped; its world is on the disk"
size=$(on "du -sm $old | cut -f1"); say "        the folders hold $size MB"

say "== taken over: the two folders moved, the server in the fleet file, $m applied"
# (its log goes: the new server's log must be the one that says it has its session)
on "mkdir -p $dd/kn-games/$name $dd/kn-games-files/$name && rm -rf $old/data/Logs && mv $old/data $dd/kn-games/$name/saves && mv $old/gamefiles $dd/kn-games-files/$name/game"
check $? "data -> kn-games/$name/saves, gamefiles -> kn-games-files/$name/game"
"$KIWI" control pull "$fleet" >/dev/null 2>&1 || true
cp "$fleet" "$before"
edit; check $? "the server is in the fleet file"
began=$(date +%s)
"$KIWI" apply "$fleet" "$m" > "$before.log" 2>&1; rc=$?
check $rc "$m applied$([ $rc = 0 ] || echo ": $(tail -n 3 "$before.log" | tr '\n' ' ' | cut -c1-240)")"
c=kn-game-$name
up "$dd/kn-games/$name/saves/Logs/AbioticFactor.log" 45; check $? "the games module's server has its session"
took=$(( $(date +%s) - began )); say "        $took s from the apply to its session"
[ "$took" -lt 600 ]; check $? "it did not download the game again (its session within ten minutes)"
saved "$dd/kn-games/$name/saves" "$began" 30; check $? "it saved its world (five minutes after its start)"
[ -n "$was" ] && [ "$(sum "$dd/kn-games/$name/saves")" = "$was" ]; check $? "and what it saved is the old world (the same content; a new world is another)"

say "== away again"
cp "$before" "$fleet"; cmp -s "$before" "$fleet"; check $? "the fleet file is as it was"
"$KIWI" apply "$fleet" "$m" > "$before.log" 2>&1; check $? "$m applied without the server"
on "! podman container exists $c"; check $? "its container is gone"
on "rm -rf $dd/kn-games/$name $dd/kn-games-files/$name $old; rmdir $dd/kn-games $dd/kn-games-files 2>/dev/null; true"
[ "$fails" = 0 ] && say "all passed" || say "$fails wrong"
exit $((fails > 0))
