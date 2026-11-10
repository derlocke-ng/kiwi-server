"""What every drill shares: the fleet's machines and how to reach them, and the guard that keeps
a drill off any machine not listed as a test machine. Drills break things on purpose.

A drill takes the fleet file and the names of the hosts it works on. It refuses every host not
named in `drills.yaml` next to the fleet file:

    allowed: [sorbet, papaya, lychee]

so a live network's fleet file, which has no such list, cannot be drilled by mistake.
"""
import argparse
import os
import shlex
import subprocess
import sys
import time

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "lib"))

from kiwiserver import cli, remote, roles as rolesmod  # noqa: E402
from kiwiserver.util import KiwiError  # noqa: E402


def say(*words):
    print(time.strftime("%H:%M:%S"), *words, flush=True)


class Machine:
    """One host of the fleet, reached the way kiwi-server reaches it."""

    def __init__(self, host):
        self.host, self.name = host, host.name
        self.target = remote.reach(host)

    @property
    def address(self):
        return self.target.rsplit("@", 1)[-1]

    def run(self, script, timeout=300):
        """(exit code, output) of a shell script as root on the machine; never raises."""
        argv = ["ssh"] + remote.ssh_opts() + ["-o", "ConnectTimeout=10", self.target, "sudo sh -c " + shlex.quote(script)]
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, ""
        return r.returncode, (r.stdout + r.stderr).strip()

    def out(self, script, timeout=300):
        return self.run(script, timeout)[1]

    def up(self):
        return self.run("true", 15)[0] == 0


def wait(check, limit, every=5):
    """Seconds until check() is true, or None after limit seconds."""
    t0 = time.time()
    while time.time() - t0 < limit:
        if check():
            return round(time.time() - t0)
        time.sleep(every)
    return None


def kiwi(*args, timeout=3600):
    """(exit code, output lines) of this tree's kiwi-server."""
    r = subprocess.run([os.path.join(ROOT, "bin", "kiwi-server"), *args], capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).splitlines()


def setup(description, roles=()):
    """Parse `fleet host...`, load the fleet and return (args, fleet, {role: Machine}). `roles` names
    the hosts a drill needs, e.g. ("master",) or ("client", "server")."""
    p = argparse.ArgumentParser(description=description)
    p.add_argument("fleet")
    for r in roles:
        p.add_argument(r, help="the host that plays %s" % r)
    args = p.parse_args()
    allow = os.path.join(os.path.dirname(os.path.abspath(args.fleet)), "drills.yaml")
    allowed = (yaml.safe_load(open(allow)) or {}).get("allowed", []) if os.path.isfile(allow) else []
    try:
        fleet, all_roles, ms = cli.load(args)
        machines = {}
        for r in roles:
            name = getattr(args, r)
            if name not in allowed:
                raise KiwiError("%s is not in %s: drills break things and only run on machines listed there" % (name, allow))
            h = fleet.hosts.get(name)
            if h is None:
                raise KiwiError("no host %s in %s" % (name, args.fleet))
            rolesmod.resolve_settings(all_roles[h.role], h, ms)
            machines[r] = Machine(h)
    except KiwiError as e:
        sys.exit("drill: %s" % e)
    return args, fleet, machines
