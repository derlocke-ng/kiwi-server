#!/usr/bin/env python3
"""Drill: do the games module's catalog entries start, on this computer?

    tests/drills/game-images.py [GAME ...] [--wait SECONDS] [--modules DIR]

For each game of the catalog (modules/games/module.yaml, as vendored here): its image runs with
the catalog's settings, command and folders (in a temporary folder), the way the module runs it on
a machine. A game passes when its container still runs and listens inside its own network (read
from /proc/<pid>/net: nothing is published on this computer): on each TCP port, or, for a game
without one, on each UDP port. (Some games bind their UDP port only when a player comes.)
--modules: a kiwi-modules checkout to take the catalog from, instead of the one vendored here.
Without GAME: the small ones (terraria, factorio, minecraft-bedrock). The big ones download the
game from Steam first (gigabytes) and need several GB of memory: name them and give a long --wait.
Containers and folders are removed afterwards.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

import yaml

root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
args = sys.argv[1:]
wait = 240
if "--wait" in args:
    i = args.index("--wait")
    wait = int(args[i + 1])
    del args[i:i + 2]
if "--modules" in args:
    i = args.index("--modules")
    root = args[i + 1]
    del args[i:i + 2]
games = args or ["terraria", "factorio", "minecraft-bedrock"]

with open(os.path.join(root, "modules", "games", "module.yaml")) as f:
    mod = yaml.safe_load(f)
catalog = next(s for s in mod["settings"] if s["key"] == "catalog")["default"]
if isinstance(catalog, str):
    catalog = yaml.safe_load(catalog)


def fill(text, server):
    return str(text).replace("{name}", "Kiwi drill").replace("{password}", "drill-pass").replace(
        "{memory}", "1G").replace("{server}", server)


def bound(pid, port, proto):
    """Is something listening on that port in the container's network?"""
    want = ":%04X" % port
    for name in ([proto, proto + "6"]):
        try:
            with open("/proc/%d/net/%s" % (pid, name)) as f:
                rows = f.read().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            cols = row.split()
            # TCP: listening (0A); UDP: bound (07)
            if cols[1].endswith(want) and cols[3] in ("0A", "07"):
                return True
    return False


bad = 0
for game in games:
    g = catalog.get(game)
    if not g:
        print("FAIL  %s: not in the catalog" % game)
        bad += 1
        continue
    name = "kiwi-drill-game-%s" % game
    tmp = tempfile.mkdtemp(prefix="kiwi-game-")
    argv = ["podman", "run", "-d", "--name", name]
    for k, v in (g.get("env") or {}).items():
        argv += ["-e", "%s=%s" % (k, fill(v, "drill"))]
    for part in ("data", "files"):
        for label, inside in (g.get(part) or {}).items():
            os.makedirs(os.path.join(tmp, label), exist_ok=True)
            argv += ["-v", "%s:%s:z" % (os.path.join(tmp, label), inside)]
    argv.append(g["image"])
    if g.get("command"):
        argv += fill(g["command"], "drill").split()
    subprocess.run(["podman", "rm", "-f", name], capture_output=True)
    r = subprocess.run(argv, capture_output=True, text=True)
    if r.returncode != 0:
        print("FAIL  %s: did not start: %s" % (game, (r.stderr.strip().splitlines() or ["?"])[-1]))
        bad += 1
        continue
    ports = [(int(p.split("/")[0]), p.split("/")[1]) for p in g.get("ports") or []]
    ports = [p for p in ports if p[1] == "tcp"] or ports
    up, end = False, time.time() + wait
    while time.time() < end:
        st = subprocess.run(["podman", "inspect", "-f", "{{.State.Running}} {{.State.Pid}}", name], capture_output=True, text=True).stdout.split()
        if not st or st[0] != "true":
            break
        if all(bound(int(st[1]), p, proto) for p, proto in ports):
            up = True
            break
        time.sleep(5)
    if up:
        print("PASS  %s: runs and listens on %s" % (game, ", ".join("%d/%s" % p for p in ports)))
    else:
        tail = subprocess.run(["podman", "logs", "--tail", "4", name], capture_output=True, text=True)
        print("FAIL  %s: %s within %ds" % (game, "stopped" if not st or st[0] != "true" else "no port bound", wait))
        for line in (tail.stdout + tail.stderr).strip().splitlines()[-4:]:
            print("      " + line[:160])
        bad += 1
    subprocess.run(["podman", "rm", "-f", name], capture_output=True)
    subprocess.run(["podman", "unshare", "rm", "-rf", tmp], capture_output=True)
    shutil.rmtree(tmp, ignore_errors=True)
print("%d wrong" % bad)
sys.exit(1 if bad else 0)
