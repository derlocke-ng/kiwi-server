#!/usr/bin/env python3
"""A group of the home network kept by a node (docs/design/lans.md), on this computer's kernel:
throwaway network namespaces in a user namespace, no root, nothing of this computer's network
touched. The node loads the nftables table kiwi-stack makes from its lan_rules.

    node 192.168.2.30 (ports 22, 443, 53/udp; 9443 published to a container's 443)
    iot  192.168.20.5   in the group iot: "192.168.20.0/24 tcp:443,tcp:80"
    pc   192.168.2.50   in no group: everything, as before

Checks, one PASS or FAIL line each: iot opens 443 and asks DNS, not 22 and not the published
9443 (refused before its DNAT); pc opens all of them; with no rules the table is gone.

    python3 tests/drills/lan-netns.py [--lib roles/common/lib.sh]
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time

ap = argparse.ArgumentParser()
ap.add_argument("--lib", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "roles", "common", "lib.sh"))
opts = ap.parse_args()
lib = os.path.abspath(opts.lib)

if os.environ.get("KIWI_NETNS_INNER") != "1":
    sys.exit(subprocess.run(["unshare", "-rnm", "--propagation", "private", sys.executable] + sys.argv,
                            env=dict(os.environ, KIWI_NETNS_INNER="1")).returncode)

work = tempfile.mkdtemp(prefix="kiwi-lan-")
fails = 0


def check(ok, what):
    global fails
    print("%s %s" % ("PASS" if ok else "FAIL", what), flush=True)
    fails += 0 if ok else 1


def sh(cmd, ns=None, check_rc=True):
    r = subprocess.run((["ip", "netns", "exec", ns] if ns else []) + ["sh", "-c", cmd], capture_output=True, text=True)
    if check_rc and r.returncode:
        raise SystemExit("%s: %s\n%s%s" % (ns or "here", cmd, r.stdout, r.stderr))
    return r


def rules(*lan):
    """kiwi-stack's ruleset for these lan_rules, as `kiwi-stack apply` loads it."""
    r = subprocess.run(["bash", "-c", '. "$1"; shift; KS_STACK_LAN_RULES=("$@"); ks_lan_nft', "-", lib] + list(lan),
                       capture_output=True, text=True, check=True)
    path = os.path.join(work, "lan-%d.nft" % len(os.listdir(work)))
    with open(path, "w") as fh:
        fh.write(r.stdout)
    return path


sh("mount -t tmpfs none /run && mkdir -p /run/netns")
for ns in ("node", "ct", "iot", "pc"):
    sh("ip netns add %s && ip netns exec %s ip link set lo up" % (ns, ns))
# the home network: one switch (a bridge in the node's namespace stands for it), the node on it
sh("ip link add name br0 netns node type bridge")
sh("ip addr add 192.168.2.30/24 dev br0 && ip addr add 192.168.20.1/24 dev br0 && ip link set br0 up && "
   "echo 1 > /proc/sys/net/ipv4/ip_forward", "node")
for ns, addr, gw in (("iot", "192.168.20.5/24", "192.168.20.1"), ("pc", "192.168.2.50/24", "192.168.2.30")):
    sh("ip link add name eth0 netns %s type veth peer name %s netns node" % (ns, ns))
    sh("ip link set %s master br0 && ip link set %s up" % (ns, ns), "node")
    sh("ip addr add %s dev eth0 && ip link set eth0 up && ip route add default via %s" % (addr, gw), ns)
# a container behind the node, published at 9443 like a stack's port
sh("ip link add name ct0 netns node type veth peer name eth0 netns ct")
sh("ip addr add 10.88.0.1/24 dev ct0 && ip link set ct0 up && "
   "iptables -t nat -A PREROUTING -p tcp --dport 9443 -j DNAT --to-destination 10.88.0.2:443", "node")
sh("ip addr add 10.88.0.2/24 dev eth0 && ip link set eth0 up && ip route add default via 10.88.0.1", "ct")

listen = textwrap.dedent('''
    import socket, sys, threading
    def tcp(port):
        s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("0.0.0.0", port)); s.listen(5)
        while True:
            c, _ = s.accept(); c.sendall(b"ok"); c.close()
    def udp(port):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("0.0.0.0", port))
        while True:
            d, p = s.recvfrom(512); s.sendto(b"ok", p)
    for p in sys.argv[1:]:
        kind, _, n = p.partition(":")
        threading.Thread(target=tcp if kind == "tcp" else udp, args=(int(n),), daemon=True).start()
    import time; time.sleep(600)
''')
procs = [subprocess.Popen(["ip", "netns", "exec", "node", sys.executable, "-c", listen, "tcp:22", "tcp:443", "udp:53"]),
         subprocess.Popen(["ip", "netns", "exec", "ct", sys.executable, "-c", listen, "tcp:443"])]
time.sleep(1)

probe = textwrap.dedent('''
    import socket, sys
    kind, port = sys.argv[1], int(sys.argv[2])
    try:
        if kind == "tcp":
            s = socket.create_connection(("192.168.2.30", port), timeout=2); print(s.recv(8).decode())
        else:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(2); s.sendto(b"q", ("192.168.2.30", port))
            print(s.recv(8).decode())
    except OSError:
        print("no")
''')


def opens(ns, kind, port):
    return sh("%s -c '%s' %s %s" % (sys.executable, probe.replace("'", "'\"'\"'"), kind, port), ns, check_rc=False).stdout.strip() == "ok"


everything = [("tcp", 22), ("tcp", 443), ("udp", 53), ("tcp", 9443)]
check(all(opens("iot", k, p) for k, p in everything), "without rules iot opens everything, as a home did before")
sh("nft -f %s" % rules("192.168.20.0/24 tcp:443,tcp:80"), "node")
check(opens("iot", "tcp", 443), "iot opens 443 (its group: the web)")
check(opens("iot", "udp", 53), "iot asks DNS (open to everyone)")
check(not opens("iot", "tcp", 22), "iot does not open 22")
check(not opens("iot", "tcp", 9443), "iot does not open the published 9443 (refused before its DNAT)")
check(all(opens("pc", k, p) for k, p in everything), "pc, in no group, opens everything")
sh("nft -f %s" % rules("192.168.20.0/24 none"), "node")
check(not opens("iot", "tcp", 443) and opens("iot", "udp", 53), "nothing for iot: not even 443, DNS still")
sh("nft -f %s" % rules("192.168.20.0/24 all"), "node")
check(all(opens("iot", k, p) for k, p in everything), "all for iot: everything again (the table replaced in place)")

for p in procs:
    p.kill()
print("%d wrong" % fails)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if fails else 0)
