"""The Machines table (docs/design/ui.md, step 4): one state per machine, the counts above the
list, the machines grouped by site, and a filter whose text form lives in the address, so a
view can be bookmarked: `site:main state:down part:dns lychee`.

Made for many machines (the owner, 20 Nov 2026: about 70 at five sites): a row per machine,
nothing asked of a machine here; it only reads what the last look wrote.
"""
import re

FRESH = ("enroll", "build", "install", "invalid", "bare")      # a machine's phases before it is set up

# (key, one, many, level), the worst first: a site's state is the first of these one of its
# machines has. Waiting for its key, asleep and being installed are their own, never "not reachable".
STATES = (
    ("bad", "has a problem", "have a problem", "bad"),
    ("down", "not reachable", "not reachable", "bad"),
    ("unlock", "waits for unlock", "wait for unlock", "warn"),
    ("warn", "wants a look", "want a look", "warn"),
    ("installing", "being installed", "being installed", "plain"),
    ("new", "not set up yet", "not set up yet", "plain"),
    ("unknown", "not checked yet", "not checked yet", "plain"),
    ("asleep", "asleep", "asleep", "plain"),
    ("ok", "fine", "fine", "ok"),
)
WORDS = {k: one for k, one, _many, _level in STATES}
LEVEL = {k: level for k, _one, _many, level in STATES}
KEYS = ("state", "site", "part", "role")


MASTERS = "Masters"


def is_master(m):
    return m.get("role") == "master" or bool(m.get("standby_of"))


def state(m):
    """One state for a machine, from what was done to it and what the last look saw."""
    if m.get("locked") or m.get("stage") == "waits":
        return "unlock"
    if m.get("stage") == "installing":
        return "installing"
    if m.get("phase") in FRESH and m.get("state") in (None, "", "unknown", "down"):
        return "new"
    s = m.get("state") or "unknown"
    return s if s in WORDS else "unknown"


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")


def parse(q):
    """The filter's text -> {"state": [...], "site": [...], "part": [...], "role": [...], "text": [...]}.
    The same key twice is either-or; different keys must all match; a bare word is part of a name."""
    f = {k: [] for k in KEYS + ("text",)}
    for word in str(q or "").split()[:20]:
        key, sep, value = word.partition(":")
        if sep and key.lower() in KEYS:
            if value:
                f[key.lower()].append(value.lower())
        else:
            f["text"].append(word.lower())
    return f


def _state_is(key, value):
    return value == key or value in slug(WORDS[key]).split("-") or value == slug(WORDS[key])


def match(m, f, titles=None):
    """Does this machine pass the filter? `titles`: {module name: its title}."""
    titles = titles or {}
    if f["state"] and not any(_state_is(m["st"], v) for v in f["state"]):
        return False
    if f["site"] and not any(v in slug(m.get("site")) for v in f["site"]):
        return False
    if f["role"] and not any(v in slug(m.get("role")) or v in slug(m.get("kind")) for v in f["role"]):
        return False
    if f["part"]:
        mine = [(n.lower(), slug(titles.get(n))) for n in m.get("modules") or []]
        if not any(v == n or (t and slug(v) in t) for v in f["part"] for n, t in mine):
            return False
    name = (m.get("name") or "").lower()
    return all(w in name for w in f["text"])


def counts(ms):
    """[{key, words, level, n}] of the states these machines have, the worst first."""
    out = []
    for key, one, many, level in STATES:
        n = sum(1 for m in ms if m["st"] == key)
        if n:
            out.append({"key": key, "words": one if n == 1 else many, "level": level, "n": n})
    return out


def toggled(q, key, value):
    """The filter's text with `key:value` set, or taken out if it is in already."""
    words = str(q or "").split()
    mine = "%s:%s" % (key, value)
    rest = [w for w in words if not w.lower().startswith(key + ":")]
    return " ".join(rest if mine in [w.lower() for w in words] else rest + [mine])


def table(machines, q="", titles=None):
    """{all, shown, counts, groups, q, filtered}: every machine with its state (`st`), the ones
    the filter leaves, the counts of all of them, and the shown ones by site."""
    for m in machines:
        m["st"] = state(m)
    f = parse(q)
    shown = [m for m in machines if match(m, f, titles)]
    # the masters are a group of their own, above the sites (the owner, 20 Nov 2026): a master is
    # the network's middle, mostly a rented server, not one of the machines "at home"
    by = {}
    for m in shown:
        by.setdefault((0, MASTERS) if is_master(m) else (1, m.get("site") or "home"), []).append(m)
    order = {k: i for i, (k, _one, _many, _level) in enumerate(STATES)}
    groups = []
    for first, site in sorted(by, key=lambda k: (k[0], k[1].lower())):
        ms = sorted(by[(first, site)], key=lambda m: (order[m["st"]], bool(m.get("standby_of")), m["name"]))
        c = counts(ms)
        groups.append({"site": site, "slug": slug(site), "machines": ms, "counts": c, "masters": first == 0,
                       "level": c[0]["level"] if c else "plain", "fine": all(x["key"] == "ok" for x in c)})
    return {"all": len(machines), "shown": len(shown), "counts": counts(machines), "groups": groups,
            "q": " ".join(str(q or "").split()), "filtered": len(shown) != len(machines),
            "sites": sorted({m.get("site") or "home" for m in machines}, key=lambda s: s.lower())}


def device_table(rows, q=""):
    """The Devices table: {all, shown, q, filtered, devices, groups, sites}. The filter's text:
    `group:NAME` and `site:NAME` (twice is either-or) and bare words, each a part of a name or the
    start of an address. `groups`: [{group, n}] of all devices, for the counts above the table.
    `sites`: the shown devices by the site of the master they connect to, [{site, slug, devices}],
    as the Machines table (ui.md); one site: one group."""
    wanted, sites, words = [], [], []
    for word in str(q or "").split()[:20]:
        key, sep, value = word.partition(":")
        if sep and key.lower() == "group":
            if value:
                wanted.append(value.lower())
        elif sep and key.lower() == "site":
            if value:
                sites.append(slug(value))
        else:
            words.append(word.lower())

    def ok(d):
        if wanted and (d.get("group") or "").lower() not in wanted:
            return False
        if sites and slug(d.get("site") or "") not in sites:
            return False
        return all(w in d["name"].lower() or (d.get("address") or "").startswith(w) for w in words)
    shown = sorted((d for d in rows if ok(d)), key=lambda d: ((d.get("group") or "~"), d["name"]))
    names = sorted({d.get("group") or "" for d in rows})
    by = {}
    for d in shown:
        by.setdefault(d.get("site") or "", []).append(d)
    return {"all": len(rows), "shown": len(shown), "q": " ".join(str(q or "").split()), "filtered": len(shown) != len(rows),
            "devices": shown, "groups": [{"group": g, "n": sum(1 for d in rows if (d.get("group") or "") == g)} for g in names if g],
            "sites": [{"site": s, "slug": slug(s), "devices": by[s]} for s in sorted(by, key=lambda s: (s == "", s.lower()))],
            "site_names": sorted({d.get("site") for d in rows if d.get("site")}, key=str.lower)}
