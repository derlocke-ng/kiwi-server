#!/usr/bin/env python3
"""Federation: a client of network B reaches what network A shares with B, and nothing else.

Enrolls a client on B's master (the device `drill-federation`, made once, fetched after), runs
it here in a gluetun container and checks: A's names resolve through B's DNS; the shared
service answers; a name on the same node that is not shared answers 403 (the node's nginx);
a node that is not shared does not answer (A's master drops it).

    tests/drills/federation.py ~/kiwi/fleet.yaml ~/kiwi2/fleet.yaml \\
        --shared cloud.papaya --unshared vault.papaya --closed lychee

B's master must be listed in drills.yaml next to B's fleet file. Needs podman with /dev/net/tun.
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
p.add_argument("--shared", required=True, help="a service A shares with B, e.g. cloud.papaya")
p.add_argument("--unshared", help="a name on the same node that A does not share")
p.add_argument("--closed", help="a node of A that is not shared")
p.add_argument("--master", help="B's master, when its name is not the only master")
p.add_argument("--ssh", help="how to reach B's master, e.g. core@188.68.38.110")
args = p.parse_args()


def fleet_data(path):
    with open(path) as fh:
        return yaml.safe_load(fh) or {}


a, b = fleet_data(args.a), fleet_data(args.b)
domain_a = (a.get("defaults") or {}).get("domain") or "home"
masters_b = [n for n, h in (b.get("hosts") or {}).items() if (h or {}).get("role", (b.get("defaults") or {}).get("role")) == "master"]
master_b = args.master or (masters_b[0] if len(masters_b) == 1 else None)
allow = os.path.join(os.path.dirname(os.path.abspath(args.b)), "drills.yaml")
allowed = (fleet_data(allow) or {}).get("allowed", []) if os.path.isfile(allow) else []
if master_b not in allowed:
    sys.exit("drill: %s is not in %s: the drill enrolls a client there" % (master_b, allow))
dns_b = ((b.get("defaults") or {}).get("master") or {}).get("vpn-server", {}).get("server_ip") or "10.10.0.1"

device = os.path.join(os.path.dirname(os.path.abspath(args.b)), "secrets", "devices", "drill-federation.conf")
cmd = ["enroll", args.b, "drill-federation"] + (["--ssh", args.ssh] if args.ssh else [])
if os.path.isfile(device):
    cmd.append("--existing")
rc, out = kiwi(*cmd, timeout=300)
if rc != 0:
    sys.exit("drill: enroll failed: %s" % " ".join(out[-2:]))

name = "kiwi-drill-federation"
subprocess.run(["podman", "rm", "-f", name], capture_output=True)
results = []
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

    def run(*c, timeout=25):
        try:
            r = subprocess.run(["podman", "exec", name] + list(c), capture_output=True, text=True, timeout=timeout)
            return r.returncode, r.stdout + r.stderr
        except subprocess.TimeoutExpired:
            return 124, ""

    def resolve(host):
        _rc, o = run("nslookup", "%s.%s" % (host, domain_a), dns_b)
        found = [x for x in re.findall(r"Address:\s*([0-9.]+)$", o, re.M) if not x.startswith(dns_b)]
        return found[0] if found else ""

    def status(host):
        ip = resolve(host)
        if not ip:
            return ip, "no name"
        # the name in the request (SNI, Host): the container's hosts file knows it
        run("sh", "-c", "grep -q ' %s.%s$' /etc/hosts || echo '%s %s.%s' >> /etc/hosts" % (host, domain_a, ip, host, domain_a))
        _rc, o = run("wget", "-S", "--no-check-certificate", "-T", "8", "-O", "/dev/null",
                     "https://%s.%s/" % (host, domain_a), timeout=20)
        m = re.search(r"HTTP/[0-9.]+ ([0-9]{3})", o)
        return ip, m.group(1) if m else "no answer"
    ip, st = status(args.shared)
    results.append(("%s resolves through B's DNS" % args.shared, bool(ip), ip or "no name"))
    results.append(("%s answers" % args.shared, st not in ("no answer", "no name", "403"), st))
    if args.unshared:
        _ip, st = status(args.unshared)
        results.append(("%s is refused (403)" % args.unshared, st == "403", st))
    if args.closed:
        ip = resolve(args.closed)
        rc, _o = run("nc", "-z", "-w", "6", ip or "0.0.0.0", "443", timeout=15)
        results.append(("%s does not answer" % args.closed, rc != 0, "closed" if rc else "OPEN"))
finally:
    subprocess.run(["podman", "rm", "-f", name], capture_output=True)
for what, ok, detail in results:
    say("%s %s (%s)" % ("ok    " if ok else "FAILED", what, detail))
sys.exit(0 if results and all(ok for _w, ok, _d in results) else 1)
