#!/usr/bin/env python3
"""A RAM master through a power cycle at its hoster: off, on, blank, its role back from home.

Powers the netcup server off and on through the SCP API (as a hoster's maintenance would),
then runs `kiwi-server watch --once` until the master has its role again, and checks it came
back as itself: the same WireGuard server key, every container running.

    tests/drills/ram-master-power.py ~/kiwi2/fleet.yaml crumble

The master and its mesh are down for a few minutes. Needs the fleet's netcup login
(secrets/netcup.token) and `vps: { server: ID }` on the host.
"""
import time

from drill import kiwi, say, setup, wait
from kiwiserver import netcup

args, fleet, m = setup(__doc__.splitlines()[0], roles=("master",))
master = m["master"]
h = master.host
sid = (h.cfg.get("vps") or {}).get("server")
if h.target != "coreos-ram" or not sid:
    raise SystemExit("drill: %s is not a coreos-ram host with vps.server" % h.name)
client = netcup.Netcup(fleet.resolve_path("secrets/netcup.token"))


def wg_key():
    return master.out("podman exec km-vpn-server wg show wg0 public-key", 30)


key = wg_key()
say("%s: WireGuard key %s…; powering off at netcup" % (h.name, key[:12]))
t0 = time.time()
client.power(sid, False)
off = wait(lambda: client.state(sid) == "SHUTOFF", 300, 10)
say("off after %s s; on again" % off)
client.power(sid, True)
t_on = time.time()


def role_back():
    rc, out = kiwi("watch", args.fleet, h.name, "--once", "--ssh", master.target, timeout=900)
    return any(line.endswith(": applied") for line in out) or master.run("test -f /var/lib/kiwi-server/role.done", 15)[0] == 0 \
        and master.run("kiwi-stack verify", 120)[0] == 0
took = wait(role_back, 1200, 20)
say("role back %s after power on" % ("%d s" % (time.time() - t_on) if took is not None else "NOT within 20 minutes"))
failures = [] if took is not None else ["no role within 20 minutes"]
if took is not None:
    now = wg_key()
    say("WireGuard key %s" % ("the same" if now == key else "CHANGED (%s…)" % now[:12]))
    if now != key:
        failures.append("a new WireGuard key")
    rc, out = master.run("kiwi-stack verify", 120)
    say(out.splitlines()[-1] if out else "verify: %d" % rc)
    if rc:
        failures.append("the stack is not up")
say("whole cycle %d s; %s" % (time.time() - t0, "passed" if not failures else "FAILED: %s" % "; ".join(failures)))
raise SystemExit(1 if failures else 0)
