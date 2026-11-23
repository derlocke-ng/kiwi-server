"""One History: what was done to the machines and what they did, as one list (the app and the
control page show the same).

Three sources said much the same thing three times (the owner, 23 Nov 2026: "logging kinda twice
in the UI and both suck"): the machine's own journal (`kiwi-server history`), the control node's
tasks and its log of who did what. Here they become one entry each, newest first, by day:

    {time, ts, what, who, kind, state, detail, job}

kind: change (an apply, a move, a restore …), update, backup, runbook, start, task.
state: ok, failed, running, waiting, or "" when nothing ends (a start).
"""
import re
import time
from datetime import datetime, timedelta

# what the control node's actions are called, as the page and the app say them (done, not doing)
VERBS = {"status": "Checked", "plan": "Previewed", "apply": "Applied", "check": "Checked the names",
         "backup": "Saved the network", "watch": "Looked for restarted masters", "save": "Saved the data",
         "update": "Updated", "rotate": "Replaced the SSH keys", "setup": "Set up", "build": "Made the install media",
         "restore": "Brought back data", "unlock": "Unlocked", "remove": "Removed", "rotate-seed": "Replaced the secrets inside the services",
         "tell-test": "Sent a test message", "start-new": "Started a new backup history", "prune": "Removed old snapshots",
         "doctor": "Checked the tools of the control node", "adopt": "Took in", "service": "Looked after a container",
         "reboot": "Restarted", "poweroff": "Switched off", "wake": "Woke", "move": "Moved a part", "switch": "Gave a part to its standby",
         "mtu": "Measured the MTU", "rotate-ring": "Replaced the keys of an unlock ring",
         "window": "Let it open by itself for a while:"}
# while it runs or waits (the control page's words since 2.200)
DOING = {"status": "Checking", "plan": "Previewing", "apply": "Applying to", "check": "Checking the names",
        "backup": "Saving the network", "watch": "Looking for restarted masters", "save": "Saving the data of",
        "update": "Updating every machine", "rotate": "Replacing the SSH keys", "setup": "Setting up", "build": "Making the install media of", "restore": "Bringing back on", "unlock": "Unlocking", "wake": "Waking", "remove": "Removing", "reboot": "Restarting", "poweroff": "Switching off",
        "rotate-seed": "Replacing the secrets inside the services", "tell-test": "Sending a test message",
        "rotate-ring": "Replacing the keys of an unlock ring",
        "window": "Letting it open by itself for a while:",
        "move": "Moving a part between", "switch": "Giving a part to its standby, from",
        "start-new": "Starting a new backup history of", "prune": "Removing old snapshots on",
        "doctor": "Checking the tools of this machine", "service": "Looking after a container on"}
KIND_OF = {"apply": "change", "setup": "change", "restore": "change", "move": "change", "switch": "change", "remove": "change",
           "adopt": "change", "rotate": "change", "rotate-seed": "change", "rotate-ring": "change", "update": "update",
           "backup": "backup", "save": "backup", "prune": "backup", "start-new": "backup", "reboot": "start", "wake": "start",
           "poweroff": "start", "unlock": "start"}
FILTERS = (("all", "All"), ("changes", "Changes"), ("backups", "Backups"), ("problems", "Problems"))
SAME = 900          # seconds: the control node's record and the machine's own of one thing


def _ts(s):
    """Epoch seconds of an ISO time, with or without its offset (without: this computer's time)."""
    s = str(s or "").strip()
    if not s:
        return 0.0
    s = s.replace("Z", "+00:00")
    m = re.match(r"(.*[T ]\d\d:\d\d(?::\d\d)?)(?:\.\d+)?([+-]\d\d):?(\d\d)$", s)
    if m:
        s = "%s%s:%s" % m.groups()
    try:
        d = datetime.fromisoformat(s.replace(" ", "T", 1))
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return 0.0
    return d.timestamp()


def _own(e):
    """An entry the machine remembers (kiwi-server history)."""
    what, k = e.get("what") or "", e.get("kind") or ""
    kind = {"apply": "change", "update": "update", "backup": "backup", "runbook": "runbook", "boot": "start"}.get(k)
    state = "ok"
    if k == "failed":
        state = "failed"
        kind = "backup" if "backup" in what else "update" if "update" in what else "change"
    if k == "boot":
        state = ""
    m = re.match(r"applied: (.*?)(?:, from (\S+))?$", what)
    who = ""
    if m:
        what, who = "Applied (%s)" % m.group(1), m.group(2) or ""
    return {"time": e.get("time") or "", "ts": _ts(e.get("time")), "what": what[:1].upper() + what[1:], "who": who,
            "kind": kind or "change", "state": state, "detail": e.get("detail") or "", "job": "", "source": "machine",
            "hosts": []}


def _logged(e):
    """A line of the control node's log: who did what there."""
    a = e.get("what") or ""
    who = e.get("user") or ""
    if e.get("address"):
        who = "%s (%s)" % (who, e["address"]) if who else e["address"]
    what = VERBS.get(a, a)
    if e.get("part"):
        what += " · %s" % e["part"]
    return {"time": e.get("time") or "", "ts": _ts(e.get("time")), "what": what, "who": who, "part": e.get("part") or "",
            "kind": KIND_OF.get(a, "task"), "state": e.get("result") or "", "detail": "", "job": "", "source": "control",
            "action": a, "hosts": list(e.get("hosts") or [])}


def _job(j):
    a = j.get("action") or ""
    when = j.get("started") or j.get("id") or ""
    doing = (j.get("state") or "") in ("running", "waiting")
    return {"time": when, "ts": _ts(when), "what": (DOING if doing else VERBS).get(a, a), "who": j.get("user") or "",
            "kind": KIND_OF.get(a, "task"), "state": j.get("state") or "", "detail": "", "job": j.get("id") or "",
            "source": "control", "action": a, "hosts": list(j.get("hosts") or [])}


def merge(own=None, jobs=(), log=()):
    """One list, newest first: a task and its log line are one entry, and so are the control node's
    apply and the machine's own record of it (who from the one, what exactly from the other)."""
    items = [_logged(e) for e in log or []]
    for j in (_job(x) for x in jobs or []):
        twin = next((i for i in items if i.get("action") == j["action"] and not i["job"] and i["hosts"] == j["hosts"]
                     and abs(i["ts"] - j["ts"]) <= SAME), None)
        if twin:
            twin["job"] = j["job"]
            if not twin["state"] or twin["state"] in ("waiting", "running"):
                twin["state"] = j["state"]
        else:
            items.append(j)
    for o in (_own(e) for e in own or []):
        twin = next((i for i in items if i["source"] == "control" and i["kind"] == o["kind"] == "change"
                     and abs(i["ts"] - o["ts"]) <= SAME and not i.get("merged")), None)
        if twin:
            twin["merged"] = True
            twin["detail"] = o["what"]
            if o["state"] == "failed":
                twin["state"] = "failed"
        else:
            items.append(o)
    items.sort(key=lambda i: i["ts"], reverse=True)
    for i in items:
        i.pop("merged", None)
        if i["state"] in ("running", "waiting") and i.get("action"):       # still going: said as going
            i["what"] = DOING.get(i["action"], i["what"]) + (" · %s" % i["part"] if i.get("part") else "")
    return items


def keep(items, which):
    """The entries a filter shows: all, changes, backups or problems."""
    if which == "changes":
        return [i for i in items if i["kind"] in ("change", "update", "runbook")]
    if which == "backups":
        return [i for i in items if i["kind"] == "backup"]
    if which == "problems":
        return [i for i in items if i["state"] == "failed"]
    return list(items)


def by_day(items, now=None):
    """[(day's name, [entries])]: Today, Yesterday, then the weekday and the date."""
    now = datetime.fromtimestamp(now if now is not None else time.time())
    out = []
    for i in items:
        d = datetime.fromtimestamp(i["ts"]).date() if i["ts"] else None
        name = ("Today" if d == now.date() else "Yesterday" if d == (now - timedelta(days=1)).date()
                else d.strftime("%A, %d %B") if d else "Earlier")
        if not out or out[-1][0] != name:
            out.append((name, []))
        out[-1][1].append(i)
    return out


def clock(i):
    """The time of day of an entry, HH:MM, in this computer's time."""
    return datetime.fromtimestamp(i["ts"]).strftime("%H:%M") if i["ts"] else ""


STATE_WORDS = {"failed": "did not work", "running": "running", "waiting": "waiting"}


def badge(i):
    """The word for how an entry ended, only when it is worth a look (done says nothing)."""
    return STATE_WORDS.get(i["state"], "")
