"""Federation: two networks linked over their masters (docs/design/federation.md).

`federation.yaml` next to the fleet file names the partners and what each may reach here:

    kiwi2:
      endpoint: 188.68.38.110        # the partner master's wg_host
      public_key: ...                # its link key (kiwi-server federate fills both in)
      mesh: 10.10.0.0/16
      domain: internal
      dns: 10.10.0.1
      share: [cloud.papaya, lychee, papaya:22]
      from: [devices, 10.10.0.3]     # its groups (by name) or addresses; empty: the partner's whole mesh
      groups: {devices: 10.10.3.0/24}   # the partner's groups (kiwi-server federate fills them in)
      exit: true                     # this network leaves to the internet through that partner …
      internet: true                 # … which that partner allows with this, in its own file
      seen_as:                       # only when its mesh or domain is this network's own:
        mesh: 10.209.0.0/16          #   how it is seen here (federate picks a free /16) …
        domain: kiwi2.home           #   … and its names (<its name>.<its domain>)
      ca: |                          # its CA's certificate, from its card: offered to this
        -----BEGIN CERTIFICATE-----  #   network's devices when it can sign only its own names
        ...

The exit rides the link: the master is not a client of the partner's VPN server.

The master's vpn-server and the nodes' reverse-proxy get their settings from it at load time.
"""
import base64
import hashlib
import ipaddress
import os
import re

import yaml

from . import util
from .util import KiwiError

FILE = "federation.yaml"
KEYS = ("endpoint", "public_key", "mesh", "domain", "dns", "share", "from", "as", "exit", "internet", "groups", "seen_as", "ca")

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
        spec["groups"] = {str(k): str(v) for k, v in (spec.get("groups") or {}).items()}
        spec["from"] = [_source(p, name, spec, x) for x in spec.get("from") or []]
        for k in ("exit", "internet"):
            if spec.get(k) not in (None, True, False):
                raise KiwiError("%s: %s: %s is true or false" % (p, name, k))
            spec[k] = bool(spec.get(k))
        spec["seen_as"] = _seen_as(p, name, spec.get("seen_as"))
        if spec["exit"] and spec["internet"]:
            raise KiwiError("%s: %s is both this network's way to the internet (exit) and let out through it (internet): "
                            "two networks cannot leave through each other" % (p, name))
        out[str(name)] = {k: spec.get(k) for k in KEYS}
    exits = [n for n, x in out.items() if x["exit"]]
    if len(exits) > 1:
        raise KiwiError("%s: only one partner can be this network's way to the internet (exit: true for %s)"
                        % (p, ", ".join(exits)))
    return out


def _seen_as(p, name, got):
    """How a partner whose mesh or domain is this network's own is seen here: {mesh, domain}, either
    may be missing; None without one."""
    if not got:
        return None
    if not isinstance(got, dict) or set(got) - {"mesh", "domain"}:
        raise KiwiError("%s: %s: seen_as takes mesh and domain" % (p, name))
    out = {}
    if got.get("mesh"):
        try:
            net = ipaddress.ip_network(str(got["mesh"]), strict=True)
        except ValueError:
            raise KiwiError("%s: %s: seen_as.mesh %s is not a network like 10.209.0.0/16" % (p, name, got["mesh"]))
        if net.version != 4 or net.prefixlen != 16:
            raise KiwiError("%s: %s: seen_as.mesh is a /16 of IPv4, like 10.209.0.0/16" % (p, name))
        out["mesh"] = str(net)
    if got.get("domain"):
        d = str(got["domain"]).strip(".").lower()
        if not all(x and len(x) < 64 and all(c.isalnum() or c == "-" for c in x) for x in d.split(".")):
            raise KiwiError("%s: %s: seen_as.domain %s is not a domain" % (p, name, got["domain"]))
        out["domain"] = d
    return out or None


def seen(p):
    """(mesh, domain) a partner has here: its alias where it has one, else its own."""
    s = p.get("seen_as") or {}
    return str(s.get("mesh") or p.get("mesh") or ""), str(s.get("domain") or p.get("domain") or "")


def moved(addr, frm, to):
    """An address of the network `frm` at the same place in `to` (10.9.0.1 in 10.9/16 -> 10.209.0.1)."""
    a, f, t = ipaddress.ip_address(addr), ipaddress.ip_network(frm), ipaddress.ip_network(to)
    if a not in f:              # not of that network: it stays where it is
        return str(a)
    return str(ipaddress.ip_address(int(t.network_address) + int(a) - int(f.network_address)))


def our_alias(name, domain, found):
    """How partners with this network's own domain see it: <its name>.<domain>; None when none has."""
    if any(str(p.get("domain") or "").lower() == str(domain).lower() for p in found.values()):
        return "%s.%s" % (name, domain)
    return None


def _source(p, name, spec, x):
    """One `from` entry as the address range the master's firewall takes: a group of the
    partner by its name, or an address or range in its mesh."""
    x = str(x).strip()
    if x in spec["groups"]:
        x = spec["groups"][x]
    try:
        net = ipaddress.ip_network(x, strict=False)
    except ValueError:
        raise KiwiError("%s: %s: from: %s is neither an address nor a group of %s (its groups: %s; kiwi-server "
                        "federate fills them in)" % (p, name, x, name, ", ".join(sorted(spec["groups"])) or "none known"))
    try:
        inside = net.subnet_of(ipaddress.ip_network(str(spec["mesh"]), strict=False))
    except (TypeError, ValueError):
        inside = False
    if not inside:
        raise KiwiError("%s: %s: from: %s is not in its mesh %s: nothing else comes over the link"
                        % (p, name, x, spec["mesh"]))
    return str(net)


CARD = ("endpoint", "public_key", "mesh", "domain", "dns", "groups", "ca")


def card(name, entry):
    """What a network tells a partner about itself (`federate --invite`): its link key's public
    half, its master's address, mesh, domain, DNS, groups and its CA's certificate. Nothing secret."""
    return {"federation": 1, "name": name, **{k: entry[k] for k in CARD if entry.get(k) not in (None, "")}}


def read_card(path):
    try:
        data = yaml.safe_load(util.read_text(path)) or {}
    except (OSError, yaml.YAMLError) as e:
        raise KiwiError("%s: not a network's card: %s" % (path, e))
    if not isinstance(data, dict) or data.get("federation") != 1 or not data.get("name"):
        raise KiwiError("%s: not a network's card (kiwi-server federate FLEET --invite writes one)" % path)
    missing = [k for k in ("endpoint", "public_key", "mesh") if not data.get(k)]
    if missing:
        raise KiwiError("%s: the card lacks %s" % (path, ", ".join(missing)))
    if len(base64.b64decode(str(data["public_key"]) + "==")) != 32:
        raise KiwiError("%s: its public_key is not a WireGuard key" % path)
    ipaddress.ip_network(str(data["mesh"]), strict=False)
    if data.get("ca") and not str(data["ca"]).startswith("-----BEGIN CERTIFICATE-----"):
        raise KiwiError("%s: its ca is not a certificate" % path)
    out = {k: data[k] for k in CARD if k in data}
    req = data.get("requests") or {}
    if not isinstance(req, dict) or any(not LABEL.match(str(k)) or "BEGIN CERTIFICATE REQUEST" not in str(v) for k, v in req.items()):
        raise KiwiError("%s: its requests are not {machine: certificate request}" % path)
    if req:
        out["requests"] = {str(k): str(v) for k, v in req.items()}
    return str(data["name"]), out


def ca_offer(entry, own_domain, permitted):
    """(may this network's devices import the partner's CA, why not). `permitted`: the domains its
    certificate may sign names under (certs.ca_permitted), None for any. Only when every one is
    the partner's own domain or below it, and none is this network's domain, above or below it
    (a translated partner has this network's domain: its CA could sign this network's names)."""
    if not entry.get("ca"):
        return False, "its card brought no CA: link it again (kiwi-server federate) to bring it"
    if not permitted:
        return False, "its CA is not limited to its own names: it could sign any name"
    theirs = str(entry.get("domain") or "").strip(".").lower()
    own = str(own_domain or "").strip(".").lower()

    def under(name, dom):
        return bool(dom) and (name == dom or name.endswith("." + dom))
    foreign = [d for d in permitted if not under(d, theirs)]
    if foreign:
        return False, "its CA may sign names under .%s, which are not its own (.%s)" % (foreign[0], theirs or "?")
    mine = [d for d in permitted if under(d, own) or under(own, d)]
    if mine:
        return False, "its CA may sign names under .%s, this network's own" % mine[0]
    return True, "it can sign names under .%s only" % ", .".join(permitted)


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


def admin_rules(rules, found, host, admin, domain):
    """`node_rules` with the node's admin pages (`admin`: their full names) closed to every
    partner, also one that is given the whole node: an admin page opens for a partner only
    when it is named in its share."""
    if not found or not admin:
        return rules
    everyone = [p["as"] for p in found.values() if p.get("as")]
    # the rest (the name a partner of this network's own domain gives it: alias) as it was
    rules = dict(rules or {}, names=dict((rules or {}).get("names") or {}), deny=list((rules or {}).get("deny") or []))
    for site in admin:
        short = site[:-len(domain) - 1] if site.endswith("." + domain) else site        # control.papaya
        allow = [p["as"] for p in found.values() if p.get("as") and short in p["share"]]
        rules["names"][site] = {"allow": allow, "deny": everyone}
    return rules


def inject(fleet_dir, data):
    """Fill the settings in from federation.yaml before the fleet's hosts are made."""
    found = partners(fleet_dir)
    if not found or not isinstance(data, dict):
        return
    for p in found.values():                # the partner's CA is for this network's devices, not its machines
        p.pop("ca", None)
    defaults = data.get("defaults") or {}
    domain = defaults.get("domain") or "home"
    mesh = (defaults.get("master") or {}).get("mesh_subnet") or "10.8.0.0/16"
    with_addresses(found, mesh)
    for p in found.values():                # what the master's link needs of a translated partner
        if p.get("seen_as"):
            sa = dict(p["seen_as"])
            if sa.get("mesh") and p.get("dns"):
                sa["dns"] = moved(p["dns"], p["mesh"], sa["mesh"])
            # this master's own address on the link: one the partner does not have itself
            sa["link"] = str(ipaddress.ip_address(int(ipaddress.ip_network(mesh, strict=False).broadcast_address) - 1))
            p["seen_as"] = sa
    alias = our_alias(os.path.basename(os.path.abspath(fleet_dir).rstrip("/")), domain, found)
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
            if alias:                    # a partner sees this network as <its name>.<domain>: its nginx answers that too
                rules = dict(rules or {}, alias={"domain": domain, "as": alias})
            if rules:
                block["reverse-proxy"] = dict(block.get("reverse-proxy") or {}, federation=rules)



HEAD = ("# federation.yaml — the partner networks and what each may reach here "
        "(docs/design/federation.md).\n# kiwi-server federate writes the link; share and from are yours.\n")


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(str, lambda d, v: d.represent_scalar("tag:yaml.org,2002:str", v, style="|" if "\n" in v else None))


def card_text(name, entry, requests=None):
    """A card as its file has it. `requests`: {machine: certificate request}, for a partner that
    has this network's domain to sign the names it gives those machines (translated at the link)."""
    c = card(name, entry)
    if requests:
        c["requests"] = dict(sorted(requests.items()))
    return ("# %s's card for a partner network (kiwi-server federate FLEET --accept THIS). Nothing secret.\n" % name
            + yaml.dump(c, Dumper=_Dumper, sort_keys=False, default_flow_style=None, allow_unicode=True))


LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def signed_text(by, them, certs):
    """What a network sends back to a partner whose machines' names it signed: {machine: fullchain}."""
    return ("# %s's certificates for %s's machines, as %s calls them (kiwi-server federate FLEET --accept THIS). "
            "Nothing secret.\n" % (by, them, by)
            + yaml.dump({"federation-signed": 1, "by": by, "for": them, "certs": dict(sorted(certs.items()))},
                        Dumper=_Dumper, sort_keys=False, default_flow_style=None, allow_unicode=True))


def dump(data):
    """federation.yaml's text: the head, then the partners; a CA's certificate as a block."""
    return HEAD + yaml.dump(data, Dumper=_Dumper, sort_keys=False, default_flow_style=None, allow_unicode=True)


def write(fleet_dir, partner, entry):
    """One partner's entry, its share and from kept when it is there already (by its name, or by
    its mesh when its folder was renamed). The name it is written under."""
    p = os.path.join(fleet_dir, FILE)
    data = (yaml.safe_load(util.read_text(p)) or {}) if os.path.isfile(p) else {}
    if partner not in data:     # the same network under the name it had (its folder renamed): that entry
        partner = next((n for n, x in data.items() if (x or {}).get("mesh") == entry.get("mesh")), partner)
    old = data.get(partner) or {}
    entry = dict(entry, share=old.get("share") or [], **{"from": old.get("from") or []})
    if not entry.get("ca"):                 # a card without one keeps the CA there
        entry.pop("ca", None)
        if old.get("ca"):
            entry["ca"] = old["ca"]
    if not entry.get("seen_as"):            # only when its mesh or domain is this network's own
        entry.pop("seen_as", None)
    for k in ("exit", "internet"):          # the owner's, like share and from
        if old.get(k):
            entry[k] = True
    data[partner] = entry
    util.write_text(p, dump(data), 0o644)
    return partner


def set_share(fleet_dir, partner, share):
    """What one partner may reach here, the rest of the file as it is."""
    p = os.path.join(fleet_dir, FILE)
    data = (yaml.safe_load(util.read_text(p)) or {}) if os.path.isfile(p) else {}
    if partner not in data:
        raise KiwiError("%s: no partner called %s" % (p, partner))
    data[partner] = dict(data[partner] or {}, share=list(share))
    util.write_text(p, dump(data), 0o644)


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
    util.write_text(p, dump(data), 0o644)

