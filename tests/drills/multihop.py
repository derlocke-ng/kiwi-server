#!/usr/bin/env python3
"""Multi-hop: a client of one network's master leaves the internet at another network's master.

Two ways are known. Over the federation link (`exit: true` for a partner in federation.yaml,
`internet: true` in the partner's): the way to use between two Kiwi networks. Or the older
one: the entry master's VPN client holds a config the exit master issued in a group with no
reach and no peers (`vpn_provider: custom`, `wireguard_config: secrets/<x>.conf`). This enrolls a
client on the entry master (the device `drill-multihop`, made once, fetched after), runs it
here in a gluetun container, and expects: its public address is the exit master's (the
Endpoint of the entry master's config), the entry master's names resolve to its own mesh
address, and the exit network's mesh is closed to it.

    tests/drills/multihop.py ~/kiwi/fleet.yaml sorbet [--exit-mesh 10.10.0.1]

Needs podman with /dev/net/tun. Touches nothing on the machines but the one client.
"""
import argparse
import os
import re
import subprocess
import sys
import time

from drill import kiwi, say, setup

sys.argv, extra = sys.argv[:3], sys.argv[3:]
args, fleet, m = setup(__doc__.splitlines()[0], roles=("master",))
more = argparse.ArgumentParser()
more.add_argument("--exit-mesh", help="an address in the exit network's mesh that must stay closed")
opts = more.parse_args(extra)
master = m["master"]
h = master.host
vc = h.module_settings.get("vpn-client") or {}
conf = (h.role_settings_raw.get("vpn-client") or {}).get("wireguard_config") or ""     # the path, as the fleet file says it
linked = {n: p for n, p in (h.module_settings["vpn-server"].get("federation") or {}).items() if p.get("exit")}
if linked:                              # over the federation link
    exit_ip = str(list(linked.values())[0]["endpoint"]).split(":")[0]
elif vc.get("vpn_provider") == "custom" and conf:
    text = open(fleet.resolve_path(conf)).read() if not conf.startswith("[") else conf
    exit_ip = re.search(r"^\s*Endpoint\s*=\s*([^:\s]+)", text, re.M).group(1)
else:
    raise SystemExit("drill: %s does not exit through another master (federation.yaml: exit, or vpn_provider: custom)" % h.name)
own_ip = h.module_settings["vpn-server"].get("server_ip")
say("%s exits through %s; its own mesh address %s" % (h.name, exit_ip, own_ip))

device = fleet.resolve_path("secrets/devices/drill-multihop.conf")
cmd = ["enroll", args.fleet, "drill-multihop", "--ssh", master.target]
if os.path.isfile(device):
    cmd.append("--existing")
rc, out = kiwi(*cmd, timeout=300)
if rc != 0:
    raise SystemExit("drill: enroll failed: %s" % " ".join(out[-2:]))

name = "kiwi-drill-multihop"
subprocess.run(["podman", "rm", "-f", name], capture_output=True)
try:
    subprocess.run(["podman", "run", "-d", "--name", name, "--security-opt", "label=disable", "--cap-add", "NET_ADMIN",
                    "--device", "/dev/net/tun", "-e", "VPN_SERVICE_PROVIDER=custom", "-e", "VPN_TYPE=wireguard",
                    "-e", "WIREGUARD_IMPLEMENTATION=userspace", "-e", "DOT=off",
                    "-v", "%s:/gluetun/wireguard/wg0.conf:ro" % device, "docker.io/qmcgaw/gluetun:v3"],
                   check=True, capture_output=True)
    seen = ""
    for _ in range(36):
        logs = subprocess.run(["podman", "logs", name], capture_output=True, text=True).stdout
        found = re.findall(r"Public IP address is ([0-9.]+)", logs)
        if found:
            seen = found[-1]
            break
        time.sleep(5)

    def run(*c, timeout=20):
        try:
            r = subprocess.run(["podman", "exec", name] + list(c), capture_output=True, text=True, timeout=timeout)
            return r.returncode, r.stdout
        except subprocess.TimeoutExpired:
            return 124, ""
    results = [("leaves the internet at the exit master", seen == exit_ip, seen or "no tunnel")]
    _rc, dns = run("nslookup", h.hostname, own_ip)
    answers = re.findall(r"Address:\s*([0-9.]+)$", dns, re.M)
    results.append(("%s resolves to its own mesh address" % h.hostname, own_ip in answers, " ".join(answers) or "no answer"))
    if opts.exit_mesh:
        rc, _o = run("wget", "-qO-", "-T", "5", "http://%s/" % opts.exit_mesh, timeout=15)
        results.append(("the exit network's mesh is closed", rc != 0, "reachable" if rc == 0 else "closed"))
finally:
    subprocess.run(["podman", "rm", "-f", name], capture_output=True)
for what, ok, detail in results:
    say("%s %s (%s)" % ("ok    " if ok else "FAILED", what, detail))
sys.exit(0 if all(ok for _w, ok, _d in results) else 1)
