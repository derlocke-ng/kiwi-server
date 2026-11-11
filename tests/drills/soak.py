#!/usr/bin/env python3
"""Watch a fleet for hours, read-only: `kiwi-server status` every few minutes, noting when each
problem starts and ends, every container that restarted, every machine that rebooted or did not
answer, and each machine's backups as the night goes by. A summary at the end.

    tests/drills/soak.py ~/kiwi/fleet.yaml --hours 3 --every 600 > soak.log

Changes nothing, so it needs no drills.yaml; run it across the nightly backup and the update
window to see a night as the machines live it.
"""
import argparse
import json
import os
import re
import subprocess
import time

from drill import ROOT, say

UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}


def up_seconds(status):
    """'Up 6 hours' -> 21600; None when it is not running."""
    m = re.match(r"Up (about |less than )?(an?|\d+) (second|minute|hour|day|week)", status or "")
    if not m:
        return None
    n = 1 if m.group(2) in ("a", "an") else int(m.group(2))
    return n * UNITS[m.group(3)]


def status(fleet):
    try:
        r = subprocess.run([os.path.join(ROOT, "bin", "kiwi-server"), "status", fleet, "--porcelain"],
                           capture_output=True, text=True, timeout=900)
        return json.loads(r.stdout)["hosts"]
    except (subprocess.TimeoutExpired, ValueError, KeyError):
        return None


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("fleet")
    p.add_argument("--hours", type=float, default=3)
    p.add_argument("--every", type=int, default=600, help="seconds between looks (default %(default)s)")
    a = p.parse_args()
    open_problems, uptime, boot, backup = {}, {}, {}, {}
    events = []

    def note(text):
        say(text)
        events.append(time.strftime("%H:%M ") + text)
    end = time.time() + a.hours * 3600
    looks = 0
    while True:
        hosts = status(a.fleet)
        looks += 1
        if hosts is None:
            note("status gave no answer")
        for h in hosts or []:
            name, f = h["host"], h.get("facts") or {}
            now = {(name, x["text"]) for x in h.get("problems") or []}
            for key in now - set(open_problems):
                open_problems[key] = time.time()
                note("%s: problem: %s" % key)
            for key in [k for k in open_problems if k[0] == name and k not in now]:
                note("%s: gone after %d min: %s" % (name, (time.time() - open_problems.pop(key)) / 60, key[1]))
            if not h.get("ok"):
                continue
            up = f.get("uptime")
            if isinstance(up, (int, float)):
                if name in boot and up < boot[name]:
                    note("%s: rebooted (up %d min)" % (name, up / 60))
                boot[name] = up
            for s in f.get("services") or []:
                key, sec = (name, s["name"]), up_seconds(s.get("status"))
                if sec is not None and key in uptime and uptime[key] is not None and sec + a.every < uptime[key]:
                    note("%s: %s restarted (%s)" % (name, s["name"], s["status"]))
                uptime[key] = sec
            b = f.get("backup") or {}
            if b.get("last") and backup.get(name) not in (None, b["last"]):
                copies = ", ".join(c["server"] for c in b.get("copies") or [] if c.get("last") == b["last"])
                note("%s: backed up (%s)" % (name, copies or "no copy has this time"))
            if b.get("last"):
                backup[name] = b["last"]
        if time.time() + a.every > end:
            break
        time.sleep(a.every)
    say("---- %d looks over %.1f h" % (looks, a.hours))
    for e in events:
        print("  " + e)
    for (name, text), since in open_problems.items():
        print("  still open on %s since %s: %s" % (name, time.strftime("%H:%M", time.localtime(since)), text))
    if not events:
        print("  nothing happened")


if __name__ == "__main__":
    main()
