"""A made-up network for looking at the control page and the app: five machines that are fine,
one that wants a look, one that does not answer. What `kiwi-server list` and `status` would say
about them (`--porcelain`), and a fleet file with the same machines."""
import os
import time
import json
import sys

GB = 1048576


def facts(load, cpus, mem, disks, running, backup=None, up=400000):
    f = {"applied": "2026-11-15T06:10:00+00:00", "role_failed": "", "kernel": "6.17.7-200.fc43.x86_64", "clock_synced": True,
         "skew": 0, "reboot": False, "failed_units": [], "runtime": "podman", "opened": {}, "partner_masters": {},
         "os": "Fedora CoreOS 43", "uptime": up, "load": [load, load, load], "cpus": cpus,
         "memory": {"total": mem[0] * GB, "available": int((mem[0] - mem[1]) * GB)},
         "disks": [{"mount": m, "size": s * GB, "used": int(u * GB)} for m, s, u in disks],
         "running": running, "expected": running, "services": [], "asleep": []}
    if backup is not None:
        f["backup"] = {"last": 1, "age": backup, "held": "", "copies": []}
    return f


MACHINES = [
    ("sorbet", "master", ["vpn-client", "vpn-server", "dns", "reverse-proxy", "backup"], ["wg", "pihole"], "ok", [],
     facts(0.21, 2, (4, 1.3), [("/var", 60, 14)], 9, 7 * 3600)),
    ("papaya", "node", ["vpn-client", "reverse-proxy", "cloud", "vault", "immich", "paperless", "gateway", "backup", "backup-server", "control", "accounts"],
     ["cloud", "vault", "photos", "paperless", "control", "id"], "ok", [], facts(0.9, 4, (16, 9.2), [("/var", 230, 61), ("/var/mnt/data", 1800, 1210)], 21, 5 * 3600)),
    ("lychee", "node", ["vpn-client", "reverse-proxy", "portainer", "backup-server", "tang", "backup"], ["portainer"], "ok", [],
     facts(0.1, 2, (4, 1.1), [("/var", 480, 395)], 6, 6 * 3600)),
    ("quince", "node", ["vpn-client", "reverse-proxy", "vault", "gateway", "backup"], ["vault"], "warn",
     [{"level": "warn", "text": "Its last backup is 3 days old.", "key": "backup-old",
       "hint": "Backups: Save it now, and look whether the backup machine answers."}], facts(0.4, 2, (4, 3.5), [("/var", 60, 51)], 5, 3 * 86400 + 4000)),
    ("medlar", "node", ["vpn-client", "reverse-proxy", "git", "onion", "tang"], ["git"], "down",
     [{"level": "bad", "text": "It does not answer."}], {}),
    ("kumquat", "node", ["vpn-client", "reverse-proxy", "backup"], [], "ok", [],
     facts(0.2, 2, (2, 0.9), [("/var", 40, 12)], 3, 4 * 3600)),
    ("lime", "node", ["vpn-client", "dns", "reverse-proxy", "gateway", "tang", "backup"], ["pihole"], "warn",
     [{"level": "warn", "text": "the disk is not encrypted: whoever takes it can read everything on it", "key": "disk-plain",
       "hint": "Install it again with encryption when it suits you, or leave this warning out in its settings."}],
     facts(0.05, 4, (2, 0.7), [("/var", 29, 9)], 5, 8 * 3600)),
]


# a company's size (docs/design/ui.md): KIWI_PREVIEW_MANY=1 adds 72 machines at five sites
SITES = (("Main building", 30), ("IT department", 10), ("Building 2", 10), ("Building 3", 2), ("China HQ", 20))
ODD = {3: ("warn", [{"level": "warn", "key": "reboot", "text": "an update is waiting for a reboot"}], {}),
       11: ("down", [], {"locked": True}), 17: ("down", [], {}), 23: ("asleep", [], {}),
       34: ("down", [], {"stage": "installing", "story": "Its install media were made 12 minutes ago and it has not answered since."}),
       41: ("bad", [{"level": "bad", "key": "service", "text": "Files (Nextcloud): nextcloud-aio-apache is not running"}], {}),
       58: ("warn", [{"level": "warn", "key": "disk", "text": "/var is 86 % full"}], {})}
PARTS = (["dns"], ["cloud", "backup"], ["git"], ["backup-server"], ["immich", "backup"], ["gateway", "dns"])


def many(hosts, said):
    n = 0
    for site, count in SITES:
        for i in range(count):
            name = "%s%02d" % ("".join(w[0] for w in site.lower().split())[:2].ljust(2, "x"), i + 1)
            modules = PARTS[n % len(PARTS)]
            state, problems, more = ODD.get(n, ("ok", [], {}))
            hosts.append({"name": name, "hostname": name + ".home", "role": "node", "modules": modules, "phase": "applied", "site": site,
                          "sites": ["%s.%s.home" % (m, name) for m in modules if m in ("cloud", "git")], "site_labels": {},
                          "vpn_ip": "10.9.1.%d" % (n + 1), "next": "", "errors": [], "target": "ucore", "provider": "",
                          "address": "10.%d.0.%d" % (20 + len(site), 10 + i), "logins": []})
            f = facts(0.2 + (n % 7) / 10, 4, (16, 4 + n % 8), [("/var", 500, 500 * (0.86 if n == 58 else 0.2 + (n % 6) / 10))], len(modules),
                      backup=3600 * (1 + n % 30) if "backup" in modules else None) if state in ("ok", "warn", "bad") else {}
            said.append(dict({"host": name, "target": "core@%s.home" % name, "ok": state in ("ok", "warn", "bad"), "state": state,
                              "problems": problems, "notes": [], "facts": f, "output": ""}, **more))
            n += 1


def answers():
    hosts, said = [], []
    for name, role, modules, sites, state, problems, f in MACHINES:
        hosts.append({"name": name, "hostname": name + ".home", "role": role, "modules": modules, "phase": "applied",
                      "sites": ["%s.%s.home" % (s, name) for s in sites],
                      "site_labels": {"%s.%s.home" % (s, name): LABELS.get(s, s.capitalize()) for s in sites}, "vpn_ip": "10.9.0.%d" % (len(hosts) + 1),
                      "next": "", "errors": [], "target": "ucore", "provider": "", "address": "192.168.1.%d" % (30 + len(hosts)), "logins": []})
        if f:           # what a machine that answers always says, from its parts
            f = dict(f, encryption="bound", tunnel={"container": "kn-vpn-client", "running": True, "mtu": 1376, "forward": True},
                     services=[{"name": "kn-" + m, "state": "active", "running": True, "status": "Up 4 days", "image": "",
                                "health": "", "stack": True} for m in modules[:f["running"]]])
        said.append({"host": name, "target": "core@%s.home" % name, "ok": state != "down", "state": state, "problems": problems, "notes": [], "facts": f,
                     "output": ""})
    # two game servers on lychee: one played on, one asleep until someone joins
    for h, w in zip(hosts, said):
        if h["name"] == "lychee":
            h["modules"].append("games")
            h["games"] = [{"server": "survival", "title": "Minecraft (Java)", "container": "kn-game-survival", "address": "lychee.home:25565"},
                          {"server": "doom", "title": "Zandronum", "container": "kn-game-doom", "address": "lychee.home:10666"}]
            w["facts"]["services"] += [{"name": "kn-game-survival", "state": "active", "running": True, "status": "Up 2 hours", "image": "",
                                        "health": "", "stack": True},
                                       {"name": "kn-game-doom", "state": "inactive", "running": False, "asleep": True, "status": "", "image": "",
                                        "health": "", "stack": True}]
    # kumquat stands at a friend's (another site); sorbet has a standby; a second network is linked
    for h in hosts:
        h["site"] = "friend" if h["name"] == "kumquat" else "home"
    hosts.append(dict(next(h for h in hosts if h["name"] == "sorbet"), name="sorbet-b", standby_of="sorbet", address="192.168.1.70"))
    said.append(dict(next(w for w in said if w["host"] == "sorbet"), host="sorbet-b", target="core@192.168.1.70"))
    master = next(w for w in said if w["host"] == "sorbet")
    master["facts"] = dict(master["facts"], opened={"kiwi2": ["git.damson", "cloud.damson", "crumble:22"]},
                           partner_masters={"kiwi2": "crumble.internal"})
    linked = [{"name": "kiwi2", "mesh": "10.10.0.0/16", "exit": False, "internet": False, "shared": 2, "reach": ["cloud.papaya", "git.medlar"]}]
    sources = [{"name": "kiwi-modules-personal", "url": "https://github.com/someone/kiwi-modules-personal",
                "commit": "d8464b8b8ffbe0ba9848fe1e2009c0e6f252204f", "tag": "v0.1.0", "modules": ["smartfritz"]}]
    if os.environ.get("KIWI_PREVIEW_MANY"):
        many(hosts, said)
    return ({"domain": "home", "mesh": "10.9.0.0/24", "master_ip": "10.9.0.1", "groups": {}, "hosts": hosts, "module_sources": sources,
             "linked": linked},
            {"hosts": said})


LABELS = {"id": "Accounts", "wg": "VPN devices (wg-easy)", "pihole": "Ad blocker (Pi-hole)", "portainer": "Containers (Portainer)",
          "git": "Git (Forgejo)", "control": "This page", "cloud": "Files (Nextcloud)", "vault": "Passwords (Vaultwarden)", "photos": "Photos", "paperless": "Documents (Paperless)",
          "git": "Git (Forgejo)"}


def health(now):
    """What the looks would have kept about this network (health.py): one service that does not
    answer on a machine that says it is fine, and an incident that is over."""
    listed, status = answers()
    down = {h["host"] for h in status["hosts"] if not h["ok"]}
    rows = []
    for h in listed["hosts"]:
        for site in h["sites"]:
            first = site.split(".", 1)[0]
            ok = h["name"] not in down and site != "paperless.papaya.home"
            rows.append({"site": site, "label": LABELS.get(first, first.capitalize()), "host": h["name"], "ok": ok,
                         "public": first in ("cloud", "id"),
                         "admin": first in ("control", "portainer", "pihole", "wg"), "since": now - (2400 if not ok else 400000)})
    # 90 days of looks, every ten minutes: most days fine, a few with a gap, one long outage
    days = {}
    for n, r in enumerate(rows):
        for i in range(90 if n % 3 else 40):          # some services are newer
            day = time.strftime("%Y-%m-%d", time.localtime(now - i * 86400))
            asked = 144 if i else 60
            missed = 0 if (i * 7 + n) % 23 else (2 if i % 2 else 30)
            if r["site"] == "cloud.papaya.home" and i == 2:
                missed = 17
            if not r["ok"] and i == 0:
                missed = 14
            days.setdefault(r["site"], {})[day] = [asked, asked - missed, (asked - missed) * (90 + n * 13), asked - missed]
    return {"checked": now - 180, "machines": {"on": len(status["hosts"]) - len(down), "of": len(status["hosts"])},
            "window": "Sun 03:30", "services": rows, "items": {}, "did": {}, "days": days,
            "onion": {"cloud.papaya.home": "c3l5ahmvbn2jdqhzw3sc6t3obkzexkojcxy7sqz6bzvwrn7ebtxwqqid.onion"},
            "join": "https://id.papaya.home/join",
            "incidents": [{"key": "service:cloud.papaya.home", "text": "Files (Nextcloud) does not answer (cloud.papaya.home)",
                           "from": now - 190000, "to": now - 187700},
                          {"key": "machine:medlar", "text": "medlar does not answer", "from": now - 5000, "to": None},
                          {"key": "service:paperless.papaya.home", "text": "Documents (Paperless) does not answer (paperless.papaya.home)",
                           "from": now - 2400, "to": None}]}


def fleet_yaml():
    lines = ["defaults:", "  domain: home", "hosts:"]
    for name, role, modules, _sites, _state, _problems, _f in MACHINES:
        lines += ["  %s:" % name, "    role: %s" % role, "    target: ucore", "    modules: [%s]" % ", ".join(modules)]
        if name == "sorbet":                    # its standby: the same master on a second machine
            lines += ["    standby:", "      sorbet-b: {address: 192.168.1.70}"]
        if name == "kumquat":                   # at a friend's
            lines += ["    network: {dhcp: true, site: friend}"]
    return "\n".join(lines) + "\n"


def hidden():
    """Two hidden services, as `onion --porcelain` lists them: one for anyone, one for a named person."""
    return {"services": [
        {"host": "papaya", "service": "vault", "site": "vault.papaya.home", "address": "kiwi" + "a2" * 26 + ".onion", "clients": []},
        {"host": "medlar", "service": "git", "site": "git.medlar.home", "address": "kiwi" + "b3" * 26 + ".onion", "clients": ["anna"]}]}


if __name__ == "__main__":          # as a stand-in for kiwi-server: madeup.py list|status|secrets|onion ...
    what = sys.argv[1] if len(sys.argv) > 1 else ""
    listed, status = answers()
    if what == "onion" and "--show" in sys.argv:        # a person's key, made up: what Tor Browser asks for
        print("MADEUPKEYFORPREVIEWONLY" + "A" * 29)
        sys.exit(0)
    print(json.dumps(listed if what == "list" else status if what == "status" else hidden() if what == "onion" else
                     {"store": True, "open": True} if what == "secrets" else {}))


def accounts(*argv, user="admin"):
    """What `kiwi-server accounts` would say about a made-up sign-up: three people, two waiting, codes."""
    now = time.time()
    what = argv[0] if argv else ""
    if "--jabber" in argv and what == "requests":
        return [{"hint": "1c2d", "name": "gustav", "why": "Gustav from the band, to chat on Jabber", "at": now - 600, "state": "waiting"}]
    if "--jabber" in argv and what == "invites":
        return [{"hint": "77aa", "uses": 5, "used": 2, "until": now + 5 * 86400, "note": "the band", "live": True}]
    if what == "list":
        return [{"name": [n], "displayname": [d], "memberof": (["jabber@id.papaya.home"] if j else [])}
                for n, d, j in (("anna", "Anna Berg", True), ("bert", "Bert", False), ("carla", "Carla from the club", False))]
    if what == "invites":
        return [{"hint": "9c21", "uses": 3, "used": 1, "until": now + 9 * 86400, "note": "the climbing club", "live": True},
                {"hint": "b0d4", "uses": 1, "used": 1, "until": now + 2 * 86400, "note": "Dora", "live": False}]
    if what == "requests":
        return [{"hint": "4db9", "name": "emil", "why": "Emil, Anna's brother: for the shared photos", "at": now - 5400, "state": "waiting"},
                {"hint": "7a7f", "name": "fritz", "why": "I would like a Jabber account to talk to my neighbours", "at": now - 86400, "state": "waiting"}]
    if what == "invite":
        return {"code": "kiwi-6350-7b83-4a21"}
    if what in ("create", "link"):
        return {"name": argv[1], "link": "https://id.papaya.home/ui/reset?token=made-up-token"}
    return {"state": "done"}
