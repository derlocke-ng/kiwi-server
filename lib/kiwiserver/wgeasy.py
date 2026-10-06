"""The master's wg-easy, driven from here: enrolling a node, a phone, a
router means one API call instead of a trip to the web page.

This speaks the API of the weejewel/wg-easy line, the image the vpn-server
module runs: a session cookie from the admin password, clients as JSON, the
client config as text. The newer wg-easy line has a different API; a second
class with the same four methods is the place for it.
"""
import http.cookiejar
import ipaddress
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .util import KiwiError


class WgEasy:
    def __init__(self, url, password):
        self.url = url.rstrip("/")
        self.password = password
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def _call(self, method, path, body=None, raw=False):
        data = None
        headers = {"Accept": "application/json, text/plain"}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=30) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as e:
            detail = (e.read() or b"").decode(errors="replace").strip()
            try:
                detail = json.loads(detail).get("error") or detail
            except ValueError:
                pass
            raise KiwiError("wg-easy %s %s: %s %s" % (method, path, e.code, detail[:200]))
        except urllib.error.URLError as e:
            raise KiwiError("wg-easy at %s is not reachable: %s" % (self.url, e.reason))
        if raw:
            return payload.decode(errors="replace")
        return json.loads(payload.decode() or "null") if payload else None

    def login(self):
        self._call("POST", "/api/session", {"password": self.password})
        return self

    def clients(self):
        return self._call("GET", "/api/wireguard/client") or []

    def client(self, name):
        for c in self.clients():
            if c.get("name") == name:
                return c
        return None

    def create(self, name):
        self._call("POST", "/api/wireguard/client", {"name": name})
        c = self.client(name)
        if c is None:
            raise KiwiError("wg-easy created the client %r but does not list it" % name)
        return c

    def set_address(self, client_id, address):
        self._call("PUT", "/api/wireguard/client/%s/address" % client_id, {"address": address})

    def configuration(self, client_id):
        return self._call("GET", "/api/wireguard/client/%s/configuration" % client_id, raw=True)


def next_address(subnet, taken, exclude=()):
    """The lowest free host address in a group's subnet: wg-easy hands out
    10.8.0.x itself; a group member is moved into the group's range."""
    net = ipaddress.IPv4Network(subnet)
    used = {str(a).split("/")[0] for a in taken} | set(exclude)
    for ip in net.hosts():
        if str(ip) not in used and ip != net.network_address + 1 and ip != net.network_address:
            return str(ip)
    raise KiwiError("no free address left in %s" % subnet)


def split_tunnel(config_text, allowed):
    """The client config with AllowedIPs narrowed to the mesh: a device that
    should reach the network but keep its own internet."""
    out, done = [], False
    for line in config_text.splitlines():
        if re.match(r"^\s*AllowedIPs\s*=", line):
            out.append("AllowedIPs = %s" % allowed)
            done = True
        else:
            out.append(line)
    if not done:
        raise KiwiError("the config has no AllowedIPs line")
    return "\n".join(out) + "\n"
