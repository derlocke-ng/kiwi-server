#!/usr/bin/env python3
"""Each machine's role script, as a fingerprint the way `list` compares it with the last apply:
one line per machine, a name and a hash, never a script or a secret. Run it before and after a
change to the fleet file that must not change any machine (`secrets keep`), and compare.

    python3 tests/drills/fingerprints.py FLEET [--lib CHECKOUT/lib]     (the store unlocked)
"""
import argparse
import os
import sys

a = argparse.ArgumentParser()
a.add_argument("fleet")
a.add_argument("--lib", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib"))
opts = a.parse_args()
sys.path.insert(0, os.path.abspath(opts.lib))
from kiwiserver import cli  # noqa: E402
from kiwiserver.util import KiwiError  # noqa: E402

args = cli.build_parser().parse_args(["list", opts.fleet])
fleet, roles, ms = cli.load(args)
r = cli.Renderer(fleet, roles, cli.out_dir_for(args, fleet), None, ms)
cli.fleet_facts(fleet, roles, ms, r)
for h in fleet.hosts.values():
    if h.role not in roles or h.validate(roles):
        print("%s -" % h.name)
        continue
    try:
        script, _b = r.script(h)
    except KiwiError as e:
        print("%s error: %s" % (h.name, str(e).splitlines()[0][:120]))
        continue
    print("%s %s" % (h.name, cli._fingerprint(script)))
