"""The master's name at a dynamic DNS provider, pointed at its standby while it is gone
(docs/design/standby.md, step 4).

Phones and laptops have the master's address in their WireGuard config as `vpn-server.wg_host`.
When that is a name at a dynamic DNS provider, the control node points it at the standby and back.
`secrets/ddns.url` holds the provider's update address, with `{address}` where the new address
goes and `{name}` for the name: one line, kept in the store like the other keys. Most providers
take one, e.g.

    https://www.duckdns.org/update?domains=mykiwi&token=TOKEN&ip={address}
    https://USER:TOKEN@update.dedyn.io/?hostname={name}&myipv4={address}
    https://USER:PASS@dynupdate.no-ip.com/nic/update?hostname={name}&myip={address}
"""
import base64
import ipaddress
import socket
import urllib.parse
import urllib.request

from . import util
from .util import KiwiError

FILE = "secrets/ddns.url"
REFUSED = ("bad", "nohost", "notfqdn", "abuse", "badauth", "badagent", "dnserr", "911", "ko", "error")


def template(fleet):
    """The update address with its placeholders, or "" when none is set."""
    p = fleet.resolve_path(FILE)
    try:
        return util.read_text(p).strip()
    except OSError:
        return ""


def is_name(host):
    try:
        ipaddress.ip_address(str(host))
        return False
    except ValueError:
        return bool(str(host or "").strip())


def _open(url, timeout=20):
    parts = urllib.parse.urlsplit(url)
    headers = {"User-Agent": "kiwi-server"}
    if parts.username:          # user:token@host: as basic auth, not in the request line
        cred = "%s:%s" % (urllib.parse.unquote(parts.username), urllib.parse.unquote(parts.password or ""))
        headers["Authorization"] = "Basic " + base64.b64encode(cred.encode()).decode()
        url = urllib.parse.urlunsplit((parts.scheme, parts.hostname + (":%d" % parts.port if parts.port else ""),
                                       parts.path, parts.query, parts.fragment))
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        return r.status, r.read(400).decode(errors="replace")


def point(fleet, name, address, opener=_open, resolve=socket.gethostbyname):
    """Point `name` at `address`; the address it had before (or "")."""
    tpl = template(fleet)
    if not tpl:
        raise KiwiError("no %s: the provider's update address is not set" % FILE)
    if not tpl.lower().startswith("https://"):
        raise KiwiError("%s: the update address must be https (it carries the provider's token)" % FILE)
    try:
        before = resolve(name)
    except OSError:
        before = ""
    url = tpl.replace("{address}", urllib.parse.quote(str(address))).replace("{name}", urllib.parse.quote(str(name)))
    try:
        status, body = opener(url)
    except OSError as e:        # never the address itself in a message: it carries the token
        raise KiwiError("the dynamic DNS provider did not answer (%s)" % type(e).__name__)
    word = body.strip().split()[0].lower() if body.strip() else ""
    if not 200 <= status < 300 or word.startswith(REFUSED):
        raise KiwiError("the dynamic DNS provider refused the change (%d %s)" % (status, word[:20]))
    return before
