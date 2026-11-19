#!/usr/bin/env python3
"""Two networks with one mesh and one domain, linked and translated at the link (docs/design/
federation.md), on this computer's kernel: throwaway network namespaces in a user namespace, no
root, nothing of this computer's own network touched. Each master runs the federation part of the
start.sh kiwi-modules renders for it, over a real WireGuard link.

    gate-a (10.9.0.1, wg0 to client 10.9.0.20) ==wg-fed== gate-b (10.9.0.1, wg0 to node web 10.9.0.5)
    a sees b as 10.209.0.0/16 and b.home; b sees a as 10.210.0.0/16 and a.home; b shares web.

Checks, one PASS or FAIL line each: the client reaches web at its alias address and web sees b's
address for a (`as`); what b does not share is refused; a's own mesh is still a's; the link's DNS
answers web.b.home with web's alias address.

    python3 tests/drills/translate-netns.py --home KIWI-MODULES-CHECKOUT      (or a kiwi-server checkout)
"""
import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap

ap = argparse.ArgumentParser()
ap.add_argument("--home", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."),
                help="a checkout with lib/kiwimodules and modules/ (kiwi-modules, or kiwi-server with its vendored copy)")
ap.add_argument("--keep", action="store_true", help="leave the work folder")
opts = ap.parse_args()
home = os.path.abspath(opts.home)

if os.environ.get("KIWI_NETNS_INNER") != "1":
    # once more, inside a user namespace with its own mounts: root there, nobody here
    sys.exit(subprocess.run(["unshare", "-rnm", "--propagation", "private", sys.executable] + sys.argv,
                            env=dict(os.environ, KIWI_NETNS_INNER="1")).returncode)

sys.path.insert(0, os.path.join(home, "lib"))
os.environ["KIWI_SERVER_HOME"] = home

work = tempfile.mkdtemp(prefix="kiwi-translate-")
fails = 0


def check(ok, what):
    global fails
    print("%s %s" % ("PASS" if ok else "FAIL", what), flush=True)
    fails += 0 if ok else 1


def sh(cmd, ns=None, check_rc=True, **kw):
    argv = (["ip", "netns", "exec", ns] if ns else []) + ["sh", "-c", cmd]
    r = subprocess.run(argv, capture_output=True, text=True, **kw)
    if check_rc and r.returncode:
        raise SystemExit("%s: %s\n%s%s" % (ns or "here", cmd, r.stdout, r.stderr))
    return r


def wg_private(secret):          # kiwi-modules' wg_key: the private key is SHA-256 of the secret
    return base64.b64encode(hashlib.sha256(secret.encode()).digest()).decode()


def wg_public(secret):
    return subprocess.run(["wg", "pubkey"], input=wg_private(secret), capture_output=True, text=True, check=True).stdout.strip()


def master(name, me, other, seen_mesh, seen_domain, share):
    """A master's machine file: mesh 10.9, domain home, linked to `other` seen as `seen_mesh`."""
    return {"name": name, "domain": "home", "target": "debian", "role": "master", "seed": "secrets/seed",
            "dns_records": [["10.9.0.1", "%s.home" % name], ["10.9.0.5", "web.home"]],
            "master": {"modules": ["vpn-server", "dns"], "mesh_subnet": "10.9.0.0/16",
                       "vpn-server": {"wg_host": me["ip"], "server_ip": "10.9.0.1", "wg_subnet": "10.9.0.0/24",
                                      "wg_default_address": "10.9.0.x", "federation_secret": me["secret"],
                                      "federation": {other["name"]: {
                                          "endpoint": other["ip"], "public_key": wg_public(other["secret"]),
                                          "mesh": "10.9.0.0/16", "domain": "home", "dns": "10.9.0.1",
                                          "share": share, "from": [], "as": "10.9.255.1", "exit": False, "internet": False,
                                          "seen_as": {"mesh": seen_mesh, "domain": seen_domain,
                                                      "dns": seen_mesh.replace(".0.0/16", ".0.1"), "link": "10.9.255.254"}}}}}}


def render(m):
    d = os.path.join(work, m["name"])
    os.makedirs(os.path.join(d, "secrets"))
    with open(os.path.join(d, "secrets", "seed"), "wb") as fh:
        fh.write(b"S" * 32)
    path = os.path.join(d, "machine.json")
    with open(path, "w") as fh:
        json.dump(m, fh)
    out = os.path.join(d, "out")
    with open(os.devnull, "w") as null:
        rc = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); from kiwimodules import cli; "
                             "sys.exit(cli.main(sys.argv[1:]))" % os.path.join(home, "lib"), "render", path, "-o", out],
                            stdout=null, stderr=subprocess.PIPE, text=True, env=dict(os.environ, KIWI_SERVER_HOME=home))
    if rc.returncode:
        raise SystemExit("render %s: %s" % (m["name"], rc.stderr[-800:]))
    with open(os.path.join(out, "km-vpn-server", "start.sh")) as fh:
        start = fh.read()
    link = start[start.index("# federation (docs/design/federation.md"):]
    return link, out


a = {"name": "gate-a", "ip": "192.0.2.1", "secret": "drill-secret-a"}
b = {"name": "gate-b", "ip": "192.0.2.2", "secret": "drill-secret-b"}
link_a, out_a = render(master("gate-a", a, b, "10.209.0.0/16", "gate-b.home", []))
link_b, out_b = render(master("gate-b", b, a, "10.210.0.0/16", "gate-a.home", ["web:80"]))

# ---- the namespaces: two masters, a client of a, b's node web, the Internet between the masters ----
sh("mount -t tmpfs none /run && mkdir -p /run/netns")
for ns in ("A", "B", "client", "web", "mine"):
    sh("ip netns add %s" % ns)
    sh("ip link set lo up", ns)
sh("ip link add name eth0 netns A type veth peer name eth0 netns B")
sh("ip addr add 192.0.2.1/24 dev eth0 && ip link set eth0 up", "A")
sh("ip addr add 192.0.2.2/24 dev eth0 && ip link set eth0 up", "B")
sh("ip link add name wg0 netns A type veth peer name eth0 netns client")
sh("ip link add name wg0 netns B type veth peer name eth0 netns web")
sh("ip link add name wgn netns A type veth peer name eth0 netns mine")       # a's own node, at b's web's address
sh("ip addr add 10.9.0.1/32 dev wg0 && ip link set wg0 up && ip route add 10.9.0.20/32 dev wg0 && "
   "ip addr add 10.9.0.1/32 dev wgn && ip link set wgn up && ip route add 10.9.0.5/32 dev wgn && "
   "echo 1 > /proc/sys/net/ipv4/ip_forward", "A")
sh("ip addr add 10.9.0.1/32 dev wg0 && ip link set wg0 up && ip route add 10.9.0.0/16 dev wg0 && "
   "echo 1 > /proc/sys/net/ipv4/ip_forward", "B")
# a WireGuard peer sends everything to its master: /32 and the master as the way (no ARP for the mesh)
sh("ip addr add 10.9.0.20/32 dev eth0 && ip link set eth0 up && ip route add 10.9.0.1/32 dev eth0 && "
   "ip route add default via 10.9.0.1", "client")
sh("ip addr add 10.9.0.5/32 dev eth0 && ip link set eth0 up && ip route add 10.9.0.1/32 dev eth0 && "
   "ip route add default via 10.9.0.1", "web")
sh("ip addr add 10.9.0.5/32 dev eth0 && ip link set eth0 up && ip route add 10.9.0.1/32 dev eth0 && "
   "ip route add default via 10.9.0.1", "mine")

# ---- the link, as each master's start.sh sets it ----------------------------------------------------
for ns, script in (("A", link_a), ("B", link_b)):
    path = os.path.join(work, "link-%s.sh" % ns)
    with open(path, "w") as fh:
        fh.write(script)            # as the container runs it: no set -e, a missing thing is not an error
    r = sh("bash %s" % path, ns, check_rc=False)
    if os.environ.get("KIWI_DRILL_DEBUG"):
        print("---- gate-%s stderr:\n%s" % (ns.lower(), r.stderr))
    said = [x for x in r.stderr.splitlines() if x.strip() and "Cannot find device" not in x and "No such file" not in x
            and "FIB table does not exist" not in x and "Flush terminated" not in x]
    check(not said, "gate-%s runs its link%s" % (ns.lower(), (": " + said[0][:140]) if said else ""))
up = sh("wg show wg-fed latest-handshakes", "A", check_rc=False).stdout
sh("ip netns exec client ping -c1 -W2 10.209.0.1 >/dev/null 2>&1 || true")      # wakes the link

# ---- web answers with who it sees ---------------------------------------------------------------------
server = textwrap.dedent('''
    import http.server, sys
    WHO = sys.argv[1]
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write((WHO + self.client_address[0]).encode())
        def log_message(self, *a): pass
    for port in (80, 81):
        import threading
        s = http.server.HTTPServer(("10.9.0.5", port), H)
        threading.Thread(target=s.serve_forever, daemon=True).start()
    import time; time.sleep(600)
''')
web = subprocess.Popen(["ip", "netns", "exec", "web", sys.executable, "-c", server, "b-web:"])
mine = subprocess.Popen(["ip", "netns", "exec", "mine", sys.executable, "-c", server, "a-own:"])
import time  # noqa: E402
time.sleep(1)


def get(url):
    return sh("curl -s -m 6 %s" % url, "client", check_rc=False).stdout.strip()


got = get("http://10.209.0.5/")
check(got == "b-web:10.9.255.1", "the client reaches b's web at 10.209.0.5 (its 10.9.0.5) and web sees b's address for a: %r" % got)
got2 = get("http://10.9.0.5/")
check(got2 == "a-own:10.9.0.20", "10.9.0.5 is still a's own node for the client: %r" % got2)
got3 = get("http://10.209.0.5:81/")
check(got3 == "", "a port b does not share (81) answers nothing")
if got != "b-web:10.9.255.1" and os.environ.get("KIWI_DRILL_DEBUG"):
    for ns in ("A", "B"):
        print("==== %s" % ns)
        for cmd in ("iptables -t mangle -S -v", "iptables -t nat -S -v", "iptables -S KIWI_FED -v", "wg show wg-fed transfer",
                    "ip rule", "ip route show table all | grep -v local", "ip -br link", "cat /proc/sys/net/ipv4/conf/all/rp_filter /proc/sys/net/ipv4/conf/wg-fed/rp_filter"):
            print("$ " + cmd)
            print(sh(cmd, ns, check_rc=False).stdout + sh(cmd, ns, check_rc=False).stderr)
    print(sh("ss -ltn", "web", check_rc=False).stdout)
up = sh("wg show wg-fed latest-handshakes", "A", check_rc=False).stdout.split()
check(len(up) == 2 and up[1] != "0", "the WireGuard link between the masters is up")
own = sh("ip netns exec client ping -c1 -W2 10.9.0.1", check_rc=False).returncode
check(own == 0, "a's own 10.9.0.1 is still a's (the client pings its master)")
# a port b does not share: the share is the whole node web here, so ask for a node b does not have
got = get("http://10.209.0.6/")
check(got == "", "an address b does not share answers nothing")

# ---- the link's DNS: web.gate-b.home through a's fed-dns, from b's DNS -----------------------------
dns_b = textwrap.dedent('''
    import socket, struct, sys
    sys.path.insert(0, %r)
    import feddns
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("10.9.0.1", 53))
    while True:
        data, peer = s.recvfrom(4096)
        m = feddns.parse(data)
        name = b".".join(m["qs"][0][0]).decode()
        m["flags"] |= 0x8000
        if name == "web.home":
            m["rrs"] = [[1, m["qs"][0][0], 1, 1, 60, ("raw", socket.inet_aton("10.9.0.5"))]]
        s.sendto(feddns.build(m), peer)
''') % work
fd_src = os.path.join(out_a, "km-vpn-server", "fed-dns", "fed-dns.py")
shutil.copy(fd_src, os.path.join(work, "feddns.py"))
dnsb = subprocess.Popen(["ip", "netns", "exec", "B", sys.executable, "-c", dns_b], stderr=subprocess.PIPE, text=True)
dnsa = subprocess.Popen(["ip", "netns", "exec", "A", sys.executable, fd_src,
                         os.path.join(out_a, "km-vpn-server", "fed-dns", "fed-dns.json")], stderr=subprocess.PIPE, text=True)
time.sleep(1.5)
ans = sh("dig +short +time=3 +tries=1 @127.0.0.1 -p 5353 web.gate-b.home", "A", check_rc=False).stdout.strip()
check(ans == "10.209.0.5", "a's link DNS answers web.gate-b.home with 10.209.0.5 (b's DNS said 10.9.0.5): %r" % ans)
if ans != "10.209.0.5" and os.environ.get("KIWI_DRILL_DEBUG"):
    for ns, cmds in (("A", ("iptables -t nat -S -v", "iptables -t mangle -S -v", "ss -lun")), ("B", ("iptables -S INPUT -v", "iptables -t mangle -S -v", "ss -lun"))):
        for cmd in cmds:
            print("==== %s $ %s\n%s" % (ns, cmd, sh(cmd, ns, check_rc=False).stdout))
    for p in (dnsa, dnsb):
        p.kill()
        print("stderr:", p.stderr.read()[-600:])
ans = sh("dig +short +time=3 +tries=1 @127.0.0.1 -p 5353 web.home", "A", check_rc=False).stdout.strip()
check(ans == "", "a name outside the alias is not the link's to answer")

for p in (web, mine, dnsa, dnsb):
    p.kill()
print("%d wrong" % fails)
if not opts.keep:
    shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if fails else 0)
