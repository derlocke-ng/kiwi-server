#!/usr/bin/env python3
"""The master's WireGuard server down for a while: what a node does meanwhile, and how fast
the mesh is back.

Stops the master's VPN server unit (its Pi-hole, nginx and Tor follow it), watches the node
every 30 seconds — which of its containers restarted, whether its own Pi-hole still answers
on the LAN when it runs one — then starts it again and measures until the node reaches the
master's mesh address.

    tests/drills/master-down.py ~/kiwi/fleet.yaml sorbet papaya [--minutes 5]

The mesh is down for the minutes given and a bit; the node's own services stay up.
"""
import sys

from drill import say, setup, wait

minutes = 5
if "--minutes" in sys.argv:
    i = sys.argv.index("--minutes")
    minutes = float(sys.argv[i + 1])
    del sys.argv[i:i + 2]
args, fleet, m = setup(__doc__.splitlines()[0], roles=("master", "node"))
master, node = m["master"], m["node"]
unit = "km-vpn-server.service"
server_ip = master.host.module_settings["vpn-server"].get("server_ip") or "10.8.0.1"
client = node.out("podman ps --format '{{.Names}}' | grep -E '^kn-vpn-client$'")
pihole = node.out("podman ps --format '{{.Names}}' | grep -E '^kn-pihole$'")
if not client:
    raise SystemExit("drill: %s runs no VPN client" % node.name)


def ids():
    out = node.out("podman ps --format '{{.Names}} {{.ID}}'")
    return dict(line.split() for line in out.splitlines() if len(line.split()) == 2)


def reaches_master():
    return node.run("podman exec %s ping -c1 -W3 %s" % (client, server_ip), 20)[0] == 0


def lan_dns():
    """An uncached name, as a client on the LAN asks: 5 seconds, twice. Any answer counts
    (NXDOMAIN too); dig exits 9 when nothing came back."""
    q = "podman exec %s dig +time=5 +tries=2 kiwi-drill-$(date +%%s%%N).quad9.net @127.0.0.1" % pihole
    return node.run(q, 30)[0] == 0


if not reaches_master():
    raise SystemExit("drill: %s does not reach %s before the drill" % (node.name, server_ip))
before = ids()
say("%s reaches %s; stopping %s on %s for %g minutes" % (node.name, server_ip, unit, master.name, minutes))
failures = []
try:
    rc, out = master.run("systemctl stop %s" % unit, 120)
    if rc != 0:
        raise SystemExit("drill: could not stop %s: %s" % (unit, out[-200:]))
    dns_down = 0
    for _ in range(int(minutes * 2)):
        wait(lambda: False, 30, 30)
        if pihole and not lan_dns():
            dns_down += 1
    if pihole:
        say("%s's Pi-hole on its own: %s" % (node.name, "answered throughout" if not dns_down else
                                                   "did not answer %d of %d times" % (dns_down, int(minutes * 2))))
        if dns_down:
            failures.append("the node's Pi-hole stopped answering")
finally:
    master.run("systemctl start %s" % unit, 180)
took = wait(reaches_master, 600, 10)
say("%s reaches %s again %s" % (node.name, server_ip, "after %d s" % took if took is not None else "NOT within 10 minutes"))
if took is None:
    failures.append("the mesh did not come back")
after = ids()
restarted = sorted(n for n in before if n in after and after[n] != before[n])
gone = sorted(n for n in before if n not in after)
say("on %s: restarted %s; gone %s" % (node.name, ", ".join(restarted) or "nothing", ", ".join(gone) or "nothing"))
if gone:
    failures.append("containers gone: %s" % ", ".join(gone))
say("passed" if not failures else "FAILED: %s" % "; ".join(failures))
sys.exit(1 if failures else 0)
