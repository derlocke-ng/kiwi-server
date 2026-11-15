"""Hidden services (docs/design/onion.md): their keys and addresses, from the fleet's seed.

A hidden service is one site of a machine's reverse proxy, open to anyone with Tor Browser
and the address, or only to named people who hold a key. Its Ed25519 key, and with it its
address, derives from the seed: kiwi-server knows the address before the machine is set up,
and a machine installed anew keeps it. A named person's X25519 key (Tor's client
authorization) derives from the seed too, so it can be shown again.

Nothing here talks to a machine or to Tor. The Ed25519 arithmetic is the reference one of
RFC 8032, slow and plain: a fleet has a handful of services.
"""
import base64
import hashlib
import hmac
import re

from . import federation
from .util import KiwiError

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
SECRET_HEAD = b"== ed25519v1-secret: type0 ==\x00\x00\x00"
PUBLIC_HEAD = b"== ed25519v1-public: type0 ==\x00\x00\x00"

_P = 2 ** 255 - 19
_D = -121665 * pow(121666, _P - 2, _P) % _P
_BY = 4 * pow(5, _P - 2, _P) % _P
_BX = 15112221349535400772501151409588531511454012693041857206046113283949847762202


def _add(a, b):
    (x1, y1), (x2, y2) = a, b
    k = _D * x1 * x2 * y1 * y2 % _P
    return ((x1 * y2 + x2 * y1) * pow(1 + k, _P - 2, _P) % _P, (y1 * y2 + x1 * x2) * pow(1 - k, _P - 2, _P) % _P)


def _mul(n, point):
    out = (0, 1)
    while n:
        if n & 1:
            out = _add(out, point)
        point = _add(point, point)
        n >>= 1
    return out


def _derive(seed, what):
    return hmac.new(seed, what.encode(), hashlib.sha256).digest()


def service_key(seed, host, service):
    """(expanded secret key, public key) of a hidden service: 64 and 32 bytes, as Tor keeps them."""
    h = bytearray(hashlib.sha512(_derive(seed, "onion/%s/%s" % (host, service))).digest())
    h[0] &= 248
    h[31] &= 127
    h[31] |= 64
    x, y = _mul(int.from_bytes(bytes(h[:32]), "little"), (_BX, _BY))
    return bytes(h), (y | ((x & 1) << 255)).to_bytes(32, "little")


def address(public):
    """The .onion name of a public key (version 3)."""
    check = hashlib.sha3_256(b".onion checksum" + public + b"\x03").digest()[:2]
    return base64.b32encode(public + check + b"\x03").decode().lower() + ".onion"


def client_key(seed, host, service, client):
    """(private, public) of a named person for one service, in base32 without padding, as
    Tor Browser and `authorized_clients` take them."""
    raw = bytearray(_derive(seed, "onion/%s/%s/%s" % (host, service, client)))
    raw[0] &= 248
    raw[31] &= 127
    raw[31] |= 64
    b32 = lambda b: base64.b32encode(bytes(b)).decode().rstrip("=")      # noqa: E731
    return b32(raw), b32(federation._x25519_base(bytes(raw)))


def services(host):
    """{service: [clients]} as a machine's settings have it; KiwiError for a name that is none."""
    found = ((host.module_settings or {}).get("onion") or {}).get("services") or {}
    if not isinstance(found, dict):
        raise KiwiError("%s: onion.services is a mapping, service -> {clients: [...]}" % host.name)
    out = {}
    for name, spec in found.items():
        clients = [str(c) for c in ((spec or {}).get("clients") or [])] if isinstance(spec or {}, dict) else None
        if clients is None or not NAME.match(str(name)) or not all(NAME.match(c) for c in clients):
            raise KiwiError("%s: onion.services.%s: a service of this machine, and clients: [names] in small letters" % (host.name, name))
        out[str(name)] = clients
    return out


def for_host(seed, host):
    """What the machine needs: [{service, address, secret, public, clients: [{name, public}]}],
    the keys base64 for the files Tor reads."""
    out = []
    for name, clients in services(host).items():
        secret, public = service_key(seed, host.name, name)
        out.append({"service": name, "address": address(public),
                    "secret": base64.b64encode(SECRET_HEAD + secret).decode(),
                    "public": base64.b64encode(PUBLIC_HEAD + public).decode(),
                    "clients": [{"name": c, "public": client_key(seed, host.name, name, c)[1]} for c in clients]})
    return out


def browser_line(seed, host, service, client):
    """What a named person gives Tor Browser when it asks for the key of this address."""
    return client_key(seed, host, service, client)[0]
