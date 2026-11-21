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
import http.client
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from . import util

# a service by the first part of its name, in the words the pages use
SERVICES = {"cloud": "Files (Nextcloud)", "vault": "Passwords (Vaultwarden)", "pihole": "Ad blocker (Pi-hole)",
            "portainer": "Containers (Portainer)", "immich": "Photos (Immich)", "paperless": "Documents (Paperless)",
            "opencloud": "Files (OpenCloud)", "dl": "Downloads (Transmission)", "jd": "Downloads (JDownloader)",
            "wg": "VPN devices (wg-easy)", "control": "This page", "backup": "Backup store", "cache": "Local cache",
            "hub": "Hub page", "relay": "Relay", "office": "Office (Collabora)", "git": "Git (Forgejo)", "jabber": "Jabber", "auth": "Authenticator (2FAuth)", "paste": "Pastebin", "ffsync": "Firefox Sync", "xmpp": "Jabber (public)", "pay": "Payments (BTCPay Server)",
            "status": "Status page", "id": "Accounts"}
# what each service is for, in a few words (the start pages)
ABOUT = {"cloud": "Your files, calendars and contacts, on every device", "vault": "Passwords, kept and shared safely",
         "immich": "Photos and videos from your phones", "photos": "Photos and videos from your phones",
         "paperless": "Letters and documents, scanned and searchable", "opencloud": "Your files, on every device",
         "git": "Code and its history (Forgejo)", "jabber": "Chat with anyone on Jabber (XMPP)",
         "xmpp": "Chat with anyone on Jabber (XMPP)", "paste": "Share a text for a while, encrypted in your browser",
         "id": "Your account for the services here", "pay": "Payments in Bitcoin", "ffsync": "Your Firefox bookmarks and tabs, synced",
         "auth": "The codes of your two-factor logins", "hub": "The hub: apps and spaces", "relay": "A Nostr relay",
         "dl": "Downloads", "jd": "Downloads", "status": "Whether everything works"}


# what the modules say of their own pages (module.yaml `pages:`): a module from a source is named like
# a built-in one. Filled from the fleet's modules (register), asked first.
PAGES = {}


def register(pages):
    """{first part of a site's name: {label, about}} of the fleet's modules."""
    for key, page in (pages or {}).items():
        if isinstance(page, dict):
            PAGES[str(key)] = {"label": str(page.get("label") or ""), "about": str(page.get("about") or "")}


def about(site):
    first = site.split(".", 1)[0]
    return (PAGES.get(first) or {}).get("about") or ABOUT.get(first, "")


KEEP_DAYS = 30
HISTORY_DAYS = 90       # the bars of the status page: one a day, as status pages show it
KEEP_INCIDENTS = 200


def label(site):
    first = site.split(".", 1)[0]
    return (PAGES.get(first) or {}).get("label") or SERVICES.get(first, first.capitalize())


def probe_one(site, timeout=6, at=None):
    """(answers, what it said): a page over HTTPS. Its certificate is not judged here, and a
    page that wants a login (401, 403) answers. A proxy that cannot reach its service (502,
    503, 504) does not. With `at`, the name is asked at that address: a public name the way
    a stranger reaches it (at the master), whatever the network's own DNS says."""
    if at:
        try:
            ctx = ssl._create_unverified_context()
            conn = http.client.HTTPSConnection(site, 443, timeout=timeout, context=ctx)
            conn.sock = ctx.wrap_socket(socket.create_connection((at, 443), timeout=timeout), server_hostname=site)
            try:
                conn.request("GET", "/", headers={"Host": site})
                code = conn.getresponse().status
            finally:
                conn.close()
            return code < 500, code
        except (OSError, ValueError, http.client.HTTPException) as e:
            return False, str(e)[:80]
    try:
        with urllib.request.urlopen(urllib.request.Request("https://%s/" % site, method="GET"), timeout=timeout,
                                    context=ssl._create_unverified_context()) as r:
            return True, r.status
    except urllib.error.HTTPError as e:
        with e:
            return e.code < 500, e.code
    except (urllib.error.URLError, OSError, ValueError) as e:
        return False, str(getattr(e, "reason", e))[:80]


def probe(sites, timeout=6, workers=8, one=probe_one, again=2, sleep=time.sleep, at=None, took=None):
    """{site: (answers, what it said)} for every site, asked side by side. One that does not
    answer is asked once more a moment later: a single lost question is not a service that is down.
    `at`: {site: address} for the names to ask at an address of their own (see probe_one).
    `took`: a dict that gets how long each answer took, in milliseconds."""
    sites, at = list(sites), at or {}
    if not sites:
        return {}

    def ask(s):
        t0 = time.monotonic()
        r = one(s, timeout, at[s]) if s in at else one(s, timeout)
        if took is not None:
            took[s] = int((time.monotonic() - t0) * 1000)
        return r
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        out = dict(zip(sites, pool.map(ask, sites)))
        silent = [s for s in sites if not out[s][0]]
        if silent and again:
            sleep(again)
            out.update(zip(silent, pool.map(ask, silent)))
    return out


def _key(text):
    """A problem's words without its numbers: "disk 91% full" and "disk 92% full" are one problem."""
    return re.sub(r"\d+([.,]\d+)?", "#", text).strip().lower()[:160]


def duration(seconds):
    s = max(0, int(seconds))
    return (util.count(max(1, s // 60), "minute") if s < 5400 else util.count(s // 3600, "hour") if s < 172800
            else util.count(s // 86400, "day"))


NEEDED_AFTER = 120      # seconds: a machine that runs a part the network needs is told about this soon
REMIND = (3600, 6 * 3600, 24 * 3600)    # seconds after the first word when the same thing is said again
EMERGENCY_AGAIN = 900   # seconds: an emergency nobody said "I have it" to is told again this often


def update(state, machines, services, happened, now, after=600, acks=None):
    """One look, written into `state` (changed in place). Returns what is to be told now:
    [{kind: wrong|fine|did, level: bad|warn|info, text}].

    machines: [{name, ok, problems: [{level, text}], need?}] of the machines that are set up (need: must,
    told after NEEDED_AFTER; wait, not told; policy.py);
    services: [{site, host, admin, ok}] or None when they could not be asked from here;
    happened: what the control node did by itself this look: [{host, kind, text}].
    A thing counts as wrong when it has been wrong for `after` seconds; it is told once then,
    and once more when it is fine again."""
    items, events, seen = state.setdefault("items", {}), [], {}
    # a machine that went down as it said it would (a restart for an update, switched off): not a case for a
    # message while its own word explains it (watchdog.step marks those events "planned")
    planned = {e["host"] for e in happened or [] if e.get("planned") and e.get("kind") in ("waiting", "off")}
    # what the look at a machine that is gone found (watchdog.step: its case), said in the message about it
    story = {e["host"]: e["text"] for e in happened or [] if e.get("kind") in ("waiting", "tell", "held", "restarted") and e.get("text")}
    # since when a machine is silent, as the look at machines that are gone counts it (its agent's last report):
    # a message about it counts from then, not from when this look first saw it
    silent_since = {e["host"]: e["since"] for e in happened or [] if isinstance(e.get("since"), (int, float))}
    soon = {}                           # key: seconds before it is told, where that is not `after`
    # an emergency (docs/design/authority.md): a machine that runs a part the network needs, gone. Told on every
    # way at once, and again every EMERGENCY_AGAIN until someone says "I have it" (acks: {key: {by, at}})
    urgent = set()
    for m in machines:
        if not m["ok"] and (m["name"] in planned or m.get("need") == "wait"):
            continue                    # went down as it said, or all it runs can wait: not told
        if not m["ok"]:
            seen["machine:" + m["name"]] = ("machine", "bad", story.get(m["name"]) or "%s does not answer" % m["name"],
                                            "%s answers again" % m["name"])
            if m.get("need") == "must":
                soon["machine:" + m["name"]] = min(after, NEEDED_AFTER)
                urgent.add("machine:" + m["name"])
            continue
        for p in m.get("problems") or []:
            if p.get("level") in ("warn", "bad"):
                seen["problem:%s:%s" % (m["name"], _key(p["text"]))] = (
                    "problem", p["level"], "%s: %s" % (m["name"], p["text"]), "%s: no longer so: %s" % (m["name"], p["text"]),
                    p.get("hint") or "")
    down = {m["name"] for m in machines if not m["ok"]}
    for s in services or []:
        if not s["ok"] and s["host"] not in down:        # a machine that is gone takes its services with it: said once
            seen["service:" + s["site"]] = ("service", "bad", "%s does not answer (%s)" % (label(s["site"]), s["site"]),
                                            "%s answers again (%s)" % (label(s["site"]), s["site"]))
    for key, said in seen.items():
        kind, level, text, fine = said[:4]
        first = silent_since.get(key.split(":", 1)[1]) if kind == "machine" else None
        item = items.setdefault(key, {"kind": kind, "since": min(now, first) if first else now, "told": False})
        item.update(level=level, text=text, fine=fine)
        ack = (acks or {}).get(key) or {}
        if ack.get("at") is not None and ack["at"] >= item["since"]:
            item["acked"] = {"by": ack.get("by") or "", "at": ack["at"]}
        if key in urgent:
            item["emergency"] = True
        if not item["told"] and now - item["since"] >= soon.get(key, after):
            item["told"], item["last"] = True, now
            events.append(dict({"kind": "wrong", "level": "emergency" if key in urgent else level, "text": text, "key": key},
                               **({"hint": said[4]} if len(said) > 4 and said[4] else {})))
            if kind in ("machine", "service"):
                state.setdefault("incidents", []).append({"key": key, "text": text, "from": item["since"], "to": None})
        elif item.get("emergency") and item["told"] and not item.get("acked") and now - (item.get("last") or item["since"]) >= EMERGENCY_AGAIN:
            item["last"] = now
            events.append({"kind": "wrong", "level": "emergency", "key": key,
                           "text": "Still, for %s, and nobody said they have it: %s" % (duration(now - item["since"]), text)})
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
    did, seen_did = state.setdefault("did", {}), set()
    for e in happened or []:
        if e.get("kind") in ("restarted", "unlocked", "locked", "held", "tell", "switch"):
            key = "%s:%s%s" % (e["host"], e["kind"], ":" + e["part"] if e.get("part") else "")
            d = did.get(key)
            if not isinstance(d, dict):         # (before 2.291.3: the time it was last told)
                d = {"first": d, "last": d, "n": 1} if isinstance(d, (int, float)) else None
            # the same thing said again: told at once, then reminded after 1, 6 and 24 hours, then once a day
            # (docs/design/authority.md: a question nobody answers is repeated, not every hour)
            due = d is None or now - d["first"] >= REMIND[min(d["n"], len(REMIND)) - 1] + (
                max(0, d["n"] - len(REMIND)) * 86400)
            if due:
                d = {"first": now, "last": now, "n": 1} if d is None else dict(d, last=now, n=d["n"] + 1)
                events.append({"kind": "did", "level": "warn" if e["kind"] in ("restarted", "unlocked", "switch") else "bad",
                               "text": e["text"] if d["n"] == 1 else "Still: %s" % e["text"]})
            did[key] = d
            seen_did.add(key)
    for key in [k for k, d in did.items() if k not in seen_did and now - (d.get("last") if isinstance(d, dict) else d or 0) > 3600]:
        del did[key]                            # no longer said for an hour: a new case when it comes again
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
                         "public": bool(s.get("public")),      # open to the Internet: the public status page shows these
                         "since": old.get("since", now) if old.get("ok") == ok else now})
        state["services"] = rows
        record_days(state, rows, {s["site"]: s.get("ms") for s in services}, now)
    return events


def _day(t):
    return time.strftime("%Y-%m-%d", time.localtime(t))


def record_days(state, rows, ms, now):
    """Each service's day: how often it was asked, how often it answered, how fast (the bars
    and the uptime of the status page). Kept for HISTORY_DAYS."""
    days = state.setdefault("days", {})
    today = _day(now)
    for r in rows:
        d = days.setdefault(r["site"], {}).setdefault(today, [0, 0, 0, 0])
        d[0] += 1
        d[1] += 1 if r["ok"] else 0
        if r["ok"] and ms.get(r["site"]) is not None:
            d[2] += int(ms[r["site"]])
            d[3] += 1
    oldest = _day(now - HISTORY_DAYS * 86400)
    for site in list(days):
        for day in [k for k in days[site] if k <= oldest]:
            del days[site][day]
        if not days[site]:
            del days[site]


def history(state, site, now, n=HISTORY_DAYS):
    """The last n days of one service, oldest first: [{day, pct (None: not asked), ms}],
    and its uptime over those days (None when it was never asked)."""
    known = (state.get("days") or {}).get(site) or {}
    out, asked, answered = [], 0, 0
    for i in range(n - 1, -1, -1):
        day = _day(now - i * 86400)
        c = known.get(day)
        if c and c[0]:
            asked, answered = asked + c[0], answered + c[1]
            out.append({"day": day, "pct": 100.0 * c[1] / c[0], "ms": c[2] // c[3] if c[3] else None})
        else:
            out.append({"day": day, "pct": None, "ms": None})
    return out, (100.0 * answered / asked if asked else None)


def message(events, network="", ack_url=""):
    """(title, text, level) of one message about these events. An emergency in it: its title says
    so, and `ack_url` (the control page's) gives the link that says "I have it"."""
    name = "Kiwi %s" % network if network else "Kiwi"
    urgent = [e for e in events if e.get("level") == "emergency"]
    if urgent:
        name += ", emergency"
    wrong = [e for e in events if e["kind"] != "fine"]
    if len(events) == 1:
        title = "%s: %s" % (name, events[0]["text"])
    elif wrong:
        title = "%s: %d things need a look" % (name, len(wrong)) if len(wrong) > 1 else "%s: %s" % (name, wrong[0]["text"])
    else:
        title = "%s: fine again" % name
    lines = ["%s %s%s" % ("Fine again:" if e["kind"] == "fine" else "Done by itself:" if e["kind"] == "did" and e["level"] == "warn"
                           else "Wrong:", e["text"], "\n  What to do: " + e["hint"] if e.get("hint") else "") for e in events]
    level = "emergency" if urgent else "bad" if any(e["level"] == "bad" for e in events) else "warn" if wrong else "info"
    if urgent and ack_url:
        lines.append("")
        lines += ["I have it (%s): %s/ack?key=%s" % (e["key"].split(":", 1)[-1], ack_url.rstrip("/"), e["key"]) for e in urgent if e.get("key")]
    return title[:200], "\n".join(lines), level


def for_strangers(state):
    """The state cut down to what the page for the Internet may know: the services open to it,
    their days, their incidents and hidden-service addresses, the sign-up page. What the public
    pages' own container gets (control.Control.write_public): nothing about the machines, nothing
    of a service on the VPN only, no window, no secret."""
    rows = [dict(s) for s in state.get("services") or [] if s.get("public") and not s.get("admin")]
    sites = {s["site"] for s in rows}
    names = ["(%s)" % x for x in sites]
    return {
        "checked": state.get("checked") or 0,
        "services": rows,
        "days": {k: v for k, v in (state.get("days") or {}).items() if k in sites},
        "incidents": [dict(i) for i in state.get("incidents") or [] if any(n in i.get("text", "") for n in names)],
        "onion": {k: v for k, v in (state.get("onion") or {}).items() if k in sites},
        "join": state.get("join") or "",
    }


def public(state, now=None, strangers=False):
    """What the status page shows: nothing but names people use and whether they work.
    `strangers`: the page for the Internet. Only what is open to it, nothing about the
    machines, and of what was wrong only what was wrong with those services."""
    now = now or time.time()
    rows = [s for s in state.get("services") or [] if not s.get("admin") and (s.get("public") or not strangers)]
    shown = []
    for s in rows:
        bars, uptime = history(state, s["site"], now)
        last = next((b["ms"] for b in reversed(bars) if b["ms"] is not None), None)
        shown.append(dict(s, bars=bars, uptime=uptime, ms=last, about=about(s["site"]),
                          onion=(state.get("onion") or {}).get(s["site"], "")))
    rows = shown
    names = ["(%s)" % s["site"] for s in rows]
    return {
        "checked": state.get("checked") or 0,
        "services": sorted(rows, key=lambda s: (s["ok"], s["label"], s["site"])),
        "machines": {} if strangers else state.get("machines") or {},
        "incidents": [dict(i, text=re.sub(r" \([^)]*\)$", "", i["text"]), when=time.strftime("%d %b, %H:%M", time.localtime(i["from"])))
                      for i in reversed(state.get("incidents") or [])
                      if (i["to"] or now) > now - 7 * 86400 and (not strangers or any(n in i["text"] for n in names))][:20],
        "window": "" if strangers else state.get("window") or "",
        "strangers": strangers,
        "onion": state.get("onion") or {},
        "join": state.get("join") or "",
    }
