#!/usr/bin/env bash
# A Satisfactory server of an older setup, taken over by the games module. v1 ran
# wolveix/satisfactory-server with one folder at /config: the game in `gamefiles`, the saves and
# the server's own settings in `saved`, beside `backups` and `logs`. The games module keeps the
# game apart (kn-games-files/<server>/gamefiles, not backed up) from the rest
# (kn-games/<server>/config). The drill makes a server the old way on MACHINE and does through
# the server's API what a player does in the game: claims it with an admin password, starts a
# session, saves. Then it stops the server, moves the folders, adds the server to the fleet file
# and applies. The new server must take the old admin password, run the old session by itself
# and list the save; and it must not download the game again. Afterwards the fleet file is as
# it was, the server and its folders are gone.
#
#   [KIWI_SERVER=bin/kiwi-server] tests/drills/satisfactory-takeover.sh FLEET MACHINE
#
# MACHINE: a podman machine with the vpn-client module, 8 gigabytes of memory and 8 of disk to
# spare, whose port 7777 is free, reached by `ssh MACHINE` (kiwi-server ssh-config --write). The
# first start downloads the game (about 3 gigabytes). REUSE=1 takes the game a run before left
# in /var/tmp/kiwi-v1sat. One PASS or FAIL line per check.
set -u
fleet=${1:?the fleet file}; m=${2:?a machine}
KIWI=${KIWI_SERVER:-kiwi-server}
here=$(cd "$(dirname "$0")/../.." && pwd)
name=takeover; image=docker.io/wolveix/satisfactory-server:latest; session=kiwi-v1; save=kiwi-drill
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
fails=0
check() { if [ "$1" = 0 ]; then say "PASS $2"; else say "FAIL $2"; fails=$((fails + 1)); fi; }
on() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$m" "sudo sh -c $(printf '%q' "$1")"; }
# the server's API, asked on the machine. The admin password is made there and stays there, in a
# file only root reads; it is a stand-in server's and goes with it
helper=/var/tmp/kiwi-sat-api.sh; pw=/var/tmp/kiwi-sat.pw
api() { on "sh $helper $*"; }
healthy() {     # TRIES: every 20 s
    local n=$1
    while [ "$n" -gt 0 ]; do api health | grep -q healthy && return 0; sleep 20; n=$((n - 1)); done
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
new = new and fleettext.put(new, ("hosts", m, "node", "games"), {"servers": {name: {"game": "satisfactory"}}})
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
old=/var/tmp/kiwi-v1sat

on "cat > $helper" <<'EOF'
# kiwi-server's satisfactory-takeover drill: the server's API on this machine
call() { curl -sk -m 30 -X POST https://127.0.0.1:7777/api/v1 -H "Content-Type: application/json" ${TOKEN:+-H "Authorization: Bearer $TOKEN"} -d "$1"; }
token() { sed -n 's/.*"authenticationToken":"\([^"]*\)".*/\1/p'; }
login() { TOKEN=$(call "{\"function\":\"PasswordLogin\",\"data\":{\"MinimumPrivilegeLevel\":\"Administrator\",\"Password\":\"$(cat /var/tmp/kiwi-sat.pw)\"}}" | token); [ -n "$TOKEN" ]; }
case $1 in
    health) call '{"function":"HealthCheck","data":{"clientCustomData":""}}' ;;
    claim)  umask 077; head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n' > /var/tmp/kiwi-sat.pw
            TOKEN=$(call '{"function":"PasswordlessLogin","data":{"MinimumPrivilegeLevel":"InitialAdmin"}}' | token)
            call "{\"function\":\"ClaimServer\",\"data\":{\"ServerName\":\"kiwi stand-in\",\"AdminPassword\":\"$(cat /var/tmp/kiwi-sat.pw)\"}}" | grep -q authenticationToken && echo claimed ;;
    new)    login && call "{\"function\":\"CreateNewGame\",\"data\":{\"NewGameData\":{\"SessionName\":\"$2\"}}}"; echo started ;;
    save)   login && call "{\"function\":\"SaveGame\",\"data\":{\"SaveName\":\"$2\"}}"; echo saved ;;
    login)  login && echo signed-in ;;
    state)  login && call '{"function":"QueryServerState","data":{}}' ;;
    saves)  login && call '{"function":"EnumerateSessions","data":{}}' ;;
esac
EOF

say "== a server the old way on $m: one folder at /config"
on "podman rm -f kiwi-v1sat >/dev/null 2>&1; true"
if [ "${REUSE:-0}" = 1 ] && on "test -d $old/gamefiles/FactoryGame"; then
    on "rm -rf $old/saved $old/backups $old/logs"; say "        the game's files of a run before are taken"
else
    on "rm -rf $old; mkdir -p $old && chown 1000:1000 $old"
fi
on "podman run -d --restart on-failure:5 --name kiwi-v1sat -e MAXPLAYERS=4 -e PUID=1000 -e PGID=1000 -p 127.0.0.1:7777:7777/tcp -v $old:/config:z $image >/dev/null"
check $? "the old server's container started"
healthy 90; check $? "the old server answers (its first start downloads the game)"
api claim | grep -q claimed; check $? "claimed, with an admin password of its own"
api new "$session" >/dev/null; n=12
while [ "$n" -gt 0 ]; do api state | grep -q "\"activeSessionName\":\"$session\".*\"isGameRunning\":true" && break; sleep 10; n=$((n - 1)); done
[ "$n" -gt 0 ]; check $? "a session runs on it ($session)"
api save "$save" >/dev/null; sleep 5
on "test -s $old/saved/server/$save.sav"; check $? "and is saved (saved/server/$save.sav)"
on "podman stop -t 60 kiwi-v1sat >/dev/null 2>&1; podman rm kiwi-v1sat >/dev/null"
size=$(on "du -sm $old | cut -f1"); say "        stopped; the folder holds $size MB"

say "== taken over: the folders moved, the server in the fleet file, $m applied"
on "mkdir -p $dd/kn-games/$name $dd/kn-games-files/$name && mv $old/gamefiles $dd/kn-games-files/$name/gamefiles && mv $old $dd/kn-games/$name/config"
check $? "gamefiles -> kn-games-files/$name/gamefiles, the rest -> kn-games/$name/config"
"$KIWI" control pull "$fleet" >/dev/null 2>&1 || true
cp "$fleet" "$before"
edit; check $? "the server is in the fleet file"
began=$(date +%s)
"$KIWI" apply "$fleet" "$m" > "$before.log" 2>&1; rc=$?
check $rc "$m applied$([ $rc = 0 ] || echo ": $(tail -n 3 "$before.log" | tr '\n' ' ' | cut -c1-240)")"
c=kn-game-$name
healthy 45; check $? "the games module's server answers"
took=$(( $(date +%s) - began )); say "        $took s from the apply to its answer"
[ "$took" -lt 600 ]; check $? "it did not download the game again (its answer within ten minutes)"
api login | grep -q signed-in; check $? "it takes the old admin password (its settings came along)"
n=12; while [ "$n" -gt 0 ]; do api state | grep -q "\"activeSessionName\":\"$session\".*\"isGameRunning\":true" && break; sleep 10; n=$((n - 1)); done
[ "$n" -gt 0 ]; check $? "it runs the old session by itself ($session)"
api saves | grep -q "\"saveName\":\"$save\""; check $? "and lists the save made under the old server ($save)"
on "ls -A $dd/kn-games/$name/config/gamefiles 2>/dev/null | wc -l" | grep -qx 0; check $? "the game lies outside the folder that is backed up"
n=10; while [ "$n" -gt 0 ]; do h=$(on "podman inspect -f '{{.State.Health.Status}}' $c 2>/dev/null"); [ "$h" = healthy ] && break; sleep 10; n=$((n - 1)); done
[ "$h" = healthy ]; check $? "its health check says ${h:-nothing} (the server's own answer, asked every minute)"

say "== away again"
cp "$before" "$fleet"; cmp -s "$before" "$fleet"; check $? "the fleet file is as it was"
"$KIWI" apply "$fleet" "$m" > "$before.log" 2>&1; check $? "$m applied without the server"
on "! podman container exists $c"; check $? "its container is gone"
on "rm -rf $dd/kn-games/$name $dd/kn-games-files/$name $old $helper $pw; rmdir $dd/kn-games $dd/kn-games-files 2>/dev/null; true"
[ "$fails" = 0 ] && say "all passed" || say "$fails wrong"
exit $((fails > 0))
