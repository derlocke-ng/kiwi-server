"""Federation: two networks linked over their masters (docs/design/federation.md).

`federation.yaml` next to the fleet file names the partners and what each may reach here:

    kiwi2:
      endpoint: 188.68.38.110        # the partner master's wg_host
      public_key: ...                # its link key (kiwi-server federate fills both in)
      mesh: 10.10.0.0/16
      domain: internal
      dns: 10.10.0.1
      share: [cloud.papaya, lychee, papaya:22]
      from: [10.10.3.0/24]           # empty: the partner's whole mesh
      exit: true                     # this network leaves to the internet through that partner …
      internet: true                 # … which that partner allows with this, in its own file

The exit rides the link: the master is not a client of the partner's VPN server.

The master's vpn-server and the nodes' reverse-proxy get their settings from it at load time.
"""
import base64
import hashlib
import ipaddress
import os

import yaml

from . import util
from .util import KiwiError

FILE = "federation.yaml"
KEYS = ("endpoint", "public_key", "mesh", "domain", "dns", "share", "from", "as", "exit", "internet")

_P = 2 ** 255 - 19


def _x25519_base(k):
    """X25519(k, 9): the public key of a private key, RFC 7748, constant-free Python."""
    k = bytearray(k)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    n = int.from_bytes(k, "little")
    x1, x2, z2, x3, z3, swap = 9, 1, 0, 9, 1, 0
    for t in reversed(range(255)):
        bit = (n >> t) & 1
        swap ^= bit
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = bit
        a, b = (x2 + z2) % _P, (x2 - z2) % _P
        aa, bb = a * a % _P, b * b % _P
        e = (aa - bb) % _P
        c, d = (x3 + z3) % _P, (x3 - z3) % _P
        da, cb = d * a % _P, c * b % _P
        x3, z3 = (da + cb) ** 2 % _P, x1 * (da - cb) ** 2 % _P
        x2, z2 = aa * bb % _P, e * (aa + 121665 * e) % _P
    if swap:
        x2, z2 = x3, z3
    return (x2 * pow(z2, _P - 2, _P) % _P).to_bytes(32, "little")


def public_key(secret):
    """The link's public key for a federation_secret (the private key is SHA-256 of it, as
    kiwi-modules' wg_key filter makes it)."""
    return base64.b64encode(_x25519_base(hashlib.sha256(str(secret).encode()).digest())).decode()


def partners(fleet_dir):
    p = os.path.join(fleet_dir, FILE)
    if not os.path.isfile(p):
        return {}
    try:
        data = yaml.safe_load(util.read_text(p)) or {}
    except yaml.YAMLError as e:
        raise KiwiError("%s: not valid YAML: %s" % (p, e))
    if not isinstance(data, dict):
        raise KiwiError("%s: a partner name per entry" % p)
    out = {}
    for name, spec in data.items():
        spec = dict(spec or {})
        missing = [k for k in ("endpoint", "public_key", "mesh") if not spec.get(k)]
        if missing:
            raise KiwiError("%s: %s lacks %s" % (p, name, ", ".join(missing)))
        spec["share"] = [str(x) for x in spec.get("share") or []]
        spec["from"] = [str(x) for x in spec.get("from") or []]
        for k in ("exit", "internet"):
            if spec.get(k) not in (None, True, False):
                raise KiwiError("%s: %s: %s is true or false" % (p, name, k))
            spec[k] = bool(spec.get(k))
        if spec["exit"] and spec["internet"]:
            raise KiwiError("%s: %s is both this network's way to the internet (exit) and let out through it (internet): "
                            "two networks cannot leave through each other" % (p, name))
        out[str(name)] = {k: spec.get(k) for k in KEYS}
    exits = [n for n, x in out.items() if x["exit"]]
    if len(exits) > 1:
        raise KiwiError("%s: only one partner can be this network's way to the internet (exit: true for %s)"
                        % (p, ", ".join(exits)))
    return out


def exit_through(fleet_dir):
    """The partner this network leaves to the internet through, or None."""
    return next((n for n, x in partners(fleet_dir).items() if x["exit"]), None)


def with_addresses(found, mesh):
    """Each partner's `as`, where it is not given: the last /24 of this mesh, one address per
    partner in the order of their names (10.9.255.1, .2, … in 10.9.0.0/16)."""
    net = ipaddress.ip_network(mesh, strict=False)
    base = int(net.broadcast_address) - 255
    for i, name in enumerate(sorted(found)):
        if not found[name].get("as"):
            found[name]["as"] = str(ipaddress.ip_address(base + i + 1))
    return found


def node_rules(found, host, domain):
    """reverse-proxy's federation setting for one node: per shared name, which partners may
    open it (by the address of this mesh their connections come from, `as`); the partners not
    given this whole node are shut out of every other name. Who of a partner may come at all
    (`from`) the master decides."""
    names, deny = {}, []
    for p in found.values():
        whole = any(e == host or e.startswith(host + ":") for e in p["share"])
        if not whole:
            deny.append(p["as"])
    for p in found.values():
        for e in p["share"]:
            if "." in e and e.split(".")[-1] == host:
                n = names.setdefault("%s.%s" % (e, domain), {"allow": [], "deny": deny})
                if p["as"] not in n["allow"]:
                    n["allow"].append(p["as"])
    return {"names": names, "deny": deny} if (names or deny) else {}


def inject(fleet_dir, data):
    """Fill the settings in from federation.yaml before the fleet's hosts are made."""
    found = partners(fleet_dir)
    if not found or not isinstance(data, dict):
        return
    defaults = data.get("defaults") or {}
    domain = defaults.get("domain") or "home"
    with_addresses(found, (defaults.get("master") or {}).get("mesh_subnet") or "10.8.0.0/16")
    for name, cfg in (data.get("hosts") or {}).items():
        if not isinstance(cfg, dict):
            continue
        role = cfg.get("role") or defaults.get("role") or "bare"
        if role == "bare":
            continue
        block = cfg.get(role) if isinstance(cfg.get(role), dict) else {}
        cfg[role] = block
        if role == "master":
            block.setdefault("vpn-server", {})
            block["vpn-server"] = dict(block["vpn-server"] or {}, federation=found)
        else:
            rules = node_rules(found, name, domain)
            if rules:
                block["reverse-proxy"] = dict(block.get("reverse-proxy") or {}, federation=rules)


def write(fleet_dir, partner, entry):
    """One partner's entry, its share and from kept when it is there already."""
    p = os.path.join(fleet_dir, FILE)
    data = (yaml.safe_load(util.read_text(p)) or {}) if os.path.isfile(p) else {}
    old = data.get(partner) or {}
    entry = dict(entry, share=old.get("share") or [], **{"from": old.get("from") or []})
    for k in ("exit", "internet"):          # the owner's, like share and from
        if old.get(k):
            entry[k] = True
    data[partner] = entry
    head = ("# federation.yaml — the partner networks and what each may reach here "
            "(docs/design/federation.md).\n# kiwi-server federate writes the link; share and from are yours.\n")
    util.write_text(p, head + yaml.safe_dump(data, sort_keys=False, default_flow_style=None), 0o644)


def set_flag(fleet_dir, partner, key, on):
    """`exit` or `internet` of one partner, the rest of the file as it is."""
    p = os.path.join(fleet_dir, FILE)
    data = (yaml.safe_load(util.read_text(p)) or {}) if os.path.isfile(p) else {}
    if partner not in data:
        raise KiwiError("%s: no partner called %s" % (p, partner))
    if on:
        data[partner][key] = True
    else:
        data[partner].pop(key, None)
    head = ("# federation.yaml — the partner networks and what each may reach here "
            "(docs/design/federation.md).\n# kiwi-server federate writes the link; share and from are yours.\n")
    util.write_text(p, head + yaml.safe_dump(data, sort_keys=False, default_flow_style=None), 0o644)

