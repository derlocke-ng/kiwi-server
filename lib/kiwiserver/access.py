"""Who may reach what (kiwi-server access).

There are two kinds of "who". A group is a range of VPN client addresses on the master
(`vpn-server.groups` in the fleet file); a device belongs to the group whose range its
address is in. A linked network is a partner in federation.yaml. Both are given the same
things, in the same words:

    all             everything on the VPN (a group only)
    nodes           every node
    apps            the web pages of every node and nothing else of them (a group only)
    papaya          one machine, all of it
    cloud.papaya    one service of a machine: its web address
    papaya:22       one port of a machine

The master's firewall lets through what is given and drops the rest (kiwi-modules,
vpn-server). All services of a machine share one address and port, so a machine that a group
reaches for some services only refuses that group its other names in its own web server:
`node_rules` makes that list for the reverse proxy. For a linked network the same is done
in federation.py.

A third kind is a group of the home network (`lan.groups` in the fleet file's defaults,
docs/design/lans.md): a subnet at home, a guest Wi-Fi or a VLAN, given things in the same
words. What is not in such a group reaches everything at home, as before. Each node keeps it
itself: its nginx refuses the names (as for a VPN group) and its nftables table kiwi_lan the
ports (`lan_ports`), since the home network reaches a node across the switch, not through
the master.

Some pages are not apps but the network's controls: the control page, Portainer, Pi-hole,
the WireGuard page, the backup server, the cache (`admin: true` in their module). They are
the admin pages. A group opens them only with its `admin` switch on, or when one is named
for it; a linked network only when one is named. "Everything" without the switch is
everything but those.
"""
import ipaddress
import re

from .util import KiwiError

FLAGS = ("peers", "internet", "admin")
FLAG_DEFAULT = {"peers": True, "internet": True, "admin": False}
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
WORD = re.compile(r"^[a-z0-9][a-z0-9.-]{0,120}(:[0-9]{1,5})?$")


def machines(fleet, roles, records, admin=None):
    """The machines a rule can name, with their services: [{name, hostname, ip, master, services,
    admin}]. `records` are the fleet's DNS records, (address, name); `admin`: {hostname: the
    full names of its admin pages}. `admin` in the result: the services that are admin pages."""
    out = []
    for h in fleet.hosts.values():
        if h.role not in roles or not roles[h.role].stack:
            continue
        ip = next((a for a, n in records if n == h.hostname), None)
        if not ip:
            continue            # not on the VPN yet: nothing to reach
        tail = "." + h.hostname
        out.append({"name": h.name, "hostname": h.hostname, "ip": ip, "master": roles[h.role].node_type == "master",
                    "services": sorted({n[:-len(tail)] for a, n in records if a == ip and n.endswith(tail)}),
                    "admin": sorted(n[:-len(tail)] for n in (admin or {}).get(h.hostname, []) if n.endswith(tail))})
    return out


def _address(text):
    try:
        ipaddress.ip_network(text, strict=False)
        return True
    except ValueError:
        return False


def canon(entry, found, domain, group=True, partners=()):
    """One thing a rule gives, in its short form; KiwiError says what it could be instead. A
    group may also be given a linked network by its name (`partners`): what that one opens."""
    e = str(entry).strip().lower()
    if group and e in partners:
        return e
    if e in ("all", "nodes", "apps"):
        if e in ("all", "apps") and not group:
            raise KiwiError("a linked network is given machines and services, not %s: name them" % e)
        return e
    if group and _address(e):
        return e
    if not WORD.match(e):
        raise KiwiError("%r is not a machine, a service like cloud.papaya or a port like papaya:22" % entry)
    host, _, port = e.partition(":")
    if host.endswith("." + domain):
        host = host[:-len(domain) - 1]
    by_name = {m["name"]: m for m in found}
    by_name.update({m["hostname"][:-len(domain) - 1]: m for m in found if m["hostname"].endswith("." + domain)})
    if host in by_name:
        if port and not 0 < int(port) < 65536:
            raise KiwiError("%s: a port is 1 to 65535" % entry)
        return by_name[host]["name"] + (":" + port if port else "")
    service, _, machine = host.partition(".")
    m = by_name.get(machine)
    if m and not port:
        if m["master"]:
            raise KiwiError("%s: the master's own pages are its admin pages; a group gets them with admin" % entry)
        if service in m["services"]:
            return "%s.%s" % (service, m["name"])
        raise KiwiError("%s has no service called %s (it has: %s)" % (m["name"], service, ", ".join(m["services"]) or "none"))
    have = ", ".join(x["name"] for x in found if not x["master"]) or "none on the VPN yet"
    raise KiwiError("no machine called %s (there is: %s)" % (machine or host, have))


def shape(reach, found):
    """A reach list as a page shows it: {all, machines: {name: {whole, services, ports}}, other}."""
    out = {"all": "all" in reach, "apps": "apps" in reach,
           "machines": {m["name"]: {"whole": False, "services": [], "ports": []} for m in found if not m["master"]}, "other": []}
    for e in reach:
        host, _, port = e.partition(":")
        service, _, machine = host.partition(".")
        if e in ("all", "apps"):
            continue
        if e == "nodes":
            for m in out["machines"].values():
                m["whole"] = True
        elif host in out["machines"]:
            (out["machines"][host]["ports"].append(port) if port else out["machines"][host].__setitem__("whole", True))
        elif machine in out["machines"] and not port and not _address(e):
            out["machines"][machine]["services"].append(service)
        else:
            out["other"].append(e)
    return out


def words(reach):
    """A reach list in a sentence."""
    if "all" in reach:
        return "everything"
    if not reach:
        return "nothing"
    return ", ".join({"nodes": "every node", "apps": "every app"}.get(e, e) for e in reach)


def _loose(reach, found, domain, group, partners=()):
    """What the file says, in short form where it can be read; the rest as written."""
    out = []
    for e in reach or []:
        try:
            e = canon(e, found, domain, group, partners)
        except KiwiError:
            e = str(e)
        if e not in out:
            out.append(e)
    return out


def view(groups, partners, found, domain, device_addresses=None):
    """Everyone a rule can be about: [{kind: group|network, name, range, reach, open, devices, …}]."""
    rows = []
    for name, g in (groups or {}).items():
        g = g or {}
        reach = _loose(g.get("reach", ["all"]), found, domain, True)
        row = {"kind": "group", "name": name, "range": g.get("subnet") or "", "reach": reach, "open": shape(reach, found),
               "says": words(reach), "devices": []}
        for k in FLAGS:
            row[k] = bool(g.get(k, FLAG_DEFAULT[k]))
        try:
            net = ipaddress.ip_network(row["range"], strict=False)
            row["devices"] = sorted(n for n, a in (device_addresses or {}).items() if ipaddress.ip_address(a) in net)
        except ValueError:
            pass
        rows.append(row)
    for name, p in (partners or {}).items():
        reach = _loose(p.get("share"), found, domain, False)
        rows.append({"kind": "network", "name": name, "range": str((p.get("seen_as") or {}).get("mesh") or p.get("mesh") or ""), "reach": reach,
                     "open": shape(reach, found), "says": words(reach), "devices": [], "from": list(p.get("from") or [])})
    return rows


def lan_view(lan, found, domain):
    """The groups of the home network as rows like the VPN's groups (kind lan)."""
    rows = []
    for name, g in (lan or {}).items():
        g = g or {}
        reach = _loose(g.get("reach", ["all"]), found, domain, True)
        rows.append({"kind": "lan", "name": name, "range": str(g.get("subnet") or ""), "reach": reach,
                     "open": shape(reach, found), "says": words(reach), "devices": [],
                     "admin": bool(g.get("admin", FLAG_DEFAULT["admin"]))})
    return rows


def lan_problems(lan, mesh="", vpn_groups=()):
    """What is wrong with the groups of the home network, in sentences."""
    bad, seen = [], []
    for name, g in (lan or {}).items():
        if not NAME.match(str(name)) or name in ("all", "nodes", "nothing", "apps"):
            bad.append("lan.groups: %r is not a name for a group (small letters, digits and -)" % name)
            continue
        if name in (vpn_groups or ()):
            bad.append("lan.groups.%s: a group of the VPN has that name too: one of them needs another" % name)
        g = g if isinstance(g, dict) else {}
        try:
            net = ipaddress.ip_network(str(g.get("subnet") or ""), strict=False)
        except ValueError:
            bad.append("lan.groups.%s: subnet %r is not a network like 192.168.20.0/24" % (name, g.get("subnet")))
            continue
        if net.version != 4 or not net.is_private:
            bad.append("lan.groups.%s: %s is not a private IPv4 network" % (name, net))
            continue
        if mesh:
            try:
                if net.overlaps(ipaddress.ip_network(str(mesh), strict=False)):
                    bad.append("lan.groups.%s: %s is in the VPN's mesh %s" % (name, net, mesh))
            except ValueError:
                pass
        for other, o in seen:
            if net.overlaps(o):
                bad.append("lan.groups.%s: %s overlaps %s's" % (name, net, other))
        seen.append((name, net))
        for k in set(g) - {"subnet", "reach", "admin"}:
            bad.append("lan.groups.%s: %s is not a setting of a group of the home network (subnet, reach, admin)" % (name, k))
    return bad


def lan_ports(lan, name, hostname):
    """kiwi-stack's lan_rules for one node: per group of the home network "SUBNET all|none|tcp:…".
    The whole node (all, nodes, it by name): all; its web pages (apps, one of its services): 80 and
    443, the names are its nginx's to refuse; a port of it: that port."""
    out = []
    for g in (lan or {}).values():
        g = g or {}
        if not g.get("subnet"):
            continue
        reach = [str(e).lower() for e in (g.get("reach", ["all"]) or [])]
        if "all" in reach or "nodes" in reach or name in reach or hostname in reach:
            out.append("%s all" % g["subnet"])
            continue
        ports = []
        if "apps" in reach or any(":" not in e and (e.endswith("." + name) or e.endswith("." + hostname)) for e in reach):
            ports += ["tcp:443", "tcp:80"]
        for e in reach:
            host, _, port = e.partition(":")
            if port and host in (name, hostname) and ("tcp:" + port) not in ports:
                ports.append("tcp:" + port)
        out.append("%s %s" % (g["subnet"], ",".join(ports) if ports else "none"))
    return out


def gateway_lan(lan, machines):
    """The gateway module's lan_groups: what each group of the home network may reach in the mesh
    through a gateway (the machines it reaches only there: masters, machines at another site,
    nodes by their mesh address). machines: [{name, hostname, ip, master}]."""
    out = []
    for g in (lan or {}).values():
        g = g or {}
        net = str(g.get("subnet") or "")
        if not net:
            continue
        reach = [str(e).lower() for e in (g.get("reach", ["all"]) or [])]
        if "all" in reach:
            out.append("%s all" % net)
            continue
        for m in machines:
            if not m.get("ip"):
                continue
            mine = {m["name"], m["hostname"]}
            if not m.get("master") and ("nodes" in reach or mine & set(reach)):
                out.append("%s to %s any" % (net, m["ip"]))
                continue
            ports = []
            if not m.get("master") and ("apps" in reach or any(":" not in e and e.rsplit(".", 1)[-1] in mine for e in reach)
                                        or any(":" not in e and e.endswith("." + m["hostname"]) for e in reach)):
                ports += ["tcp:443", "tcp:80"]
            ports += ["tcp:" + e.partition(":")[2] for e in reach if ":" in e and e.partition(":")[0] in mine]
            if ports:
                out.append("%s to %s %s" % (net, m["ip"], ",".join(dict.fromkeys(ports))))
        out.append("%s drop" % net)
    return out


def node_rules(groups, name, hostname, admin=()):
    """reverse-proxy's `access` for one node: which client ranges it refuses where. names:
    site -> the ranges refused there; others: the ranges refused at every site not listed.
    Two reasons to refuse. A group that reaches the node for some services only is refused
    the others. And an admin page (`admin`: their full names) is refused to every group
    that has not the admin switch, whatever else it reaches here, unless the page is named
    for it. Empty when nothing is refused."""
    limited, plain = {}, []     # range -> the services of this node it has; ranges without the admin pages
    named = {}                  # range -> the sites of this node named for it
    for g in (groups or {}).values():
        g = g or {}
        reach = [str(e).lower() for e in (g.get("reach", ["all"]) or [])]
        if not g.get("subnet"):
            continue
        mine = {e.rsplit(".", 1)[0] for e in reach if ":" not in e and e.endswith("." + name)}
        mine |= {e[:-len(hostname) - 1] for e in reach if ":" not in e and e.endswith("." + hostname)}
        named[str(g["subnet"])] = mine
        whole = "all" in reach or "nodes" in reach or name in reach or hostname in reach
        if whole or "apps" in reach:
            if not g.get("admin", FLAG_DEFAULT["admin"]):
                plain.append(str(g["subnet"]))
        elif mine:
            limited[str(g["subnet"])] = mine
    tail = "." + hostname
    pages = sorted(n[:-len(tail)] for n in admin if n.endswith(tail))
    names = {}
    for s in sorted(set().union(*limited.values()) if limited else set()):
        names["%s.%s" % (s, hostname)] = sorted(r for r, has in limited.items() if s not in has)
    for s in pages if (plain or limited) else []:
        refused = set(r for r in plain if s not in named.get(r, ())) | set(r for r, has in limited.items() if s not in has)
        names["%s.%s" % (s, hostname)] = sorted(refused)
    if not limited and not any(names.values()):
        return {}
    return {"names": names, "others": sorted(limited)}


def default_group(groups):
    """The group a new device lands in unless someone chooses another: `family` (the apps), or else
    the first group without the admin pages. A device outside every group reaches everything,
    admin pages included, so that is never the default."""
    groups = groups or {}
    if "family" in groups and not (groups["family"] or {}).get("admin", FLAG_DEFAULT["admin"]):
        return "family"
    return next((n for n in sorted(groups) if not (groups[n] or {}).get("admin", FLAG_DEFAULT["admin"])), None)


def admin_ranges(groups):
    """The client ranges of the VPN groups without the admin switch: refused the admin areas
    inside a site (ejabberd's /admin, Vaultwarden's /admin), whatever else they reach there.
    A site that is an admin page as a whole goes by node_rules (it may be named for a group)."""
    return sorted(str(g["subnet"]) for g in (groups or {}).values()
                  if g and g.get("subnet") and not g.get("admin", FLAG_DEFAULT["admin"]))


def next_range(groups, mesh):
    """A free /24 of the mesh for a new group: after the highest one in use."""
    net = ipaddress.ip_network(mesh, strict=False)
    used = []
    for g in (groups or {}).values():
        try:
            used.append(ipaddress.ip_network((g or {}).get("subnet"), strict=False))
        except (ValueError, TypeError):
            pass
    for sub in list(net.subnets(new_prefix=24))[1:255]:      # the first is the plain mesh, the last the partners' addresses
        if not any(sub.overlaps(u) for u in used):
            return str(sub)
    raise KiwiError("no free range left in %s" % mesh)


def change(groups, name, found, domain, mesh, reach=None, add=(), remove=(), new=False, flags=None, partners=()):
    """The groups with one of them changed (a copy). `reach` replaces what it has; `add`
    and `remove` change it by entries; `new` makes the group with the next free range."""
    groups = {k: dict(v or {}) for k, v in (groups or {}).items()}
    if name not in groups:
        if not new:
            raise KiwiError("no group called %s (there is: %s). --new makes it; --new --lan 192.168.20.0/24 a group of "
                            "the home network" % (name, ", ".join(groups) or "none"))
        if not NAME.match(name) or name in ("all", "nodes", "nothing"):
            raise KiwiError("a group's name: small letters, digits and -")
        groups[name] = {"subnet": next_range(groups, mesh), "reach": [], "peers": False}
    g = groups[name]
    have = _loose(g.get("reach", ["all"]), found, domain, True, partners)
    if reach is not None:
        have = []
        add = reach
    for e in add:
        e = canon(e, found, domain, True, partners)
        if e not in have:
            have.append(e)
    gone = [canon(e, found, domain, True, partners) for e in remove]
    if gone and "all" in have:
        raise KiwiError("%s reaches everything: say what it may reach instead, e.g. access <fleet> %s nodes" % (name, name))
    have = [e for e in have if e not in gone]
    if "all" in have:
        have = ["all"]
    g["reach"] = have
    for k, v in (flags or {}).items():
        if v is not None:
            g[k] = bool(v)
    return groups


def put(data, master, role, groups):
    """The groups into the fleet file's data, where it has them: the master's own settings,
    or the defaults when they are set there and not on the master."""
    host = (data.get("hosts") or {}).get(master)
    own = isinstance(host, dict) and isinstance(host.get(role), dict) and isinstance(host[role].get("vpn-server"), dict) \
        and "groups" in host[role]["vpn-server"]
    d = data.get("defaults") if isinstance(data.get("defaults"), dict) else {}
    shared = isinstance(d.get(role), dict) and isinstance(d[role].get("vpn-server"), dict) and "groups" in d[role]["vpn-server"]
    if shared and not own:
        d[role]["vpn-server"]["groups"] = groups
        return
    if not isinstance(host, dict):
        host = data.setdefault("hosts", {})[master] = {}
    block = host.setdefault(role, {})
    if not isinstance(block.get("vpn-server"), dict):
        block["vpn-server"] = {}
    block["vpn-server"]["groups"] = groups


def _block(lines, path):
    """Where a mapping key lives in a YAML text, by indentation: (first line, line after its
    block, indent) for the key at `path`, or None when the text is not laid out so plainly."""
    top, start, end, indent = 0, 0, len(lines), -1
    for key in path:
        hit = None
        child = None
        for i in range(start, end):
            line = lines[i]
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            ind = len(line) - len(line.lstrip())
            if child is None:
                if ind <= indent:
                    return None
                child = ind
            if ind == child and re.match(r"%s\s*:(\s|$)" % re.escape(key), line.lstrip()):
                hit = i
                break
        if hit is None:
            return None
        stop = end
        for j in range(hit + 1, end):
            line = lines[j]
            if line.strip() and not line.lstrip().startswith("#") and len(line) - len(line.lstrip()) <= child:
                stop = j
                break
        while stop > hit + 1 and (not lines[stop - 1].strip() or lines[stop - 1].lstrip().startswith("#")):
            stop -= 1               # comments and blank lines before the next key belong to that key
        top, start, end, indent = hit, hit + 1, stop, child
    return top, end, indent


def rewrite(text, master, role, groups):
    """The fleet file's text with the groups changed. Only their lines are replaced where the
    file has them as a block of their own, so its comments stay; otherwise the whole file is
    written anew. Returns (text, comments kept)."""
    import yaml

    from . import form
    doc = form.Doc.parse(text)
    put(doc.data, master, role, groups)
    lines = text.split("\n")
    for path in (["hosts", master, role, "vpn-server", "groups"], ["defaults", role, "vpn-server", "groups"]):
        at = _block(lines, path)
        if not at or lines[at[0]].split("#")[0].rstrip().split(":", 1)[1].strip():
            continue                # not there, or written on one line with its key
        pad = " " * at[2]
        new = [pad + "groups:"] + ["%s  %s: %s" % (pad, name, yaml.safe_dump(g, default_flow_style=True, width=1000, sort_keys=False).strip())
                                   for name, g in groups.items()]
        out = "\n".join(lines[:at[0]] + new + lines[at[1]:])
        try:
            same = yaml.safe_load(out) == yaml.safe_load(doc.dump())
        except yaml.YAMLError:
            same = False
        if same:
            return out, True
    return doc.dump(), False
