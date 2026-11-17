#!/usr/bin/env python3
"""A standby master (docs/design/standby.md): a node follows its master to the standby and back.

The master's VPN server is stopped (its machine stays up). The node's tunnel must move to the
standby within five minutes and the mesh answer there: the master's mesh address, a name through
its DNS. Then the master's VPN starts again; the node must move back once the master's machine has
answered for ten minutes. One PASS or FAIL line per check. Leaves everything as it found it,
the master's VPN server running also after a failure.

    tests/drills/standby.py ~/kiwi-2/fleet.yaml crumble damson

Touches the master's VPN server (its devices lose the network for some minutes) and nothing else.
"""
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import argparse  # noqa: E402

from kiwiserver import cli, remote, roles as rolesmod  # noqa: E402
from kiwiserver.util import KiwiError  # noqa: E402

fleet_path, master_name, node_name = sys.argv[1:4]
fleet, roles, ms = cli.load(argparse.Namespace(fleet=fleet_path))       # the fleet's keys and known hosts too
master, node = fleet.hosts[master_name], fleet.hosts[node_name]
standbys = fleet.standbys(master_name)
if not standbys:
    sys.exit("drill: %s has no standby" % master_name)
for h in (master, node) + tuple(standbys):
    rolesmod.resolve_settings(roles[h.role], h, ms)
vs = master.module_settings["vpn-server"]
mesh_ip = str(vs.get("server_ip") or "10.10.0.1")
first = "%s:%s" % (vs["wg_host"], vs.get("wg_port") or 51820)
second = "%s:%s" % (standbys[0].standby_endpoint, vs.get("wg_port") or 51820)
fails = 0


def say(m):
    print("%s %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def check(ok, what):
    global fails
    say("%s %s" % ("PASS" if ok else "FAIL", what))
    fails += 0 if ok else 1
    return ok


def on(h, script):
    try:
        return remote.run(remote.reach(h, None), script).decode(errors="replace")
    except KiwiError as e:
        return "error: %s" % e


def endpoint():
    out = on(node, "sudo sed -n 's/^Endpoint *= *//p' %s/kn-vpn-client/wg0.conf"
         % ((node.role_settings or {}).get("docker_dir") or "/home/user/docker"))
    return out.strip()


def through(what):
    """Does the node reach the mesh through its tunnel: the master's address, a name through its DNS?"""
    if what == "ping":
        return "ok" in on(node, "sudo podman exec kn-vpn-client ping -c 2 -W 3 %s >/dev/null && echo ok" % mesh_ip)
    out = on(node, "sudo podman exec kn-vpn-client nslookup %s %s 2>&1" % (master.hostname, mesh_ip))
    return bool(re.search(r"Address:\s*%s\s*$" % re.escape(mesh_ip), out, re.M))


def wait_for(cond, seconds):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(15)
    return cond()


def keys():
    return [on(h, "sudo podman exec km-vpn-server wg show wg0 public-key 2>/dev/null").strip() for h in (master, standbys[0])]


k = keys()
check(k[0] and k[0] == k[1] and not k[0].startswith("error"), "the standby carries the master's WireGuard key")
check(on(node, "systemctl is-active kn-master-switch.service").strip() == "active", "the node's switch runs")
check(endpoint() == first, "the node's tunnel goes to the master (%s)" % endpoint())
check(through("ping"), "the node reaches %s through it" % mesh_ip)
try:
    say("the master's VPN server stops (its machine stays up)")
    on(master, "sudo systemctl stop km-vpn-server.service")
    t0 = time.time()
    moved = wait_for(lambda: endpoint() == second, 330)
    check(moved, "the node moves to the standby %s (%d s)" % (second, time.time() - t0))
    if moved:
        check(wait_for(lambda: through("ping"), 90), "the node reaches %s at the standby" % mesh_ip)
        check(through("dns"), "%s resolves through the standby's DNS" % master.hostname)
finally:
    on(master, "sudo systemctl start km-vpn-server.service")
    say("the master's VPN server runs again")
t1 = time.time()
back = wait_for(lambda: endpoint() == first, 900)
check(back, "the node is back at the master (%d s after its VPN came back)" % (time.time() - t1))
if back:
    check(wait_for(lambda: through("ping"), 120), "the node reaches %s at the master again" % mesh_ip)
say("all passed" if not fails else "%d failed" % fails)
sys.exit(1 if fails else 0)
