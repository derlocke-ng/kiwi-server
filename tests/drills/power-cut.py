#!/usr/bin/env python3
"""The power goes out in a home, and comes back.

Every machine named goes down in the same moment, without a clean shutdown, and starts again
together: virtual machines are switched off hard at their hypervisor and started after the
pause, real ones are restarted on the spot (the kernel's sysrq, no sync, no unmount). Then
the drill only watches, from this computer, with the time of every change per machine:

    off -> starting -> locked (waits at boot for its disk) -> up -> running (its services are back)

and at the end whether the control node opened by itself. It helps nobody: a machine that
stays locked, or two that wait for each other's unlock ring, are what it is there to find.

    tests/drills/power-cut.py ~/kiwi/fleet.yaml papaya lychee quince medlar lime --vm-host sh3 [--pause 30] [--limit 25]

Every machine must be in drills.yaml. Their data is at the risk a real power cut puts it at.
"""
import argparse
import os
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import yaml  # noqa: E402

from drill import say  # noqa: E402
from kiwiserver import cli, remote, roles as rolesmod, unlock as unlockmod  # noqa: E402

p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
p.add_argument("fleet")
p.add_argument("hosts", nargs="+")
p.add_argument("--vm-host", help="ssh name of the hypervisor (virsh -c qemu:///system) of the machines that are virtual")
p.add_argument("--pause", type=float, default=30, help="seconds without power")
p.add_argument("--limit", type=float, default=25, help="minutes to watch")
args = p.parse_args()
allow = os.path.join(os.path.dirname(os.path.abspath(args.fleet)), "drills.yaml")
allowed = (yaml.safe_load(open(allow)) or {}).get("allowed", []) if os.path.isfile(allow) else []
missing = [h for h in args.hosts if h not in allowed]
if missing:
    sys.exit("drill: %s not in %s: drills break things and only run on machines listed there" % (", ".join(missing), allow))
fleet, roles, ms = cli.load(args)
V = "virsh -c qemu:///system"
SSH = ["ssh"] + remote.ssh_opts() + ["-o", "ConnectTimeout=6"]


def hv(command, timeout=60):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", args.vm_host, command],
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


vms = set()
if args.vm_host:
    rc, out = hv("%s list --all --name" % V)
    vms = {x.strip() for x in out.splitlines()} & set(args.hosts)


class M:
    def __init__(self, name):
        self.name, self.host = name, fleet.hosts[name]
        rolesmod.resolve_settings(roles[self.host.role], self.host, ms)
        net = self.host.cfg.get("network") or {}
        self.addr = str(net.get("address") or "").split("/")[0] or self.host.hostname      # its address at home
        self.target = "%s@%s" % (self.host.admin["user"], self.addr)
        self.port = remote.ssh_port(self.host)
        self.seen, self.was = [], None
        self.before = self.count()

    def run(self, script, timeout=20):
        try:
            r = subprocess.run(SSH + ["-p", str(self.port), self.target, "sudo sh -c " + cli.shlex.quote(script)],
                               capture_output=True, text=True, timeout=timeout)
            return r.returncode, r.stdout.strip()
        except subprocess.TimeoutExpired:
            return 124, ""

    def count(self):
        rc, out = self.run("podman ps -q 2>/dev/null | wc -l")
        return int(out) if rc == 0 and out.isdigit() else -1

    def state(self):
        # a login first: it is what fail2ban there does not hold against us. Only when there is
        # none, is the machine waiting for its disk key (a key scan), or merely on?
        if remote._answers(self.addr, self.port):
            n = self.count()
            if n >= 0:
                return "running" if n >= self.before else "up (%d of %d services)" % (n, self.before)
            if unlockmod.waiting_at(self.host):
                return "locked"
            return "starting"
        return "starting" if subprocess.run(["ping", "-c", "1", "-W", "1", self.addr], capture_output=True).returncode == 0 else "off"

    def look(self, t0):
        now = self.state()
        if now != self.was:
            self.seen.append((time.time() - t0, now))
            say("%5.1f min  %-8s %s" % ((time.time() - t0) / 60, self.name, now))
            self.was = now


machines = [M(h) for h in args.hosts]
down = [m.name for m in machines if m.before < 0]
if down:
    sys.exit("drill: %s do not answer now: a power cut is tried on a home that runs" % ", ".join(down))
say("%d machines (%s virtual: %s); every one runs. The power goes out for %d seconds" % (
    len(machines), len(vms), ", ".join(sorted(vms)) or "none", args.pause))
t0 = time.time()


def cut(m):
    if m.name in vms:
        hv("%s destroy %s" % (V, m.name))
    else:       # no sync, no unmount: as close to a pulled plug as a machine can do to itself
        subprocess.run(SSH + ["-p", str(m.port), m.target, "sudo sh -c 'echo 1 > /proc/sys/kernel/sysrq; (sleep 1; echo b > /proc/sysrq-trigger) >/dev/null 2>&1 &'"],
                       capture_output=True, timeout=20)


threads = [threading.Thread(target=cut, args=(m,)) for m in machines]
for t in threads:
    t.start()
for t in threads:
    t.join()
say("the power is out")
time.sleep(args.pause)
for name in sorted(vms):
    hv("%s start %s" % (V, name))
say("the power is back")
end = t0 + args.limit * 60
while time.time() < end:
    for m in machines:
        m.look(t0)
    if all(m.was == "running" for m in machines):
        break
    time.sleep(10)
print()
for m in machines:
    print("%-8s %s" % (m.name, " -> ".join("%s (%.1f)" % (s, t / 60) for t, s in m.seen)))
stuck = [m.name for m in machines if m.was != "running"]
# off again after it had started: someone reset it, or its watchdog did. Not "by itself" without a look
again = [m.name for m in machines if any(s == "off" for _t, s in m.seen[1:])]
if again:
    print("went off a second time on the way (reset by a person, or by its watchdog): %s" % ", ".join(again))
control = next((m for m in machines if "control" in (m.host.modules or [])), None)
if control:
    for _ in range(18):          # it asks its ring every half minute: three minutes to open by itself
        r = subprocess.run(["curl", "-sk", "--max-time", "8", "--resolve", "control.%s:443:%s" % (control.host.hostname, control.addr),
                            "https://control.%s/healthz" % control.host.hostname], capture_output=True, text=True)
        if '"locked":false' in r.stdout:
            break
        time.sleep(10)
    print("control node on %s: %s" % (control.name, r.stdout.strip() or "no answer"))
    if '"locked":false' not in r.stdout:
        stuck.append("the control node (locked)")
print("\n%s" % ("every machine is back by itself, %.0f minutes after the power went out" % ((time.time() - t0) / 60) if not stuck
               else "NOT back by themselves after %.0f minutes: %s" % (args.limit, ", ".join(stuck))))
sys.exit(1 if stuck else 0)
