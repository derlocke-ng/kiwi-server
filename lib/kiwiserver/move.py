"""Moving a whole network to another mesh or domain (docs/design/federation.md, "Moving a whole
network"): for just in case, a plan first. `kiwi-server move FLEET --mesh 10.X.0.0/16 --domain D`
says what would change and what people would have to do, and refuses what cannot work. It changes
nothing: the move itself comes later, on this plan.
"""
import glob
import ipaddress
import os

from . import certs, config, federation as fedmod, rotate, roles as rolesmod, util
from .util import KiwiError


def _mesh(fleet):
    return str(((fleet.defaults.get("master") or {}).get("mesh_subnet")) or "10.8.0.0/16")


def plan(fleet, roles, ms, mesh=None, domain=None):
    """{from, to, problems, warnings, machines, groups, devices, partners, secrets, pins, disks,
    certs, ca, people}: what a move would change. Nothing is touched."""
    if not mesh and not domain:
        raise KiwiError("move to what? --mesh 10.X.0.0/16 and/or --domain D")
    old_mesh, old_domain = _mesh(fleet), str(fleet.defaults.get("domain") or "home")
    problems, warnings, people = [], [], []
    new_mesh = None
    if mesh:
        try:
            new_mesh = ipaddress.ip_network(str(mesh), strict=True)
        except ValueError:
            raise KiwiError("--mesh %s is not a network like 10.42.0.0/16" % mesh)
        if new_mesh.version != 4 or not new_mesh.is_private or new_mesh.prefixlen != 16:
            problems.append("the mesh must be a private IPv4 /16, like 10.42.0.0/16")
        if str(new_mesh) == old_mesh:
            problems.append("the mesh is %s already" % old_mesh)
    if domain:
        domain = str(domain).strip().strip(".").lower()
        advice = config.domain_advice(domain)
        if advice and advice[0] == "error":
            problems.append(advice[1])
        elif advice:
            warnings.append(advice[1])
        if domain == old_domain:
            problems.append("the domain is %s already" % old_domain)

    machines, disks, lans = [], [], []
    for h in sorted(fleet.hosts.values(), key=lambda x: x.name):
        if h.role not in roles:
            continue
        try:
            rolesmod.resolve_settings(roles[h.role], h, ms)
            vpn = rolesmod.stack_spec(h, roles[h.role]).vpn_ip if roles[h.role].stack else ""
        except KiwiError:
            vpn = ""
        row = {"name": h.name, "hostname": h.hostname}
        if new_mesh and vpn:
            row["mesh"] = [vpn, fedmod.moved(vpn, old_mesh, str(new_mesh))]
        if domain and h.hostname.endswith("." + old_domain):
            row["hostname_to"] = h.hostname[:-len(old_domain)] + domain
        machines.append(row)
        enc = [at for _d, at, _f, e in config.mounts_of(h.cfg) if e]
        if enc:
            disks.append({"name": h.name, "mounts": enc})
        net = h.cfg.get("network") or {}
        if not net.get("dhcp", True) and net.get("address"):
            try:
                lans.append((h.name, ipaddress.ip_interface(str(net["address"])).network))
            except ValueError:
                pass

    groups = []
    master = next((h for h in fleet.hosts.values() if h.role in roles and roles[h.role].node_type == "master"
                   and not h.standby_of), None)
    for name, g in sorted(((master.module_settings.get("vpn-server") or {}).get("groups") or {}).items() if master else []):
        sub = str((g or {}).get("subnet") or "")
        if sub:
            groups.append({"name": name, "subnet": [sub, fedmod.moved(sub.split("/")[0], old_mesh, str(new_mesh)) + "/" +
                                                    sub.split("/")[1] if new_mesh else sub]})
    devices = sorted(os.path.basename(p)[:-5] for p in glob.glob(fleet.resolve_path("secrets/devices/*.conf")))
    try:
        partners = fedmod.partners(fleet.dir)
    except KiwiError:
        partners = {}

    if new_mesh:
        hit = {}
        for name, net in lans:
            if net.overlaps(new_mesh):
                hit.setdefault(str(net), []).append(name)
        for net, names in sorted(hit.items()):
            problems.append("the network at home %s (%s) overlaps %s" % (net, ", ".join(names), new_mesh))
        for gname, g in sorted(((fleet.defaults.get("lan") or {}).get("groups") or {}).items()):
            try:
                if ipaddress.ip_network(str((g or {}).get("subnet")), strict=False).overlaps(new_mesh):
                    problems.append("the home network's group %s (%s) overlaps %s" % (gname, g["subnet"], new_mesh))
            except ValueError:
                pass
        for pname, p in sorted(partners.items()):
            if str(p.get("mesh")) == str(new_mesh):
                people.append("%s has the mesh %s too: the link would be translated (as now for a partner of one mesh)" % (pname, new_mesh))
        if devices:
            people.append("every phone and laptop on the VPN gets a new config once (a new QR code): %d of them" % len(devices))
    if partners:
        people.append("each linked network links again (kiwi-server federate, or new cards): %s" % ", ".join(sorted(partners)))

    secrets, pins = {}, {}
    if domain:
        for hostname, module, s in rotate.generated(fleet, ms):
            secrets.setdefault(hostname, []).append("%s.%s" % (module, s.key))
        try:
            kept = fleet.pins()
        except (KiwiError, OSError):
            kept = {}
        for key in kept:
            host = key.split("/", 1)[0]
            if host.endswith("." + old_domain):
                pins.setdefault(host, 0)
                pins[host] += 1
        people.append("every machine is applied again: new names, certificates and DNS")

    ca = {}
    if domain:
        pem = fleet.resolve_path(((fleet.defaults.get("tls") or {}).get("ca_dir") or "secrets/ca") + "/kiwiCA.pem")
        if os.path.isfile(pem):
            try:
                permitted = certs.ca_permitted(util.read_text(pem))
            except KiwiError:
                permitted = None
            ca = {"permitted": permitted or []}
            if permitted and not any(domain == d or domain.endswith("." + d) for d in permitted):
                ca["new"] = True
                people.append("the CA may sign names under .%s only: a new CA, and every device imports it again"
                              % ", .".join(permitted))
    return {"from": {"mesh": old_mesh, "domain": old_domain}, "to": {"mesh": str(new_mesh) if new_mesh else None,
            "domain": domain or None}, "problems": problems, "warnings": warnings, "machines": machines, "groups": groups,
            "devices": devices, "partners": sorted(partners), "secrets": secrets, "pins": pins, "disks": disks,
            "ca": ca, "people": people}


def say(p, out=print):
    """The plan in words: short, what changes, what people do, what stands in the way."""
    f, t = p["from"], p["to"]
    head = []
    if t["mesh"]:
        head.append("mesh %s -> %s" % (f["mesh"], t["mesh"]))
    if t["domain"]:
        head.append("domain .%s -> .%s" % (f["domain"], t["domain"]))
    out("A move of this network: %s. Nothing is changed by this plan." % ", ".join(head))
    for m in p["machines"]:
        bits = []
        if m.get("mesh"):
            bits.append("%s -> %s" % tuple(m["mesh"]))
        if m.get("hostname_to"):
            bits.append("%s -> %s" % (m["hostname"], m["hostname_to"]))
        n = len(p["secrets"].get(m["hostname"]) or [])
        if n:
            bits.append("%d generated password(s) kept as they are (pinned first)" % n)
        d = next((x for x in p["disks"] if x["name"] == m["name"]), None)
        if d and t["domain"]:
            bits.append("data disk key(s) for %s kept" % ", ".join(d["mounts"]))
        if bits:
            out("  %s: %s" % (m["name"], "; ".join(bits)))
    for g in p["groups"]:
        if g["subnet"][0] != g["subnet"][1]:
            out("  group %s: %s -> %s" % (g["name"], g["subnet"][0], g["subnet"][1]))
    if p["pins"]:
        out("  kept passwords to move to the new names: %d" % sum(p["pins"].values()))
    for x in p.get("warnings") or []:
        out("Take care: %s" % x)
    for x in p["people"]:
        out("To do for people: %s" % x)
    for x in p["problems"]:
        out("Not possible: %s" % x)
    if not p["problems"]:
        out("The move itself is not built yet: this is what it would do.")
