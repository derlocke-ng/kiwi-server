#!/usr/bin/env python3
"""Drill: a machine that booted the starter stick and waits on the home network is taken
into a network, the way the control page's "A machine with the starter stick is waiting"
form does it (Control.take_in), run here on the operator's computer.

    tests/drills/starter-join.py FLEET ADDRESS CODE [--preset minimal] [--name plum]

ADDRESS and CODE are what the waiting machine shows on its screen. Then: the adopt job
runs (`kiwi-server adopt … --as core@ADDRESS`), and the stick's own control container is
gone afterwards (`kiwi-starter-done joined`). Exit 0 when the job finished well."""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib"))
from kiwiserver.control import Control     # noqa: E402

a = argparse.ArgumentParser()
a.add_argument("fleet"); a.add_argument("address"); a.add_argument("code")
a.add_argument("--preset", default="minimal"); a.add_argument("--name", default="")
o = a.parse_args()
c = Control(os.path.abspath(o.fleet))
jid = c.take_in(o.address, o.code, o.preset, o.name)
print("job", jid, flush=True)
off = 0
while True:
    text, off = c.log(jid, off)
    if text:
        sys.stdout.write(text); sys.stdout.flush()
    j = c.job(jid) or {}
    if j.get("state") not in (None, "waiting", "running"):
        print("\njob state:", j.get("state"), "rc:", j.get("rc"))
        sys.exit(0 if j.get("rc") == 0 else 1)
    time.sleep(5)
