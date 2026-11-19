"""A service's log for a person (the owner, 19 Nov 2026: "people who have no clue need to read logs
they don't understand"): first in words why it stops, if it does; then its own lines with a short
time, without podman's lines about creating and removing its container. The full log stays one
step away (`service … logs --raw`, a download on the page, Save in the app).
"""
import re

from . import util

# journalctl -o short-iso: 2026-11-19T20:51:34+01:00 quince.home kn-signup[803776]: message
LINE = re.compile(r"^\d{4}-\d\d-\d\dT(\d\d:\d\d:\d\d)\S* \S+ ([^\s\[:]+)(?:\[\d+\])?: (.*)$")
ERROR = re.compile(r"error|fatal|traceback|exception|panic|denied|refused|not found|no such|failed|cannot|"
                   r"can't|invalid|expected|unable|timed out|no space", re.I)


def tidy(text, name):
    """(summary lines, kept lines, how many left out) of a unit's journal; another log (docker
    logs) is kept as it is."""
    kept, left_out, restarts, state = [], 0, 0, ""
    own = []
    for line in text.splitlines():
        m = LINE.match(line)
        if not m:
            if line.strip():
                kept.append(line)
                own.append(line)
            continue
        t, ident, msg = m.groups()
        if ident in ("podman", "conmon") or re.fullmatch(r"[0-9a-f]{64}", msg.strip()):
            left_out += 1                   # the container made, started, died, removed; its id
            continue
        if ident == "systemd":
            msg = msg.replace(name + ".service: ", "")
            r = re.search(r"restart counter is at (\d+)", msg)
            if r:
                restarts = int(r.group(1))
            if msg.startswith("Started "):
                state = "started"
            elif "Failed with result" in msg or msg.startswith("Main process exited"):
                state = "failed"
            elif msg.startswith("Stopped ") or msg.startswith("Deactivated"):
                state = "stopped"
            kept.append("%s  [systemd] %s" % (t, msg))
            continue
        kept.append("%s  %s" % (t, msg))
        own.append(msg)
    head = []
    if restarts >= 2 and state in ("failed", "started"):
        head.append("%s keeps stopping right after it starts (%s so far). Restarting it does not help "
                    "while the cause stays." % (name, util.count(restarts, "restart")))
    elif state == "failed":
        head.append("%s stopped with an error." % name)
    errors = [x for x in own if ERROR.search(x)]
    if errors and head:
        head.append("Its last words: %s" % errors[-1].strip())
    return head, kept, left_out


def say(text, name, out=print):
    head, kept, left_out = tidy(text, name)
    for h in head:
        out(h)
    if head:
        out("")
    for k in kept:
        out(k)
    if left_out:
        out("(%s of podman about the container left out: --raw shows them)" % util.count(left_out, "line"))
