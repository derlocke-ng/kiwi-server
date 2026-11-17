"""RiseupVPN as a VPN client's exit, with no account (`vpn-client: {vpn_provider: riseup}`).

Riseup hands every caller an anonymous client certificate, valid for about five weeks, its CA and
the list of its gateways (api.black.riseup.net; the steps of github.com/BarbossHack/RiseupVPN-OpenVPN).
The computer that renders fetches them into the fleet's secrets (secrets/riseup/<machine>.ovpn,
sealed with the other keys) and renews the certificate when fewer than RENEW_DAYS are left; the
machine gets it as a custom OpenVPN config. A gateway that does not answer is skipped.
"""
import json
import os
import re
import socket
import ssl
import tempfile
import time
import urllib.request

from . import util
from .util import KiwiError

API = "https://api.black.riseup.net/3"
CA = "https://black.riseup.net/ca.crt"
RENEW_DAYS = 10         # the weekly update applies at least once before it runs out
_done = {}              # path -> what was said, once per run


def path_for(host):
    return "secrets/riseup/%s.ovpn" % host.name


def _get(url, accept):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "kiwi-server"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode()


def days_left(text):
    """Days until the client certificate in an .ovpn's text runs out; None without one."""
    m = re.search(r"<cert>\s*(-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----)", text or "", re.S)
    if not m:
        return None
    with tempfile.NamedTemporaryFile("w", suffix=".pem") as f:     # the certificate only: public
        f.write(m.group(1) + "\n")
        f.flush()
        try:
            end = ssl.cert_time_to_seconds(ssl._ssl._test_decode_cert(f.name)["notAfter"])
        except (ssl.SSLError, KeyError, ValueError):
            return None
    return (end - time.time()) / 86400


def _answers(ip, port):
    try:
        socket.create_connection((ip, int(port)), timeout=4).close()
        return True
    except OSError:
        return False


def gateway(service, location="", proto="tcp", answers=_answers):
    """(address, port, host, location) of the first gateway with OpenVPN over `proto` there that
    answers (a TCP connect; UDP cannot be asked), or None."""
    for g in service.get("gateways") or []:
        if location and location.lower() not in (g.get("location") or "").lower():
            continue
        for t in (g.get("capabilities") or {}).get("transport") or []:
            if t.get("type") == "openvpn" and proto in (t.get("protocols") or []) and t.get("ports"):
                port = "1194" if "1194" in t["ports"] else str(t["ports"][0])  # OpenVPN's own port before 53 and 80
                if proto == "tcp" and not answers(g["ip_address"], port):
                    util.warn("riseup: %s (%s) does not answer, the next" % (g.get("host", ""), g["ip_address"]))
                    break
                return g["ip_address"], port, g.get("host", ""), g.get("location", "")
    return None


def config_text(key, cert, ca, remote, proto="tcp"):
    return "\n".join([
        "client", "dev tun", "proto %s" % proto, "nobind", "remote %s %s" % tuple(remote[:2]),
        "persist-key", "persist-tun", "keepalive 10 20", "server-poll-timeout 10",
        "cipher AES-256-GCM", "data-ciphers AES-256-GCM", "remote-cert-tls server", "verb 1",
        "<key>", key, "</key>", "<cert>", cert, "</cert>", "<ca>", ca.strip(), "</ca>", ""])


def fetch(location="", proto="tcp", get=_get, answers=_answers):
    """A new .ovpn's text and where its gateway is."""
    try:
        key_cert = get(API + "/cert", "text/html")
        ca = get(CA, "text/html")
        service = json.loads(get(API + "/config/eip-service.json", "application/json"))
    except (OSError, ValueError) as e:
        raise KiwiError("riseup: its API does not answer (%s)" % e)
    key = re.search(r"-----BEGIN (?:RSA )?PRIVATE KEY-----.*?-----END (?:RSA )?PRIVATE KEY-----", key_cert, re.S)
    cert = re.search(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", key_cert, re.S)
    if not (key and cert and "BEGIN CERTIFICATE" in ca):
        raise KiwiError("riseup: no key, certificate or CA in its answers")
    remote = gateway(service, location, proto, answers)
    if not remote:
        raise KiwiError("riseup: no gateway answers with OpenVPN over %s%s" % (proto, " in " + location if location else ""))
    return config_text(key.group(0), cert.group(0), ca, remote, proto), remote


def ensure(full, location="", proto="tcp", get=_get, answers=_answers):
    """The .ovpn at `full` there and good for RENEW_DAYS more, fetched or renewed when not. Riseup
    unreachable with an old one still good: it stays, with a warning."""
    if full in _done:
        return
    old = util.read_text(full) if os.path.isfile(full) else ""
    left = days_left(old)
    if left is not None and left >= RENEW_DAYS:
        _done[full] = "kept"
        return
    try:
        text, remote = fetch(location, proto, get, answers)
    except KiwiError as e:
        if left is not None and left > 0:
            util.warn("%s; the certificate in %s stays, %d days left" % (e, os.path.basename(full), left))
            _done[full] = "stale"
            return
        raise
    os.makedirs(os.path.dirname(full), mode=0o700, exist_ok=True)
    fd = os.open(full + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(full + ".new", full)
    util.say("riseup: %s %s, gateway %s (%s)" % ("a new certificate in" if old else "a certificate in",
                                                  os.path.basename(full), remote[2], remote[3]))
    _done[full] = "new"


def settle(host):
    """A machine whose VPN client is Riseup's: its certificate fetched or renewed, and the block
    given to the module as the custom OpenVPN config it is."""
    raw = host.role_settings_raw or {}
    vc = raw.get("vpn-client")
    if not (isinstance(vc, dict) and vc.get("vpn_provider") == "riseup"):
        return
    vc = dict(vc)
    location = str(vc.pop("server_cities", "") or vc.pop("server_countries", "") or "")
    if str(vc.get("vpn_type") or "openvpn") != "openvpn":
        raise KiwiError("%s: vpn-client: Riseup is reached over OpenVPN only (vpn_type openvpn)" % host.name)
    rel = path_for(host)
    ensure(host.secrets.resolve_path(rel), location)
    vc.update(vpn_provider="custom", vpn_type="openvpn", openvpn_config=rel)
    raw = dict(raw)
    raw["vpn-client"] = vc
    host.role_settings_raw = raw
