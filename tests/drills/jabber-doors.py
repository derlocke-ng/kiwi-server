#!/usr/bin/env python3
"""Jabber's doors, tried for real: the Internet, the VPN and Tor, with one account.

For a machine that runs the xmpp module. It makes a throwaway account on the service
(ejabberdctl, on the machine), logs in with it the way an app does (STARTTLS or TLS, then
name and password) through each door, looks at the page for new accounts and its captcha
picture, asks the service which address it saw the visitor from, and removes the account.

    tests/drills/jabber-doors.py ~/kiwi/fleet.yaml medlar --domain xmpp.dlhost.eu \\
        --node core@192.168.2.63 --master core@5.45.103.177 --master-port 41022 \\
        --public 5.45.103.177 --vpn-via user@192.168.2.104 --onion-site jabber.medlar

--public is the address the public name is tried at (never taken from DNS: a wrong record
must not send the drill to another machine). --vpn-via is a device on the VPN (its DNS and
gateway). Tor runs on the master only, in a container that is removed again; this computer
reaches it through ssh, so no Tor traffic leaves from here. Two instances: --instance
kn-xmpp-public with the public name, and again without it for the VPN's.
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
import time

ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument("fleet"); ap.add_argument("machine")
ap.add_argument("--domain", required=True, help="what comes after the @ at this instance")
ap.add_argument("--internal", help="the name on the VPN (default: jabber.<machine>.home); 'none' to skip that door")
ap.add_argument("--node", required=True, help="user@address of the machine, for ssh")
ap.add_argument("--node-port", default="22")
ap.add_argument("--master", help="user@address of the master, for Tor; without it that door is skipped")
ap.add_argument("--master-port", default="22")
ap.add_argument("--public", help="the address the public name is tried at; without it that door is skipped")
ap.add_argument("--vpn-via", help="user@address of a device on the VPN; without it the VPN door is skipped")
ap.add_argument("--onion-site", help="the hidden service, as `kiwi-server onion` names it (jabber.medlar)")
ap.add_argument("--instance", default="", help="the container (default: <prefix>-xmpp)")
args = ap.parse_args()
ks = os.environ.get("KIWI_SERVER", "kiwi-server")
fdir = os.path.dirname(os.path.abspath(args.fleet))
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
       "-o", "LogLevel=ERROR", "-o", "UserKnownHostsFile=%s" % os.path.join(fdir, "secrets", "known_hosts"),
       "-i", os.path.join(fdir, "secrets", "admin_ed25519")]
rows, fails = [], 0


def say(verdict, door, what):
    global fails
    fails += verdict == "FAIL"
    print("%-5s %-9s %s" % (verdict, door, what), flush=True)


def on(login, port, cmd, stdin="", timeout=120):
    return subprocess.run(SSH + ["-p", str(port), login, cmd], input=stdin, capture_output=True, text=True, timeout=timeout)


# runs here, or on the device on the VPN: plain Python
CHECK = r'''
import base64, json, socket, ssl, sys, time, urllib.request
q = json.loads(sys.stdin.readline())
out = {}
def connect():
    if q.get("socks"):                      # through Tor: the name goes to the proxy, not to a resolver
        s = socket.create_connection(tuple(q["socks"]), 30)
        s.sendall(b"\x05\x01\x00"); s.recv(2)
        name = q["connect"][0].encode()
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(name)]) + name + int(q["connect"][1]).to_bytes(2, "big"))
        s.settimeout(90)
        r = s.recv(10)
        if len(r) < 2 or r[1] != 0:
            raise OSError("the proxy could not reach it (%s)" % (r[1] if len(r) > 1 else "?"))
        return s
    return socket.create_connection(tuple(q["connect"]), 20)
def until(s, mark):
    buf = b""
    while mark not in buf:
        d = s.recv(4096)
        if not d:
            break
        buf += d
    return buf.decode("utf-8", "replace")
ctx = ssl.create_default_context()
if not q.get("verify", True):
    ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
dom = q["domain"]
head = ("<?xml version='1.0'?><stream:stream to='%s' xmlns='jabber:client' "
        "xmlns:stream='http://etherx.jabber.org/streams' version='1.0'>" % dom).encode()
try:
    s = connect()
    if not q.get("direct_tls"):
        s.sendall(head); until(s, b"</stream:features>")
        s.sendall(b"<starttls xmlns='urn:ietf:params:xml:ns:xmpp-tls'/>"); until(s, b"proceed")
    s = ctx.wrap_socket(s, server_hostname=dom)
    s.sendall(head); until(s, b"</stream:features>")
    s.sendall(("<auth xmlns='urn:ietf:params:xml:ns:xmpp-sasl' mechanism='PLAIN'>%s</auth>"
               % base64.b64encode(("\0%s\0%s" % (q["user"], q["pw"])).encode()).decode()).encode())
    said = until(s, b">")
    out["login"] = "PASS" if "<success" in said else "FAIL"
    out["why"] = "logged in" if "<success" in said else "not authorized"
    if out["login"] == "PASS" and q.get("hold"):
        s.sendall(head); until(s, b"</stream:features>")
        s.sendall(b"<iq type='set' id='b1'><bind xmlns='urn:ietf:params:xml:ns:xmpp-bind'><resource>drill</resource></bind></iq>")
        until(s, b"</iq>")
        print(json.dumps({"held": True}), flush=True)
        time.sleep(q["hold"])
    s.close()
except ssl.SSLCertVerificationError as e:
    out["login"], out["why"] = "FAIL", "certificate: %s" % e.verify_message
except (OSError, ValueError) as e:
    out["login"], out["why"] = "FAIL", "%s: %s" % (type(e).__name__, e)
if q.get("page"):
    c = ssl.create_default_context()
    if not q.get("verify_page", True):
        c.check_hostname = False; c.verify_mode = ssl.CERT_NONE
    host, port = q["page_at"]
    pinned = q["page"].split("//")[1].split("/")[0]
    class At(urllib.request.HTTPSHandler):          # the name's page at the address given, not at what DNS says
        def https_open(self, req):
            import http.client
            class C(http.client.HTTPSConnection):
                def connect(self2):        # only that name: a picture under another name is found as a browser finds it
                    at = (host, port) if self2.host == pinned else (self2.host, self2.port)
                    self2.sock = c.wrap_socket(socket.create_connection(at, 20), server_hostname=self2.host)
            return self.do_open(C, req)
    op = urllib.request.build_opener(At(context=c))
    try:
        with op.open(q["page"] + "/register/new", timeout=30) as r:
            body = r.read(300000).decode("utf-8", "replace")
        out["page"] = 200
        import re
        m = re.search(r'<img[^>]+src=["\']([^"\']*captcha[^"\']*)["\']', body)
        out["captcha"] = False
        if m:
            src = m.group(1)
            if src.startswith("/"):
                src = q["page"] + src
            with op.open(src, timeout=30) as r:
                out["captcha"] = r.status == 200 and r.headers.get("content-type", "").startswith("image/")
    except Exception as e:
        out["page"] = "%s: %s" % (type(e).__name__, str(e)[:80])
print(json.dumps(out))
'''
RUN = "import sys; exec(sys.stdin.readline().encode().decode('unicode_escape'))"


def check(q, via=None, wait=True):
    feed = CHECK.encode("unicode_escape").decode() + "\n" + json.dumps(q) + "\n"
    argv = (SSH[:-4] + ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-i", SSH[-1], via,
                        'python3 -c "%s"' % RUN]) if via else [sys.executable, "-c", RUN]
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    p.stdin.write(feed); p.stdin.close()
    if not wait:
        return p
    p.wait(180)
    lines = p.stdout.read().strip().splitlines()
    return json.loads(lines[-1]) if lines else {"login": "FAIL", "why": p.stderr.read().strip()[-200:] or "no answer"}


# the machine, its container and a throwaway account
r = on(args.node, args.node_port, "sudo sh -c 'command -v podman || command -v docker'")
rt = os.path.basename((r.stdout.strip().splitlines() or ["podman"])[-1])
prefix = "km" if on(args.node, args.node_port, "sudo %s ps --format '{{.Names}}' | grep -c '^km-'" % rt).stdout.strip() not in ("", "0") else "kn"
box = args.instance or "%s-xmpp" % prefix
user, pw = "drill-%s" % secrets.token_hex(3), "k" + secrets.token_urlsafe(18)     # never a leading dash: su would read it as an option
ctl = "sudo %s exec -i %s su ejabberd -s /bin/sh -c" % (rt, box)
r = on(args.node, args.node_port, "read pw; %s 'ejabberdctl register \"$0\" \"$1\" \"$2\"' %s %s \"$pw\"" % (ctl, user, args.domain),
       stdin=pw + "\n")
if r.returncode != 0:
    sys.exit("drill: no account on %s (%s): %s" % (box, args.domain, (r.stderr or r.stdout).strip()[-200:]))
say("ok", "account", "%s@%s made on %s (%s)" % (user, args.domain, args.machine, box))
me = {"domain": args.domain, "user": user, "pw": pw}
try:
    # ---- the Internet ----------------------------------------------------------------
    if args.public:
        a = check(dict(me, connect=[args.public, 5222], page="https://" + args.domain, page_at=[args.public, 443]))
        say(a["login"], "internet", "5222 at %s: %s" % (args.public, a["why"]))
        say("PASS" if a.get("page") == 200 and a.get("captcha") else "FAIL", "internet",
            "the page for new accounts: %s, captcha picture: %s" % (a.get("page"), a.get("captcha")))
        a = check(dict(me, connect=[args.public, 5223], direct_tls=True))
        say(a["login"], "internet", "5223 (TLS at once): %s" % a["why"])
        # who does the service see? held open while it is asked
        p = check(dict(me, connect=[args.public, 5222], hold=12), wait=False)
        p.stdout.readline()
        seen = on(args.node, args.node_port, "%s 'ejabberdctl connected_users_info'" % ctl).stdout
        line = next((x for x in seen.splitlines() if user in x), "")
        ip = next((w for w in line.split() if w.count(".") == 3 or ":" in w and "@" not in w), "")
        p.wait(60)
        inside = ip.startswith(("10.", "127.", "172.", "192.168.")) or ip in ("", "::1")
        say("FAIL" if inside else "PASS", "internet",
            "the service sees the visitor's own address (%s)" % (ip if inside else ip.split(".")[0] + ".x.x.x" if "." in ip else "an IPv6 address"))
        w = check(dict(me, pw="wrong-" + pw, connect=[args.public, 5222]))
        say("PASS" if w["login"] == "FAIL" and "not authorized" in w["why"] else "FAIL", "internet", "a wrong password is refused")
    # ---- the VPN ---------------------------------------------------------------------
    internal = args.internal or "jabber.%s.home" % args.machine
    if args.vpn_via and internal != "none":
        a = check(dict(me, connect=[internal, 5222], verify=False, page="https://" + internal, page_at=[internal, 443],
                       verify_page=False), via=args.vpn_via)
        say(a["login"], "vpn", "5222 at %s from %s: %s" % (internal, args.vpn_via.split("@")[-1], a["why"]))
        say("PASS" if a.get("page") == 200 and a.get("captcha") else "FAIL", "vpn",
            "the page for new accounts: %s, captcha picture: %s" % (a.get("page"), a.get("captcha")))
    # ---- Tor --------------------------------------------------------------------------
    if args.master and args.onion_site:
        r = subprocess.run([ks, "onion", args.fleet], capture_output=True, text=True)
        onion = next((w.replace("http://", "").rstrip("/") for x in (r.stdout + r.stderr).splitlines()
                      if x.split() and x.split()[0] == args.onion_site for w in x.split() if ".onion" in w), "")
        if not onion:
            say("FAIL", "tor", "%s is no hidden service (kiwi-server onion)" % args.onion_site)
        else:
            up = """D=$(mktemp -d); mkdir -p $D/data
printf 'SocksPort 127.0.0.1:9150\\nDataDirectory /data\\n' > $D/torrc
chown -R 101:102 $D/data
podman rm -f kiwi-drill-tor >/dev/null 2>&1
podman run -d --rm --name kiwi-drill-tor --network host --entrypoint su-exec -v $D/torrc:/torrc:ro,z \\
    -v $D/data:/data:z docker.io/dockurr/tor tor tor -f /torrc >/dev/null
for i in $(seq 30); do podman logs kiwi-drill-tor 2>&1 | grep -q 'Bootstrapped 100' && break; sleep 5; done
podman logs kiwi-drill-tor 2>&1 | grep -c 'Bootstrapped 100'
"""
            # the script on the connection's input: no quoting to get wrong on the way
            ready = on(args.master, args.master_port, "sudo sh -s", stdin=up, timeout=300).stdout.strip()
            tunnel = subprocess.Popen(SSH + ["-p", str(args.master_port), "-N", "-L", "127.0.0.1:19150:127.0.0.1:9150", args.master])
            try:
                time.sleep(4)
                if not ready or ready.splitlines()[-1] == "0":
                    say("FAIL", "tor", "Tor on the master did not get ready")
                else:
                    a = {"login": "FAIL", "why": "?"}
                    for _ in range(4):               # a hidden service takes a few minutes to be known
                        a = check(dict(me, connect=[onion, 5222], verify=False, socks=["127.0.0.1", 19150]))
                        if a["login"] == "PASS":
                            break
                        time.sleep(45)
                    say(a["login"], "tor", "5222 at %s….onion: %s" % (onion[:8], a["why"]))
                    c = on(args.master, args.master_port,
                           "for i in 1 2 3 4; do n=$(curl -sL --max-time 90 --socks5-hostname 127.0.0.1:9150 "
                           "http://%s/register/new/ | grep -c captcha); [ \"$n\" != 0 ] && break; sleep 20; done; echo $n" % onion,
                           timeout=500).stdout.strip()
                    say("PASS" if c not in ("", "0") else "FAIL", "tor", "the page for new accounts with its captcha")
            finally:
                tunnel.terminate()
                on(args.master, args.master_port, "sudo podman rm -f kiwi-drill-tor >/dev/null 2>&1")
finally:
    r = on(args.node, args.node_port, "%s 'ejabberdctl unregister \"$0\" \"$1\"' %s %s" % (ctl, user, args.domain))
    say("ok" if r.returncode == 0 else "FAIL", "account", "removed again")
print("%d failed" % fails)
sys.exit(1 if fails else 0)
