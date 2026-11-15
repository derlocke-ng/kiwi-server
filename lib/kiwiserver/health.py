"""How the network is, kept over time: for the status page and for messages.

`kiwi-server status` asks every machine how it is. That says whether its containers run,
not whether a service answers: a Nextcloud whose database lost its settings in a power cut
has every container "healthy" and serves nobody. So every service is also asked for a page
over HTTPS, from the control node, the way a device on the network asks.

What is found is kept in one small file (no secret in it): since when each thing is wrong,
and the incidents of the last weeks. From it come the status page, which needs no login,
and the messages (`notify.py`): one when something has been wrong for a while, one when it
is fine again.
"""
import concurrent.futures
import re
import ssl
import time
import urllib.error
import urllib.request

# a service by the first part of its name, in the words the pages use
SERVICES = {"cloud": "Files (Nextcloud)", "vault": "Passwords (Vaultwarden)", "pihole": "Ad blocker (Pi-hole)",
            "portainer": "Containers (Portainer)", "immich": "Photos (Immich)", "paperless": "Documents (Paperless)",
            "opencloud": "Files (OpenCloud)", "dl": "Downloads (Transmission)", "jd": "Downloads (JDownloader)",
            "wg": "VPN devices (wg-easy)", "control": "This page", "backup": "Backup store", "cache": "Local cache",
            "hub": "Hub page", "relay": "Relay", "office": "Office (Collabora)", "git": "Git (Forgejo)", "chat": "Jabber",
            "status": "Status page"}
KEEP_DAYS = 30
KEEP_INCIDENTS = 200


def label(site):
    first = site.split(".", 1)[0]
    return SERVICES.get(first, first.capitalize())


def probe_one(site, timeout=6):
    """(answers, what it said): a page over HTTPS. Its certificate is not judged here, and a
    page that wants a login (401, 403) answers. A proxy that cannot reach its service (502,
    503, 504) does not."""
    try:
        with urllib.request.urlopen(urllib.request.Request("https://%s/" % site, method="GET"), timeout=timeout,
                                    context=ssl._create_unverified_context()) as r:
            return True, r.status
    except urllib.error.HTTPError as e:
        with e:
            return e.code < 500, e.code
    except (urllib.error.URLError, OSError, ValueError) as e:
        return False, str(getattr(e, "reason", e))[:80]


def probe(sites, timeout=6, workers=8, one=probe_one, again=2, sleep=time.sleep):
    """{site: (answers, what it said)} for every site, asked side by side. One that does not
    answer is asked once more a moment later: a single lost question is not a service that is down."""
    sites = list(sites)
    if not sites:
        return {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        out = dict(zip(sites, pool.map(lambda s: one(s, timeout), sites)))
        silent = [s for s in sites if not out[s][0]]
        if silent and again:
            sleep(again)
            out.update(zip(silent, pool.map(lambda s: one(s, timeout), silent)))
    return out


def _key(text):
    """A problem's words without its numbers: "disk 91% full" and "disk 92% full" are one problem."""
    return re.sub(r"\d+([.,]\d+)?", "#", text).strip().lower()[:160]


def duration(seconds):
    s = max(0, int(seconds))
    return ("%d minutes" % max(1, s // 60) if s < 5400 else "%d hours" % (s // 3600) if s < 172800 else "%d days" % (s // 86400))


def update(state, machines, services, happened, now, after=600):
    """One look, written into `state` (changed in place). Returns what is to be told now:
    [{kind: wrong|fine|did, level: bad|warn|info, text}].

    machines: [{name, ok, problems: [{level, text}]}] of the machines that are set up;
    services: [{site, host, admin, ok}] or None when they could not be asked from here;
    happened: what the control node did by itself this look: [{host, kind, text}].
    A thing counts as wrong when it has been wrong for `after` seconds; it is told once then,
    and once more when it is fine again."""
    items, events, seen = state.setdefault("items", {}), [], {}
    for m in machines:
        if not m["ok"]:
            seen["machine:" + m["name"]] = ("machine", "bad", "%s does not answer" % m["name"], "%s answers again" % m["name"])
            continue
        for p in m.get("problems") or []:
            if p.get("level") in ("warn", "bad"):
                seen["problem:%s:%s" % (m["name"], _key(p["text"]))] = (
                    "problem", p["level"], "%s: %s" % (m["name"], p["text"]), "%s: no longer so: %s" % (m["name"], p["text"]))
    down = {m["name"] for m in machines if not m["ok"]}
    for s in services or []:
        if not s["ok"] and s["host"] not in down:        # a machine that is gone takes its services with it: said once
            seen["service:" + s["site"]] = ("service", "bad", "%s does not answer (%s)" % (label(s["site"]), s["site"]),
                                            "%s answers again (%s)" % (label(s["site"]), s["site"]))
    for key, (kind, level, text, fine) in seen.items():
        item = items.setdefault(key, {"kind": kind, "since": now, "told": False})
        item.update(level=level, text=text, fine=fine)
        if not item["told"] and now - item["since"] >= after:
            item["told"] = True
            events.append({"kind": "wrong", "level": level, "text": text})
            if kind in ("machine", "service"):
                state.setdefault("incidents", []).append({"key": key, "text": text, "from": item["since"], "to": None})
    for key in [k for k in items if k not in seen]:
        if services is None and key.startswith("service:"):
            continue                    # not asked this time: nothing is known about it
        item = items.pop(key)
        if item["told"]:
            events.append({"kind": "fine", "level": "info",
                           "text": "%s, after %s" % (item.get("fine") or item["text"], duration(now - item["since"]))})
            for inc in state.get("incidents") or []:
                if inc["key"] == key and inc["to"] is None:
                    inc["to"] = now
    did = state.setdefault("did", {})
    for e in happened or []:
        if e.get("kind") in ("restarted", "unlocked", "locked", "held", "tell"):
            key = "%s:%s" % (e["host"], e["kind"])
            if now - did.get(key, 0) > 3600:       # the same thing said again and again is told once an hour at most
                did[key] = now
                events.append({"kind": "did", "level": "warn" if e["kind"] in ("restarted", "unlocked") else "bad", "text": e["text"]})
    for key in [k for k, t in did.items() if now - t > 86400]:
        del did[key]
    state["incidents"] = [i for i in state.get("incidents") or [] if (i["to"] or now) > now - KEEP_DAYS * 86400][-KEEP_INCIDENTS:]
    state["checked"] = now
    state["machines"] = {"on": len(machines) - len(down), "of": len(machines)}
    if services is not None:
        was = {s["site"]: s for s in state.get("services") or []}
        rows = []
        for s in services:
            ok = s["ok"] and s["host"] not in down
            old = was.get(s["site"]) or {}
            rows.append({"site": s["site"], "label": label(s["site"]), "host": s["host"], "admin": bool(s.get("admin")), "ok": ok,
                         "since": old.get("since", now) if old.get("ok") == ok else now})
        state["services"] = rows
    return events


def message(events, network=""):
    """(title, text, level) of one message about these events."""
    name = "Kiwi %s" % network if network else "Kiwi"
    wrong = [e for e in events if e["kind"] != "fine"]
    if len(events) == 1:
        title = "%s: %s" % (name, events[0]["text"])
    elif wrong:
        title = "%s: %d things need a look" % (name, len(wrong)) if len(wrong) > 1 else "%s: %s" % (name, wrong[0]["text"])
    else:
        title = "%s: fine again" % name
    lines = ["%s %s" % ("Fine again:" if e["kind"] == "fine" else "Done by itself:" if e["kind"] == "did" and e["level"] == "warn"
                         else "Wrong:", e["text"]) for e in events]
    level = "bad" if any(e["level"] == "bad" for e in events) else "warn" if wrong else "info"
    return title[:200], "\n".join(lines), level


def public(state, now=None):
    """What the status page shows: nothing but names people use and whether they work."""
    now = now or time.time()
    rows = [s for s in state.get("services") or [] if not s.get("admin")]
    return {
        "checked": state.get("checked") or 0,
        "services": sorted(rows, key=lambda s: (s["ok"], s["label"], s["site"])),
        "machines": state.get("machines") or {},
        "incidents": [dict(i, text=re.sub(r" \([^)]*\)$", "", i["text"])) for i in reversed(state.get("incidents") or [])
                      if (i["to"] or now) > now - 7 * 86400][:20],
        "window": state.get("window") or "",
    }
