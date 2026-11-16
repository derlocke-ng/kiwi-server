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


def about(site):
    return ABOUT.get(site.split(".", 1)[0], "")


KEEP_DAYS = 30
HISTORY_DAYS = 90       # the bars of the status page: one a day, as status pages show it
KEEP_INCIDENTS = 200


def label(site):
    first = site.split(".", 1)[0]
    return SERVICES.get(first, first.capitalize())


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
