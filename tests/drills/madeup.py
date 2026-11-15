"""A made-up network for looking at the control page and the app: five machines that are fine,
one that wants a look, one that does not answer. What `kiwi-server list` and `status` would say
about them (`--porcelain`), and a fleet file with the same machines."""
import json
import sys

GB = 1048576


def facts(load, cpus, mem, disks, running, backup=None, up=400000):
    f = {"applied": "2026-11-15T06:10:00+00:00", "role_failed": "", "kernel": "6.17.7-200.fc43.x86_64", "clock_synced": True,
         "skew": 0, "reboot": False, "failed_units": [], "runtime": "podman",
         "os": "Fedora CoreOS 43", "uptime": up, "load": [load, load, load], "cpus": cpus,
         "memory": {"total": mem[0] * GB, "available": int((mem[0] - mem[1]) * GB)},
         "disks": [{"mount": m, "size": s * GB, "used": int(u * GB)} for m, s, u in disks],
         "running": running, "expected": running, "services": []}
    if backup is not None:
        f["backup"] = {"last": 1, "age": backup, "held": "", "copies": []}
    return f


MACHINES = [
    ("sorbet", "master", ["vpn-client", "vpn-server", "dns", "reverse-proxy", "backup"], ["wg", "pihole"], "ok", [],
     facts(0.21, 2, (4, 1.3), [("/var", 60, 14)], 9, 7 * 3600)),
    ("papaya", "node", ["vpn-client", "reverse-proxy", "cloud", "vault", "immich", "paperless", "gateway", "backup", "backup-server", "control"],
     ["cloud", "vault", "photos", "paperless", "control"], "ok", [], facts(0.9, 4, (16, 9.2), [("/var", 230, 61), ("/var/mnt/data", 1800, 1210)], 21, 5 * 3600)),
    ("lychee", "node", ["vpn-client", "reverse-proxy", "portainer", "backup-server", "tang", "backup"], ["portainer"], "ok", [],
     facts(0.1, 2, (4, 1.1), [("/var", 480, 395)], 6, 6 * 3600)),
    ("quince", "node", ["vpn-client", "reverse-proxy", "vault", "gateway", "backup"], ["vault"], "warn",
     [{"level": "warn", "text": "Its last backup is 3 days old."}], facts(0.4, 2, (4, 3.5), [("/var", 60, 51)], 5, 3 * 86400 + 4000)),
    ("medlar", "node", ["vpn-client", "reverse-proxy", "git", "onion", "tang"], ["git"], "down",
     [{"level": "bad", "text": "It does not answer."}], {}),
    ("lime", "node", ["vpn-client", "dns", "reverse-proxy", "gateway", "tang", "backup"], ["pihole"], "ok", [],
     facts(0.05, 4, (2, 0.7), [("/var", 29, 9)], 5, 8 * 3600)),
]


def answers():
    hosts, said = [], []
    for name, role, modules, sites, state, problems, f in MACHINES:
        hosts.append({"name": name, "hostname": name + ".home", "role": role, "modules": modules, "phase": "applied",
                      "sites": ["%s.%s.home" % (s, name) for s in sites], "vpn_ip": "10.9.0.%d" % (len(hosts) + 1),
                      "next": "", "errors": [], "target": "ucore", "provider": "", "address": "192.168.1.%d" % (30 + len(hosts)), "logins": []})
        if f:           # what a machine that answers always says, from its parts
            f = dict(f, encryption="bound", tunnel={"container": "kn-vpn-client", "running": True, "mtu": 1376, "forward": True},
                     services=[{"name": "kn-" + m, "state": "active", "running": True, "status": "Up 4 days", "image": "",
                                "health": "", "stack": True} for m in modules[:f["running"]]])
        said.append({"host": name, "target": "core@%s.home" % name, "ok": state != "down", "state": state, "problems": problems, "notes": [], "facts": f,
                     "output": ""})
    return {"domain": "home", "mesh": "10.9.0.0/24", "master_ip": "10.9.0.1", "groups": {}, "hosts": hosts}, {"hosts": said}


LABELS = {"cloud": "Files (Nextcloud)", "vault": "Passwords (Vaultwarden)", "photos": "Photos", "paperless": "Documents (Paperless)",
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
                         "admin": first in ("control", "portainer", "pihole", "wg"), "since": now - (2400 if not ok else 400000)})
    return {"checked": now - 180, "machines": {"on": len(status["hosts"]) - len(down), "of": len(status["hosts"])},
            "window": "Sun 03:30", "services": rows, "items": {}, "did": {},
            "incidents": [{"key": "service:cloud.papaya.home", "text": "Files (Nextcloud) does not answer (cloud.papaya.home)",
                           "from": now - 190000, "to": now - 187700},
                          {"key": "machine:medlar", "text": "medlar does not answer", "from": now - 5000, "to": None},
                          {"key": "service:paperless.papaya.home", "text": "Documents (Paperless) does not answer (paperless.papaya.home)",
                           "from": now - 2400, "to": None}]}


def fleet_yaml():
    lines = ["defaults:", "  domain: home", "hosts:"]
    for name, role, modules, _sites, _state, _problems, _f in MACHINES:
        lines += ["  %s:" % name, "    role: %s" % role, "    target: ucore", "    modules: [%s]" % ", ".join(modules)]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":          # as a stand-in for kiwi-server: madeup.py list|status|secrets ...
    what = sys.argv[1] if len(sys.argv) > 1 else ""
    listed, status = answers()
    print(json.dumps(listed if what == "list" else status if what == "status" else
                     {"store": True, "open": True} if what == "secrets" else {}))
