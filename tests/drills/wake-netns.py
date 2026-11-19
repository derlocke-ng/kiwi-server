#!/usr/bin/env python3
"""Wake-on-LAN's packet (lib/kiwiserver/wake.py) on this computer's kernel: throwaway network
namespaces in a user namespace, no root, nothing of this computer's network touched.

    waker    192.168.2.30   runs wake.send_script as `kiwi-server wake` has a machine of the LAN do
    sleeper  its card has the MAC and no address, as a machine that is off: a packet socket
             watches what reaches the card

Checks, one PASS or FAIL line each: the magic packet reaches the card of that MAC; the waker's
neighbour entry for it is gone afterwards; an address that is not on the LAN is refused.
Whether a real card then starts its machine is the firmware's part, not tested here.

    python3 tests/drills/wake-netns.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time

if os.environ.get("KIWI_NETNS_INNER") != "1":
    sys.exit(subprocess.run(["unshare", "-rnm", "--propagation", "private", sys.executable] + sys.argv,
                            env=dict(os.environ, KIWI_NETNS_INNER="1")).returncode)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib"))
from kiwiserver import wake  # noqa: E402

work = tempfile.mkdtemp(prefix="kiwi-wake-")
fails = 0
MAC = "02:00:00:00:00:40"


def check(ok, what):
    global fails
    print("%s %s" % ("PASS" if ok else "FAIL", what), flush=True)
    fails += 0 if ok else 1


def sh(cmd, ns=None, check_rc=True):
    r = subprocess.run((["ip", "netns", "exec", ns] if ns else []) + ["sh", "-c", cmd], capture_output=True, text=True)
    if check_rc and r.returncode:
        raise SystemExit("%s: %s\n%s%s" % (ns or "here", cmd, r.stdout, r.stderr))
    return r


sh("mount -t tmpfs none /run && mkdir -p /run/netns")
for ns in ("waker", "sleeper", "sw"):
    sh("ip netns add %s && ip netns exec %s ip link set lo up" % (ns, ns))
sh("ip link add name br0 netns sw type bridge && ip netns exec sw ip link set br0 up")
for ns in ("waker", "sleeper"):
    sh("ip link add name eth0 netns %s type veth peer name p-%s netns sw" % (ns, ns))
    sh("ip link set p-{0} master br0 && ip link set p-{0} up".format(ns), "sw")
sh("ip addr add 192.168.2.30/24 dev eth0 && ip link set eth0 up && ip route add default via 192.168.2.1", "waker")
sh("ip link set dev eth0 address %s && ip link set eth0 up" % MAC, "sleeper")     # no address: it is off

catch = os.path.join(work, "caught")
sniff = textwrap.dedent('''
    import socket, sys, time
    want = bytes.fromhex(sys.argv[2])
    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(3)); s.bind(("eth0", 0)); s.settimeout(1)
    end = time.time() + 20
    while time.time() < end:
        try:
            frame = s.recv(2048)
        except socket.timeout:
            continue
        if want in frame:
            open(sys.argv[1], "w").write(frame[:6].hex())
            break
''')
p = subprocess.Popen(["ip", "netns", "exec", "sleeper", sys.executable, "-c", sniff, catch, wake.magic(MAC).hex()],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
r = sh(wake.send_script("192.168.2.40", MAC), "waker", check_rc=False)
check(r.returncode == 0 and "sent on eth0" in r.stdout, "the waker sends it (%s)" % (r.stdout.strip() or r.stderr.strip()))
p.wait(timeout=25)
got = open(catch).read() if os.path.exists(catch) else ""
check(got == MAC.replace(":", ""), "the card of %s gets the magic packet, addressed to it%s" % (MAC, "" if got else ": nothing came"))
check("192.168.2.40" not in sh("ip neigh show", "waker").stdout, "the waker's neighbour entry for it is gone again")
r = sh(wake.send_script("10.20.30.40", MAC), "waker", check_rc=False)
check(r.returncode == 4 and "not on this LAN" in r.stderr, "an address that is not on its LAN is refused")

print("%d wrong" % fails)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if fails else 0)
