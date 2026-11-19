#!/usr/bin/env python3
"""A machine on a tagged VLAN (docs/design/lans.md, step 2), on this computer's kernel: throwaway
network namespaces in a user namespace, no root, nothing of this computer's network touched.
kiwi-stack's ks_vlans makes the node's tagged interface as `kiwi-stack apply` does.

    switch  a bridge with vlan_filtering: the node on a trunk (untagged 1, tagged 20),
            iot on an access port of VLAN 20, pc and a stray device on VLAN 1
    node    192.168.2.30 untagged, 192.168.20.30 on VLAN 20 (network.vlans: [{id: 20, ...}])
    iot     192.168.20.5   pc 192.168.2.50   stray 192.168.20.6 untagged (on VLAN 1)

Checks, one PASS or FAIL line each: the interface is there with its address and no default
route; iot reaches the node on VLAN 20, the stray device with a VLAN 20 address does not; the
group of the same range keeps what iot may open; a changed address replaces the old one; the
boot script brings it back; with no VLANs it is gone.

    python3 tests/drills/vlan-netns.py [--lib roles/common/lib.sh]
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

work = tempfile.mkdtemp(prefix="kiwi-vlan-")
fails = 0


def check(ok, what):
    global fails
    print("%s %s" % ("PASS" if ok else "FAIL", what), flush=True)
    fails += 0 if ok else 1


def sh(cmd, ns=None, check_rc=True, env=None):
    r = subprocess.run((["ip", "netns", "exec", ns] if ns else []) + ["bash", "-c", cmd], capture_output=True, text=True,
                       env=dict(os.environ, **(env or {})))
    if check_rc and r.returncode:
        raise SystemExit("%s: %s\n%s%s" % (ns or "here", cmd, r.stdout, r.stderr))
    return r


def ks_vlans(*vlans):
    """kiwi-stack's ks_vlans in the node, its files in the work folder, systemctl a no-op."""
    arr = " ".join("'%s'" % v for v in vlans)
    return sh('systemctl() { :; }; . "%s"; KS_SYSTEMD_DIR=%s; KS_STACK_VLANS=(%s); ks_vlans'
              % (lib, os.path.join(work, "systemd"), arr), "node",
              env={"KS_VLANS_FILE": os.path.join(work, "etc", "vlans"), "KS_VLANS_BIN": os.path.join(work, "sbin", "kiwi-vlans")})


sh("mount -t tmpfs none /run && mkdir -p /run/netns")
for ns in ("node", "sw", "iot", "pc", "stray"):
    sh("ip netns add %s && ip netns exec %s ip link set lo up" % (ns, ns))
sh("ip link add name br0 netns sw type bridge vlan_filtering 1 && ip netns exec sw ip link set br0 up")
# the node on a trunk port: VLAN 1 untagged (its home address), VLAN 20 tagged
sh("ip link add name eth0 netns node type veth peer name p-node netns sw")
sh("ip link set p-node master br0 && ip link set p-node up && bridge vlan add dev p-node vid 20", "sw")
sh("ip addr add 192.168.2.30/24 dev eth0 && ip link set eth0 up && ip route add default via 192.168.2.1", "node")
# iot on an access port of VLAN 20 (untagged on its side), pc and the stray device on VLAN 1
for ns, addr, vlan in (("iot", "192.168.20.5/24", 20), ("pc", "192.168.2.50/24", 1), ("stray", "192.168.20.6/24", 1)):
    sh("ip link add name eth0 netns %s type veth peer name p-%s netns sw" % (ns, ns))
    sh("ip link set p-{0} master br0 && ip link set p-{0} up".format(ns), "sw")
    if vlan != 1:
        sh("bridge vlan del dev p-{0} vid 1 && bridge vlan add dev p-{0} vid {1} pvid untagged".format(ns, vlan), "sw")
    sh("ip addr add %s dev eth0 && ip link set eth0 up" % addr, ns)

listen = textwrap.dedent('''
    import socket, sys, threading
    def tcp(port):
        s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("0.0.0.0", port)); s.listen(5)
        while True:
            c, _ = s.accept(); c.sendall(b"ok"); c.close()
    for p in sys.argv[1:]:
        threading.Thread(target=tcp, args=(int(p),), daemon=True).start()
    import time; time.sleep(600)
''')
procs = [subprocess.Popen(["ip", "netns", "exec", "node", sys.executable, "-c", listen, "22", "443"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
time.sleep(1)


def opens(ns, host, port):
    code = ("import socket\ntry:\n    s = socket.create_connection((%r, %d), timeout=2); print(s.recv(8).decode())\n"
            "except OSError:\n    print('no')\n" % (host, port))
    return sh("%s -c %s" % (sys.executable, "'" + code.replace("'", "'\"'\"'") + "'"), ns, check_rc=False).stdout.strip() == "ok"


import atexit  # noqa: E402
atexit.register(lambda: [p.kill() for p in procs])
r = ks_vlans("20 192.168.20.30/24")
check("eth0.20" in r.stdout, "ks_vlans names the interface it made (%s)" % r.stdout.strip().splitlines()[-1:])
link = sh("ip -d -o addr show dev eth0.20", "node", check_rc=False).stdout
check("192.168.20.30/24" in link, "the node has 192.168.20.30 on eth0.20")
check("vlan protocol 802.1Q id 20" in sh("ip -d link show eth0.20", "node").stdout, "eth0.20 is tagged with VLAN 20 on eth0")
check(sh("ip route show default", "node").stdout.split() [:5] == ["default", "via", "192.168.2.1", "dev", "eth0"]
      and "eth0.20" not in sh("ip route show default", "node").stdout, "no default route on the VLAN: the one there stays")
unit = open(os.path.join(work, "systemd", "kiwi-vlans.service")).read()
check("sys-subsystem-net-devices-eth0.device" in unit and "Before=network.target" in unit,
      "kiwi-vlans.service waits for eth0 and comes before the network")
check(opens("iot", "192.168.20.30", 443), "iot (VLAN 20) reaches the node at 192.168.20.30")
check(not opens("stray", "192.168.20.30", 443), "a device untagged with an address of VLAN 20 does not")
check(opens("pc", "192.168.2.30", 22), "pc (untagged) reaches the node at 192.168.2.30 as before")
# the group of the same range keeps what iot may open (kiwi-stack's nftables kiwi_lan)
nft = sh('. "%s"; KS_STACK_LAN_RULES=("192.168.20.0/24 tcp:443"); ks_lan_nft' % lib).stdout
with open(os.path.join(work, "lan.nft"), "w") as fh:
    fh.write(nft)
sh("nft -f %s" % os.path.join(work, "lan.nft"), "node")
check(opens("iot", "192.168.20.30", 443) and not opens("iot", "192.168.20.30", 22),
      "the group iot (192.168.20.0/24: tcp:443) opens 443 on the VLAN, not 22")
sh("nft delete table inet kiwi_lan", "node")
# a changed address replaces the old one
ks_vlans("20 192.168.20.31/24")
addrs = sh("ip -o -4 addr show dev eth0.20", "node").stdout
check("192.168.20.31/24" in addrs and "192.168.20.30/" not in addrs, "a changed address replaces the old one")
# after a restart the boot script brings it back
sh("ip link del dev eth0.20", "node")
sh("%s %s" % (os.path.join(work, "sbin", "kiwi-vlans"), os.path.join(work, "etc", "vlans")), "node")
check(opens("iot", "192.168.20.31", 443), "kiwi-vlans brings it back as at boot")
# no VLANs: gone, with its files
ks_vlans()
gone = sh("ip link show dev eth0.20", "node", check_rc=False).returncode != 0
check(gone and not os.path.exists(os.path.join(work, "etc", "vlans")) and not os.path.exists(os.path.join(work, "systemd", "kiwi-vlans.service")),
      "with no VLANs the interface, its list and its unit are gone")

for p in procs:
    p.kill()
print("%d wrong" % fails)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if fails else 0)
