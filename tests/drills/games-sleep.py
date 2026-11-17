#!/usr/bin/env python3
"""Game servers that sleep: one with `sleep: N` stops after N minutes without players, and the
first player who knocks wakes it.

    tests/drills/games-sleep.py ~/kiwi/fleet.yaml medlar

Waits until every server of the machine with `sleep` set is asleep (stopped, marked in
/run/kiwi-stack/sleeping), checks that `kiwi-server status` calls that asleep and not a problem,
then knocks the way a game's client does (tests/drills/game-ports.py: Minecraft's server list,
Zandronum's launcher question) until each answers, and says how long the waking took. Nobody
should play on the machine meanwhile: a player keeps a server awake.
"""
import os
import subprocess
import sys
import time

from drill import kiwi, say, setup, wait

args, fleet, m = setup(__doc__.splitlines()[0], roles=("host",))
mach = m["host"]
g = mach.host.module_settings.get("games") or {}
catalog = g.get("catalog") or {}
sleepers = {}
for name, s in (g.get("servers") or {}).items():
    if s.get("sleep") and s.get("game") in catalog:
        game = catalog[s["game"]]
        first = int(game["ports"][0].split("/")[0])
        port = int(s.get("port") or first)
        sleepers["kn-game-%s" % name] = (s["game"], port, int(s["sleep"]))
if not sleepers:
    sys.exit("drill: no game server of %s has sleep set (games.servers.<name>.sleep)" % mach.name)
say("%s: %s" % (mach.name, ", ".join("%s (%s:%d, %d min)" % (c, g_, p, n) for c, (g_, p, n) in sleepers.items())))
if mach.run("systemctl -q is-active kn-games-sleep.service")[0] != 0:
    sys.exit("drill: kn-games-sleep.service does not run on %s: apply first" % mach.name)
addr = mach.address
here = os.path.dirname(os.path.abspath(__file__))
bad = 0


def asleep(c):
    return mach.run("test -e /run/kiwi-stack/sleeping/%s && ! systemctl -q is-active %s.service" % (c, c), 30)[0] == 0


def answers(c):
    game, port, _ = sleepers[c]
    who = game if game in ("minecraft", "zandronum") else "tcp"
    r = subprocess.run([sys.executable, os.path.join(here, "game-ports.py"), addr, "%s:%d" % (who, port)],
                       capture_output=True, text=True, timeout=60)
    return r.returncode == 0


longest = max(n for _, _, n in sleepers.values())
t0 = time.time()
for c in sleepers:
    took = wait(lambda: asleep(c), (longest + 3) * 60, every=15)
    if took is None:
        say("FAILED: %s did not fall asleep within %d minutes" % (c, longest + 3))
        bad += 1
    else:
        say("%s asleep after %d s of this drill" % (c, round(time.time() - t0)))
rc, out = kiwi("status", args.fleet, mach.name, timeout=300)
text = "\n".join(out)
if any("asleep" in line for line in out) and not any("is not running" in line and "kn-game-" in line for line in out):
    say("status calls it asleep, not a problem")
else:
    say("FAILED: status does not say asleep:\n" + text[-800:])
    bad += 1
for c in sleepers:
    t1 = time.time()
    took = wait(lambda: answers(c), 300, every=5)         # each knock counts; the first wakes it
    if took is None:
        say("FAILED: %s did not wake within 5 minutes of knocking" % c)
        bad += 1
    else:
        say("%s answers %d s after the first knock" % (c, round(time.time() - t1)))
print()
print("every sleeping server slept and woke for its first player" if not bad else "%d step(s) FAILED" % bad)
sys.exit(1 if bad else 0)
