#!/usr/bin/env python3
"""A backup server that hangs: the client's run gives up in time and leaves nothing frozen.

Pauses the server's restic REST container (connections open, nothing answers, as a hung
machine), runs a backup on the client and expects: it ends with an error within the backup
module's limits, no container on the client is left paused, the client's stack runs. Then
unpauses the server and expects the next run to succeed.

    tests/drills/backup-server-hung.py ~/kiwi/fleet.yaml lychee papaya

The client's backup to that server fails once; its other servers, if any, get their copy.
"""
import time

from drill import say, setup

args, fleet, m = setup(__doc__.splitlines()[0], roles=("client", "server"))
client, server = m["client"], m["server"]
rest = server.out("podman ps --format '{{.Names}}' | grep -E '^k[mn]-backup-server$'")
if not rest:
    raise SystemExit("drill: %s runs no backup server" % server.name)
if client.run("command -v kiwi-stack >/dev/null && test -x \"$(sed -n 's/^STACK_DIR=//p' /etc/kiwi-server/stack.env)/kiwi/backup.sh\"")[0]:
    raise SystemExit("drill: %s has no backup module" % client.name)
failures = []


def backup(limit):
    t0 = time.time()
    rc, out = client.run("kiwi-stack backup", limit)
    return rc, round(time.time() - t0), out


try:
    server.run("podman pause %s" % rest)
    say("%s paused on %s; a backup on %s" % (rest, server.name, client.name))
    rc, took, out = backup(1800)
    say("the run ended after %d s with %d: %s" % (took, rc, (out.splitlines() or [""])[-1][:160]))
    if rc == 0:
        failures.append("the run said it succeeded against a hung server")
    if rc == 124:
        failures.append("the run did not end within 30 minutes")
    paused = client.out("podman ps --filter status=paused --format '{{.Names}}'")
    say("paused on %s: %s" % (client.name, paused or "nothing"))
    if paused:
        failures.append("left paused: %s" % paused.replace("\n", ", "))
    rc_v, out_v = client.run("kiwi-stack verify", 120)
    say("%s's stack: %s" % (client.name, out_v.splitlines()[-1] if out_v else rc_v))
    if rc_v:
        failures.append("the stack is not up after the run")
finally:
    server.run("podman unpause %s" % rest)
rc, took, out = backup(1800)
say("with %s back: %s after %d s" % (server.name, "succeeded" if rc == 0 else "FAILED (%d)" % rc, took))
if rc:
    failures.append("the run after the server came back failed")
say("passed" if not failures else "FAILED: %s" % "; ".join(failures))
raise SystemExit(1 if failures else 0)
