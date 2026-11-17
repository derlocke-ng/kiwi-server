#!/usr/bin/env python3
"""Federation, each way of sharing: what a partner's client reaches for each `share` and `from`.

Network A shares, network B is the partner. One client of B (the device `drill-fed-group` in
B's group `devices`, made once, fetched after) runs here in a gluetun container. For each style
A's federation.yaml gets that style's `share` and `from`, A's master (and a node whose rules
change) is applied, and the client tries what should open and what should stay shut:

  1. one service, the partner's whole mesh       4. one service, a group of theirs it is not in
  2. one service, the client's own address       5. a whole node
  3. one service, one of their groups            6. one port of a node
  then: an address of theirs that is not the client's: shut

A's federation.yaml is put back and applied at the end, also after a failure.

    tests/drills/federation-styles.py ~/kiwi/fleet.yaml ~/kiwi-2/fleet.yaml \\
        --service cloud.papaya --unshared vault.papaya --node lychee --port papaya:22

B's master must be in drills.yaml next to B's fleet file. Needs podman with /dev/net/tun.
Applies A's master and the nodes named: not while another apply runs on them.
"""
import argparse
import os
import re
import subprocess
import sys
import time

import yaml

from drill import kiwi, say

p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
p.add_argument("a", help="the sharing network's fleet file")
p.add_argument("b", help="the partner network's fleet file")
p.add_argument("--service", required=True, help="a service of A, e.g. cloud.papaya")
p.add_argument("--unshared", required=True, help="another name on the same node, e.g. vault.papaya")
p.add_argument("--node", required=True, help="a node of A to share whole, e.g. lychee")
p.add_argument("--port", required=True, help="a node's port to share, e.g. papaya:22")
p.add_argument("--only", help="the styles to run, e.g. 1,5")
args = p.parse_args()
here = os.path.dirname(os.path.abspath(__file__))
dir_a, dir_b = (os.path.dirname(os.path.abspath(x)) for x in (args.a, args.b))


def load(path):
    with open(path) as fh:
        return yaml.safe_load(fh) or {}


fa, fb = load(args.a), load(args.b)
domain_a = (fa.get("defaults") or {}).get("domain") or "home"
master_a = [n for n, h in fa["hosts"].items() if (h or {}).get("role") == "master"][0]
master_b = [n for n, h in fb["hosts"].items() if (h or {}).get("role") == "master"][0]
if master_b not in ((load(os.path.join(dir_b, "drills.yaml")) if os.path.isfile(os.path.join(dir_b, "drills.yaml")) else {})
                    .get("allowed") or []):
    sys.exit("drill: %s is not in %s/drills.yaml: the drill enrolls a client there" % (master_b, dir_b))
fed_path = os.path.join(dir_a, "federation.yaml")
original = open(fed_path).read()
mesh_b = (((fb.get("defaults") or {}).get("master") or {}).get("mesh_subnet") or "10.10.0.0/16")
partner = next(n for n, x in (yaml.safe_load(original) or {}).items() if (x or {}).get("mesh") == mesh_b)
dns_b = mesh_b.split("/")[0].rsplit(".", 1)[0] + ".1"
node_port, _, port = args.port.partition(":")

rc, out = kiwi("federate", args.a, args.b, timeout=300)          # the partner's groups into A's file
if rc != 0:
    sys.exit("drill: federate: %s" % " ".join(out[-2:]))
original_with_groups = open(fed_path).read()

device = os.path.join(dir_b, "secrets", "devices", "drill-fed-group.conf")
rc, out = kiwi("enroll", args.b, "drill-fed-group", "--group", "devices", *(["--existing"] if os.path.isfile(device) else []),
               timeout=300)
if rc != 0:
    sys.exit("drill: enroll failed: %s" % " ".join(out[-2:]))
mine = re.search(r"^Address\s*=\s*([0-9.]+)", open(device).read(), re.M).group(1)
say("the client: %s in %s's group devices" % (mine, os.path.basename(dir_b)))
other = mine.rsplit(".", 1)[0] + "." + str(int(mine.rsplit(".", 1)[1]) % 250 + 3)

STYLES = [
    ("one service, their whole mesh", [args.service], [],
     [("open", args.service), ("403", args.unshared), ("shut", args.node)]),
    ("one service, the client's own address", [args.service], [mine], [("open", args.service), ("shut", args.node)]),
    ("one service, their group devices", [args.service], ["devices"], [("open", args.service)]),
    ("one service, their group guests (the client is not in it)", [args.service], ["guests"], [("shut", args.service)]),
    ("a whole node", [args.node], [], [("port", args.node, 443), ("port", args.node, 22), ("shut", args.service)]),
    ("one port of a node", [args.port], [], [("port", node_port, int(port)), ("shut", args.service)]),
    ("one service, another address of theirs", [args.service], [other], [("shut", args.service)]),
]
only = {int(x) for x in args.only.split(",")} if args.only else set(range(1, len(STYLES) + 1))


def write_style(share, frm):
    data = yaml.safe_load(original_with_groups)
    data[partner]["share"], data[partner]["from"] = share, frm
    with open(fed_path, "w") as fh:
        fh.write("# federation.yaml — written by tests/drills/federation-styles.py while it runs\n"
                 + yaml.safe_dump(data, sort_keys=False, default_flow_style=None))


def apply(hosts):
    for h in hosts:
        if h != master_a:
            r = subprocess.run([sys.executable, os.path.join(here, "nginx-check.py"), args.a, h], capture_output=True, text=True,
                               env=dict(os.environ, KIWI_SERVER=os.path.join(here, "..", "..", "bin", "kiwi-server")))
            if "0 of" not in r.stdout:
                raise SystemExit("drill: nginx-check refused %s: %s" % (h, r.stdout.strip().splitlines()[-2:]))
        rc, out = kiwi("apply", args.a, h, timeout=1200)
        if rc != 0:
            raise SystemExit("drill: apply %s: %s" % (h, " ".join(out[-3:])))


name = "kiwi-drill-fedstyles"
subprocess.run(["podman", "rm", "-f", name], capture_output=True)


def run(*c, timeout=25):
    try:
        r = subprocess.run(["podman", "exec", name] + list(c), capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return 124, ""


def resolve(host):
    _rc, o = run("nslookup", "%s.%s" % (host, domain_a), dns_b)
    found = [x for x in re.findall(r"Address:\s*([0-9.]+)$", o, re.M) if x != dns_b]
    return found[0] if found else ""


def https(host):
    ip = resolve(host)
    if not ip:
        return "no name"
    run("sh", "-c", "grep -q ' %s.%s$' /etc/hosts || echo '%s %s.%s' >> /etc/hosts" % (host, domain_a, ip, host, domain_a))
    _rc, o = run("wget", "-S", "--no-check-certificate", "-T", "8", "-O", "/dev/null", "https://%s.%s/" % (host, domain_a), timeout=20)
    m = re.search(r"HTTP/[0-9.]+ ([0-9]{3})", o)
    return m.group(1) if m else "no answer"


def tcp(host, port):
    ip = resolve(host) or resolve(host.split(".")[-1])
    rc, _o = run("nc", "-z", "-w", "6", ip or "0.0.0.0", str(port), timeout=15)
    return rc == 0


results, applied = [], set()
try:
    subprocess.run(["podman", "run", "-d", "--name", name, "--security-opt", "label=disable", "--cap-add", "NET_ADMIN",
                    "--device", "/dev/net/tun", "-e", "VPN_SERVICE_PROVIDER=custom", "-e", "VPN_TYPE=wireguard",
                    "-e", "WIREGUARD_IMPLEMENTATION=userspace", "-e", "DOT=off",
                    "-v", "%s:/gluetun/wireguard/wg0.conf:ro" % device, "docker.io/qmcgaw/gluetun:v3"],
                   check=True, capture_output=True)
    for _ in range(36):
        if "Public IP address is" in subprocess.run(["podman", "logs", name], capture_output=True, text=True).stdout:
            break
        time.sleep(5)
    nodes_now = set()
    for i, (title, share, frm, checks) in enumerate(STYLES, 1):
        if i not in only:
            continue
        say("style %d: %s (share %s, from %s)" % (i, title, share, frm or "their whole mesh"))
        write_style(share, frm)
        # the master always; a node when what is shared of it changes (its nginx rules)
        nodes = {e.split(":")[0].split(".")[-1] for e in share}
        apply([master_a] + sorted((nodes | nodes_now) - {master_a}))
        applied |= nodes | nodes_now
        nodes_now = nodes
        time.sleep(20)          # the link and the client's handshake after the master's restart
        for c in checks:
            if c[0] == "open":
                st = https(c[1]); ok = st not in ("no answer", "no name", "403")
            elif c[0] == "403":
                st = https(c[1]); ok = st == "403"
            elif c[0] == "shut":
                st = "open" if tcp(c[1], 443) else "shut"; ok = st == "shut"
            else:
                st = "open" if tcp(c[1], c[2]) else "shut"; ok = st == "open"
            what = "%s %s%s" % (c[0], c[1], ":%d" % c[2] if c[0] == "port" else "")
            results.append(("%d %s" % (i, what), ok, st))
            say("  %s %s (%s)" % ("ok    " if ok else "FAILED", what, st))
finally:
    subprocess.run(["podman", "rm", "-f", name], capture_output=True)
    with open(fed_path, "w") as fh:
        fh.write(original_with_groups)
    say("federation.yaml as before; applying %s again" % ", ".join([master_a] + sorted(applied - {master_a})))
    apply([master_a] + sorted(applied - {master_a}))
bad = [r for r in results if not r[1]]
say("%d of %d checks passed" % (len(results) - len(bad), len(results)))
sys.exit(1 if bad or not results else 0)
