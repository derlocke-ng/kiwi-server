#!/usr/bin/env python3
"""An OpenVPN config for RiseupVPN, for a master's exit in tests (vpn-client: vpn_provider custom,
vpn_type openvpn, openvpn_config: <this file>). The steps of github.com/BarbossHack/RiseupVPN-OpenVPN,
done here: Riseup's own API hands every caller an anonymous client certificate (no account), its CA,
and the list of its gateways.

    tests/drills/riseup-ovpn.py OUT.ovpn [--location paris] [--proto tcp|udp]

OUT holds a private key: write it into a fleet's secrets/ (a sealed store), never next to code.
"""
import argparse
import json
import os
import re
import socket
import sys
import urllib.request

API = "https://api.black.riseup.net/3"
CA = "https://black.riseup.net/ca.crt"

a = argparse.ArgumentParser()
a.add_argument("out")
a.add_argument("--location", default="", help="a gateway location (amsterdam, paris, ...); empty: the first")
a.add_argument("--proto", default="tcp", choices=("tcp", "udp"))
o = a.parse_args()


def get(url, accept):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "kiwi-server drill"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode()


key_cert = get(API + "/cert", "text/html")
key = re.search(r"-----BEGIN RSA PRIVATE KEY-----.*?-----END RSA PRIVATE KEY-----", key_cert, re.S)
cert = re.search(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", key_cert, re.S)
ca = get(CA, "text/html").strip()
if not (key and cert and "BEGIN CERTIFICATE" in ca):
    sys.exit("riseup: no key, certificate or CA in the answers")
service = json.loads(get(API + "/config/eip-service.json", "application/json"))
remote = None
for g in service.get("gateways", []):
    if o.location and o.location.lower() not in (g.get("location") or "").lower():
        continue
    for t in (g.get("capabilities") or {}).get("transport", []):
        if t.get("type") == "openvpn" and o.proto in (t.get("protocols") or []) and t.get("ports"):
            port = "1194" if "1194" in t["ports"] else t["ports"][0]     # OpenVPN's own port before 53 and 80
            if o.proto == "tcp":        # some gateways do not answer at all: the first that does
                try:
                    socket.create_connection((g["ip_address"], int(port)), timeout=4).close()
                except OSError:
                    print("riseup: %s (%s) does not answer, the next" % (g.get("host", ""), g["ip_address"]), file=sys.stderr)
                    break
            remote = (g["ip_address"], port, g.get("host", ""), g.get("location", ""))
            break
    if remote:
        break
if not remote:
    sys.exit("riseup: no gateway with OpenVPN over %s%s" % (o.proto, " in " + o.location if o.location else ""))
text = "\n".join([
    "client", "dev tun", "proto %s" % o.proto, "nobind",
    "remote %s %s" % remote[:2],
    "persist-key", "persist-tun", "keepalive 10 20", "server-poll-timeout 10",
    "cipher AES-256-GCM", "data-ciphers AES-256-GCM", "remote-cert-tls server", "verb 1",
    "<key>", key.group(0), "</key>", "<cert>", cert.group(0), "</cert>", "<ca>", ca, "</ca>", ""])
fd = os.open(o.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(text)
print("riseup: %s, gateway %s (%s), %s %s" % (o.out, remote[2], remote[3], o.proto, remote[1]))
