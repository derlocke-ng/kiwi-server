#!/usr/bin/env python3
"""The master's server is switched off at its provider. Does the network bring it back?

The control node looks at the machines every ten minutes. With `recovery: { master: { after: N,
do: restart } }` in the fleet's defaults it switches a master that does not answer off and on
at its provider, and forwards the unlock ring when the master then waits at boot with its disk
locked. This drill pulls the plug (a power-off through the provider, nothing on the disk is
touched) and watches from outside what happens, with the time of every change:

    KIWI_NEVER="<ids and addresses that must never be touched>" \\
        tests/drills/master-gone.py ~/kiwi/fleet.yaml sorbet [--limit 45]

It does nothing itself to bring the master back. Exit 1 when the master is not back in time.
"""
import argparse
import os
import sys
import time

from drill import say, setup
from kiwiserver import cli, netcup, remote, unlock as unlockmod

extra = argparse.ArgumentParser()
extra.add_argument("--limit", type=float, default=45, help="minutes to wait for the master")
opts, rest = extra.parse_known_args()
sys.argv = [sys.argv[0]] + rest
args, fleet, m = setup(__doc__.splitlines()[0], roles=("master",))
host = m["master"].host
never = os.environ.get("KIWI_NEVER", "").split()
if not never:
    sys.exit("drill: set KIWI_NEVER to the server ids and addresses that must never be touched")
client = netcup.Netcup(fleet.resolve_path("secrets/netcup.token"), netcup.BASE)
server = cli.vps_server(client, fleet, host.name, None)
addresses = [a.get("ip") for a in server.get("ipv4Addresses") or []]
label = server.get("nickname") or server.get("name")
if str(server["id"]) in never or any(a in never for a in addresses) or label in never:
    sys.exit("drill: %s (%s) is in KIWI_NEVER: not touched" % (label, server["id"]))
addr = (host.role_settings_raw.get("vpn-server") or {}).get("wg_host") or addresses[0]
port = remote.ssh_port(host)


def look():
    """What the master is, seen from outside: off, starting, locked (waits at boot), up."""
    try:
        state = client.state(server["id"])
    except Exception as e:      # noqa: BLE001 — the provider's API hiccups; the drill goes on
        state = "? (%s)" % str(e)[:40]
    if unlockmod.waiting_at(host):          # first: the waiting machine may answer on the system's port too
        return "locked"
    if remote._answers(addr, port):
        return "up"
    return "off" if state == "SHUTOFF" else "starting (%s)" % state


say("%s is server %s (%s) at %s; it is %s" % (host.name, server["id"], label, addr, look()))
if look() != "up":
    sys.exit("drill: the master is not up: nothing to pull")
t0 = time.time()
client.power(server["id"], False)
say("switched off at the provider")
was, seen, end = None, [], t0 + opts.limit * 60
while time.time() < end:
    now = look()
    if now != was:
        seen.append((time.time() - t0, now))
        say("%5.1f min: %s" % ((time.time() - t0) / 60, now))
        was = now
    if now == "up" and any(s == "off" for _t, s in seen):
        break
    time.sleep(15)
back = was == "up" and any(s == "off" for _t, s in seen)
print("\n%s: %s" % (host.name, " -> ".join("%s (%.0f min)" % (s, t / 60) for t, s in seen)))
if back:
    print("back by itself %.0f minutes after the power-off%s" % (seen[-1][0] / 60,
          ", its disk opened on the way" if any(s == "locked" for _t, s in seen) else ""))
else:
    print("NOT back after %.0f minutes: switch it on at the provider, then kiwi-server unlock" % opts.limit)
sys.exit(0 if back else 1)
