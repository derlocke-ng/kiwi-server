#!/usr/bin/env python3
"""A commercial VPN exit (Mullvad through gluetun) stuck on a server through which its DNS fails.

Seen on 10 November 2026: the tunnel up, DNS-over-TLS through that one server reset, and
gluetun restarting the tunnel to the same server every few seconds for good. Only a new
container picks another server. This drops DNS-over-TLS inside the container's own network,
which a new container does not inherit, as it would not inherit the bad server, and expects
podman's health check to kill the container after five unhealthy minutes and systemd to start
it anew.

    tests/drills/exit-server-stuck.py ~/kiwi/fleet.yaml sorbet

Takes about eight minutes; the exit is down for most of them. Covers kiwi-modules' vpn-client
health check (x-kiwi-health-on-failure) and the quadlet generator's HealthOnFailure.
"""
import re

from drill import say, setup, wait

args, fleet, m = setup(__doc__.splitlines()[0], roles=("machine",))
host = m["machine"]
c = host.out("podman ps --format '{{.Names}}' | grep -E '^k[mn]-vpn-client$'")
if not c:
    raise SystemExit("drill: %s runs no VPN client" % host.name)
if "VPN_SERVICE_PROVIDER=custom" in host.out("podman exec %s env" % c):
    raise SystemExit("drill: %s's VPN client joins a master (provider custom); this is for a commercial exit" % host.name)


def exit_ok():
    return "You are connected" in host.out("podman exec %s wget -q -T8 -O- https://am.i.mullvad.net/connected" % c, 30)


def container_id():
    """The container's ID, which a new container changes; None when the machine did not answer."""
    rc, out = host.run("podman inspect %s --format '{{.Id}}'" % c, 30)
    return out if rc == 0 and out else None


def endpoint():
    found = re.findall(r"Connecting to ([0-9.]+):", host.out("podman logs --since 30m %s 2>&1" % c))
    return found[-1] if found else ""


say("%s on %s: exit %s, server %s" % (c, host.name, "works" if exit_ok() else "DOWN", endpoint()))
blocked, before = endpoint(), container_id()
if not blocked:
    raise SystemExit("drill: no server in %s's log" % c)
# inside the container's network: the next container does not have it
block = "podman exec %s iptables -I OUTPUT 1 -p tcp --dport 853 -j DROP" % c
try:
    rc, out = host.run(block)
    if rc != 0:
        raise SystemExit("drill: the block did not go in: %s" % out[:200])
    say("DNS-over-TLS dropped inside %s (server %s)" % (c, blocked))
    took = wait(lambda: container_id() not in (None, before), 900, 15)
    say("container started anew after %s s" % took if took is not None else "the container was NOT started anew within 15 minutes")
    took = wait(exit_ok, 300, 10)
    now = endpoint()
    say("exit works %s, server %s" % ("after %d s more" % took if took is not None else "NOT within 5 minutes", now))
finally:
    # gone with a new container; this is for a run that ended before one came
    host.run("podman exec %s iptables -D OUTPUT -p tcp --dport 853 -j DROP" % c)
