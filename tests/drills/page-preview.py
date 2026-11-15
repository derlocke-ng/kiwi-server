#!/usr/bin/env python3
"""The control page with a made-up network, to look at and click on this computer.

A throwaway fleet in a temporary folder, the page on 127.0.0.1 and machines that exist only
as the answers of a stand-in for `kiwi-server list` and `status`: five that are fine, one
that wants a look, one that does not answer. Nothing real is asked or changed.

    podman run --rm --network host -v "$PWD":/src:ro,z -w /src --entrypoint python3 -e PYTHONPATH=/src/lib \
        ghcr.io/derlocke-ng/kiwi-server-control:2 tests/drills/page-preview.py [port]

The control image has what the page needs (FastAPI, uvicorn, age). Then open
http://127.0.0.1:8741: the password is printed.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "..", "lib"), os.path.join(HERE, "..")]
import test_kiwiserver as t                                  # noqa: E402

GB = 1048576


def facts(load, cpus, mem, disks, running, backup=None, up=400000):
    f = {"os": "Fedora CoreOS 43", "uptime": up, "load": [load, load, load], "cpus": cpus,
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
                      "next": "", "provider": "", "address": "192.168.1.%d" % (30 + len(hosts)), "logins": []})
        said.append({"host": name, "state": state, "problems": problems, "notes": [], "facts": f, "output": ""})
    return {"domain": "home", "hosts": hosts}, {"hosts": said}


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8741
    t.remote.free_port = lambda: port
    case = t.TestWeb("test_the_page_changes_its_password_and_replaces_the_keys")
    case.setUp()
    listed, status = answers()
    stand_in = os.path.join(case.tmp, "stand-in.py")
    with open(stand_in, "w") as fh:
        fh.write("import sys\nwhat = sys.argv[1] if len(sys.argv) > 1 else ''\n"
                 "print(%r if what == 'list' else %r if what == 'status' else 'ran ' + ' '.join(sys.argv[1:]))\n"
                 % (json.dumps(listed), json.dumps(status)))
    case.c.kiwi = [sys.executable, stand_in]
    case.ask("/login", {"password": case.PW})               # the store opens: the devices go into it, not beside it
    case.c.refresh_status()
    os.makedirs(os.path.join(case.f.dir, "secrets", "devices"), exist_ok=True)
    for d in ("annas-phone", "laptop", "tablet"):
        with open(os.path.join(case.f.dir, "secrets", "devices", d + ".conf"), "w") as fh:
            fh.write("# made up\n")
    print("http://127.0.0.1:%d   password: %s   (Ctrl+C ends it)" % (port, case.PW), flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        case.doCleanups()
        case.tearDown()


if __name__ == "__main__":
    main()
