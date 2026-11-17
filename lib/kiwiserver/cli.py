"""The command line. bin/kiwi-server execs this; kiwi-server-gui calls it
with --porcelain and parses JSON, so the CLI stays the single source of truth."""
import argparse
import concurrent.futures
import ipaddress
import getpass
import socket
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

from . import VERSION, access as accessmod, address as addressmod, adopt as adoptmod, qr as qrmod, backup as backupmod, certs, config, federation as fedmod, iso as isomod, modules as modmod, plan as planmod, roles as rolesmod, sshca, sshport
from . import facts, fleettext, form as formmod, logins as loginsmod, onion as onionmod, luks, mtu, netcup, providers, remote, router as routermod, setup as setupmod, store, toolchain as tcmod, unlock, util, wgeasy
from . import health as healthmod, recovery as recoverymod, sources as sourcesmod, starter as startermod
from .config import TARGETS
from .targets import armbian as armbian_t, coreos as coreos_t, debian as debian_t, ram as ram_t
from .util import KiwiError

EXIT_INVALID = 2


def cache_dir_for(args):
    return (getattr(args, "cache", None) or os.environ.get("KIWI_SERVER_CACHE")
            or os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "kiwi-server"))


def media_dir_for(args, fleet):
    """Where ISOs go: on disk, since they have to reach a stick or a provider anyway."""
    # KIWI_MEDIA_DIR: a control node keeps them in memory, since nothing of the fleet rests on its disk in the clear
    return os.path.abspath(getattr(args, "output_dir", None) or os.environ.get("KIWI_MEDIA_DIR")
                           or os.path.join(fleet.dir, "output"))


def out_dir_for(args, fleet):
    """Where rendered files go: while the secret store is open, into its tmpfs beside it, since
    they hold the fleet's secrets and nothing in them is a source; else output/ on disk."""
    if getattr(args, "output_dir", None):
        return os.path.abspath(args.output_dir)
    st = store.Store.for_fleet(fleet.path)
    if st.exists() and st.is_open():
        return os.path.join(st.runtime(), "output")
    return media_dir_for(args, fleet)


RENDERED = (".role.sh", ".bu", ".ign", ".preseed.cfg", ".post-install.sh", ".kiwi-server", ".stack")


def retire_rendered(media, name):
    """The rendered files an older release left on disk beside the ISO, now that they live in
    the store's tmpfs: plain copies of the fleet's secrets."""
    for suffix in RENDERED:
        p = os.path.join(media, name + suffix)
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p, ignore_errors=True)
        elif os.path.isfile(p):
            os.remove(p)
    readme = os.path.join(media, "README.txt")
    if os.path.isfile(readme):
        os.remove(readme)


def make_tc(args):
    return tcmod.Toolchain(mode=getattr(args, "toolchain", "auto") or "auto",
                           image=getattr(args, "image", None), verbose=bool(getattr(args, "verbose", False)))


def mask(value):
    return "********" if value else value


def static_lan(host):
    """The host's static LAN address as an interface (address and network), or None."""
    net = host.cfg["network"]
    if net.get("dhcp", True) or not net.get("address"):
        return None
    try:
        return ipaddress.ip_interface(str(net["address"]))
    except ValueError:
        return None


def has_master(fleet, roles):
    return any(h.role in roles and roles[h.role].node_type == "master" and not h.standby_of for h in fleet.hosts.values())


class Outputs:
    """Where a host's files go and which of them exist."""

    def __init__(self, out_dir, host, media_dir=None):
        self.dir = os.path.join(out_dir, host.name)
        self.media = os.path.join(media_dir or out_dir, host.name)
        n = host.name
        self.script = os.path.join(self.dir, n + ".role.sh")
        self.bu = os.path.join(self.dir, n + ".bu")
        self.ign = os.path.join(self.dir, n + ".ign")
        self.preseed = os.path.join(self.dir, n + ".preseed.cfg")
        self.files = os.path.join(self.dir, n + ".kiwi-server")
        self.stack = os.path.join(self.dir, n + ".stack")
        # the install media; on a board, the image to flash
        self.iso = os.path.join(self.media, n + (".img" if host.target == "armbian" else ".iso"))
        self.bare_mark = self.iso + ".bare"        # the ISO carries no role and no secrets of it
        self.readme = os.path.join(self.dir, "README.txt")
        self.work = os.path.join(self.media, ".work")
        self.host = host

    def status(self):
        debian = self.host.target == "debian"
        return {
            "script": os.path.isfile(self.script),
            "config": os.path.isfile(self.script if self.host.target == "armbian" else self.preseed if debian else self.bu),
            "ignition": (not debian) and os.path.isfile(self.ign),
            "stack": os.path.isfile(os.path.join(self.stack, "docker-compose.yml")),
            "iso": os.path.isfile(self.iso),
            "iso_size": os.path.getsize(self.iso) if os.path.isfile(self.iso) else 0,
        }


def host_readme(host, o, bundle=None):
    disk = host.cfg.get("disk") or "<disk: not set>"
    disk = "the machine's one disk (found at the install)" if disk == "auto" else disk
    L = ["%s — %s" % (host.name, host.hostname),
         "target: %s    role: %s    %s" % (host.target, host.role,
                                              "in RAM, no disk" if host.target == "coreos-ram" else "install disk: %s" % disk)]
    if host.modules:
        L.append("modules: %s" % ", ".join(host.modules))
    L += ["",
          "%s.role.sh" % host.name,
          "    The first-boot script: runs once as root on the installed machine",
          "    (kiwi-role.service). Also usable on an existing %s system:" % (
              "Debian" if host.target in ("debian", "armbian") else "CoreOS/uCore"),
          "        scp %s.role.sh host:  &&  ssh host sudo bash %s.role.sh" % (host.name, host.name),
          "    It contains this host's secrets — keep it private.", ""]
    if bundle is not None:
        L += ["%s.stack/" % host.name,
              "    The rendered stack the script unpacks into %s:" % (host.role_settings.get("docker_dir") or "/home/user/docker"),
              "    docker-compose.yml and the module configs — for review; the script carries a copy.",
              "    quadlets/: the systemd units podman runs (runtime: podman); the compose file is the source."
              if getattr(bundle, "quadlets", None) else "    runtime: docker compose.",
              "    Container addresses: %s" % ", ".join("%s=%s" % kv for kv in bundle.container_ips.items()),
              "    On the machine: sudo kiwi-stack start|stop|update|status|logs", ""]
    if host.target == "coreos-ram":
        L += ["%s.bu / %s.ign" % (host.name, host.name),
              "    Butane and Ignition of the live system, which runs from RAM and touches no disk.",
              "    No role in it; its one secret is the SSH host key in secrets/ram/%s/." % host.name, "",
              "%s.iso" % host.name,
              "    The whole system: boot from it every time (a provider's CD-ROM, a stick).",
              "    After every boot it waits; kiwi-server watch (or apply) sends the role.",
              "    A new image is the OS update.", ""]
        return "\n".join(L)
    if host.target == "armbian":
        L += ["%s.img" % host.name,
              "    The board's Armbian (%s) with the first boot written in. Flash it to the SD card" % host.cfg["armbian"].get("board"),
              "    or the eMMC and boot: it makes the admin user with the fleet's keys, then the role runs.",
              "        sudo dd if=%s.img of=/dev/sdX bs=4M status=progress oflag=sync" % host.name, "",
              "On the board:",
              "    journalctl -u kiwi-adopt -u kiwi-role -f   follow the first boot", ""]
        return "\n".join(L)
    if host.target == "debian":
        L += ["%s.preseed.cfg + %s.kiwi-server/" % (host.name, host.name),
              "    What the Debian installer reads. The preseed is written for the ISO:",
              "    its late command copies the files from /cdrom/kiwi-server.", ""]
    else:
        L += ["%s.bu / %s.ign" % (host.name, host.name),
              "    Butane and Ignition. The ISO embeds the Ignition for the installed system."]
        if host.target == "ucore":
            L += ["    First boot rebases to %s (unsigned, reboot," % host.cfg["ucore"]["image"],
                  "    then signed, reboot); the role runs on the third boot."]
        L.append("")
    L += ["%s.iso" % host.name,
          "    Boot it and walk away: it installs to %s WITHOUT ASKING, reboots, and" % disk,
          "    the role runs on first boot. Write it with:",
          "        sudo dd if=%s.iso of=/dev/sdX bs=4M status=progress oflag=sync" % host.name, "",
          "On the machine:",
          "    journalctl -u kiwi-role -f            follow the first boot",
          "    cat /var/lib/kiwi-server/role.done    when it finished",
          "    sudo bash /var/lib/kiwi-server/role.sh --force     run the role again", ""]
    return "\n".join(L)


def tls_problem(host):
    """Why this host's TLS settings cannot work, or None. Checked by validate,
    so the surprise does not wait for render."""
    if "reverse-proxy" not in host.modules:
        return None
    tls = host.cfg.get("tls") or {}
    have = [k for k in ("tls_fullchain", "tls_privkey") if "reverse-proxy/" + k in host.module_files]
    if len(have) == 1:
        return "%s: reverse-proxy: tls_fullchain and tls_privkey go together — give both or neither" % host.name
    if not have and not tls.get("auto", True):
        return ("%s: reverse-proxy needs tls_fullchain and tls_privkey, or tls.auto: true "
                "for a certificate from the fleet CA" % host.name)
    return None


NOT_ENROLLED = "not enrolled yet"


def not_enrolled(error):
    """A node whose WireGuard config is not there yet: its master issues it once it runs.
    A step still to take, not a mistake in the fleet file."""
    return NOT_ENROLLED in error


def settings_errors(roles, ms, host):
    """Resolve the host's role and module settings; the errors, as strings."""
    if host.role not in roles or host.target not in TARGETS:
        return []
    role = roles[host.role]
    try:
        rolesmod.resolve_settings(role, host, ms)
    except KiwiError as e:
        if re.search(r"vpn-client\.wireguard_config: file not found", str(e)):
            return ["%s: %s: kiwi-server enroll %s %s, once its master runs"
                    % (host.name, NOT_ENROLLED, os.path.basename(host.fleet.path), host.name)]
        return [str(e)]
    p = tls_problem(host)
    if p:
        return [p]
    return stack_errors(ms, host, role)


def stack_errors(ms, host, role):
    """The stack rendered once and thrown away: a module's own validate:
    rules, a mesh address a module needs, two services on one port, a
    compose key the quadlet converter does not know — the errors `render`
    and `apply` would stop on, so `validate` reports them too."""
    if not role.stack:
        return []
    # a node open to the internet: checked as it will be rendered, with a master that hands on
    rp = host.module_settings.get("reverse-proxy") if "reverse-proxy" in host.modules else None
    stand_in = (rp is not None and role.node_type != "master" and rp.get("public") and not rp.get("via")
                and any(h.role == "master" for h in host.fleet.hosts.values()))
    if stand_in:
        rp["via"] = "192.0.2.1"
    # https at its hidden services: the certificate is issued at the render; here something stands in for it
    files, cert = host.module_files, []
    if rp is not None and "onion" in host.modules and (host.module_settings.get("onion") or {}).get("tls"):
        cert = [k for k in ("reverse-proxy/onion_fullchain", "reverse-proxy/onion_privkey") if k not in files]
        for k in cert:
            files[k] = b"stand-in\n"
    try:
        modmod.Renderer(ms).render(rolesmod.stack_spec(host, role))
    except KiwiError as e:
        return [str(e)]
    finally:
        if stand_in:
            rp.pop("via", None)
        for k in cert:
            files.pop(k, None)
    return []


def tls_needs_ca(host):
    """Does this host get its certificate from the fleet CA?"""
    tls = host.cfg.get("tls") or {}
    return ("reverse-proxy" in host.modules and tls.get("auto", True)
            and "reverse-proxy/tls_fullchain" not in host.module_files)


class Renderer:
    def __init__(self, fleet, roles, out_dir, tc, modset=None, media_dir=None):
        self.fleet, self.roles, self.out_dir, self.tc = fleet, roles, out_dir, tc
        self.media_dir = media_dir or out_dir
        self.ms = modset or modmod.discover(extra=sourcesmod.folders(fleet))
        self._scanned = False
        self._records = []
        self._node_lan = []
        self._needs_ca = False
        self._master_ip = ""
        self._groups = {}
        self._partners = {}
        self._admin = {}        # hostname -> the full names of its admin pages
        self.aliases = set()     # VPN names that send on to a public name (no service of their own)
        self._told = set()      # (machine, service): what was said once about a hidden service

    def standby_endpoints(self):
        """address:WireGuard port:SSH port of the master, then of each of its standbys; [] without one."""
        master = next((h for h in self.fleet.hosts.values() if h.role in self.roles and not h.standby_of
                       and self.roles[h.role].node_type == "master"), None)
        standbys = self.fleet.standbys(master.name) if master else []
        if not standbys:
            return []
        self.prepare(master)
        vs = master.module_settings.get("vpn-server") or {}
        port = int(vs.get("wg_port") or 51820)
        ends = ["%s:%d:%d" % (vs.get("wg_host"), port, int(master.admin.get("ssh_port") or 22))]
        for s in standbys:
            if s.standby_endpoint:
                ends.append("%s:%d:%d" % (s.standby_endpoint, port, int(s.admin.get("ssh_port") or 22)))
        return ends if len(ends) > 1 else []

    def prepare(self, host):
        role = self.roles[host.role]
        rolesmod.resolve_settings(role, host, self.ms)
        p = tls_problem(host)
        if p:
            raise KiwiError(p)
        return role

    # ---- fleet-wide knowledge ------------------------------------------------------
    def scan(self):
        """Every valid host once: the fleet's DNS records — (address, name)
        for every mesh host and every service name its reverse proxy answers
        for — and whether any host gets a certificate from the fleet CA. An
        invalid host elsewhere in the fleet is skipped with a warning."""
        if self._scanned:
            return
        self._scanned = True
        recs, needs_ca, master_ip, node_lan = [], False, "", []
        self._caches = []       # (LAN interface, hostname, registries, apt) of the nodes that run the cache module
        open_nodes = []         # (name, mesh address, public names, ports) of the nodes open to the internet
        mesh_only = []          # names whose service answers at the mesh address only: never a home address for them
        for h in self.fleet.hosts.values():
            try:
                if h.standby_of or h.role not in self.roles or h.validate(self.roles):     # a standby is its master's twin
                    continue
                role = self.prepare(h)
                if not role.stack:
                    continue
                needs_ca = needs_ca or tls_needs_ca(h)
                spec = rolesmod.stack_spec(h, role)
                if not spec.vpn_ip:
                    continue
                if role.node_type == "master" and not master_ip:
                    master_ip = spec.vpn_ip   # the nodes' DNS upstream and the mesh route's far end
                    self._groups = (h.module_settings.get("vpn-server") or {}).get("groups") or {}
                    self._partners = (h.module_settings.get("vpn-server") or {}).get("federation") or {}
                lan = static_lan(h)
                if lan and role.node_type != "master":      # a master's stack lives on the mesh, not its LAN
                    node_lan.append((spec.vpn_ip, lan.ip))
                    if "cache" in h.modules:
                        c = h.module_settings["cache"]
                        self._caches.append((lan, h.hostname, list(c.get("registries") or []), bool(c.get("apt"))))
                names = [h.hostname] + modmod.Renderer(self.ms).service_names(spec)
                self._admin[h.hostname] = modmod.Renderer(self.ms).admin_names(spec)
                # a VPN name that sends on to a public one: in the DNS and the certificate, but no service of its own
                rr = modmod.Renderer(self.ms)
                ordered = self.ms.resolve(spec.modules, spec.node_type, spec.without)
                _up, blocks = rr.nginx_blocks(spec, ordered, rr.base_context(spec, ordered))
                self.aliases.update(b["alias"] for b in blocks if b.get("alias") and b["alias"] != b["server_name"])
                mesh_only += modmod.Renderer(self.ms).mesh_only_names(spec)
                if role.node_type != "master":      # its public names (with a public proxy) and the ports its modules ask for
                    public, ports = modmod.Renderer(self.ms).exposure(spec)
                    if public or ports:
                        open_nodes.append((h.name, spec.vpn_ip, public, ports))
                for n in names:
                    if "*" not in n and (spec.vpn_ip, n) not in recs:
                        recs.append((spec.vpn_ip, n))
            except KiwiError as e:
                if not re.search(r"vpn-client\.wireguard_config: file not found", str(e)):   # not enrolled yet: no mesh name yet
                    util.warn("dns records: skipping %s: %s" % (h.name, e))
        # a node open to the internet: the master hands its public names and ports on. On the
        # VPN those names stay the node's own (the records above): a device reaches the service
        # there directly, when its group may reach that machine
        exposed = {"names": {}, "ports": []}
        for name, ip, public, ports in open_nodes if master_ip else []:
            for n in public:
                exposed["names"][n] = ip
            for p in ports:
                proto = p.get("proto") or "tcp"
                taken = next((x for x in exposed["ports"] if (x["port"], x["proto"]) == (p["port"], proto)), None)
                if taken:
                    raise KiwiError("%s: the master's port %d/%s is handed on to %s already: one machine per public port"
                                    % (name, p["port"], proto, taken["to"].split(":")[0]))
                exposed["ports"].append({"port": p["port"], "to": "%s:%d" % (ip, p["to"]),
                                         "proxy_protocol": p["proxy_protocol"], "proto": proto})
        self._exposed = exposed
        self._mesh_only = mesh_only
        self._records, self._needs_ca, self._master_ip, self._node_lan = recs, needs_ca, master_ip, node_lan

    def dns_records(self):
        self.scan()
        return self._records

    def use_cache(self, host, role):
        """The cache node on this host's own LAN, when the fleet has one, as the host's registry
        mirrors and package proxy, unless the fleet file sets them (docs/design/local-first.md).
        Only there: over the mesh every byte would cross the master."""
        self.scan()
        own = static_lan(host)
        if not own or not role.stack or role.node_type == "master":
            return
        cache = next((c for c in self._caches if c[0].ip in own.network), None)
        if not cache:
            return
        lan, name, registries, apt = cache
        rs = host.role_settings
        if not rs.get("registry_mirrors") and registries:
            rs["registry_mirrors"] = {r: "cache.%s/%s" % (name, r) for r in registries}
        if not rs.get("apt_proxy") and apt and host.target in ("debian", "armbian"):
            rs["apt_proxy"] = "http://%s:3142" % lan.ip

    def lan_addresses(self, host, role):
        """{mesh address: LAN address} of the fleet's nodes on this node's own LAN.
        Its Pi-hole and hosts file answer with those, so machines on one switch
        reach each other across it and not through the master. A master's Pi-hole
        answers mesh clients, who are anywhere: it keeps the mesh addresses."""
        self.scan()
        own = static_lan(host)
        if not own or role.node_type == "master":
            return {}
        return {mesh: str(ip) for mesh, ip in self._node_lan if ip in own.network}

    def ca_dir(self, host):
        tls = host.cfg.get("tls") or {}
        return self.fleet.resolve_path(tls.get("ca_dir") or "secrets/ca")

    def tls_for(self, host):
        if not tls_needs_ca(host):
            return
        tls = host.cfg.get("tls") or {}
        mf = host.module_files
        key, full = certs.ensure_host_cert(self.ca_dir(host), host.hostname,
                                           days=int(tls.get("cert_days") or certs.CERT_DAYS),
                                           ca_days=int(tls.get("days") or certs.DAYS),
                                           name_constraints=tls.get("name_constraints") or [])
        mf["reverse-proxy/tls_fullchain"] = util.read_bytes(full)
        mf["reverse-proxy/tls_privkey"] = util.read_bytes(key)
        host.module_settings["reverse-proxy"]["tls_fullchain"] = "fullchain.pem"
        host.module_settings["reverse-proxy"]["tls_privkey"] = "privkey.pem"

    def ca_cert(self, host):
        """The fleet CA, embedded so the machine trusts the others' services.
        Created here when any host in the fleet gets a certificate from it,
        so a bare host rendered first trusts it too; a fleet that never
        issues a certificate has no CA and embeds nothing."""
        tls = host.cfg.get("tls") or {}
        if not tls.get("auto", True):
            return None
        self.scan()
        pem = os.path.join(self.ca_dir(host), "kiwiCA.pem")
        if self._needs_ca:
            _key, pem = certs.ensure_ca(self.ca_dir(host), int(tls.get("days") or certs.DAYS),
                                        tls.get("name_constraints") or [])
        return util.read_bytes(pem) if os.path.isfile(pem) else None

    def bundle(self, host, role):
        if not role.stack:
            return None
        records = self.dns_records()   # the scan re-resolves every host, this one included,
        self.tls_for(host)             # so the certificate files are injected afterwards
        if "onion" in host.modules:
            # its hidden services: their keys and addresses from the seed, and the proxy's site for each
            keys, sites = {}, {}
            mine, usable, owner = site_names(self.ms, host, role, with_usable=True)
            for k in onionmod.for_host(self.fleet.seed(), host):
                if k["service"] not in mine:
                    raise KiwiError("%s: onion.services: it has no site called %s (it has: %s)"
                                    % (host.name, k["service"], ", ".join(sorted(mine)) or "none"))
                if k["service"] not in usable and (host.name, k["service"]) not in self._told:
                    self._told.add((host.name, k["service"]))       # an old one keeps working: said once, not refused
                    util.warn("%s: the hidden service %s is of a site not marked as usable over Tor: it may send "
                              "visitors to its name on the VPN, or not work at all" % (host.name, k["service"]))
                keys[k["service"]] = {"address": k["address"], "secret": k["secret"], "public": k["public"],
                                      "clients": {c["name"]: c["public"] for c in k["clients"]}}
                sites[mine[k["service"]]] = k["address"]
            host.module_settings["onion"]["keys"] = keys
            host.module_settings["reverse-proxy"]["onion"] = sites
            if host.module_settings["onion"].get("tls") and keys:
                # https at the hidden services: one certificate for this machine's addresses. From the
                # fleet's CA when it may vouch for them; a CA limited to the fleet's names may not, and
                # then the certificate is its own (a stranger's browser warns in both cases)
                tls = host.cfg.get("tls") or {}
                limits = tls.get("name_constraints") or []
                own = bool(limits) and not any(str(x).lstrip(".") == "onion" for x in limits)
                key, full = certs.ensure_names_cert(self.ca_dir(host), "%s.onion-services" % host.hostname,
                                                    [k["address"] for k in keys.values()],
                                                    days=int(tls.get("cert_days") or certs.CERT_DAYS),
                                                    ca_days=int(tls.get("days") or certs.DAYS), name_constraints=limits, own=own)
                host.module_files["reverse-proxy/onion_fullchain"] = util.read_bytes(full)
                host.module_files["reverse-proxy/onion_privkey"] = util.read_bytes(key)
                host.module_settings["reverse-proxy"]["onion_fullchain"] = "onion-fullchain.pem"
                host.module_settings["reverse-proxy"]["onion_privkey"] = "onion-privkey.pem"
            # a module that wants to know the addresses of its own sites (a setting `onion`) is told:
            # Jabber answers at its hidden service's name too, Paperless takes a login sent from there
            for module in {owner.get(n) for n in keys} - {"", "onion", "reverse-proxy"}:
                if any(s.key == "onion" for s in self.ms.get(module).settings):
                    host.module_settings[module]["onion"] = {n: k["address"] for n, k in keys.items() if owner.get(n) == module}
        if "reverse-proxy" in host.modules:
            # a node open to the internet: its public names and ports come in through the master
            rp = host.module_settings["reverse-proxy"]
            if role.node_type == "master" and (self._exposed["names"] or self._exposed["ports"]):
                rp["exposed"] = self._exposed
            elif role.node_type != "master" and rp.get("public") and self._master_ip:
                rp["via"] = self._master_ip
        for module in CONNECTABLE:
            # the network's accounts sign people in here (kiwi-server accounts connect)
            path = accounts_file(self.fleet, host.name, module)
            if module in host.modules and os.path.isfile(path):
                a = json.loads(util.read_text(path))
                host.module_settings[module].update(accounts_client=a["client"], accounts_issuer=a["issuer"],
                                                    accounts_secret=a["secret"])
        if role.node_type == "master" and "tang" in host.modules and "vpn-server" in host.modules:
            # a ring here (away): the VPN server publishes it, to the home's addresses only
            host.module_settings["vpn-server"]["local_rings"] = dict(host.module_settings["tang"].get("rings") or {})
        if "reverse-proxy" in host.modules and accessmod.admin_ranges(self._groups):
            # the admin areas inside a site (a master's too): not for the groups without the admin pages
            host.module_settings["reverse-proxy"].setdefault("access", {})["admin"] = accessmod.admin_ranges(self._groups)
        if role.node_type != "master" and "reverse-proxy" in host.modules:
            # a VPN group that reaches this node for some services only: refused at its other names
            pages = self._admin.get(host.hostname) or []
            rules = accessmod.node_rules(self._groups, host.name, host.hostname, pages)
            if rules:
                host.module_settings["reverse-proxy"]["access"] = dict(rules, **{
                    k: v for k, v in (host.module_settings["reverse-proxy"].get("access") or {}).items() if k == "admin"})
            if pages and self._partners:        # a linked network opens an admin page only when it is named for it
                rp = host.module_settings["reverse-proxy"]
                rp["federation"] = fedmod.admin_rules(rp.get("federation"), self._partners, host.name, pages,
                                                     str(self.fleet.defaults.get("domain") or "home"))
        if role.node_type != "master" and "vpn-client" in host.modules and self._master_ip:
            # its master has a standby: the tunnel moves there when the master is gone (docs/design/standby.md)
            vc = host.module_settings["vpn-client"]
            if vc.get("vpn_provider") == "custom" and vc.get("wireguard_config") and vc.get("vpn_type", "wireguard") == "wireguard":
                ends = self.standby_endpoints()
                if ends:
                    vc.update(standby_endpoints=ends, standby_ping=self._master_ip)
        spec = rolesmod.stack_spec(host, role, records, master_ip=self._master_ip, ca_cert=self.ca_cert(host),
                                   lan_addresses=self.lan_addresses(host, role), mesh_only=self._mesh_only)
        b = modmod.Renderer(self.ms).render(spec)
        b.no_resolved_stub = any(bool(self.ms.get(m).host.get("disable_resolved_stub")) for m in b.modules)
        return b

    def script(self, host):
        role = self.prepare(host)
        self.use_cache(host, role)
        b = self.bundle(host, role)
        return rolesmod.render_script(host, role, VERSION, bundle=b, ca_cert=self.ca_cert(host)), b

    def write_stack(self, o, bundle, host, role):
        if os.path.isdir(o.stack):
            shutil.rmtree(o.stack)
        os.makedirs(o.stack, mode=0o700)
        for rel, (content, mode) in bundle.files.items():
            p = os.path.join(o.stack, rel)
            if isinstance(content, bytes):
                util.write_bytes(p, content, mode)
            else:
                util.write_text(p, content, mode)
        for name, text in bundle.units.items():
            util.write_text(os.path.join(o.stack, "host-units", name), text)
        for name, text in getattr(bundle, "quadlets", {}).items():
            util.write_text(os.path.join(o.stack, "quadlets", name), text)
        util.write_text(os.path.join(o.stack, "stack.sh"), rolesmod.stack_manifest(host, role, VERSION, bundle), 0o644)
        self.check_compose(os.path.join(o.stack, "docker-compose.yml"))

    def check_compose(self, path):
        """`docker compose config` is a real validator and needs no daemon;
        when the plugin is around, a broken compose never leaves here."""
        if not util.which("docker"):
            return
        r = subprocess.run(["docker", "compose", "-f", path, "config", "-q"],
                           capture_output=True, text=True, env={**os.environ, "DOCKER_HOST": "unix:///nonexistent"})
        if r.returncode != 0:
            err = (r.stderr or "").strip()
            if "is not a docker command" in err or "unknown command" in err:
                return
            raise KiwiError("rendered docker-compose.yml is invalid:\n  %s" % err.replace("\n", "\n  "))

    def render(self, host):
        o = Outputs(self.out_dir, host, self.media_dir)
        os.makedirs(o.dir, exist_ok=True)
        if o.media != o.dir:
            retire_rendered(o.media, host.name)
        script, bundle = self.script(host)
        util.write_text(o.script, script, 0o600)
        produced = [o.script]
        if bundle is not None:
            self.write_stack(o, bundle, host, self.roles[host.role])
            produced.append(o.stack)
        elif os.path.isdir(o.stack):
            shutil.rmtree(o.stack)
        if host.target == "debian":
            util.write_text(o.preseed, debian_t.preseed(host, VERSION), 0o600)
            if os.path.isdir(o.files):
                shutil.rmtree(o.files)
            for rel, (text, mode) in debian_t.iso_files(host, script).items():
                util.write_text(os.path.join(o.files, rel), text, mode)
            os.chmod(o.files, 0o700)
            produced += [o.preseed, o.files]
            for stale in (o.bu, o.ign):
                if os.path.exists(stale):
                    os.unlink(stale)
        elif host.target == "armbian":
            for stale in (o.bu, o.ign, o.preseed, o.files):     # the image is made at build time from the script
                if os.path.isdir(stale):
                    shutil.rmtree(stale)
                elif os.path.exists(stale):
                    os.unlink(stale)
        else:
            host.unlock_standin = False
            if unlock.wanted(host):      # the thumbprints go into the Ignition: Ignition binds the disk
                try:
                    host.unlock_pins = unlock.pins(host, unlock.ring_servers(
                        self.fleet, lambda x: rolesmod.resolve_settings(self.roles[x.role], x, self.ms)))
                except KiwiError as e:
                    # its rings do not answer yet (the machine serving them is installed later): the
                    # machine's TPM opens the disk until the first apply binds them
                    host.unlock_pins = None
                    host.unlock_standin = host.target in ("coreos", "ucore")
                    if not host.unlock_standin:
                        raise
                    util.warn("%s: its rings do not answer yet (%s): its TPM opens the disk until the first apply binds them"
                              % (host.name, e))
            elif host.cfg.get("encryption", True) and host.target in ("coreos", "ucore"):
                # encrypted as every machine is: no ring yet, so the TPM until the first apply leaves the
                # key for its screen instead (docs/design/encryption.md)
                host.unlock_standin = True
            # coreos-ram: the live system's Ignition, without the role (apply puts it on the state partition)
            util.write_text(o.bu, ram_t.render(host) if host.target == "coreos-ram" else coreos_t.render(host, script), 0o600)
            produced.append(o.bu)
            how, why = self.tc.how("butane")
            if how:
                coreos_t.to_ignition(self.tc, o.bu, o.ign)
                os.chmod(o.ign, 0o600)
                produced.append(o.ign)
            else:
                util.warn("%s: Ignition not generated — %s" % (host.name, why))
            for stale in (o.preseed, o.files):
                if os.path.isdir(stale):
                    shutil.rmtree(stale)
                elif os.path.exists(stale):
                    os.unlink(stale)
        util.write_text(o.readme, host_readme(host, o, bundle))
        return o, produced

    def build(self, host, cache_dir):
        o, _ = self.render(host)
        if host.target == "coreos-ram":
            if not os.path.isfile(o.ign):
                raise KiwiError("%s: no Ignition config — butane is needed (see kiwi-server doctor)" % host.name)
            os.makedirs(o.media, exist_ok=True)
            ram_t.iso(self.tc, host, o.ign, o.iso, cache_dir)
            # the live system carries no role; its one secret is the machine's SSH host key
            util.write_text(o.bare_mark, "this ISO carries no role and none of its secrets, only the machine's SSH host key\n", 0o644)
            return o
        if host.target != "armbian" and not host.cfg.get("disk"):
            raise KiwiError("%s: disk: is required to build an ISO (it is wiped on boot)" % host.name)
        os.makedirs(o.media, exist_ok=True)
        if host.target == "armbian":       # the board's own image, the first boot written in
            base = armbian_t.base_image(self.tc, host.cfg["armbian"], cache_dir)
            armbian_t.image(self.tc, host, util.read_text(o.script), base, o.iso, o.work)
        elif host.target == "debian":
            base = isomod.debian_base_iso(self.tc, host.cfg["debian"], cache_dir)
            isomod.debian_iso(self.tc, host, o.preseed, o.files, o.iso, base, o.work)
        else:
            if not os.path.isfile(o.ign):
                raise KiwiError("%s: no Ignition config — butane is needed (see kiwi-server doctor)" % host.name)
            isomod.coreos_iso(self.tc, host, o.ign, o.iso, cache_dir)
        # what the ISO carries, kept beside it: the rendered role may be gone with the tmpfs
        if "KS_STACK=0" in util.read_text(o.script):
            util.write_text(o.bare_mark, "this ISO carries no role and none of its secrets\n", 0o644)
        elif os.path.isfile(o.bare_mark):
            os.remove(o.bare_mark)
        return o


# ---- commands -----------------------------------------------------------------------

def load(args, need_roles=True):
    st = store.Store.for_fleet(args.fleet)
    if st.locked() and not os.environ.get("KIWI_SECRETS_DIR"):     # (a private copy is open for this process)
        # before anything looks in secrets/: a missing seed would be made anew
        raise KiwiError("the secret store is locked: kiwi-server secrets unlock %s" % args.fleet)
    fleet = config.Fleet.load(args.fleet)
    remote.configure(fleet.resolve_path("secrets/known_hosts"), fleet_identities(fleet))
    if st.exists() and st.is_open() and not getattr(args, "porcelain", False):
        store.touch(st, args.fleet)     # in use: it locks itself the idle time from now (the app's looks do not count)
    roles = rolesmod.discover() if need_roles else None
    ms = modset(fleet) if need_roles else None
    return fleet, roles, ms


def modset(fleet=None):
    """The modules: the built-in ones, and the fleet's module sources (sources.py), fetched where needed."""
    if fleet is None:
        return modmod.discover()
    return modmod.discover(extra=sourcesmod.folders(fleet))


def modset_for(args):
    """The modules for a command whose fleet is optional (--fleet): the schema of the page and the app."""
    path = getattr(args, "fleet_file", None)
    return modset(config.Fleet.load(path) if path else None)


def warn_literal_secrets(h, ms):
    """Passwords written in the fleet file that need not be there. A warning,
    not an error, until there is a secret store to put the rest in (ROADMAP)."""
    msgs = []
    if h.cfg["admin"].get("password"):
        msgs.append("admin.password is a literal in the fleet file: password_hash keeps it out (openssl passwd -6)")
    names = []
    for m in h.modules:
        given = h.role_settings_raw.get(m) or {}
        names += ["%s.%s" % (m, s.key) for s in ms.get(m).settings if s.generate and given.get(s.key)]
    if names:
        msgs.append("%s: literal in the fleet file, and generated from the seed when left out "
                    "(kiwi-server secrets show); leaving one out on a running machine changes that password"
                    % ", ".join(names))
    for msg in msgs:
        if msg not in h.warnings:      # settings are checked more than once per command
            h.warnings.append(msg)


def check(fleet, roles, ms, hosts, porcelain=False, strict=True):
    errs = list(fleet.errors) + config.unknown_keys(getattr(fleet, "defaults_raw", {}), roles, "defaults")
    for h in hosts:
        errs += h.validate(roles)
        errs += settings_errors(roles, ms, h)
        warn_literal_secrets(h, ms)
    if errs and strict:
        if porcelain:
            print(json.dumps({"ok": False, "errors": errs}, indent=2))
        else:
            for e in errs:
                print("error: %s" % e, file=sys.stderr)
        sys.exit(EXIT_INVALID)
    return errs


def cmd_init(args):
    """A new fleet: asked step by step in a terminal, from answers the app collected, or the
    commented example (--example)."""
    if args.questions:
        found = setupmod.detect()
        print(json.dumps({"found": found, "steps": setupmod.questions(found),
                          "suggestions": {"masters": setupmod.MASTERS, "nodes": setupmod.NODES,
                                          "addresses": found["addresses"]}}, indent=2))
        return
    if args.provider_login:     # for the app: sign in through the browser; the page to open is printed
        providers.get(args.provider).login(args.provider_login, lambda url: print(url, flush=True), args.api)
        return
    if args.provider_servers:   # for the app: the servers of the account that signed in
        print(json.dumps(providers.get(args.provider).servers(args.provider_servers, args.api)))
        return
    if args.check:              # for the app: what is wrong with these answers, by question
        print(json.dumps(setupmod.check(json.load(sys.stdin if args.check == "-" else open(args.check)))))
        return
    asking = not (args.answers or args.example or args.domain)
    # a wizard's network lives in ~/kiwi unless a place is named, as the app's does
    dest = args.path or (os.path.join(os.path.expanduser("~/kiwi"), "fleet.yaml") if asking else "fleet.yaml")
    if os.path.exists(dest):
        raise KiwiError("%s exists — not overwriting it%s" % (dest, "" if args.path else
                        ": name another file, kiwi-server init ~/kiwi-2/fleet.yaml"))
    if asking and not sys.stdin.isatty():
        raise KiwiError("the questions need a terminal, and this is none. Run kiwi-server init in a terminal, give the "
                        "answers as a file (init --answers FILE, see init --questions), or take the commented example "
                        "(init --example)")
    if args.answers or asking:
        if args.answers:
            answers = json.load(sys.stdin if args.answers == "-" else open(args.answers))
        else:
            print("A new network in %s. Enter takes what is in brackets." % shown_path(os.path.abspath(dest)))
            def lookup(provider, show):
                who = providers.get(provider)
                token = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/dev/shm", "kiwi-%s-%d.token" % (who.id, os.getpid()))
                who.login(token, lambda url: show("   Open this page and sign in to %s:\n     %s\n"
                                                  "   Waiting until you have done that…" % (who.name, url)), args.api)
                return who.servers(token, args.api), token
            try:
                answers = setupmod.ask(everything=args.all, lookup=lookup)
            except (EOFError, KeyboardInterrupt):
                raise KiwiError("stopped. Nothing was written; kiwi-server init starts again")
        fleet_dir = os.path.dirname(os.path.abspath(dest))
        os.makedirs(fleet_dir, mode=0o700, exist_ok=True)
        data, made = setupmod.make(answers, fleet_dir)
        token = answers.get("provider_token")
        if token and os.path.isfile(token):     # the login made while asking belongs to this network now
            keep = os.path.join(fleet_dir, providers.get(answers["master_where"]).token_file())
            os.makedirs(os.path.dirname(keep), mode=0o700, exist_ok=True)
            util.write_bytes(keep, util.read_bytes(token), 0o600)
            os.remove(token)
        setupmod.write(dest, data)
        util.say("wrote %s%s" % (dest, "".join(", %s" % os.path.relpath(p, fleet_dir) for p in made)))
        if not args.no_store and answers.get("store", True):
            st = store.Store.for_fleet(dest)
            recovery, saved = st.init()
            util.say("the keys are encrypted now (%s) and open while you work" % store.FILE)
            if not saved:
                util.warn("no keyring took this computer's key: unlocking will ask for the recovery key below")
            print("\nYour recovery key. Write it down now, on paper or in a password manager.\n"
                  "It is shown once. If this computer is lost, it is the only way back to your network's keys:\n\n  %s\n" % recovery)
            if asking:
                input("Written down? Press Enter. ")
        print("\n".join(setupmod.next_steps(shown_path(os.path.abspath(dest)))))
        return
    text = util.read_text(os.path.join(util.home_dir(), "examples", "fleet.yaml"))
    if args.domain:
        d = args.domain.strip().strip(".").lower()
        if not d or not all(config._LABEL.match(x) for x in d.split(".")):
            raise KiwiError("the domain must be DNS labels: home, internal, my.corp")
        adv = config.domain_advice(d)
        if adv and adv[0] == "error":
            raise KiwiError(adv[1])
        if adv:
            util.warn(adv[1])
        text = (text.replace("domain: home ", "domain: %s " % d).replace("name_constraints: [home]", "name_constraints: [%s]" % d)
                .replace(".home.conf", ".%s.conf" % d).replace("sh3.home", "sh3.%s" % d))
    util.write_text(dest, text, 0o600)
    util.say("wrote %s — edit it, then: kiwi-server validate %s" % (dest, dest))


def cmd_names(args):
    """Names for new machines: a fruit dish for a master, a fruit for anything else, free in
    the fleet when one is given. JSON, for the app."""
    taken = set()
    if args.fleet:
        import yaml
        taken = set((yaml.safe_load(util.read_text(args.fleet)) or {}).get("hosts") or {})
    print(json.dumps({"master": setupmod.free_name("master", taken), "node": setupmod.free_name("node", taken),
                      "masters": setupmod.MASTERS, "nodes": setupmod.NODES}))


def cmd_add(args):
    """A new host in the fleet file, named after a fruit (a node) or a fruit dish (a master)
    unless a name is given."""
    import yaml
    data = yaml.safe_load(util.read_text(args.fleet)) or {}
    taken = set(data.get("hosts") or {})
    off = setupmod.alone(data)
    name = args.name or setupmod.free_name(args.role, taken)
    setupmod.add_host(args.fleet, name, args.role, args.preset or "", args.target or "", args.address or "")
    util.say("added %s as a %s to %s" % (name, args.role, args.fleet))
    fname = os.path.basename(args.fleet)
    if args.role == "master" and off:   # a network without a master gets one: its machines go onto the VPN
        moved = setupmod.onto_the_vpn(args.fleet)
        util.say("%s go%s onto the VPN: build and install %s first%s, then for each: kiwi-server enroll %s <machine>, "
                 "kiwi-server apply %s <machine>" % (", ".join(moved) or "no machine", "es" if len(moved) == 1 else "",
                                                       name, "" if args.address else " (set its wg_host first)", fname, fname))
    elif args.role == "master":
        util.say("next: %skiwi-server build %s %s" % ("" if args.address else "set its wg_host (the public address) in the "
                                                      "fleet file, then ", fname, name))
    elif args.role == "node" and off:
        util.say("next: give it its fixed address at home (network.address in the fleet file); without a master its "
                 "services answer there. Then kiwi-server build %s %s" % (fname, name))
    elif args.role == "node":
        util.say("next: once its master runs, kiwi-server enroll %s %s; then kiwi-server build %s %s" % (fname, name, fname, name))
    else:
        util.say("next: kiwi-server build %s %s" % (fname, name))


def cmd_remove(args):
    """A machine leaves the network for good: its place on the VPN, its keys here, its block
    in the fleet file. The machine itself is not touched: wipe it, or switch it off."""
    fleet, roles, ms = load(args)
    path, name = args.fleet, args.host
    h = fleet.hosts.get(name)
    if h is None:
        raise KiwiError("no such machine: %s" % name)

    def is_master(x):
        return x.role in roles and roles[x.role].node_type == "master"
    others = [x for x in fleet.hosts.values() if x is not h]
    if not others:
        raise KiwiError("%s is the last machine of this network. A network without machines is its folder: delete %s "
                        "when you do not want it any more" % (name, shown_path(fleet.dir)))
    if is_master(h) and not any(is_master(x) for x in others):
        raise KiwiError("%s is the master of %d other machine(s): they would lose their network. Remove them first, or "
                        "set up another master" % (name, len(others)))
    if not args.yes:
        if not sys.stdin.isatty():
            raise KiwiError("this takes %s out of the network for good: add --yes to go ahead" % name)
        if input("%s leaves the network for good. Type its name to go ahead: " % name).strip() != name:
            raise KiwiError("not confirmed, nothing touched")
    # who still counts on it: said, not changed (their settings are the owner's)
    for x in others:
        raw = x.role_settings_raw
        if h.hostname in ((raw.get("backup-server") or {}).get("clients") or []):
            util.warn("%s still lists %s as a machine whose backups it takes (backup-server.clients)" % (x.name, h.hostname))
        if any(h.hostname in str(u) for u in (raw.get("backup") or {}).get("servers") or []):
            util.warn("%s still sends its backups to %s (backup.servers): it has one copy less from now on" % (x.name, name))
        via = (x.cfg.get("unlock") or {})
        if name in str(via.get("via") or "") or any(name == str(r.get("host")) for r in (via.get("rings") or []) if isinstance(r, dict)):
            util.warn("%s unlocks its disk with the help of %s: give it another ring before its next restart" % (x.name, name))
    # its place on the VPN
    if not is_master(h):
        try:
            master = _master_of(fleet, roles, ms, args.master)
            vs = master.module_settings.get("vpn-server") or {}

            def drop(url):
                api = wgeasy.WgEasy(url, vs["wg_password"]).login()
                c = api.client(h.hostname)
                if c is not None:
                    api.delete(c["id"])
                return c is not None
            if args.api:
                was = drop(args.api)
            else:
                with remote.Tunnel(remote.reach(master, args.ssh), 51821) as url:
                    was = drop(url)
            if was:
                util.say("%s is off the VPN: its key no longer works" % name)
                if not args.api:
                    try:
                        keep_identity(fleet, master, args.ssh)
                    except KiwiError as e:
                        util.warn("%s: the fleet's copy of its WireGuard state was not refreshed: %s" % (master.name, e))
        except KiwiError as e:
            util.warn("its place on the VPN could not be taken away (%s): do it on the master's VPN page (wg.<master>), "
                      "or run this again when the master answers" % str(e).splitlines()[0])
    # what this computer keeps of it
    for rel in ("secrets/%s.conf" % h.hostname, "secrets/ram/%s" % name):
        p = fleet.resolve_path(rel)
        if os.path.isdir(p):
            shutil.rmtree(p)
        elif os.path.isfile(p):
            os.remove(p)
    known = fleet.resolve_path("secrets/known_hosts")
    if os.path.isfile(known):
        port = remote.ssh_port(h)
        for a in remote.addresses(h):
            for n in (a, "[%s]:%d" % (a, port)):
                subprocess.run(["ssh-keygen", "-q", "-R", n, "-f", known], capture_output=True)
        if os.path.isfile(known + ".old"):
            os.remove(known + ".old")
    for d in {os.path.join(out_dir_for(args, fleet), name), os.path.join(media_dir_for(args, fleet), name)}:
        if os.path.isdir(d):
            shutil.rmtree(d)
    state = fleet_state(fleet)
    for key in ("applied", "backup_clients"):
        (state.get(key) or {}).pop(name, None)
    try:
        util.write_text(_state_path(fleet), json.dumps(state, indent=1, sort_keys=True) + "\n", 0o644)
    except OSError:
        pass
    setupmod.remove_host(fleet.path, name)
    util.say("%s is out of %s. The machine itself still has what it had: wipe it, or switch it off" % (name, shown_path(fleet.path)))


def cmd_validate(args):
    fleet, roles, ms = load(args)
    hosts = fleet.select(args.hosts)
    errs = check(fleet, roles, ms, hosts, args.porcelain, strict=False)
    warns = list(fleet.warnings) + ["%s: %s" % (h.name, w) for h in hosts for w in h.warnings]
    # a machine that is not on the VPN yet is a step still to take, not a mistake in the file
    steps = [e for e in errs if not_enrolled(e)]
    errs = [e for e in errs if not not_enrolled(e)]
    if args.porcelain:
        print(json.dumps({"ok": not errs, "errors": errs, "warnings": warns, "steps": steps}, indent=2))
    else:
        for e in errs:
            print("error: %s" % e, file=sys.stderr)
        for w in warns:
            util.warn(w)
        for e in steps:
            util.say("still to do: %s" % e)
        if not errs:
            util.say("%s: %d host(s) valid" % (os.path.basename(fleet.path), len(hosts)))
            for name, x in fedmod.partners(fleet.dir).items():
                if x["exit"]:
                    util.say("its devices leave to the internet through %s, over the link (federation.yaml: exit)" % name)
                if x["internet"]:
                    util.say("%s leaves to the internet through this network (federation.yaml: internet)" % name)
    if errs:
        sys.exit(EXIT_INVALID)


def cmd_list(args):
    fleet, roles, ms = load(args)
    out_dir = out_dir_for(args, fleet)
    rows = []
    r = Renderer(fleet, roles, out_dir, None, ms)
    domain, master_ip, mesh, _mtu = fleet_facts(fleet, roles, ms, r)
    records = r.dns_records()
    for h in fleet.hosts.values():
        errs = h.validate(roles)
        if not errs:
            errs = settings_errors(roles, ms, h)
        o = Outputs(out_dir, h, media_dir_for(args, fleet))
        mesh_ip = (h.role_settings or {}).get("vpn_ip") or (
            master_ip if h.role in roles and roles[h.role].node_type == "master" else "")
        nxt = next_step(fleet, roles, h, errs, o)
        rows.append({"name": h.name, "hostname": h.hostname, "target": h.target, "role": h.role, "standby_of": h.standby_of or "",
                     "phase": nxt[0], "next": nxt[1], "here": starter_host(fleet) == h.name, "provider": (h.cfg.get("vps") or {}).get("provider") or "",
                     "on_done": (h.cfg.get("install") or {}).get("on_done", "poweroff"), "admin_user": h.admin["user"],
                     "applied": (fleet_state(fleet).get("applied") or {}).get(h.name),
                     "modules": list(h.modules), "disk": h.cfg.get("disk"),
                     "address": h.cfg["network"].get("address") if not h.cfg["network"].get("dhcp", True) else "dhcp",
                     "vpn_ip": mesh_ip,
                     "sites": [n for ip, n in records if mesh_ip and ip == mesh_ip and n != h.hostname and n not in r.aliases],
                     # what each site is, in words: the app and the page say the same
                     "site_labels": {n: healthmod.label(n) for ip, n in records if mesh_ip and ip == mesh_ip and n != h.hostname and n not in r.aliases},
                     "admin_sites": sorted(r._admin.get(h.hostname) or []),
                     "logins": [] if errs else loginsmod.listed(h, roles, ms),     # without the passwords
                     "games": [] if errs else game_servers(h),      # the game page lists them
                     "status": o.status(), "dir": o.dir, "iso": o.iso,
                     "updates": h.cfg.get("updates") or {},
                     "errors": errs, "warnings": h.warnings})
    if args.porcelain:
        groups, default_group = {}, None     # the master's client groups: where a new device may go
        for h in fleet.hosts.values():
            if h.role in roles and roles[h.role].node_type == "master" and not groups:
                full = ((h.module_settings or {}).get("vpn-server") or {}).get("groups") or {}
                groups = {g: (v or {}).get("subnet") for g, v in full.items()}
                default_group = accessmod.default_group(full)
        play = None             # the game page, where a control node has it: its name and who may use it
        for h in fleet.hosts.values():
            c = (h.module_settings or {}).get("control") or {}
            if "control" in (h.modules or []) and c.get("play_page") and not play:
                play = {"host": h.name, "site": c.get("play_hostname") or "play." + h.hostname, "group": c.get("play_group") or "games"}
        print(json.dumps({"fleet": fleet.path, "output_dir": out_dir, "domain": domain, "mesh": mesh,
                          "default_group": default_group, "play": play,
                          # modules beyond the built-in ones, and which source each came from
                          "module_sources": [dict(x, modules=sorted(m.name for m in r.ms.modules.values() if m.source == x["name"]))
                                             for x in fleet.module_sources],
                          "master_ip": master_ip, "groups": groups, "hosts": rows}, indent=2))
        return
    fmt = "%-12s %-24s %-7s %-11s %-16s %s"
    print(fmt % ("NAME", "HOSTNAME", "TARGET", "ROLE", "ADDRESS", "OUTPUT"))
    for r in rows:
        s = r["status"]
        have = [k for k in ("script", "stack", "config", "ignition", "iso") if s.get(k)]
        state = "INVALID" if r["phase"] == "invalid" else (", ".join(have) if have else "-")
        print(fmt % (r["name"], r["hostname"], r["target"], "standby" if r["standby_of"] else r["role"], r["address"] or "", state))
        if r["standby_of"]:
            print("    %s's standby: the same master on its own machine (docs/design/standby.md)" % r["standby_of"])
        for e in r["errors"]:
            if r["phase"] == "invalid":
                print("    error: %s" % e)
        print("    next: %s" % r["next"])


def usb_sticks():
    """[(device, size and model)] of the USB drives plugged in right now."""
    try:
        r = subprocess.run(["lsblk", "-dnpo", "NAME,TRAN,RM,SIZE,MODEL"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for line in r.stdout.splitlines():
        f = line.split(None, 4)
        if len(f) >= 4 and f[1] == "usb":
            out.append((f[0], " ".join(f[3:])))
    return out


def cmd_next(args):
    """The next step of building the network, done: the master first, then one machine after the
    other. Run it again until it says that everything runs. The app's Getting started list
    is the same steps as buttons."""
    fleet, roles, ms = load(args)
    out_dir = out_dir_for(args, fleet)
    path = args.fleet

    def is_master(h):
        return h.role in roles and roles[h.role].node_type == "master"
    todo = []
    for h in sorted(fleet.hosts.values(), key=lambda x: 0 if is_master(x) else 1):
        errs = h.validate(roles) or settings_errors(roles, ms, h)
        o = Outputs(out_dir, h, media_dir_for(args, fleet))
        phase, text = next_step(fleet, roles, h, errs, o)
        if phase != "applied":
            todo.append((h, phase, text, errs, o))
    masters_pending = [t[0].name for t in todo if is_master(t[0]) and not t[0].standby_of]   # (a standby waits for nobody)
    if args.host:               # this one, whatever is on record: it was installed anew
        if args.host not in fleet.hosts:
            raise KiwiError("no such machine: %s" % args.host)
        again = fleet.hosts[args.host]
        todo = [t for t in todo if t[0] is again] or [
            (again, "install", "", [], Outputs(out_dir, again, media_dir_for(args, fleet)))]
    if not todo:
        for server, new in sorted(backup_untold(fleet).items()):
            util.say("%s does not take the backups of %s yet" % (server, ", ".join(new)))
            if args.yes or not sys.stdin.isatty() or not input(
                    "Tell it now? [Enter to go on, n to stop]: ").strip().lower().startswith("n"):
                rc = main(["apply", path, server])
                if rc:
                    raise SystemExit(rc)
        util.say("every machine is set up. How they are: kiwi-server status %s" % path)
        util.say("your phone or laptop on the VPN: kiwi-server enroll %s phone (it shows a QR code to scan)" % path)
        return
    h, phase, text, errs, o = todo[0]
    util.say("%s (%d of %d machines still to set up)" % (h.name, len(todo), len(fleet.hosts)))

    def go(what):
        if args.yes or not sys.stdin.isatty():
            return True
        return not input("%s [Enter to go on, n to stop]: " % what).strip().lower().startswith("n")

    def run(*argv):
        rc = main(list(argv))
        if rc:
            raise SystemExit(rc)
    built = False
    if phase == "invalid":
        raise KiwiError("%s: its settings are not complete yet: %s" % (h.name, [e for e in errs if not not_enrolled(e)][0]))
    if phase == "bare":
        util.say("%s has no job yet: give it a role in %s (role: node, and node: { preset: cloud }), then run this again"
                 % (h.name, path))
        return
    if phase == "enroll":
        if masters_pending and not is_master(h):
            # the master hands out the places: without it the step ended in an SSH error about a
            # name nobody can resolve yet (a dry run of the first steps, 17 Nov 2026)
            raise KiwiError("%s gets its place on your VPN from the master, and %s is not set up yet. "
                            "Set it up first: kiwi-server next %s" % (h.name, masters_pending[0], path))
        if go("Give %s its place on your VPN (the master has to be running)?" % h.name):
            run("enroll", path, h.name)
            util.say("done. Run this again for the next step")
        return
    if phase == "build":
        if go("Make the install media for %s (a file of about 1 GB; this takes a few minutes)?" % h.name):
            run("build", path, h.name)
            phase, built = "install", True
        else:
            return
    if starter_host(fleet) == h.name:
        # the machine the starter stick installed, set up by the control part that runs on it: what an
        # install would have written is put in place with the starter key, then its role, as a unit
        # of the machine itself, since the role replaces the container this runs in
        if go("%s is the machine this runs on. Set it up now (a few minutes; it downloads what it runs)?" % h.name):
            check(fleet, roles, ms, [h])
            script, _b = Renderer(fleet, roles, out_dir, make_tc(args), ms, media_dir_for(args, fleet)).script(h)
            tgt = adoptmod.run_coreos(h, "core@127.0.0.1", script, key=STARTER_KEY)
            os.environ["KIWI_CONTROL_HOST"] = h.name
            run("apply", path, h.name, "--ssh", tgt)
            util.say("%s: this page moves. When the machine is set up, open https://control.%s from a device on your VPN "
                     "and log in again" % (h.name, h.hostname))
        return
    # phase == "install": the media exist. Is the machine there already? A machine nobody has
    # met is not looked for by its name (that resolves on the VPN only): its fixed address at
    # home, or a master's address on the Internet, is where the first contact goes.
    net = h.cfg.get("network") or {}
    addr = (str(net["address"]).split("/")[0] if not net.get("dhcp", True) and net.get("address")
            else (h.role_settings_raw.get("vpn-server") or {}).get("wg_host") if is_master(h) else None)
    tgt = "%s@%s" % (h.admin["user"], addr) if addr else None
    if addr and luks.at_first_boot(h) and h.cfg["unlock"].get("ssh") and remote.speaks_ssh(addr, unlock.ssh_port(h)) \
            and not remote.speaks_ssh(addr, remote.ssh_port(h)):
        # installed encrypted, its first start waits for the one-time key of its media (its initramfs
        # answers, its system not yet; that initramfs's key is on record only after the first apply)
        if go("%s waits for its disk's key at %s. Give it the one-time key of its install media?" % (h.name, addr)):
            run("unlock", path, h.name, "--manual")
            util.say("%s starts now. Run this again in a minute to set it up" % h.name)
        return
    if addr and (remote._answers(addr, 22) or remote._answers(addr, remote.ssh_port(h))):
        if go("%s answers at %s. Set it up now (a few minutes; it downloads what it runs)?" % (h.name, addr)):
            # never set up from here: a key on record for its address is from an older install
            run("apply", path, h.name, "--ssh", tgt, "--new-host-key")
            util.say("%s is set up. Run this again for the next machine" % h.name)
        return
    where = (h.cfg.get("vps") or {}).get("provider")
    if args.host and not built and not sys.stdin.isatty() and where not in providers.PROVIDERS:
        # asked for by a page or a script, with nobody to read the steps below: it is not there
        raise KiwiError("%s does not answer%s yet. Start it from its install media, wait until it is on again, then set it up"
                        % (h.name, " at %s" % addr if addr else ""))
    iso = shown_path(os.path.abspath(o.iso))
    on_done = (h.cfg.get("install") or {}).get("on_done", "poweroff")
    after = ("it turns itself off when it is done: take the media out and switch it on again" if on_done == "poweroff"
             else "it restarts by itself when it is done")
    util.say("the install media for %s: %s" % (h.name, iso))
    where = (h.cfg.get("vps") or {}).get("provider")
    if where in providers.PROVIDERS:        # a hoster kiwi-server can install at by itself
        who = providers.get(where)
        by_hand = "upload the file as an ISO at %s and start the server from it, then run this again" % who.name
        if not os.path.isfile(fleet.resolve_path(who.token_file())):
            if not go("Sign in to %s (it opens a page in your browser)?" % who.name):
                util.say("without it: " + by_hand)
                return
            try:
                run("vps", "login", path)
            except SystemExit:
                # the error alone left a newcomer without a way on (a dry run of the first steps, 17 Nov 2026)
                util.say("the sign-in at %s did not work just now. Run this again in a while, or %s" % (who.name, by_hand))
                raise
        if not args.yes and not sys.stdin.isatty():     # erasing a server is never taken for granted
            raise KiwiError("installing %s erases the server at %s: run this in a terminal, or with --yes" % (h.name, who.name))
        if go("Install %s at %s now? EVERYTHING ON THAT SERVER IS ERASED." % (h.name, who.name)):
            run("vps", "install", path, h.name, "--yes")    # uploads, installs, takes the media out, switches it on
            util.say("%s is installed and starting. Run this again in a minute to set it up" % h.name)
        return
    elif is_master(h):
        util.say("a rented server: upload the file as an ISO at your hoster and start the server from it")
    else:
        sticks = usb_sticks()
        dev = sticks[0][0] if len(sticks) == 1 else "/dev/sdX"
        util.say("write it to a USB stick (everything on the stick is erased):")
        print("    sudo dd if=%s of=%s bs=4M status=progress conv=fsync" % (iso, dev))
        if len(sticks) == 1:
            util.say("the stick plugged in right now is %s (%s)" % sticks[0])
        elif sticks:
            util.say("USB drives plugged in right now: %s" % ", ".join("%s (%s)" % s for s in sticks))
        else:
            util.say("no USB stick is plugged in right now; lsblk shows its name once it is")
        util.say("then start %s from the stick. EVERYTHING ON THAT MACHINE'S DISK IS ERASED." % h.name)
    util.say("the machine installs itself; %s. Then run this again" % after)
    if not tgt:
        util.say("it gets its address from your router: kiwi-server apply %s %s --ssh %s@<its address> sets it up"
                 % (path, h.name, h.admin["user"]))


def _state_path(fleet):
    return fleet.resolve_path(".kiwi-state.json")


def fleet_state(fleet):
    """What this fleet's folder remembers about its machines, nothing secret: when each was
    last applied. It goes with the folder to another computer."""
    try:
        return json.loads(util.read_text(_state_path(fleet)))
    except (OSError, ValueError):
        return {}


def record_applied(fleet, *names, only_new=False):
    """Remember that these machines have their role. `only_new`: for machines `status` found
    with a role applied, which an older release or another computer applied; their time stays."""
    state = fleet_state(fleet)
    applied = state.setdefault("applied", {})
    new = [n for n in names if not (only_new and n in applied)]
    if not new:
        return
    for n in new:
        applied[n] = int(time.time())
    try:
        util.write_text(_state_path(fleet), json.dumps(state, indent=1, sort_keys=True) + "\n", 0o644)
    except OSError:
        if not only_new:
            raise


def _backup_clients(host):
    return sorted((host.role_settings_raw.get("backup-server") or {}).get("clients") or [])


def record_backup_clients(fleet, host):
    """Remember whose backups this server was told to take when it got its role."""
    if "backup-server" not in host.role_settings_raw:
        return
    state = fleet_state(fleet)
    state.setdefault("backup_clients", {})[host.name] = _backup_clients(host)
    util.write_text(_state_path(fleet), json.dumps(state, indent=1, sort_keys=True) + "\n", 0o644)


def backup_untold(fleet):
    """Backup servers whose list of clients grew since they got their role: {server: [new
    clients]}. Until the server is applied again it refuses the new machine's backups."""
    told, out = fleet_state(fleet).get("backup_clients") or {}, {}
    for h in fleet.hosts.values():
        new = [c for c in _backup_clients(h) if c not in told.get(h.name, [c])]
        if new:
            out[h.name] = new
    return out


def shown_path(path):
    """A path as a person reads it: relative below the working directory, else from ~."""
    rel = os.path.relpath(path)
    if not rel.startswith(".."):
        return rel
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


STARTER_KEY = "/starter/id_ed25519"


def starter_host(fleet):
    """On a machine the starter stick installed, as long as it is not a node yet: the name of the
    fleet's machine that is this one (the one with the control part). None everywhere else."""
    if os.environ.get("KIWI_STARTER") != "1" or not os.path.isfile(STARTER_KEY):
        return None
    return next((h.name for h in fleet.hosts.values() if "control" in (h.modules or [])), None)


def next_step(fleet, roles, host, errors, out):
    """(phase, what to do next) for a machine, from what this computer knows: the fleet file
    and the output. `status` knows whether it runs; the app combines the two."""
    fname = os.path.basename(fleet.path)
    real = [e for e in errors if not not_enrolled(e)]
    if real:
        return "invalid", "fix the fleet file: %s" % real[0]
    if errors:
        masters = [h.name for h in fleet.hosts.values() if h.role in roles and roles[h.role].node_type == "master" and not h.standby_of]
        return "enroll", "once %s runs: kiwi-server enroll %s %s" % (" or ".join(masters) or "its master", fname, host.name)
    if (fleet_state(fleet).get("applied") or {}).get(host.name):
        return "applied", "kiwi-server status %s %s says how it is" % (fname, host.name)
    if host.role == "bare":
        return "bare", "give it a role in the fleet file, or install it bare: kiwi-server build %s %s" % (fname, host.name)
    if starter_host(fleet) == host.name:
        return "install", "it is the machine this runs on: kiwi-server next %s %s sets it up" % (fname, host.name)
    if not out.status()["iso"]:
        if (host.cfg.get("vps") or {}).get("server") or host.role in roles and roles[host.role].node_type == "master":
            return "build", "kiwi-server build %s %s, then kiwi-server vps install (netcup) or boot the ISO" % (fname, host.name)
        return "build", "kiwi-server build %s %s, write the ISO to a stick and boot it" % (fname, host.name)
    return "install", "install it from %s, then kiwi-server apply %s %s" % (shown_path(os.path.abspath(out.iso)), fname, host.name)


def cmd_show(args):
    fleet, roles, ms = load(args)
    h = fleet.select([args.host])[0]
    errs = h.validate(roles)
    role_settings, module_settings = {}, {}
    if h.role in roles:
        try:
            rolesmod.resolve_settings(roles[h.role], h, ms)
            errs += [p for p in [tls_problem(h)] if p]
            role_settings = dict(h.role_settings)
            for s in roles[h.role].settings:
                if s.type == "secret":
                    role_settings[s.key] = mask(role_settings.get(s.key))
            for m, vals in h.module_settings.items():
                vals = dict(vals)
                for s in ms.get(m).settings:
                    if s.type == "secret":
                        vals[s.key] = mask(vals.get(s.key))
                module_settings[m] = vals
        except KiwiError as e:
            errs.append(str(e))
    cfg = json.loads(json.dumps(h.cfg))
    cfg["admin"]["password"] = mask(cfg["admin"].get("password"))
    cfg["admin"]["password_hash"] = mask(cfg["admin"].get("password_hash"))
    cfg["debian"]["passphrase"] = mask(cfg["debian"].get("passphrase"))
    for r in roles:
        cfg.pop(r, None)
    data = {"name": h.name, "hostname": h.hostname, "target": h.target, "role": h.role,
            "modules": list(h.modules), "config": cfg, "role_settings": role_settings,
            "module_settings": module_settings, "errors": errs, "warnings": h.warnings}
    if args.porcelain:
        print(json.dumps(data, indent=2))
    else:
        import yaml
        print(yaml.safe_dump(data, sort_keys=False, default_flow_style=False))


def cmd_logins(args):
    """What a person types to get into each service: the address, the user name and the
    password, from the fleet on this computer. The passwords are this command's output."""
    fleet, roles, ms = load(args)
    rows = []
    for h in fleet.select(args.hosts):
        errs = h.validate(roles) or settings_errors(roles, ms, h)
        if errs and args.hosts:
            raise KiwiError(errs[0])
        row = {"name": h.name, "hostname": h.hostname, "logins": []}
        if errs:                # one of many: the others are still listed
            row["error"] = errs[0]
            util.warn("left out: %s" % errs[0])
        else:
            row["logins"] = loginsmod.for_host(h, roles[h.role], ms)
        rows.append(row)
    if args.porcelain:
        print(json.dumps({"hosts": rows}, indent=2))
        return
    said = loginsmod.text(rows)
    if said:
        print(said)
    else:
        util.say("nothing to sign in to: %s" % ("these machines run" if len(rows) != 1 else "%s runs" % rows[0]["name"])
                 + " no service with a login")


def cmd_roles(args):
    roles = rolesmod.discover()
    ms = modset_for(args)
    if args.porcelain:
        # base: the fields every machine has, which no role publishes (form.py); a tuple is a list here
        print(json.dumps({"roles": [r.as_dict(ms) for r in roles.values()],
                          "modules": [m.as_dict() for m in ms.modules.values()],
                          "base": formmod.BASE_GROUPS}, indent=2))
        return
    for r in roles.values():
        flag = "" if r.status == "stable" else "  [%s]" % r.status
        print("%-12s %s%s" % (r.name, r.title, flag))
        print("             %s" % r.description)
        print("             targets: %s" % ", ".join(r.targets))
        if r.stack and r.modules:
            print("             modules: %s" % ", ".join(ms.resolve(r.modules)))
        for pname, pr in r.presets.items():
            print("             preset %-9s %s" % (pname + ":", ", ".join(ms.resolve(pr["modules"]))))
        if args.verbose:
            for s in r.settings:
                req = " (required)" if s.required else ""
                print("               %-24s %-7s default=%r%s" % (s.key, s.type, s.default, req))
            names = list(r.modules)
            for pr in r.presets.values():
                names += [m for m in pr["modules"] if m not in names]
            for m in (ms.resolve(names) if r.stack else []):
                preset = r.module_defaults.get(m) or {}
                for s in ms.get(m).settings:
                    req = " (required)" if s.required else ""
                    # the role's preset (module_defaults) wins over the module's own default
                    print("               %-24s %-7s default=%r%s" % (m + "." + s.key, s.type, preset.get(s.key, s.default), req))


def cmd_modules(args):
    ms = modset_for(args)
    if args.porcelain:
        print(json.dumps({"modules": [m.as_dict() for m in ms.modules.values()]}, indent=2))
        return
    for m in ms.modules.values():
        print("%-14s %-12s %-12s %s%s" % (m.name, m.category, "/".join(m.node_types), m.description,
                                         "  (from the module source %s)" % m.source if getattr(m, "source", None) else ""))
        if m.requires:
            print("               requires: %s" % ", ".join(m.requires))
        if args.verbose:
            for s in m.settings:
                req = " (required)" if s.required else ""
                print("               %-24s %-7s default=%r%s" % (s.key, s.type, s.default, req))


def cmd_sources(args):
    """Module sources (sources.py): list them, add one by its link (pinned to the commit it is now),
    move one's pin to what it is now, remove one, or make a new one's folder to start from."""
    import yaml
    what, rest = args.what, list(args.arg or [])
    if args.fleet == "new" and not os.path.isfile("new"):      # `sources new FOLDER`: no fleet to it
        what, rest, args.fleet = "new", ([args.what] if args.what and args.what != "list" else []) + rest, None
    if what not in ("list", "add", "update", "remove", "new"):
        raise KiwiError("sources: list, add, update, remove or new, not %r" % what)
    if what == "new":
        if not rest:
            raise KiwiError("sources new FOLDER: where the new module source goes")
        made = sourcesmod.scaffold(rest[0], name=args.name or "hello")
        util.say("a module source in %s, with the module %s to start from: put it in a git repository, push it, "
                 "then: kiwi-server sources FLEET add <its https link>" % (made, args.name or "hello"))
        return
    if not args.fleet:
        raise KiwiError("sources %s needs the fleet file: kiwi-server sources FLEET %s" % (what, what))
    fleet = config.Fleet.load(args.fleet)
    listed = sourcesmod.raw(fleet)
    names = [str(x.get("name")) for x in listed if isinstance(x, dict)]
    if what == "list":
        if args.porcelain:
            print(json.dumps({"sources": fleet.module_sources}, indent=2))
            return
        if not listed:
            util.say("no module sources: only the built-in modules (kiwi-server sources %s add <link>)" % args.fleet)
        for x in fleet.module_sources:
            print("%-12s %s" % (x["name"], x.get("path") or "%s @ %s" % (x["url"], x["commit"][:12])))
            try:
                folder = sourcesmod.fetch(x)
                print("%-12s modules: %s" % ("", ", ".join(sorted(n for n in os.listdir(folder)
                                                                   if os.path.isfile(os.path.join(folder, n, "module.yaml"))))))
            except KiwiError as e:
                print("%-12s %s" % ("", e))
        return
    if what == "add":
        if not rest and not args.path:
            raise KiwiError("sources FLEET add LINK [--ref TAG] [--name NAME], or add --path FOLDER --name NAME")
        if args.path:
            name = args.name or os.path.basename(os.path.abspath(os.path.expanduser(args.path))).lower()
            entry = {"name": name, "path": args.path}
        else:
            url = rest[0].rstrip("/")
            name = args.name or re.sub(r"\.git$", "", url.rsplit("/", 1)[-1]).lower()
            entry = {"name": name, "url": url, "commit": sourcesmod.resolve(url, args.ref)}
        if name in names:
            raise KiwiError("there is a module source called %s already: --name another, or sources %s update %s"
                            % (name, args.fleet, name))
        found, errs = sourcesmod.check([entry])
        if errs:
            raise KiwiError(errs[0])
        folder = sourcesmod.fetch(found[0])
        ms = modmod.discover(extra=sourcesmod.folders(fleet) + [(name, folder)])     # clashes are said now, not later
        mods = sorted(m.name for m in ms.modules.values() if m.source == name)
        sourcesmod.write(args.fleet, listed + [entry])
        util.say("module source %s: %s. Its templates and scripts run as root on the machines that use its modules"
                 % (name, ", ".join(mods)))
        if entry.get("url"):
            util.say("pinned to commit %s: nothing of it changes until kiwi-server sources %s update %s"
                     % (entry["commit"][:12], args.fleet, name))
        else:
            util.say("a folder on this computer: other computers and the control node do not have it")
        return
    if what in ("update", "remove"):
        if not rest or rest[0] not in names:
            raise KiwiError("which module source: %s" % (", ".join(names) or "there are none"))
        name = rest[0]
        entry = next(x for x in listed if x.get("name") == name)
        if what == "remove":
            roles = rolesmod.discover()
            ms = modset(fleet)
            theirs = {m.name for m in ms.modules.values() if m.source == name}
            users = sorted("%s (%s)" % (h.name, ", ".join(sorted(theirs & set(h.modules))))
                           for h in fleet.hosts.values() if h.role in roles and theirs & set(h.modules))
            if users:
                raise KiwiError("its modules are in use: %s. Take them off those machines first" % "; ".join(users))
            sourcesmod.write(args.fleet, [x for x in listed if x.get("name") != name])
            util.say("module source %s removed" % name)
            return
        if not entry.get("url"):
            raise KiwiError("%s is a folder on this computer: it is always as it is now" % name)
        new = sourcesmod.resolve(entry["url"], args.ref)
        if new == entry["commit"]:
            util.say("%s is at %s already" % (name, new[:12]))
            return
        moved = dict(entry, commit=new)
        folder = sourcesmod.fetch(moved)
        old_folder = sourcesmod.fetch(entry)

        def versions(d):
            out = {}
            for n in sorted(os.listdir(d)):
                if os.path.isfile(os.path.join(d, n, "module.yaml")):
                    meta = yaml.safe_load(util.read_text(os.path.join(d, n, "module.yaml"))) or {}
                    out[n] = str((meta.get("module") or {}).get("version") or "?")
            return out
        before, after = versions(old_folder), versions(folder)
        for n in sorted(set(before) | set(after)):
            if before.get(n) != after.get(n):
                util.say("  %s: %s -> %s" % (n, before.get(n, "none"), after.get(n, "gone")))
        sourcesmod.write(args.fleet, [moved if x.get("name") == name else x for x in listed])
        util.say("%s now at %s. Apply the machines that use its modules to bring the change" % (name, new[:12]))
        return
    raise KiwiError("sources: list, add, update, remove or new")


def cmd_targets(args):
    if args.porcelain:
        print(json.dumps({"targets": TARGETS}, indent=2))
        return
    for k, v in TARGETS.items():
        print("%-11s %s" % (k, v))


def make_bare(fleet, hosts, note):
    """Media without the stack: the role script carries the base system only
    (hostname, network, the admin user, the update policy) and the machine
    comes up as `bare`; its role and every secret of its stack arrive
    afterwards with `apply` over SSH, which checks the role's settings then.
    So a node's WireGuard config may still be missing here (enroll comes
    after the master exists): only the base system is validated."""
    for h in hosts:
        if h.role != "bare":
            note("%s: bare media — afterwards: kiwi-server apply %s %s --ssh %s@<address>"
                 % (h.name, os.path.basename(fleet.path), h.name, h.admin["user"]))
        h.role, h.role_settings_raw, h.role_settings = "bare", {}, {}


def warn_offline(hosts, what):
    """Said where the media are made: an offline ISO or script is the whole role."""
    for h in hosts:
        if h.role != "bare":
            util.warn("%s: this %s holds the %s role's secrets in the clear, and whoever has it has them: "
                      "keep it off a provider's store and delete it once the machine is installed"
                      % (h.name, what, h.role))


def cmd_script(args):
    fleet, roles, ms = load(args)
    h = fleet.select([args.host])[0]
    if not args.offline:
        make_bare(fleet, [h], lambda m: print(":: %s" % m, file=sys.stderr))   # stdout is the script
    check(fleet, roles, ms, [h])
    tc = make_tc(args)
    util.QUIET = True   # stdout is the script; the CA / certificate notes would end up inside it
    script, _bundle = Renderer(fleet, roles, out_dir_for(args, fleet), tc, ms, media_dir_for(args, fleet)).script(h)
    if args.offline:
        warn_offline([h], "script")
    sys.stdout.write(script)


def vps_server(client, fleet, name, override):
    """A server named directly, or the one a fleet host says it is."""
    h = fleet.hosts.get(name)
    if override or h is None:
        return client.find(override or name)
    return client.find((h.cfg.get("vps") or {}).get("server")
                       or (h.role_settings_raw.get("vpn-server") or {}).get("wg_host") or h.name)


COREOS_LUKS_MIB = 3500


def _iso_key(host):
    """A name of its own for every upload: netcup booted the previous ISO of the same name."""
    return "kiwi-%s-%d.iso" % (host.name, time.time())


def vps_boot_ram(args, fleet, client, server, h):
    base = os.path.join(media_dir_for(args, fleet), h.name, h.name)
    if not os.path.isfile(base + ".iso"):
        raise KiwiError("no image for %s yet: kiwi-server build %s %s" % (h.name, os.path.basename(fleet.path), h.name))
    label = server.get("nickname") or server.get("name")
    if not args.yes:
        if not sys.stdin.isatty():
            raise KiwiError("this restarts %s from the RAM image: add --yes to go ahead" % label)
        if input("%s boots the RAM image from now on (its disk is left as it is). Type its name to go ahead: "
                 % label).strip() != label:
            raise KiwiError("not confirmed, nothing touched")
    netcup.boot_live(client, server, base + ".iso", _iso_key(h), lambda m: util.say("%s: %s" % (h.name, m)))
    util.say("%s: next: kiwi-server watch %s sends it its role once it answers" % (h.name, os.path.basename(fleet.path)))


def vps_install(args, fleet, client, server):
    h = fleet.hosts.get(args.target)
    if h is None:
        raise KiwiError("vps install needs a host of the fleet; %s is not one" % args.target)
    memory = (server.get("serverLiveInfo") or {}).get("maxServerMemoryInMiB") or 0
    if h.target != "debian" and unlock.wanted(h) and memory and memory < COREOS_LUKS_MIB:
        raise KiwiError("%s has %d MiB of memory, and encrypting a CoreOS root copies the whole root into memory at the first boot: "
                        "it runs out and stops in emergency mode, with nothing to see from here. Give it about 4 GiB, or target: debian"
                        % (h.name, memory))
    if h.target == "coreos-ram":
        return vps_boot_ram(args, fleet, client, server, h)
    disks = client.disks(server["id"])
    want = netcup.device_for(disks[0]) if disks else None
    if want and h.cfg.get("disk") not in (want, "auto"):
        raise KiwiError("%s: disk: says %s, but the server's disk is %s (%s): set disk: %s and build again"
                        % (h.name, h.cfg.get("disk"), want, disks[0].get("storageDriver"), want))
    base = os.path.join(media_dir_for(args, fleet), h.name, h.name)
    if not os.path.isfile(base + ".iso"):
        raise KiwiError("no ISO for %s yet: kiwi-server build %s %s" % (h.name, os.path.basename(fleet.path), h.name))
    if not os.path.isfile(base + ".iso.bare"):
        raise KiwiError("%s.iso carries the role's secrets and this would put it on a provider's store: "
                        "kiwi-server build %s %s, without --offline" % (h.name, os.path.basename(fleet.path), h.name))
    label = server.get("nickname") or server.get("name")
    if not args.yes:
        if not sys.stdin.isatty():
            raise KiwiError("this wipes %s: add --yes to go ahead" % label)
        if input("This wipes the disk of %s. Type its name to go ahead: " % label).strip() != label:
            raise KiwiError("not confirmed, nothing touched")
    netcup.install(client, server, base + ".iso", _iso_key(h), lambda m: util.say("%s: %s" % (h.name, m)))
    addresses = [a["ip"] for a in server.get("ipv4Addresses") or []]
    known = fleet.resolve_path("secrets/known_hosts")
    if os.path.isfile(known):                          # the reinstall made new host keys, and this is why
        for a in addresses + [h.hostname]:
            subprocess.run(["ssh-keygen", "-q", "-R", a, "-f", known], capture_output=True)
    if luks.uses_one_time_key(h):
        if h.cfg["unlock"].get("ssh"):
            util.say("%s: the disk waits for its key: kiwi-server unlock %s %s --manual" % (h.name, os.path.basename(fleet.path), h.name))
        else:
            util.say("%s: at the console, the disk asks for the key in %s" % (h.name, luks.once_file(h)))
    util.say("%s: next: kiwi-server apply %s %s --ssh %s@%s"
             % (h.name, os.path.basename(fleet.path), h.name, h.admin["user"], addresses[0] if addresses else "<address>"))


def print_probe(p):
    print("%s (id %s)%s" % (p["name"], p["id"], "  %s" % p["nickname"] if p["nickname"] else ""))
    print("  state:      %s, %s, %s CPUs, %s MiB" % (p["state"], p["architecture"], p["cpus"], p["memory_mib"]))
    print("  addresses:  %s" % (", ".join(p["addresses"]) or "-"))
    print("  boot:       %s%s, ISO attached: %s, rescue: %s"
          % (" ".join(p["bootorder"] or []) or "-", " (UEFI)" if p["uefi"] else "", p["iso"] or "no", "on" if p["rescue"] else "off"))
    for d in p["disks"]:
        print("  disk:       %s, %s MiB, %s  ->  disk: %s" % (d["name"], d["mib"], d["driver"], d["device"] or "(unknown driver)"))


def cmd_vps(args):
    fleet, _roles, _ms = load(args, need_roles=False)
    if fleet.errors:
        raise KiwiError(fleet.errors[0])
    client = netcup.Netcup(fleet.resolve_path("secrets/netcup.token"), args.api or netcup.BASE)
    if args.action == "login":
        client.login(lambda url: print("Open this page and sign in to netcup:\n  %s" % url, flush=True))
        util.say("logged in; the token is in %s and lasts while it is used once in 30 days" % client.token_path)
    elif args.action == "list":
        rows = client.servers()
        if args.porcelain:
            print(json.dumps(rows, indent=2))
        for s in [] if args.porcelain else rows:
            print("%-10s %-22s %-30s %s" % (s["id"], s.get("name") or "-", s.get("hostname") or "-", s.get("nickname") or "-"))
    else:
        if not args.target:
            raise KiwiError("which server? kiwi-server vps %s <fleet> <host or server>" % args.action)
        server = vps_server(client, fleet, args.target, args.server)
        if args.action == "install":
            vps_install(args, fleet, client, server)
        elif args.action == "restart":          # a power cycle: nothing on the server is touched
            label = server.get("nickname") or server.get("name")
            if client.state(server["id"]) != "SHUTOFF":
                util.say("%s: powering off" % label)
                client.power(server["id"], False)
            util.say("%s: powering on" % label)
            client.power(server["id"], True)
        elif args.porcelain:
            print(json.dumps(netcup.probe(client, server), indent=2))
        else:
            print_probe(netcup.probe(client, server))


def cmd_recover(args):
    """One look at the machines that are gone, and what the policy allows done about it
    (watchdog.py). The control node runs this after every look at the machines."""
    import contextlib
    import io
    from . import watchdog
    fleet, roles, ms = load(args)
    state = fleet_state(fleet)
    applied = state.get("applied") or {}
    kinds = {h.name: ("master" if h.role in roles and roles[h.role].node_type == "master" and not h.standby_of else "node")
             for h in fleet.hosts.values() if h.name in applied}
    if args.status:
        with open(args.status) as fh:
            seen = json.load(fh)
    else:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(["status", args.fleet, "--porcelain"])
        seen = json.loads(buf.getvalue() or "{}")
    said = {h["host"]: bool(h.get("ok")) for h in seen.get("hosts") or []}
    waits = {h["host"]: bool(h.get("locked")) for h in seen.get("hosts") or []}      # there, its disk locked: not gone

    def open_it(name):
        h = fleet.hosts[name]
        key_only = not h.cfg["unlock"].get("rings") and h.cfg["unlock"].get("ssh")
        if not unlock._forwarded_rings(h) and not key_only:
            # a ring at home it reaches by itself: it opens when a machine that serves it is back
            raise KiwiError(unlock.locked_advice(h, args.fleet))
        if args.dry_run:
            return
        if main(["unlock", args.fleet, name]):
            raise KiwiError("forwarding its ring did not open it")

    def where(name):
        h = fleet.hosts[name]
        if not os.path.isfile(fleet.resolve_path("secrets/netcup.token")):
            return None             # nobody signed in to the provider from here
        return (h.cfg.get("vps") or {}).get("server") or (h.role_settings_raw.get("vpn-server") or {}).get("wg_host")

    def restart(name):
        if args.dry_run:
            return
        rc = main(["vps", "restart", args.fleet, name] + (["--api", args.api] if args.api else []))
        if rc:
            raise KiwiError("the provider did not do it")
    from . import ddns
    master = next((h for h in fleet.hosts.values() if kinds.get(h.name) == "master"), None)
    wg_host = (master.role_settings_raw.get("vpn-server") or {}).get("wg_host") if master else ""

    def point(address):
        if args.dry_run:
            return ""
        return ddns.point(fleet, wg_host, address)
    can_point = bool(master and fleet.standbys(master.name) and ddns.is_name(wg_host) and ddns.template(fleet))
    events = watchdog.step(fleet, kinds, said, state, time.time(), restart, where, waits, open_it,
                           point if can_point else None)
    if not args.dry_run:
        try:
            util.write_text(_state_path(fleet), json.dumps(state, indent=1, sort_keys=True) + "\n", 0o644)
        except OSError:
            pass
    if args.porcelain:
        print(json.dumps({"events": events}))
        return
    for e in events:
        (util.warn if e["kind"] in ("tell", "held", "locked") else util.say)(e["text"])
    if not events:
        util.say("every machine that is set up answers")


def cmd_notify(args):
    """After a look at the machines: every service asked for a page, what is wrong kept with
    its time (health.py), and a message sent about what has been wrong for a while or is fine
    again (notify.py). The control node runs this after every look. --test sends one message."""
    import contextlib
    import io
    from . import health, notify
    fleet, _roles, _ms = load(args)
    roles, ms = _roles, _ms
    cfg = fleet.defaults.get("notify") or {}
    network = str(fleet.defaults.get("domain") or "")
    if args.test:
        ways = notify.channels(cfg)
        if not ways:
            raise KiwiError("nowhere to send it yet: notify: in the settings for every machine (mail, ntfy or a webhook)")
        failed = 0
        for name, ok, why in notify.send(cfg, "Kiwi %s: a test" % network, "This is a test message from your network. "
                                         "Messages like it come when something is wrong, and when it is fine again.", "info", network):
            (util.say if ok else util.warn)("%s: %s" % (name, "sent" if ok else "not sent: " + why))
            failed += not ok
        if failed:
            raise KiwiError("%d of %d did not go out" % (failed, len(ways)))
        return

    def asked(*argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(list(argv))
        return json.loads(buf.getvalue() or "{}")
    if args.status:
        with open(args.status) as fh:
            seen = json.load(fh)
    else:
        seen = asked("status", args.fleet, "--porcelain")
    rows = {h["name"]: h for h in asked("list", args.fleet, "--porcelain").get("hosts") or []}
    said = {h["host"]: h for h in seen.get("hosts") or []}
    machines = [{"name": n, "ok": bool((said.get(n) or {}).get("ok")), "problems": (said.get(n) or {}).get("problems") or []}
                for n, h in rows.items() if h.get("phase") == "applied" and n in said]
    up = {m["name"] for m in machines if m["ok"]}
    sites = [(s, n, s in (h.get("admin_sites") or [])) for n, h in rows.items() if n in up for s in h.get("sites") or []]
    # a public name is asked the way a stranger reaches it: at the master's public address, where
    # a node's public names come in too. The network's own DNS would lead straight to the node
    ending = "." + network
    master = next((h for h in fleet.hosts.values() if h.role == "master"), None)
    door = str(((master.cfg.get("master") or {}).get("vpn-server") or {}).get("wg_host") or "") if master else ""
    outside = {s for s, _n, _a in sites if not s.endswith(ending)}
    took = {}
    answers = health.probe([s for s, _n, _a in sites], took=took, **({"at": {s: door for s in outside}} if door and outside else {}))
    services = [{"site": s, "host": n, "admin": a, "ok": answers[s][0], "public": s in outside, "ms": took.get(s)} for s, n, a in sites]
    if services and not any(x["ok"] for x in services):
        # not one answers: this computer is not on the network (its names do not open from here),
        # so nothing is known about the services, and nothing is said about them
        services = None
    happened = []
    if args.events and os.path.isfile(args.events):
        with open(args.events) as fh:
            happened = json.load(fh)
    path = args.state or fleet.resolve_path(".kiwi-health.json")
    try:
        state = json.loads(util.read_text(path))
    except (OSError, ValueError):
        state = {}
    u = fleet.defaults.get("updates") or {}
    state["window"] = "%s %s" % (", ".join(str(d) for d in u.get("days") or []), u.get("time") or "") if u.get("enabled") else ""
    # what the start pages show while the fleet is locked: the hidden services anyone may open
    # (a named person's stays a secret), and where people get an account
    try:
        state["onion"] = {site: a for _h, _s, a, c, site in _hidden(fleet, roles, ms) if not c}
    except KiwiError:
        pass
    state["join"] = ""
    for h in fleet.hosts.values():
        try:
            if h.role in roles and not h.validate(roles):
                rolesmod.resolve_settings(roles[h.role], h, ms)
                acc = h.module_settings.get("accounts") if "accounts" in (h.modules or []) else None
                if acc is not None and acc.get("signup", True):
                    state["join"] = "https://%s/join" % (acc.get("accounts_hostname") or "id." + h.hostname)
        except KiwiError:
            continue
    after = cfg.get("after")
    events = health.update(state, machines, services, happened, time.time(), after=60 * (10 if after is None else int(after)))
    sent = []
    if events and not args.no_send:
        title, text, level = health.message(events, network)
        sent = notify.send(cfg, title, text, level, network)
    if cfg.get("heartbeat") and machines and not args.no_send:
        notify.heartbeat(cfg["heartbeat"])
    util.write_text(path, json.dumps(state, indent=1, sort_keys=True) + "\n", 0o644)
    if args.porcelain:
        print(json.dumps({"events": events, "sent": [{"channel": c, "ok": ok, "why": why} for c, ok, why in sent],
                          "services": None if services is None else len(services),
                          "down": [x["site"] for x in services or [] if not x["ok"]]}))
        return
    if services is None:
        util.warn("no service answers from this computer: it is not on the network, so the services were not judged")
    for x in services or []:
        if not x["ok"]:
            util.warn("%s does not answer (%s): %s" % (health.label(x["site"]), x["site"], answers[x["site"]][1]))
    for e in events:
        (util.say if e["kind"] == "fine" else util.warn)(e["text"])
    for c, ok, why in sent:
        (util.say if ok else util.warn)("%s: %s" % (c, "told" if ok else "not told: " + why))
    if not events:
        util.say("nothing new to tell (%d machines, %s services asked)" % (len(machines), "no" if services is None else len(services)))


def cmd_mtu(args):
    """How big a packet really gets through one tunnel, and what to set."""
    fleet, roles, ms = load(args)
    _domain, master_ip, _mesh, master_mtu = fleet_facts(fleet, roles, ms)
    masters = [h for h in fleet.hosts.values() if h.role in roles and roles[h.role].node_type == "master" and not h.standby_of]
    if args.master:
        masters = [h for h in masters if h.name == args.master]
    if not masters:
        raise KiwiError("no master in the fleet" if not args.master else "%s is not a master of the fleet" % args.master)
    master = masters[0]
    host = fleet.hosts.get(args.target)
    if host is not None:
        if host.role not in roles or roles[host.role].node_type != "node":
            raise KiwiError("%s is not a node: this measures the tunnel of a node or a device" % host.name)
        rolesmod.resolve_settings(roles[host.role], host, ms)
        tgt, addr, cap = remote.reach(host, args.ssh), master_ip, int(host.role_settings.get("wg_mtu") or 1376)
        container = "%s-vpn-client" % modmod.PREFIX["node"]
    else:
        addr = args.target
        conf = fleet.resolve_path("secrets/devices/%s.conf" % args.target)
        if os.path.isfile(conf):
            m = re.search(r"^\s*Address\s*=\s*([0-9.]+)", util.read_text(conf), re.M)
            if not m:
                raise KiwiError("%s has no Address line" % conf)
            addr = m.group(1)
        elif not re.fullmatch(r"[0-9.]+", args.target):
            raise KiwiError("%s is neither a host of the fleet, a device in secrets/devices/, nor a mesh address" % args.target)
        tgt, cap = remote.reach(master, args.ssh), int(master_mtu or 1376)
        container = "%s-vpn-server" % modmod.PREFIX["master"]
    util.say("%s: measuring through the tunnel to %s (MTU %d)" % (args.target, addr, cap))
    largest, tried = mtu.search(lambda size: mtu.send(tgt, container, addr, size), cap, util.say)
    if args.porcelain:
        print(json.dumps({"target": args.target, "largest": largest, "wg_mtu": cap, "tried": tried}, indent=2))
        return
    if largest is None:
        raise KiwiError("%s: even 1280 bytes do not get through whole: this path needs a look by hand" % args.target)
    if largest + mtu.STEP > cap:
        util.say("%s: %d fits, the tunnel's MTU is right for this path" % (args.target, cap))
        return
    if host is None:
        util.say("%s: use %d: set MTU = %d in this device's WireGuard config; nothing else has to change" % (args.target, largest, largest))
        return
    util.say("%s: use %d: this node's path is permanent, so wg_mtu has to be %d on it and on the master" % (args.target, largest, largest))
    text = util.read_text(fleet.path)
    new = mtu.set_wg_mtu(mtu.set_wg_mtu(text, host.name, host.role, largest) or "", master.name, master.role, largest)
    if not args.write or not new:
        util.say("  in %s, under %s's %s: and %s's %s:, wg_mtu: %d%s"
                 % (os.path.basename(fleet.path), host.name, host.role, master.name, master.role, largest,
                    "" if args.write else " (or run this again with --write)"))
        if args.write:
            raise KiwiError("%s is not laid out so that one line can change: set it by hand" % fleet.path)
        return
    util.write_text(fleet.path, new, 0o600)
    util.say("  wrote wg_mtu: %d for %s and %s; next: kiwi-server apply %s %s %s"
             % (largest, host.name, master.name, os.path.basename(fleet.path), master.name, host.name))


def secrets_store(args):
    if args.action == "autolock":     # this computer's, for every network on it
        screen = {"on": True, "off": False}.get(args.screen) if args.screen else None
        if args.screen and screen is None:
            raise KiwiError("--screen on or off")
        now = store.set_autolock(idle=args.idle, screen=screen) if (args.idle or args.screen) else store.autolock()
        if screen is not None:
            store.screen_watcher(screen)
        idle = str(now["idle"])
        print("this computer locks the secret stores %s, %s" % (
            "never when unused" if re.fullmatch(r"0+[smhd]?", idle) else "after %s unused" % idle,
            "and with the screen" if now["screen"] else "not with the screen"))
        return
    if args.action == "screen-watch":
        store.watch_screen()
        return
    st = store.Store.for_fleet(args.fleet)
    if args.action == "init":
        recovery, saved = st.init()
        util.say("secrets/ is now %s, and open in memory" % store.FILE)
        if not saved:
            util.warn("no keyring took the desktop key: unlocking will ask for the recovery key below")
        print("\nYour recovery key. Save it somewhere safe now, on paper or in a password manager.\n"
              "It is shown once, and without it a lost keyring means lost secrets:\n\n  %s\n" % recovery)
    elif not st.exists():
        if args.action == "status":
            if args.porcelain:
                print(json.dumps({"store": False, "open": True, "locks_at": None, "opens_with": []}))
                return
            print("no secret store: secrets/ is plain files (kiwi-server secrets init %s)" % args.fleet)
            return
        raise KiwiError("this fleet has no secret store: kiwi-server secrets init %s" % args.fleet)
    elif args.action == "unlock":
        if args.password_stdin:
            store.Store.password_given = sys.stdin.readline().rstrip("\n")
        if not st.is_open():
            key = st.identity(args.identity)
            st.unlock(key)
            if not args.identity and store.is_wrapped(store.keyring_get(st.store_id())):
                store.remember_key(st.store_id(), key)      # its password once per unlock: gone when it locks
        auto = store.autolock()
        after = getattr(args, "for") or str(auto["idle"])      # unused that long: locked again
        timed = store.schedule_lock(st, args.fleet, after)
        if auto["screen"]:
            store.screen_watcher(True)      # and with the screen
        util.say("the secret store is open%s%s" % (" until it is unused for %s" % after if timed else "",
                                                    ", and locks with the screen" if auto["screen"] else ""))
    elif args.action == "lock":
        st.lock()
        util.say("the secret store is locked")
    elif args.action == "recovery":
        # a new recovery key, when the old one is lost. It replaces the old one only once it is
        # kept: typed back here, or written to a file of the person's (never printed then)
        if args.make:                   # a new key on stdout for the app to show; nothing changes yet
            print(store.keygen()[0])
            return
        if not st.is_open():
            st.unlock(st.identity(args.identity))
        if args.key_stdin:              # the one the app showed and saw typed back
            st.new_recovery(secret=sys.stdin.readline().strip())
            util.say("the store opens with the new recovery key, and copies are signed with it from now on; "
                     "the old one opens only the copies made before today")
            return
        out = getattr(args, "out", None)
        if out and os.path.exists(out):
            raise KiwiError("%s is there already: give a new file" % out)
        if not out and not sys.stdin.isatty():
            raise KiwiError("without a terminal the new key goes to a file: --out FILE")

        def keep(secret):
            if out:
                fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(secret + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                util.say("the new recovery key is in %s (only you can read it): copy it somewhere safe, away "
                         "from this computer" % out)
                return True
            print("\nYour new recovery key. Write it down or put it in a password manager now:\n\n  %s\n" % secret)
            typed = input("Type its last 8 characters to show it is kept (anything else: nothing changes): ")
            print("\033[2J\033[H", end="")         # off the screen again
            return typed.strip().upper() == secret[-8:].upper()
        st.new_recovery(keep)
        util.say("the store opens with the new recovery key, and copies are signed with it from now on; "
                 "the old one opens only the copies made before today")
    elif args.action == "password":
        # optional: this computer's key in the keyring only together with a password
        sid = st.store_id()
        held = store.keyring_get(sid)
        if not held:
            raise KiwiError("this computer has no key for this store in its keyring (kiwi-server secrets join)")
        lines = sys.stdin.read().splitlines() if args.password_stdin else None
        ask = (lambda prompt: lines.pop(0) if lines else "") if lines is not None else getpass.getpass
        if not sys.stdin.isatty() and lines is None:
            raise KiwiError("give the passwords on stdin (--password-stdin), or run this in a terminal")
        key = store.unwrap(held, ask("Its password now: ")) if store.is_wrapped(held) else held
        if args.off:
            if not store.keyring_put(sid, key):
                raise KiwiError("the keyring did not take the key")
            util.say("this computer opens the store with its keyring alone again")
            return
        new = ask("New password (8 characters or more): ")
        if ask("The same again: ") != new:
            raise KiwiError("the two were not the same: nothing changed")
        wrapped = store.wrap(key, new)
        if store.unwrap(wrapped, new) != key or not store.keyring_put(sid, wrapped):
            raise KiwiError("the keyring did not take it: nothing changed")
        if st.is_open():
            store.remember_key(sid, key)
        util.say("this computer's key opens the store only with its password from now on: it is asked once per "
                 "unlock (the app asks too). Forgotten: the recovery key opens it, or remove this computer and join again")
    elif args.action == "copy-key":
        # once, for a store made before 2.170.0: the key its copies are signed with, from the recovery key
        if not args.identity:
            raise KiwiError("give the recovery key: kiwi-server secrets copy-key %s --identity RECOVERY-KEY-FILE" % args.fleet)
        identity = st.identity(args.identity)
        if store.public_of(identity) != store.recovery_public(util.read_text(st.recipients)):
            raise KiwiError("that is not this store's recovery key: the copy key comes from the recovery key only")
        if not st.is_open():
            st.unlock(identity)
        path = os.path.join(st.runtime(), "secrets", store.COPY_KEY)
        util.write_text(path, store.copy_key_from(identity) + "\n", 0o600)
        st.sync()
        util.say("this network's copies are signed from now on; a restore checks them with the recovery key")
    elif args.action == "join":
        public = st.join()
        util.say("this computer has a key for the store now, in its keyring; on a computer that can open it run:")
        print("\n  kiwi-server secrets add %s %s %s\n" % (args.fleet, args.host or "<name of this computer>", public))
        util.say("then bring the fleet folder here again (its secrets.age changes) and kiwi-server secrets unlock %s" % args.fleet)
    elif args.action == "add":
        if not (args.host and args.setting):
            raise KiwiError("kiwi-server secrets add <fleet> <name> <age1… key from `kiwi-server secrets join` on that computer>")
        st.add(args.host, args.setting)
        util.say("%s can open the store now: bring it the fleet folder with the new %s" % (args.host, store.FILE))
    elif args.action == "remove":
        if not args.host:
            raise KiwiError("kiwi-server secrets remove <fleet> <name>   (kiwi-server secrets status names them)")
        st.remove(args.host)
        util.say("%s can no longer open %s as it is now. A copy it kept still opens with its key: if that "
                 "computer is lost, treat the fleet's secrets as known" % (args.host, store.FILE))
    else:
        names = [n for n, _k in st.ways()]
        if args.porcelain:
            print(json.dumps({"store": True, "open": st.is_open(), "locks_at": st.locks_at(), "opens_with": names,
                              "password": store.is_wrapped(store.keyring_get(st.store_id())), "autolock": store.autolock()}))
            return
        print("%s; opens with: %s" % ("open" if st.is_open() else "locked", ", ".join(names)))


def cmd_secrets(args):
    """The secrets a host's role resolved to, generated or written down, read
    from the fleet on this machine: nothing is asked of the host."""
    if args.action != "show":
        return secrets_store(args)
    if not args.host:
        raise KiwiError("which host? kiwi-server secrets show <fleet> <host> [setting]")
    fleet, roles, ms = load(args)
    h = fleet.select([args.host])[0]
    util.QUIET = True           # stdout is the value
    check(fleet, roles, ms, [h])
    if h.role not in roles:
        raise KiwiError("%s: role %s is unknown" % (h.name, h.role))
    rolesmod.resolve_settings(roles[h.role], h, ms)
    found = {}
    for m, vals in h.module_settings.items():
        for s in ms.get(m).settings:
            if s.type == "secret" and vals.get(s.key):
                found["%s.%s" % (m, s.key)] = str(vals[s.key])
    if not args.setting:
        for name in sorted(found):
            print(name)
        return
    matches = [n for n in found if args.setting in (n, n.split(".", 1)[1])]
    if len(matches) != 1:
        raise KiwiError("%s: %s names %s" % (
            h.name, args.setting, "several secrets: %s" % ", ".join(sorted(matches)) if matches else "no secret of this host"))
    print(found[matches[0]])


# on the machine, after an apply: the containers whose image is newer than the one they run
UPDATE_IMAGES = "if [ -x /usr/local/bin/kiwi-stack ]; then /usr/local/bin/kiwi-stack update; fi"


def fleet_identities(fleet):
    """The private keys the fleet itself holds for its machines: every
    admin.ssh_keys entry that is a path to a .pub with the private key next
    to it (secrets/admin_ed25519.pub -> secrets/admin_ed25519)."""
    out = []
    blocks = [fleet.defaults.get("admin") or {}] + [h.admin for h in fleet.hosts.values()]
    for a in blocks:
        for k in a.get("ssh_keys") or []:
            k = str(k)
            if config.is_ssh_key(k) or not k.endswith(".pub"):
                continue
            priv = fleet.resolve_path(k[:-4])
            if os.path.isfile(priv) and priv not in out:
                out.append(priv)
                if os.path.isfile(priv + ".old"):       # a rotation under way: some machines know only this one
                    out.append(priv + ".old")
    return out


def fleet_facts(fleet, roles, ms, r=None):
    """(domain, master's mesh address, mesh subnet, the master's wg_mtu or None):
    what a router needs to know."""
    r = r or Renderer(fleet, roles, out_dir_for(argparse.Namespace(output_dir=None), fleet), None, ms)
    r.scan()
    mesh, wg_mtu = "10.8.0.0/16", None
    for h in fleet.hosts.values():
        if h.role in roles and roles[h.role].node_type == "master" and h.role_settings.get("mesh_subnet"):
            mesh = h.role_settings["mesh_subnet"]
            wg_mtu = h.role_settings.get("wg_mtu") or None
            break
    return str(fleet.defaults.get("domain") or ""), r._master_ip, mesh, wg_mtu


def cmd_openwrt(args):
    fleet, roles, ms = load(args)
    domain, master_ip, mesh, wg_mtu = fleet_facts(fleet, roles, ms)
    if not master_ip and not args.via:
        util.warn("no master with a mesh address in the fleet — the DNS forward is left out")
    wg = via = None
    if args.wireguard:
        wg = routermod.parse_wireguard(util.read_text(fleet.resolve_path(args.wireguard)))
    if args.via:
        via = args.via
        if via in fleet.hosts:   # a gateway node: its static LAN address
            addr = fleet.hosts[via].cfg["network"].get("address")
            if fleet.hosts[via].cfg["network"].get("dhcp", True) or not addr:
                raise KiwiError("%s has no static LAN address (network.dhcp: false + address:) — give the address instead" % via)
            via = str(addr).split("/", 1)[0]
    if wg is None and via is None:
        raise KiwiError("give --wireguard <client config from the master> or --via <gateway node or its LAN address>")
    sys.stdout.write(routermod.openwrt_script(domain, master_ip, mesh, wg=wg, via=via, full=args.full,
                                              name=args.name, wg_mtu=wg_mtu))


# ---- managing machines that run ----------------------------------------------------

def cmd_plan(args):
    """What `apply` would change on each running machine: files, units, and which containers
    restart. Read-only; run it before an update."""
    fleet, roles, ms = load(args)
    hosts = fleet.select(args.hosts)
    check(fleet, roles, ms, hosts)
    r = Renderer(fleet, roles, out_dir_for(args, fleet), make_tc(args), ms, media_dir_for(args, fleet))
    report = []
    for h in hosts:
        _script, b = r.script(h)
        if b is None:
            report.append({"host": h.name, "stack": False})
            continue
        want = planmod.wanted(h, b)
        try:
            version, have, before = planmod.machine(remote.reach(h, args.ssh), sorted(want))
        except KiwiError as e:
            report.append({"host": h.name, "error": str(e)})
            continue
        report.append(dict(planmod.compare(b, want, have, before), host=h.name, release=version))
    if args.porcelain:
        print(json.dumps({"release": VERSION, "hosts": report}, indent=1))
        return
    for x in report:
        if "error" in x:
            print("%s: not reached: %s" % (x["host"], x["error"]))
            continue
        if not x.get("stack", True):
            print("%s: no stack" % x["host"])
            continue
        head = "%s (%s -> %s): " % (x["host"], x["release"] or "?", VERSION)
        if not x["changed"] and not x["removed_services"]:
            print(head + "nothing changes")
            continue
        bits = ["%d files" % len(x["changed"])]
        for key, word in (("restart", "restarts"), ("new_services", "new"), ("removed_services", "goes")):
            if x[key]:
                bits.append("%s %s" % (word, " ".join(x[key])))
        if not x["restart"] and x["quadlets"]:
            bits.append("nothing restarts")
        print(head + "; ".join(bits))
        for c in x["changed"]:
            print("   %s%s" % (c["path"], " (new)" if c["new"] else ""))


CHECK_SCRIPT = r"""
p=$(podman ps --format '{{.Names}}' | grep -E '^k[mn]-pihole$' | head -1)
[ -n "$p" ] || { echo "skip no Pi-hole here"; exit 0; }
for n in $NAMES; do
    code=$(podman exec "$p" curl -sk -o /dev/null -w '%{http_code}' --max-time 8 "https://$n/" 2>/dev/null)
    echo "https $n ${code:-000}"
done
for n in $LOOKUP; do
    a=$(podman exec "$p" dig +short +time=3 +tries=1 "$n" @127.0.0.1 2>/dev/null | grep -E '^[0-9.]+$' | tail -1)
    echo "dns $n ${a:-none}"
done
v=$(podman ps --format '{{.Names}}' | grep -E '^k[mn]-vpn-client$' | head -1)
if [ -n "$v" ]; then
    ip=$(podman exec "$v" wget -qO- -T 10 https://api.ipify.org 2>/dev/null)
    echo "exit $v ${ip:-none}"
fi
"""


def cmd_check(args):
    """After an update: from each master's Pi-hole container (the fleet's own resolver), every
    name of the fleet over HTTPS, a fleet name and one outside in DNS, and where its exit
    leaves. Read-only; says what fails."""
    fleet, roles, ms = load(args)
    r = Renderer(fleet, roles, out_dir_for(args, fleet), make_tc(args), ms, media_dir_for(args, fleet))
    records = r.dns_records()
    names = sorted({n for _ip, n in records if n.count(".") >= 2})        # services: <service>.<host>.<domain>
    hosts = sorted({n for _ip, n in records if n.count(".") == 1})
    failed = []
    masters = [h for h in fleet.select(args.hosts) if h.role in roles and roles[h.role].node_type == "master"]
    if not masters and not has_master(fleet, roles):
        # a network without a master (docs/design/no-master.md): asked from the machines that answer for the names
        for h in fleet.select(args.hosts):
            if h.role in roles and not h.validate(roles):
                rolesmod.resolve_settings(roles[h.role], h, ms)
                if "dns" in h.modules:
                    masters.append(h)
        if not masters:
            raise KiwiError("this network has no master, and none of these machines answers for the names (dns): "
                            "check asks from one that does")
    if not masters:
        raise KiwiError("no master among these hosts: check asks from the master")
    for h in masters:
        rolesmod.resolve_settings(roles[h.role], h, ms)
        lookup = " ".join(hosts[:1] + ["quad9.net"])
        script = "NAMES=%s\nLOOKUP=%s\n%s" % (shlex.quote(" ".join(names)), shlex.quote(lookup), CHECK_SCRIPT)
        try:
            out = remote.run(remote.reach(h, args.ssh), script).decode(errors="replace")
        except KiwiError as e:
            print("%s: not reached: %s" % (h.name, e))
            failed.append(h.name)
            continue
        for line in out.splitlines():
            kind, _sp, rest = line.partition(" ")
            name, _sp, value = rest.rpartition(" ")
            if kind == "https":
                ok = value[:1] in "234"            # a redirect, a login or a refusal: nginx and the service answered
            elif kind in ("dns", "exit"):
                ok = value != "none"
            else:
                continue
            print("%s %-7s %-5s %s %s" % ("ok    " if ok else "FAILED", h.name, kind, name, value))
            if not ok:
                failed.append("%s %s" % (kind, name))
    if failed:
        raise KiwiError("%d checks failed" % len(failed))


def cmd_apply(args):
    """Re-render a host and run the role script on it: settings changed, a
    certificate renewed, a module added — without a reinstall."""
    fleet, roles, ms = load(args)
    hosts = fleet.select(args.hosts)
    if not hosts:
        raise KiwiError("which host? kiwi-server apply <fleet> <host...>")
    if args.hosts and not args.ssh:     # a master's standbys come with it: the same master on their machines
        hosts += [s for h in list(hosts) for s in fleet.standbys(h.name) if s not in hosts]
    if args.ssh and len(hosts) != 1:
        raise KiwiError("--ssh goes with exactly one host")
    check(fleet, roles, ms, hosts)
    r = Renderer(fleet, roles, out_dir_for(args, fleet), make_tc(args), ms, media_dir_for(args, fleet))
    failed = []
    servers = None
    update = getattr(args, "update", False)
    for h in hosts:
        if getattr(args, "new_host_key", False) and args.ssh:
            adoptmod.forget_host_key(args.ssh)      # installed anew: the key it showed before is not its key
        tgt = remote.reach(h, args.ssh)
        try:
            if servers is None and unlock.wanted(h):
                servers = unlock.ring_servers(fleet, lambda x: rolesmod.resolve_settings(roles[x.role], x, ms))
            if not args.no_run and h.target == "ucore":
                coreos_t.wait_for_ucore(h.name, tgt)    # a fresh one restarts twice by itself first
            if not args.no_run:
                luks.swap(h, tgt)       # before the role script: its secrets must not land on a disk the old key opens
            script, _b = r.script(h)
            if not args.no_run and backupmod.carries_wireguard(h) and backupmod.put_back_wireguard(h, tgt):
                util.say("%s: its WireGuard identity is back from the fleet's copy" % h.name)
            if not args.no_run and h.target == "coreos-ram" and ram_t.put_back(h, tgt):
                util.say("%s: its certificates are back from the fleet's copy" % h.name)
            util.say("%s: sending the role script to %s" % (h.name, tgt))
            remote.put(tgt, script.encode(), "/var/lib/kiwi-server/role.sh", "0700")
            if args.no_run:
                util.say("%s: in place at /var/lib/kiwi-server/role.sh — run it with: sudo bash /var/lib/kiwi-server/role.sh --force" % h.name)
                continue
            if os.environ.get("KIWI_CONTROL_HOST") == h.name:
                # its disk first (over SSH, nothing restarts): the starter's TPM gives way to the rings or the console key
                if unlock.wanted(h):
                    unlock.bind(h, tgt, servers)
                elif h.target in ("coreos", "ucore"):
                    unlock.drop_standin(h, tgt)
                save_store(args)    # the disk's key sealed now: the role below restarts this container
                # the control node applies itself: the role may restart the container this runs in,
                # so it runs as a unit of its own on the machine and outlives the connection
                # a machine the starter stick installed takes away what the starter needed, when the role is in
                remote.run(tgt, "systemctl reset-failed kiwi-self-apply 2>/dev/null; systemd-run --collect "
                                "--unit=kiwi-self-apply bash -c %s" % shlex.quote(
                                    "bash /var/lib/kiwi-server/role.sh --force" + (" && " + UPDATE_IMAGES if update else "")
                                    + " && { [ ! -x %s ] || %s; }" % (startermod.DONE, startermod.DONE)))
                util.say("%s: applying itself, detached (this page may drop for a moment); on the machine: "
                         "journalctl -u kiwi-self-apply -f" % h.name)
                continue
            util.say("%s: applying (the output below is the machine's)" % h.name)
            rc = remote.run_stream(tgt, "bash /var/lib/kiwi-server/role.sh --force")
            if rc != 0:
                raise KiwiError("the role script exited with %d — journalctl -u kiwi-role on the machine" % rc)
            if update:      # an apply fetches newer images; this brings them into use (only what changed restarts)
                util.say("%s: newer images into use" % h.name)
                if remote.run_stream(tgt, UPDATE_IMAGES) != 0:
                    util.warn("%s: the images were not updated — sudo kiwi-stack update on the machine" % h.name)
            if unlock.wanted(h):
                try:
                    unlock.bind(h, tgt, servers)
                except KiwiError as e:
                    # its rings are not there yet (the machine that serves them comes later): the disk
                    # keeps the key for its screen until an apply after they answer
                    util.warn("%s: its disk is not bound to its rings yet: %s. It opens with its key until an apply after "
                              "they answer" % (h.name, e))
                    if h.target in ("coreos", "ucore"):
                        unlock.drop_standin(h, tgt)
                    else:
                        remote.run(tgt, unlock.INSTALL_KEY_ONLY)
            elif h.target in ("coreos", "ucore"):      # the starter's TPM, on a machine with no rings: the console key instead
                unlock.drop_standin(h, tgt)
            elif h.target == "debian" and luks.uses_one_time_key(h):    # encrypted, no ring: its key only
                remote.run(tgt, unlock.INSTALL_KEY_ONLY)
            if backupmod.carries_wireguard(h):
                backupmod.keep_wireguard(h, tgt)
            if h.target == "coreos-ram":    # RAM forgets: the fleet keeps what the next boot needs
                ram_t.keep(h, tgt)
                ram_t.kept_now(fleet, h.name)
            tgt = sshport.ensure(h, tgt)     # admin.ssh_port: moved without locking anyone out
            if sshca.exists(fleet):
                sshca.install(fleet, h, tgt)
                util.say("%s: its host certificate is signed, its sshd takes the fleet's user certificates" % h.name)
            tgt = addressmod.settle(h, tgt)     # last: the connection goes away with the old address
            record_applied(fleet, h.name)
            record_backup_clients(fleet, h)
            util.say("%s: applied" % h.name)
        except KiwiError as e:
            failed.append(h.name)
            print("error: %s: %s" % (h.name, e), file=sys.stderr)
    for server, new in sorted(backup_untold(fleet).items()):
        util.warn("%s does not take the backups of %s yet: kiwi-server apply %s %s"
                  % (server, ", ".join(new), shown_path(fleet.path), server))
    if failed:
        raise KiwiError("failed: %s" % ", ".join(failed))


def cmd_adopt(args):
    """A machine that already runs Debian (a board's own image) becomes the host, then applies."""
    fleet, roles, ms = load(args)
    hosts = fleet.select([args.host])
    if len(hosts) != 1:
        raise KiwiError("which host? kiwi-server adopt <fleet> <host> --as user@address")
    check(fleet, roles, ms, hosts)      # the fleet's mistakes before the board is touched
    if hosts[0].target == "coreos":     # a machine that runs CoreOS already (the starter stick's)
        script, _b = Renderer(fleet, roles, out_dir_for(args, fleet), make_tc(args), ms, media_dir_for(args, fleet)).script(hosts[0])
        tgt = adoptmod.run_coreos(hosts[0], getattr(args, "as"), script, key=args.key)
    else:
        tgt = adoptmod.run(hosts[0], getattr(args, "as"), new_host_key=args.new_host_key)
    util.say("%s: adopted; now its role" % hosts[0].name)
    args.hosts, args.ssh, args.no_run = [hosts[0].name], tgt, False
    cmd_apply(args)
    if hosts[0].target == "coreos":     # a starter's machine: what the starter needed goes now
        mode = "" if "control" in hosts[0].modules else " joined"
        remote.run(tgt, "sudo sh -c '[ ! -x %s ] || %s%s'" % (startermod.DONE, startermod.DONE, mode))
        util.say("%s: the starter's page and key are gone; it is a node of the network" % hosts[0].name)


def cmd_take_in(args):
    """A machine started from the starter stick, waiting on the home network, taken into this
    network from here, as the control page's "A machine with the starter stick is waiting" does:
    the code from its screen, the network's key told to it, the machine added to the fleet file with
    its address, then adopted and set up (`adopt`)."""
    from . import control as controlmod, setup as setupmod
    fleet_path = os.path.realpath(args.fleet)
    fleet_dir = os.path.dirname(fleet_path)
    fleet, _roles, _ms = load(args)
    domain = str(fleet.defaults.get("domain") or "")
    address, name = controlmod.starter_handshake(fleet_path, fleet_dir, args.address, args.code, args.preset, args.name or "", domain)
    name = setupmod.add_home_machine(fleet_path, args.preset, address, name, target="coreos")
    util.say("%s: taken in at %s; now it is set up" % (name, address))
    args.host, args.new_host_key = name, False
    setattr(args, "as", "core@%s" % address)
    args.key = os.path.join(fleet_dir, "secrets", "admin_ed25519")
    cmd_adopt(args)


def cmd_control(args):
    """The fleet to the control node or back; or this computer asks to become a controller."""
    from . import controlsync
    fleet_path = os.path.abspath(args.fleet)
    fleet_dir, fleet_name = os.path.dirname(fleet_path), os.path.basename(fleet_path)
    key = None
    if args.identity and args.action != "join":
        with open(args.identity) as f:
            key = next((line.strip() for line in f if line.startswith("AGE-SECRET-KEY-")), None)
        if not key:
            raise KiwiError("%s holds no age secret key" % args.identity)
    me = args.name or socket.gethostname().split(".")[0].lower()
    folder = args.dir or (None if args.ssh else controlsync.folder_of(fleet_dir))
    if args.action == "join" and args.dir:      # through a synced folder: ask, then (once accepted) fetch
        if controlsync.folder_of(fleet_dir):
            if args.identity and os.path.isfile(args.identity):
                key = store.Store.for_fleet(args.fleet).identity(args.identity)
            n = controlsync.pull_dir(fleet_dir, fleet_name, args.dir, args.force, key)
            util.say("accepted: the fleet is in %s (%d files) and stays in step with kiwi-server control pull / push"
                     % (fleet_dir, n))
            return
        controlsync.request_join(fleet_dir, args.dir, me, args.identity)
        util.say("%s asks to join as %s. On a computer that has the fleet, accept it (Kiwi Server shows the request, "
                 "or: kiwi-server control accept <fleet> %s). Then run this command again here." % (socket.gethostname(), me, me))
        return
    if args.action in ("requests", "accept", "deny"):
        if not folder:
            raise KiwiError("this fleet keeps no synced folder: kiwi-server control push <fleet> --dir FOLDER first")
        if args.action == "requests":
            found = controlsync.requests(folder)
            if args.porcelain:
                print(json.dumps({"folder": folder, "requests": found}))
            else:
                for r in found:
                    print("%s (the computer %s) asks to join: kiwi-server control accept %s %s" % (
                        r["name"], r["host"], args.fleet, r["name"]))
                if not found:
                    util.say("nobody asks to join")
            return
        if not args.link:
            raise KiwiError("which computer? kiwi-server control %s <fleet> <name>" % args.action)
        controlsync.answer(fleet_dir, fleet_name, folder, args.link, args.action == "accept")
        util.say("%s can open the fleet now; it fetches it with its next sync" % args.link if args.action == "accept"
                 else "%s was denied" % args.link)
        return
    if args.action in ("push", "pull") and folder and (args.dir or not controlsync.remote_of(fleet_dir)):
        if args.action == "push":
            n = controlsync.push_dir(fleet_dir, fleet_name, folder, args.force)
            util.say("the folder %s holds this copy now (%d files, encrypted)" % (folder, n))
            waiting = controlsync.requests(folder)
            for r in waiting:
                util.say("%s (the computer %s) asks to join: kiwi-server control accept %s %s"
                         % (r["name"], r["host"], args.fleet, r["name"]))
        else:
            n = controlsync.pull_dir(fleet_dir, fleet_name, folder, args.force, key)
            util.say("%d files from %s: this copy is the folder's" % (n, folder) if n else "in step with %s" % folder)
        return
    if args.action == "invite":
        from . import qr
        link = controlsync.invite_api(fleet_dir, key)
        if args.porcelain:
            try:
                picture = qr.svg(link)
            except KiwiError:
                picture = ""
            print(json.dumps({"link": link, "qr": picture}))
            return
        util.say("for one more computer: there, in Kiwi Server, Join, and paste this link "
                 "(or: kiwi-server control join <new folder>/fleet.yaml). It works once, for fifteen minutes")
        print(link)
        if sys.stdout.isatty():
            try:
                print(qr.text_blocks(link))
            except KiwiError:
                pass
        return
    if args.action == "join":
        link = args.link or (getpass.getpass("invite link (the control page: Computers, Add a computer): ")
                             if sys.stdin.isatty() else sys.stdin.readline().strip())
        name = controlsync.join(fleet_dir, link, me, args.address, args.identity)
        util.say("joined as %s: the fleet is in %s and stays in step with kiwi-server control pull / push"
                 % (name, fleet_dir))
        return
    if args.action == "ring":           # for the control node itself: where its unlock ring is, and what it offers
        fleet, roles, ms = load(args)
        for x in fleet.hosts.values():
            if x.role in roles:
                rolesmod.resolve_settings(roles[x.role], x, ms)
        h = controlsync.control_host(fleet, args.to)
        ring = str(h.cfg["unlock"].get("control") or "")
        if not ring:
            print(json.dumps({}))
            return
        servers = unlock.ring_servers(fleet, lambda x: None)
        if ring not in servers or isinstance(servers[ring][0], unlock.OperatorRing):
            raise KiwiError("%s: unlock.control names the ring %s, which no machine of the fleet serves" % (h.name, ring))
        server, port = servers[ring]
        if server is h:
            raise KiwiError("%s cannot open itself: the ring %s is its own. Name a ring on another machine" % (h.name, ring))
        try:
            where = unlock.url(h, ring, server, port)
        except KiwiError:                # not on one LAN: over the mesh (the tang module forwards its rings there)
            mesh_ip = (server.role_settings or {}).get("vpn_ip")
            if not mesh_ip:
                raise
            where = "http://%s:%d" % (mesh_ip, port)
        print(json.dumps({"ring": ring, "server": server.name, "url": where,
                          "adv": json.loads(unlock.advertisement(server, ring))}))
        return
    if args.action == "activity":       # the control page's Activity: tasks, and who did what
        out = controlsync.activity_api(fleet_dir, key)
        if args.porcelain:
            print(json.dumps(out))
            return
        print("Tasks")
        for j in out.get("jobs") or []:
            print("  %-16s %-8s %s %s  (%s)" % ((j.get("started") or j.get("id") or "")[:16].replace("T", " "),
                                                j.get("state") or "", j.get("action") or "", " ".join(j.get("hosts") or []),
                                                j.get("user") or ""))
        print("Log")
        for e in out.get("entries") or []:
            print("  %-16s %s %s  (%s%s)" % ((e.get("time") or "")[:16].replace("T", " "), e.get("what") or "",
                                             " ".join(e.get("hosts") or []), e.get("user") or "",
                                             ", " + e["address"] if e.get("address") else ""))
        return
    if controlsync.remote_of(fleet_dir) and not args.ssh:      # joined: through the control node's API
        if args.action == "push":
            try:
                n = controlsync.push_api(fleet_dir, fleet_name, args.force, key)
            except KiwiError as e:
                if "does not know a controller" in str(e):
                    # a node whose copy is damaged (or was installed anew) knows no computer any more
                    raise KiwiError("%s. If its copy is damaged or it was installed anew, this computer's copy goes there over "
                                    "SSH: kiwi-server control push %s --ssh <admin>@<its address> --force" % (e, fleet_name))
                raise
            util.say("the control node holds this copy now (%d files)" % n)
            old = controlsync.older_node(fleet_dir, VERSION, key)
            if old:
                util.warn("the control node runs %s, this computer %s. It does not know what came since, and its page may "
                          "call a machine's settings wrong. Update it: kiwi-server apply %s <its machine> --update"
                          % (old, VERSION, fleet_name))
        else:
            n = controlsync.pull_api(fleet_dir, fleet_name, args.force, key)
            util.say("%d files from the control node: this copy is its" % n if n else "in step with the control node")
        return
    fleet, roles, ms = load(args)                               # the first controller: over SSH
    for x in fleet.hosts.values():          # a host's modules are known once its role is resolved
        if x.role in roles:
            rolesmod.resolve_settings(roles[x.role], x, ms)
    h = controlsync.control_host(fleet, args.to)
    tgt = remote.reach(h, args.ssh)
    if args.action == "push":
        n = controlsync.push(fleet, h, tgt, args.force)
        controlsync.remember(fleet_dir, h)
        util.say("%s holds the fleet now (%d files): https://control.%s" % (h.name, n, h.hostname))
    else:
        n = controlsync.pull(fleet, h, tgt, args.force, key)
        util.say("%d files from %s: this copy is the control node's" % (n, h.name))


def cmd_serve(args):
    """The control node's pages and API (needs fastapi, uvicorn; the control image has them)."""
    from . import control as controlmod
    c = controlmod.Control(args.fleet, kiwi_cmd=[sys.executable, "-m", "kiwiserver"],
                           status_minutes=args.status_minutes)
    if args.set_password:
        import getpass
        pw = getpass.getpass("control password: ") if sys.stdin.isatty() else sys.stdin.readline().rstrip("\n")
        c.set_password(pw)
        util.say("the control password opens the store now; kiwi-server control push %s takes it to the node" % args.fleet)
        return
    if c.fresh() and args.wait:
        # nothing here yet: the page asks the questions of `init`. The code proves that the
        # person at the page is the one who started this
        util.say("no network here yet: the page sets one up. Its setup code: %s" % c.setup_code())
    elif not c.ready():
        if not args.wait:
            raise KiwiError("nothing to serve yet: the fleet needs its secret store (kiwi-server secrets init) and "
                            "a control password (kiwi-server serve %s --set-password)" % args.fleet)
        util.say("waiting for the fleet: kiwi-server control push <fleet>, from the operator's computer")
        sys.stdout.flush()
        while not c.ready():
            time.sleep(10)
    if not c.fresh() and c.locked():
        util.say("the fleet is locked: the first login on the page opens it")
    try:
        from . import web
    except ImportError as e:
        raise KiwiError("serve needs fastapi and uvicorn (%s): the control image has them" % e)
    tls_dir = None
    if args.https:          # a page with no proxy in front (the starter): a certificate of its own, made now
        tls_dir = os.path.join(c.run, "tls")
        _k, _c, fp = web.self_signed(tls_dir)
        util.say("serving %s on https://%s (http://%s sends the browser there). Its page's certificate: %s"
                 % (args.fleet, args.https, args.listen, fp))
    else:
        util.say("serving %s on http://%s" % (args.fleet, args.listen))
    sys.stdout.flush()      # into a container's log now: without a terminal the setup code above would wait in a buffer
    web.serve(c, args.listen, https=args.https, tls_dir=tls_dir)


ACCOUNT_PAGE_OPS = ("invite", "invites", "revoke", "requests", "accept", "deny")
ACCOUNT_OPS = ("create", "link", "list", "delete", "jabber", "member", "members")
# the services that can sign people in with the network's accounts, and where Forgejo & co. come back to
CONNECTABLE = {"git": ("Forgejo", lambda s, h: s.get("git_hostname") or "git." + h.hostname, "/user/oauth2/accounts/callback"),
               "cloud": ("Nextcloud", lambda s, h: s.get("nc_hostname") or "cloud." + h.hostname, "/apps/user_oidc/code"),
               "paperless": ("Paperless", lambda s, h: s.get("paperless_domain") or "paperless." + h.hostname,
                             "/accounts/oidc/accounts/login/callback/"),
               "vault": ("Vaultwarden", lambda s, h: (s.get("vault_domain") or "").split("://")[-1].strip("/") or "vault." + h.hostname,
                         "/identity/connect/oidc-signin"),
               # Immich: its web page twice, and the page that hands the phone app its answer
               "immich": ("Immich", lambda s, h: s.get("immich_domain") or "immich." + h.hostname,
                          ("/auth/login", "/user-settings", "/api/oauth/mobile-redirect")),
               # the game page of the control node: only its group's members (play_group) get in
               "control": ("Game page", lambda s, h: s.get("play_hostname") or "play." + h.hostname, "/back")}
# the services that send a PKCE challenge themselves: Kanidm keeps checking it for them
PKCE = {"immich", "control"}
# the services only one group of the accounts may use: the setting that names it, and its default
GROUPED = {"control": ("play_group", "games")}


def accounts_file(fleet, host, module):
    return fleet.resolve_path("secrets/accounts/%s-%s.json" % (host, module))


def cmd_accounts(args):
    """The network's accounts (the accounts module), from here: invite codes and requests (the
    sign-up page's), and the accounts themselves with the links that set their passwords. Prints
    JSON: the control page reads it."""
    fleet, roles, ms = load(args)
    found = []
    module = "xmpp" if args.jabber else "accounts"
    for h in fleet.hosts.values():
        if h.role in roles and not h.validate(roles):
            rolesmod.resolve_settings(roles[h.role], h, ms)
            if module not in (h.modules or []) or (args.to and h.name != args.to):
                continue
            x = h.module_settings.get("xmpp") or {}
            if module == "accounts" or x.get("invites") or x.get("requests"):
                found.append(h)
    if not found:
        raise KiwiError("no machine runs %s%s" % ("Jabber with invite codes or requests (xmpp: invites, requests)" if args.jabber
                                                  else "the accounts module", " called %s" % args.to if args.to else ""))
    if len(found) > 1:
        raise KiwiError("several machines run the accounts module (%s): say which with --to" % ", ".join(h.name for h in found))
    h = found[0]
    prefix = "km" if roles[h.role].node_type == "master" else "kn"
    what, rest = args.what, [a for a in (args.arg or [])]
    if args.jabber:
        if what not in ACCOUNT_PAGE_OPS:
            raise KiwiError("accounts --jabber: %s (the Jabber accounts themselves are in its admin page)" % ", ".join(ACCOUNT_PAGE_OPS))
        extra = ["--uses", str(args.uses), "--days", str(args.days), "--note", args.note or ""] if what == "invite" else []
        cmd = "sudo podman exec %s-xmpp-join kiwi-server signup %s --state /state" % (
            prefix, " ".join(shlex.quote(x) for x in [what] + rest[:1] + extra))
        print(remote.run(remote.reach(h, args.ssh), cmd).decode(errors="replace").strip())
        return
    if what == "connect":
        # a service signs people in with these accounts: its client in Kanidm, its secret in the store
        if not rest:
            raise KiwiError("accounts connect HOST [MODULE]: which machine's service (%s)" % ", ".join(CONNECTABLE))
        target = fleet.hosts.get(rest[0])
        module = rest[1] if len(rest) > 1 else "git"
        if not target or module not in CONNECTABLE:
            raise KiwiError("accounts connect: a machine of the fleet and one of %s" % ", ".join(CONNECTABLE))
        rolesmod.resolve_settings(roles[target.role], target, ms)
        if module not in target.modules:
            raise KiwiError("%s does not run %s" % (target.name, module))
        label, site, callbacks = CONNECTABLE[module]
        name = site(target.module_settings.get(module) or {}, target)
        client = "%s-%s" % (module, target.name)
        words = [client, "%s on %s" % (label, target.name), "https://" + name]
        words += ["https://" + name + c for c in ((callbacks,) if isinstance(callbacks, str) else callbacks)]
        words += ["--pkce"] if module in PKCE else []
        if module in GROUPED:
            key, default = GROUPED[module]
            words += ["--group", str((target.module_settings.get(module) or {}).get(key) or default)]
        cmd = ("S=$(ls /home/*/docker/kiwi/accounts.sh /var/home/*/docker/kiwi/accounts.sh 2>/dev/null | head -1); sudo sh \"$S\" oauth %s"
               % " ".join(shlex.quote(x) for x in words))
        answer = json.loads(remote.run(remote.reach(h, args.ssh), cmd).decode(errors="replace").strip().splitlines()[-1])
        path = accounts_file(fleet, target.name, module)
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        util.write_text(path, json.dumps(answer) + "\n", 0o600)
        util.say("%s on %s signs people in with the network's accounts after its next apply: kiwi-server apply %s %s"
                 % (label, target.name, args.fleet, target.name))
        print(json.dumps({"client": answer["client"], "issuer": answer["issuer"], "host": target.name, "module": module}))
        return
    if what in ACCOUNT_PAGE_OPS:
        extra = []
        if what == "invite":
            extra = ["--uses", str(args.uses), "--days", str(args.days), "--note", args.note or ""]
        cmd = "sudo podman exec %s-signup kiwi-server signup %s --state /state" % (
            prefix, " ".join(shlex.quote(x) for x in [what] + rest[:1] + extra))
    elif what in ACCOUNT_OPS:
        name = {"create": "create", "link": "reset", "list": "list", "delete": "delete", "jabber": "jabber",
                "member": "member", "members": "members"}[what]
        cmd = ("S=$(ls /home/*/docker/kiwi/accounts.sh /var/home/*/docker/kiwi/accounts.sh 2>/dev/null | head -1); "
               "sudo sh \"$S\" %s" % " ".join(shlex.quote(x) for x in [name] + rest[:3]))
    else:
        raise KiwiError("accounts: %s" % ", ".join(ACCOUNT_PAGE_OPS + ACCOUNT_OPS))
    out = remote.run(remote.reach(h, args.ssh), cmd).decode(errors="replace").strip()
    print(out)


def cmd_signup(args):
    """The sign-up page of the network's accounts (the accounts module runs it), and what the
    control node asks of it: invite codes, the requests, accepting or denying one."""
    from . import signup as signupmod
    what = args.what
    # the page remembers where Kanidm is, so that the control node's calls need only the folder
    conf = os.path.join(args.state, "config.json")
    ejabberd = json.loads(args.ejabberd) if args.ejabberd else None
    ways = [w for w in (args.ways or "invites,requests").split(",") if w]
    if what == "serve":
        os.makedirs(args.state, mode=0o700, exist_ok=True)
        util.write_text(conf, json.dumps({"kanidm": args.kanidm, "token_file": args.token_file, "id_host": args.id_host,
                                          "ejabberd": ejabberd, "ways": ways, "id_onion": args.id_onion}) + "\n", 0o600)
    elif not args.kanidm and not ejabberd and os.path.isfile(conf):
        c = json.loads(util.read_text(conf))
        args.kanidm, args.token_file, args.id_host = c.get("kanidm", ""), c.get("token_file", ""), c.get("id_host", "")
        ejabberd, ways = c.get("ejabberd"), c.get("ways") or ways
        args.id_onion = args.id_onion or c.get("id_onion", "")
    s = signupmod.Signup(args.state, kanidm=args.kanidm or "", token_file=args.token_file or "", id_host=args.id_host or "",
                         ejabberd=ejabberd, invites="invites" in ways, requests="requests" in ways, captcha="captcha" in ways,
                         id_onion=args.id_onion or "", kanidm_cert=getattr(args, "kanidm_cert", "") or "")
    if what == "serve":
        import uvicorn
        host, _, port = args.listen.rpartition(":")
        util.say("sign-up page on http://%s, accounts at https://%s" % (args.listen, args.id_host))
        sys.stdout.flush()
        terms = None
        if args.terms_file and os.path.isfile(args.terms_file):
            terms = util.read_text(args.terms_file)
        uvicorn.run(signupmod.make_app(s, brand=args.brand or "", terms=terms), host=host.strip("[]"), port=int(port),
                    log_level="warning", proxy_headers=True, forwarded_allow_ips=util.forwarded_allow())
        return
    if what == "invite":
        print(json.dumps({"code": s.invite(uses=args.uses, days=args.days, note=args.note or "")}))
    elif what == "invites":
        print(json.dumps(s.invites()))
    elif what == "revoke":
        print(json.dumps({"revoked": s.revoke(args.arg or "")}))
    elif what == "requests":
        print(json.dumps(s.requests()))
    elif what in ("accept", "deny"):
        print(json.dumps({"state": s.answer(args.arg or "", what == "accept")}))
    elif what == "link":
        print(json.dumps({"link": s.link(args.arg or "")}))
    else:
        raise KiwiError("signup: serve, invite, invites, revoke HINT, requests, accept HINT, deny HINT, link NAME")


def cmd_watch(args):
    """Home pushes: a RAM master that booted waits blank until its role comes; this sends it,
    and keeps the fleet's copies of a running one fresh."""
    if args.timer:
        return ram_t.watch_timer(os.path.abspath(args.fleet), args.timer == "on")
    while True:
        copy = _open_for_watch(args.fleet)
        try:
            if copy:
                os.environ["KIWI_SECRETS_DIR"] = copy[1]["tree"]
            _watch_once(args)
        finally:
            os.environ.pop("KIWI_SECRETS_DIR", None)
            if copy:
                copy[0].save_private(copy[1])
        if args.once:
            return
        time.sleep(args.interval)


def _open_for_watch(path):
    """A locked secret store decrypted from this computer's keyring into a copy of its own for
    one look (a timer has nobody to ask): the store's link is never touched, so the owner can
    open and lock it meanwhile. None when it is open already (then the look uses it) or there
    is none."""
    st = store.Store.for_fleet(path)
    if not st.exists() or st.is_open():
        return None
    try:
        return st, st.private_copy(st.identity())
    except KiwiError as e:
        raise KiwiError("the secret store is locked and the keyring did not open it (%s): "
                        "a RAM master that boots now stays blank until kiwi-server secrets unlock %s" % (e, path))


def _watch_once(args):
    """One look at every RAM master of the fleet."""
    fleet, roles, ms = load(args)
    hosts = [h for h in fleet.select(args.hosts) if h.target == "coreos-ram"]
    if not hosts:
        raise KiwiError("no coreos-ram host to watch in %s" % args.fleet)
    for h in hosts:
        try:
            rolesmod.resolve_settings(roles[h.role], h, ms)     # where its stack and addresses are
        except KiwiError as e:
            util.warn("%s: %s" % (h.name, e))
            continue
        # its key is the fleet's own: on record for its addresses before the first look,
        # also on a computer that never built its image
        ram_t.pin(h, ram_t.host_key(h)[1], fleet.resolve_path("secrets/known_hosts"))
        tgt = remote.reach(h, args.ssh)
        st = ram_t.state(tgt)
        if st == "blank":
            util.say("%s: booted and blank, sending its role" % h.name)
            a = argparse.Namespace(**vars(args))
            a.hosts, a.no_run = [h.name], False
            try:
                cmd_apply(a)
            except KiwiError as e:
                util.warn("%s: %s" % (h.name, e))
        elif st == "ready" and ram_t.keep_due(fleet, h.name):
            try:                    # what changed since (peers enrolled, a certificate renewed)
                if backupmod.carries_wireguard(h):
                    backupmod.keep_wireguard(h, tgt)
                ram_t.keep(h, tgt)
                ram_t.kept_now(fleet, h.name)
            except KiwiError as e:
                util.warn("%s: the fleet's copy is not refreshed: %s" % (h.name, e))
        elif st != "ready":
            util.say("%s: %s" % (h.name, st))


def cmd_status(args):
    """What a running machine says: when its role was applied, what its stack
    is doing, and what is wrong with it. Every host at once, so one that is
    off costs the others nothing."""
    fleet, roles, ms = load(args)
    hosts = fleet.select(args.hosts)
    if args.ssh and len(hosts) != 1:
        raise KiwiError("--ssh goes with exactly one host")

    def ask(h):
        tgt = remote.reach(h, args.ssh)
        try:
            human, data = facts.split(remote.run(tgt, facts.SCRIPT).decode(errors="replace"))
        except KiwiError as e:
            at = unlock.waiting_at(h)
            if at:
                e = "its disk is locked, the machine waits at %s: %s" % (at, unlock.locked_advice(h, args.fleet))
            return {"host": h.name, "target": tgt, "ok": False, "output": str(e), "state": "down", "locked": bool(at),
                    "problems": [{"level": "bad", "text": ("%s" if at else "unreachable: %s") % e}], "notes": [], "facts": {}}
        measured = facts.parse(data)
        state, problems, notes = facts.assess(measured)
        if "backup-server" in (h.modules or []):
            level, text = backupmod.prune_note(fleet, h.name)
            if level:
                problems.append({"level": level, "text": text})
                state = "bad" if "bad" in (state, level) else "warn"
            else:
                notes.append(text)
        return {"host": h.name, "target": tgt, "ok": True, "output": human, "state": state,
                "problems": problems, "notes": notes, "facts": measured}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        out = list(pool.map(ask, hosts))
    record_applied(fleet, *[o["host"] for o in out if o["ok"] and o["facts"].get("applied")], only_new=True)
    if args.porcelain:
        print(json.dumps({"hosts": out}, indent=2))
        return
    for o in out:
        print("== %s (%s)%s" % (o["host"], o["target"], "" if o["ok"] else " — disk locked" if o.get("locked") else " — unreachable"))
        print("   " + o["output"].rstrip().replace("\n", "\n   "))
        if o["ok"]:
            for p in o["problems"]:
                print("   ! %s" % p["text"])
            for n in o["notes"]:
                print("   - %s" % n)


def _master_of(fleet, roles, ms, name=None):
    for h in fleet.hosts.values():
        if name and h.name != name:
            continue
        if h.role in roles and roles[h.role].node_type == "master" and (name or not h.standby_of):
            errs = h.validate(roles) or settings_errors(roles, ms, h)
            if errs:
                raise KiwiError("%s: the master is not valid: %s" % (h.name, "; ".join(errs)))
            return h
    raise KiwiError("no master in the fleet%s" % (" named %s" % name if name else ""))


def keep_identity(fleet, master, ssh=None):
    """The master's WireGuard state into the fleet's copy, and on to its standbys: a device added
    or removed there is added or removed on them too (a removed key must not keep working)."""
    backupmod.keep_wireguard(master, remote.reach(master, ssh))
    for s in fleet.standbys(master.name):
        try:
            if backupmod.put_back_wireguard(s, remote.reach(s)):
                util.say("%s: the master's devices are there too now" % s.name)
        except KiwiError as e:
            util.warn("%s (standby) not reached: it has the master's devices as of before (%s); "
                      "the next apply of %s brings them" % (s.name, e, master.name))


def cmd_enroll(args):
    """A WireGuard client on the master's wg-easy, by API: a node (its config
    lands where the fleet file looks for it), a phone, a laptop, a router."""
    fleet, roles, ms = load(args)
    master = _master_of(fleet, roles, ms, args.master)
    vs = master.module_settings.get("vpn-server") or {}
    name = args.name
    host = fleet.hosts.get(name)
    if host is None and not re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?", name):
        raise KiwiError("%r is neither a fleet host nor a device name (lowercase letters, digits and dashes)" % name)
    client_name = host.hostname if host else name
    groups = vs.get("groups") or {}
    if args.group and args.group not in groups:
        raise KiwiError("no client group %r on %s (have: %s)" % (args.group, master.name, ", ".join(groups) or "none"))
    if host is None and not args.group and not args.address and not args.existing and accessmod.default_group(groups):
        # a new device: in a group without the admin pages, unless one is chosen
        args.group = accessmod.default_group(groups)
        util.say("%s: group %s (the default for a new device; --group chooses another)" % (name, args.group))
    mesh = (master.role_settings or {}).get("mesh_subnet") or "10.8.0.0/16"
    wg_mtu = int(vs.get("wg_mtu") or 1420)
    if args.mtu and not 1280 <= args.mtu <= wg_mtu:     # said before the master is touched
        raise KiwiError("--mtu is between 1280 and %d, what %s's tunnel carries (wg_mtu)" % (wg_mtu, master.name))

    def enroll(url):
        api = wgeasy.WgEasy(url, vs["wg_password"]).login()
        c = api.client(client_name)
        if c is None:
            util.say("creating client %s on %s" % (client_name, master.name))
            c = api.create(client_name)
        elif not args.existing:
            raise KiwiError("%s already has a client named %s — --existing fetches its config" % (master.name, client_name))
        if args.address or args.group:
            addr = args.address or wgeasy.next_address(groups[args.group]["subnet"], [x.get("address") for x in api.clients()],
                                                      exclude=[c.get("address")])
            if addr != c.get("address"):
                util.say("address %s (group %s)" % (addr, args.group or "given"))
                api.set_address(c["id"], addr)
        return api.configuration(c["id"])

    if args.remove:                     # a lost phone: off the VPN, its config gone from the fleet
        if host:
            raise KiwiError("%s is a machine of the fleet, not a device: remove it from the fleet file instead" % name)

        def drop(url):
            api = wgeasy.WgEasy(url, vs["wg_password"]).login()
            c = api.client(client_name)
            if c is not None:
                api.delete(c["id"])
            return c is not None
        if args.api:
            was = drop(args.api)
        else:
            with remote.Tunnel(remote.reach(master, args.ssh), 51821) as url:
                was = drop(url)
        dest = fleet.resolve_path("secrets/devices/%s.conf" % name)
        had = os.path.isfile(dest)
        if had:
            os.remove(dest)
        if not was and not had:
            raise KiwiError("there is no device called %s" % name)
        try:
            keep_identity(fleet, master, args.ssh)
        except KiwiError as e:
            util.warn("%s: the fleet's copy of its WireGuard state was not refreshed: %s" % (master.name, e))
        util.say("%s is off the VPN: its key no longer works" % name)
        return
    if args.api:
        text = enroll(args.api)
    else:
        with remote.Tunnel(remote.reach(master, args.ssh), 51821) as url:
            text = enroll(url)
    if args.split:
        text = wgeasy.split_tunnel(text, mesh)
    # a device roams, a machine stays where its path was measured: the device gets the size that fits everywhere
    mtu = args.mtu or (min(wgeasy.DEVICE_MTU, wg_mtu) if not host else None)
    if mtu:
        text = wgeasy.with_mtu(text, mtu)
    if host:
        dest = fleet.resolve_path("secrets/%s.conf" % host.hostname)
    else:
        dest = fleet.resolve_path("secrets/devices/%s.conf" % name)
    util.write_text(dest, text if text.endswith("\n") else text + "\n", 0o600)
    util.say("%s" % dest)
    try:                                 # the new peer is part of what a rebuilt master needs
        keep_identity(fleet, master, args.ssh)
    except KiwiError as e:
        util.warn("%s: the fleet's copy of its WireGuard state was not refreshed: %s" % (master.name, e))
    if host:
        util.say("%s's vpn-client picks it up by itself (wireguard_config falls back to secrets/<hostname>.conf) — "
                 "kiwi-server render %s" % (host.name, os.path.basename(fleet.path)))
    if args.qr:
        print(qrmod.text_blocks(text))


def cmd_qr(args):
    """A file (a device's WireGuard config) as a QR code: in the terminal, or as an image."""
    data = util.read_bytes(args.file)
    if args.png:
        sys.stdout.buffer.write(qrmod.png(data))
    elif args.svg:
        print(qrmod.svg(data))
    else:
        print(qrmod.text_blocks(data))


def connect_name(machines, wanted, hostname):
    """What this computer is called on the VPN: the name given, else its own. A computer that
    happens to be called like one of the machines gets a name of its own, the same one each
    time, so that the app needs no question for it."""
    if wanted:
        if wanted in machines:
            raise KiwiError("%s is a machine of this network already: give this computer another name with --name" % wanted)
        return wanted
    name = re.sub(r"[^a-z0-9-]+", "-", hostname.split(".")[0].lower()).strip("-") or "computer"
    if name not in machines:
        return name
    return next(n for n in ["%s-pc" % name] + ["%s-pc-%d" % (name, i) for i in range(2, 50)] if n not in machines)


def cmd_connect(args):
    """This computer becomes a device of the network: its own place on the VPN, a connection
    that carries only the network, and the network's certificates trusted in its browsers."""
    from . import connect as connectmod
    fleet, roles, ms = load(args)
    path = args.fleet
    domain = fleet.defaults.get("domain") or "home"
    name = connect_name(fleet.hosts, args.name, socket.gethostname())
    if not args.name and name != connect_name({}, None, socket.gethostname()):
        util.say("a machine of this network has this computer's name: on the VPN this computer is called %s" % name)
    if args.remove:
        if connectmod.have_networkmanager() and connectmod.down(domain):
            util.say("the connection %s is gone from this computer" % connectmod.connection_name(domain))
        rc = main(["enroll", path, name, "--remove"] + (["--ssh", args.ssh] if args.ssh else []))
        if rc:
            raise SystemExit(rc)
        return
    conf = fleet.resolve_path("secrets/devices/%s.conf" % name)
    if (os.path.isfile(conf) and not args.existing and connectmod.have_networkmanager()
            and not connectmod.exists(domain)):
        # two computers with one name would share one key, and each would cut the other off
        raise KiwiError("a device called %s is part of the network already, and this computer has no connection to it yet. "
                        "If it is this computer (it was connected before), run this again with --existing. If it is "
                        "another computer of the same name, give this one its own: --name %s-2" % (name, name))
    if not os.path.isfile(conf):
        util.say("%s gets its place on the VPN" % name)
        rc = main(["enroll", path, name, "--split"] + (["--group", args.group] if args.group else [])
                  + (["--ssh", args.ssh] if args.ssh else []))
        if rc:
            raise SystemExit(rc)
    tls = fleet.defaults.get("tls") or {}
    ca = os.path.join(fleet.resolve_path(tls.get("ca_dir") or "secrets/ca"), "kiwiCA.pem")
    if not connectmod.have_networkmanager():
        util.say("this computer has no NetworkManager: import %s into its WireGuard program yourself" % shown_path(conf))
    else:
        con = connectmod.up(conf, domain)
        util.say("connected: %s in this computer's network settings. It carries your network only, comes up by "
                 "itself, and names ending in .%s are asked there; everything else is as before" % (con, domain))
        master = next((h for h in fleet.hosts.values() if h.role in roles and roles[h.role].node_type == "master" and not h.standby_of), None)
        if master:
            if connectmod.resolves(master.hostname, 15):
                util.say("%s answers: this computer reaches the network" % master.hostname)
            else:
                # the connection is set up either way: it works from the moment the master answers
                util.warn("%s does not answer yet. Is the master running? This computer is connected as soon as it is: "
                          "nothing more to do here" % master.hostname)
    if os.path.isfile(ca) and not args.no_trust:
        took, why = connectmod.trust(ca, domain, make_tc(args))
        if took:
            util.say("your browsers trust the network's certificates now (%d certificate store%s). Restart a browser that is open"
                     % (len(took), "" if len(took) == 1 else "s"))
        elif why:
            util.say("your browsers were not told about the network's certificates: %s. Then run this again" % why)
        else:
            util.say("no browser of yours keeps a certificate store here yet: start it once, then run this again")
        util.say("for every program on this computer, once, as administrator:")
        print("    %s" % connectmod.system_trust_command(shown_path(ca), domain))
    util.say("what is where, and the logins: kiwi-server status %s" % path)


def cmd_ca(args):
    fleet, _roles, _ms = load(args, need_roles=False)
    tls = fleet.defaults.get("tls") or {}
    ca_dir = fleet.resolve_path(tls.get("ca_dir") or "secrets/ca")
    key, pem = certs.ensure_ca(ca_dir, int(tls.get("days") or certs.DAYS), tls.get("name_constraints") or [])
    if args.porcelain:
        print(json.dumps({"key": key, "cert": pem}))
        return
    util.say("CA certificate: %s   (import this on your devices; keep %s private)" % (pem, key))
    util.say("hosts that run the reverse proxy get a *.<hostname> certificate from it at render time")


def _render_or_build(args, build):
    fleet, roles, ms = load(args)
    hosts = fleet.select(args.hosts)
    offline = getattr(args, "offline", False)
    if offline and args.bare:
        raise KiwiError("--bare and --offline exclude each other")
    if offline and any(h.target == "coreos-ram" for h in hosts):
        raise KiwiError("a coreos-ram image never carries the role: build it without --offline, then kiwi-server apply")
    if args.bare or (build and not offline):       # an ISO is bare unless it is asked for by name
        make_bare(fleet, hosts, util.say)
    check(fleet, roles, ms, hosts)
    tc = make_tc(args)
    out_dir = out_dir_for(args, fleet)
    r = Renderer(fleet, roles, out_dir, tc, ms, media_dir_for(args, fleet))
    failed = []
    for h in hosts:
        try:
            if build:
                util.say("building %s (%s, %s) -> %s" % (h.name, h.target, h.role, out_dir))
                o = r.build(h, cache_dir_for(args))
                util.say("%s: %s (%d MB)" % (h.name, o.iso, os.path.getsize(o.iso) >> 20))
                if luks.uses_one_time_key(h):
                    util.say("%s: at its first boot the disk asks for the key in %s" % (h.name, luks.once_file(h)))
            else:
                o, produced = r.render(h)
                util.say("%s: %s" % (h.name, ", ".join(os.path.basename(p) for p in produced)))
            for w in h.warnings:
                util.warn("%s: %s" % (h.name, w))
        except KiwiError as e:
            failed.append(h.name)
            print("error: %s" % e, file=sys.stderr)
    if failed:
        raise KiwiError("failed: %s" % ", ".join(failed))
    if offline:
        warn_offline(hosts, "ISO")
    if build and any(h.target not in ("coreos-ram", "armbian") for h in hosts):
        util.say("done — these ISOs wipe their target disk on boot; label them")
    elif build and any(h.target == "armbian" for h in hosts):
        util.say("done — flash the .img to the board's SD card or eMMC and boot it")
    elif build:
        util.say("done — boot from it every time; kiwi-server watch (or apply) sends the role after every boot")
    if not getattr(args, "no_backup", False):
        auto_backup(fleet)


# ---- the fleet's own backup --------------------------------------------------------

def auto_backup(fleet):
    """After a successful render or build: the fleet directory to backup.hosts.
    A node that is down is a warning, never a failed render."""
    cfg = fleet.defaults.get("backup") or {}
    targets = [str(h) for h in (cfg.get("hosts") or [])]
    if not targets:
        return
    stored = backupmod.has_store(fleet)         # then its recovery key opens the copy: no passphrase
    if not stored and not (cfg.get("passphrase_file") or os.environ.get("KIWI_BACKUP_PASS")):
        util.warn("backup.hosts is set but backup.passphrase_file is not — the fleet was not backed up "
                  "(kiwi-server backup asks for a passphrase)")
        return
    try:
        data = backupmod.create_copy(fleet) if stored else backupmod.create(fleet, backupmod.passphrase(fleet))
    except KiwiError as e:
        util.warn("fleet backup skipped: %s" % e)
        return
    name = backupmod.archive_name(fleet)
    for h in fleet.select(targets):
        try:
            backupmod.store_remote(data, backupmod.ssh_target(h), name, int(cfg.get("keep") or 10))
            util.say("fleet backed up to %s (%s)" % (h.name, name))
            recoverymod.record_copy(fleet, [h.name])
        except KiwiError as e:
            util.warn("fleet backup to %s failed: %s" % (h.name, e))


def cmd_backup(args):
    fleet, _roles, _ms = load(args, need_roles=False)
    if fleet.errors:
        raise KiwiError(fleet.errors[0])
    cfg = fleet.defaults.get("backup") or {}
    hosts = fleet.select(args.hosts or [str(h) for h in (cfg.get("hosts") or [])])
    if args.hosts == [] and not cfg.get("hosts"):
        hosts = []
        if not args.local:          # nobody said where: every machine that is set up keeps a copy
            applied = fleet_state(fleet).get("applied") or {}
            hosts = [h for h in fleet.hosts.values() if h.name in applied]
            if hosts:
                util.say("no place is named for the copy (backup.hosts): it goes to every machine that is set up (%s)"
                         % ", ".join(h.name for h in hosts))
    if not hosts and not args.local:
        raise KiwiError("nowhere to back up to: no machine is set up yet. Name hosts, set backup.hosts in the fleet, "
                        "or give --local DIR")
    if args.ssh and len(hosts) != 1:
        raise KiwiError("--ssh goes with exactly one host")
    if backupmod.has_store(fleet):          # the store's keys encrypt it: the recovery key opens it
        if args.passphrase_file:
            util.say("this fleet has a secret store: its recovery key opens the copy, the passphrase is not used")
        data = backupmod.create_copy(fleet)
        n = 2 + len(backupmod.copy_files(fleet))
    else:
        pw = backupmod.passphrase(fleet, args.passphrase_file, confirm=not args.passphrase_file)
        data = backupmod.create(fleet, pw)
        n = len(backupmod.fleet_files(fleet))
    name = backupmod.archive_name(fleet)
    if args.local:
        util.say("%s (%d files, %d KB)" % (backupmod.store_local(data, args.local, name), n, len(data) >> 10))
        recoverymod.record_copy(fleet, local=args.local)
    failed = []
    for h in hosts:                         # a machine that is off does not keep the others from their copy
        try:
            kept = backupmod.store_remote(data, backupmod.ssh_target(h, args.ssh), name, int(cfg.get("keep") or 10))
            util.say("%s: %s stored in %s (%d kept)" % (h.name, name, backupmod.REMOTE_DIR, len(kept)))
            recoverymod.record_copy(fleet, [h.name])
        except KiwiError as e:
            if len(hosts) == 1:
                raise
            failed.append(h.name)
            util.warn("%s: not stored: %s" % (h.name, e))
    if failed:
        raise KiwiError("the copy did not reach %s" % ", ".join(failed))


def parse_snapshots(text):
    """`kiwi-stack snapshots --json`: per backup server its snapshots, newest first."""
    servers = []
    for line in text.splitlines():
        name, _tab, data = line.partition("\t")
        try:
            raw = json.loads(data) if data else None
        except ValueError:
            raw = None
        snaps = []
        for sn in raw or []:
            tags = sn.get("tags") or []
            snaps.append({"id": sn.get("short_id") or (sn.get("id") or "")[:8], "time": sn.get("time", ""),
                          "module": next((t for t in tags if t not in ("frozen", "bulk")), ""),
                          "kind": "frozen" if "frozen" in tags else "bulk" if "bulk" in tags else ""})
        snaps.sort(key=lambda x: x["time"], reverse=True)
        servers.append({"server": name, "ok": raw is not None, "snapshots": snaps})
    return servers


def cmd_backups(args):
    """A machine's own backups: what each backup server keeps of it, a backup now, or modules
    brought back. The fleet folder's backup is `backup`."""
    fleet, roles, ms = load(args)
    h = fleet.select([args.host])[0]
    rolesmod.resolve_settings(roles[h.role], h, ms)
    if "backup" not in (h.modules or []):
        raise KiwiError("%s has no backup module" % h.name)
    tgt = remote.reach(h, args.ssh)
    mods = " ".join(shlex.quote(m) for m in args.modules)
    if args.now or args.new:
        rc = remote.run_stream(tgt, "kiwi-stack backup" + (" --new" if args.new else ""))
    elif args.restore:
        if not args.modules:            # everything there is a snapshot of
            found = parse_snapshots(remote.run(tgt, "kiwi-stack snapshots --json").decode(errors="replace"))
            mods = " ".join(sorted({sn["module"] for sv in found for sn in sv["snapshots"] if sn["module"]}))
            if not mods:
                raise KiwiError("%s: the backup servers keep nothing of it" % h.name)
        at = " --at %s" % shlex.quote(args.at) if args.at else ""
        rc = remote.run_stream(tgt, "kiwi-stack restore%s %s" % (at, mods))
    else:
        servers = parse_snapshots(remote.run(tgt, "kiwi-stack snapshots --json %s" % mods).decode(errors="replace"))
        if args.porcelain:
            print(json.dumps({"host": h.name, "servers": servers}, indent=1))
            return
        for sv in servers:
            print("== %s%s" % (sv["server"], "" if sv["ok"] else ": nothing found (not reached, or no repository of this machine)"))
            for sn in sv["snapshots"]:
                print("   %s  %-12s %-6s %s" % (sn["time"][:19].replace("T", " "), sn["module"], sn["kind"], sn["id"]))
        return
    if rc != 0:
        raise KiwiError("%s: kiwi-stack exited with %d" % (h.name, rc))


def cmd_sshca(args):
    """The fleet's SSH CA: made once, then every apply gives the machine a host certificate and
    its sshd takes user certificates the CA signed."""
    fleet, roles, ms = load(args)
    for h in fleet.hosts.values():          # the machines' addresses, for the certificates' names
        try:
            rolesmod.resolve_settings(roles[h.role], h, ms)
        except (KiwiError, KeyError):
            pass
    if args.action == "init":
        pub = sshca.init(fleet)
        util.say("SSH CA made: %s (%s); every apply from now on signs the machine's host key" % (sshca.CA, pub.split()[1][-12:]))
        util.say("next: kiwi-server apply %s, then kiwi-server sshca %s trust" % (os.path.basename(args.fleet), os.path.basename(args.fleet)))
        return
    if not sshca.exists(fleet):
        raise KiwiError("the fleet has no SSH CA yet: kiwi-server sshca %s init" % args.fleet)
    if args.action == "trust":
        known = os.path.expanduser(args.known_hosts or "~/.ssh/known_hosts")
        changed = sshca.trust(fleet, known)
        util.say("%s %s: ssh to the fleet's machines checks their certificates" % ("written to" if changed else "already in", known))
        return
    if args.action == "sign":
        if not args.key:
            raise KiwiError("which key? kiwi-server sshca %s sign ~/.ssh/id_ed25519.pub" % args.fleet)
        key = os.path.expanduser(args.key)
        user = args.user or (fleet.defaults.get("admin") or {}).get("user") or "core"
        cert = sshca.sign(fleet, util.read_text(key), "%s (%s)" % (user, os.path.basename(key)), [user], args.days)
        out = key[:-4] + "-cert.pub" if key.endswith(".pub") else key + "-cert.pub"
        util.write_text(out, cert, 0o644)
        util.say("%s: lets this key in as %s on every machine for %d days (ssh finds it next to the key)" % (out, user, args.days))
        return
    print("SSH CA %s  %s" % (sshca.CA, sshca.fingerprint(fleet)))
    print("trusted for: %s" % ", ".join(sshca.patterns(fleet)))


def share(fleet_path, action):
    """push or pull the way this fleet is shared (a control node, a synced folder). False
    when it is not shared, or this is the control node itself."""
    from . import controlsync
    fleet_dir, name = os.path.dirname(os.path.abspath(fleet_path)), os.path.basename(fleet_path)
    if os.environ.get("KIWI_CONTROL_HOST"):
        return False
    if controlsync.remote_of(fleet_dir):
        (controlsync.push_api if action == "push" else controlsync.pull_api)(fleet_dir, name)
    elif controlsync.folder_of(fleet_dir):
        folder = controlsync.folder_of(fleet_dir)
        (controlsync.push_dir if action == "push" else controlsync.pull_dir)(fleet_dir, name, folder)
    else:
        return False
    return True


def cmd_rotate(args):
    """What a computer that managed this network could read, made new. Without an action: the
    list. `ssh`: a new admin key and SSH CA on every machine; the old ones work until the last
    machine has changed. `seed`: a new seed; each machine is applied and restarts the
    containers whose secrets follow it."""
    from . import rotate
    if args.action and share(args.fleet, "pull"):      # before anything is made: this copy is the newest
        util.say("in step with the other computers")
    fleet, roles, ms = load(args)
    for h in fleet.hosts.values():
        try:
            rolesmod.resolve_settings(roles[h.role], h, ms)
        except (KiwiError, KeyError):
            pass
    if not args.action:
        ways = [n for n, _k in store.Store.for_fleet(args.fleet).ways()] if store.Store.for_fleet(args.fleet).exists() else []
        for what, state, how in rotate.plan(fleet, ways, ms):
            print("%-48s %s" % (what, "  ".join(x for x in (state, how) if x)))
        return
    for n in list(args.hosts) + list(args.waive or []):
        if n not in fleet.hosts:
            raise KiwiError("no such machine: %s" % n)
    if args.action == "seed":
        rotate.seed_begin(fleet, ms, args.why or "")
        if share(args.fleet, "push"):       # the others first, as with the keys
            util.say("the other computers and the control node know the new seed")

        def apply(name):
            return main(["apply", args.fleet, name])
        def before(name):
            h = fleet.hosts[name]
            try:
                rotate.in_place(fleet, h, remote.reach(h), roles, ms)
            except KiwiError:
                pass                    # not reachable: the apply says so
        left = rotate.seed(fleet, ms, apply, args.hosts, args.waive or (), args.why or "", before=before)
        if share(args.fleet, "push"):
            util.say("the other computers and the control node have the result")
        if left:
            raise SystemExit(1)
        return
    rotate.begin(fleet, args.why or "")
    remote.configure(fleet.resolve_path("secrets/known_hosts"), fleet_identities(fleet))    # the old keys too
    # the others first: a control node that knows both keys keeps reaching every machine
    # while they change (found: one that did not learned new host keys meanwhile, and its
    # copy and this one had both changed)
    if share(args.fleet, "push"):
        util.say("the other computers and the control node know the new keys")
    left = rotate.ssh(fleet, args.hosts, args.waive or (), args.why or "")
    if share(args.fleet, "push"):
        util.say("the other computers and the control node have the result")
    if left:
        raise SystemExit(1)


def _access_facts(args):
    """What `access` works on: the fleet, its one master, the groups as the master has them,
    the partners, the machines a rule can name and where each device's address is."""
    fleet, roles, ms = load(args)
    r = Renderer(fleet, roles, out_dir_for(args, fleet), None, ms)
    domain, _master_ip, mesh, _mtu = fleet_facts(fleet, roles, ms, r)
    masters = [h for h in fleet.hosts.values() if h.role in roles and roles[h.role].node_type == "master" and not h.standby_of
               and "vpn-server" in (h.modules or [])]
    if len(masters) != 1:
        if not masters and not has_master(fleet, roles):
            raise KiwiError("this network has no master yet, so no VPN: who may reach what comes with a master")
        raise KiwiError("access is about the VPN of one master: this fleet has %d with a VPN server" % len(masters))
    master = masters[0]
    groups = (master.module_settings.get("vpn-server") or {}).get("groups") or {}
    found = accessmod.machines(fleet, roles, r.dns_records(), r._admin)
    for m in found:                 # which of its sites can be hidden services: the ones their modules mark
        try:
            m["tor"] = sorted(site_names(ms, fleet.hosts[m["name"]], roles[fleet.hosts[m["name"]].role], with_usable=True)[1])
        except KiwiError:
            m["tor"] = []
    devices = {}
    ddir = fleet.resolve_path("secrets/devices")
    for f in sorted(os.listdir(ddir)) if os.path.isdir(ddir) else []:
        if f.endswith(".conf") and not f.startswith("."):
            m = re.search(r"^\s*Address\s*=\s*([0-9.]+)", util.read_text(os.path.join(ddir, f)), re.M)
            if m:
                devices[f[:-5]] = m.group(1)
    partners = fedmod.partners(os.path.dirname(fleet.path))
    return fleet, master, groups, partners, found, domain, mesh, devices


def cmd_access(args):
    """Who on the VPN may reach what: the VPN's groups and the linked networks, shown or
    changed (access.py). The rules reach the machines with the next apply."""
    fleet, master, groups, partners, found, domain, mesh, devices = _access_facts(args)
    yes = {"yes": True, "no": False, None: None}
    flags = {k: yes[getattr(args, k)] for k in accessmod.FLAGS}
    changing = args.who and (args.what or args.new or args.add or args.remove or any(v is not None for v in flags.values()))
    if not changing:
        rows = accessmod.view(groups, partners, found, domain, devices)
        if args.who:
            rows = [x for x in rows if x["name"] == args.who]
            if not rows:
                raise KiwiError("no group or linked network called %s" % args.who)
        if args.porcelain:
            print(json.dumps({"master": master.name, "who": rows, "machines": found}, indent=2))
            return
        for x in rows:
            more = ""
            if x["kind"] == "group":
                more = "   (%s)" % ", ".join("%s: %s" % (w, "yes" if x[k] else "no") for k, w in
                                             (("peers", "other devices"), ("internet", "internet"), ("admin", "admin pages")))
            print("%-10s %-15s %s%s" % (x["name"], x["range"] if x["kind"] == "group" else "linked network", x["says"], more))
            if x["devices"]:
                print("%-10s %-15s devices: %s" % ("", "", ", ".join(x["devices"])))
        for h, s, a, c, _site in _hidden(fleet, rolesmod.discover(), modset(fleet)) if not args.who else []:
            print("%-10s %-15s %s.%s  (%s)" % ("Tor", "hidden service", s, h.name, ("only: " + ", ".join(c)) if c else "anyone with the address"))
        grouped = {d for x in rows for d in x["devices"]}
        loose = sorted(d for d in devices if d not in grouped)
        if loose and not args.who:
            print("%-10s %-15s everything\n%-10s %-15s devices: %s" % ("(no group)", "", "", "", ", ".join(loose)))
        pages = ["%s.%s" % (s, m["name"]) for m in found for s in m["admin"]]
        if pages and not args.who:
            print("\nadmin pages (only with \"admin pages: yes\", or named): %s" % ", ".join(pages))
        print("\nmachines: %s" % "; ".join("%s%s" % (m["name"], (" (%s)" % ", ".join(m["services"])) if m["services"] else "")
                                           for m in found if not m["master"]))
        print("change:   kiwi-server access %s <who> <what…>    what: all, nodes, apps, nothing, a machine, service.machine, machine:port"
              % os.path.basename(fleet.path))
        return
    what = [w for w in args.what if w != "nothing"]
    if args.who in groups and args.who in partners:
        raise KiwiError("%s is a group and a linked network: rename one of them" % args.who)
    touched = set()             # the nodes whose own web server refuses something else from now on
    if args.who in partners:
        if args.new or any(v is not None for v in flags.values()):
            raise KiwiError("%s is a linked network: it is given machines and services, nothing else" % args.who)
        have = [] if not (args.add or args.remove) else list(partners[args.who]["share"])
        gone = [accessmod.canon(e, found, domain, False) for e in what] if args.remove else []
        for e in ([] if args.remove else what):
            e = accessmod.canon(e, found, domain, False)
            if e not in have:
                have.append(e)
        have = [e for e in have if e not in gone]
        names = {m["name"] for m in found}
        touched = {e.split(".")[-1] for e in list(partners[args.who]["share"]) + have
                   if ":" not in e and "." in e and e.split(".")[-1] in names} | \
                  ({m["name"] for m in found if not m["master"]} if set(have) != set(partners[args.who]["share"]) and not have else set())
        fedmod.set_share(os.path.dirname(os.path.realpath(fleet.path)), args.who, have)
        util.say("%s (linked network) may reach: %s" % (args.who, accessmod.words(have)))
    else:
        new = accessmod.change(groups, args.who, found, domain, mesh, reach=None if (args.add or args.remove) else what,
                               add=what if args.add else (), remove=what if args.remove else (), new=args.new, flags=flags)
        pages = lambda m: ["%s.%s" % (s, m["hostname"]) for s in m["admin"]]      # noqa: E731
        touched = {m["name"] for m in found if not m["master"] and
                   accessmod.node_rules(groups, m["name"], m["hostname"], pages(m))
                   != accessmod.node_rules(new, m["name"], m["hostname"], pages(m))}
        path = os.path.realpath(fleet.path)
        before = util.read_text(path)
        text, kept = accessmod.rewrite(before, master.name, master.role, new)
        util.write_text(path, text, 0o600)
        errs = config.Fleet.load(fleet.path).errors
        if errs:
            util.write_text(path, before, 0o600)
            raise KiwiError(errs[0])
        g = new[args.who]
        util.say("%s (%s) may reach: %s" % (args.who, g["subnet"], accessmod.words(g["reach"])))
        if args.new:
            util.say("a device joins it with: kiwi-server enroll %s <device> --group %s" % (os.path.basename(fleet.path), args.who))
        if not kept and any(x.lstrip().startswith("#") for x in before.splitlines()[2:]):
            util.say("%s was written anew: the comments in it are not kept" % os.path.basename(fleet.path))
    util.say("bring it to the machines: kiwi-server apply %s %s" % (os.path.basename(fleet.path),
                                                                    " ".join([master.name] + sorted(touched - {master.name}))))


def site_names(ms, host, role, with_usable=False):
    """A machine's sites by their short names -> full names: what comes before the machine's
    own name (cloud for cloud.papaya.home), and for a public name what its module calls the
    site (xmpp for chat.example.org). A hidden service is named by the short one."""
    spec = rolesmod.stack_spec(host, role)
    rr = modmod.Renderer(ms)
    ordered = ms.resolve(spec.modules, spec.node_type, spec.without)
    _up, servers = rr.nginx_blocks(spec, ordered, rr.base_context(spec, ordered))
    out, usable, owner = {}, set(), {}
    for s in servers:
        name = s["server_name"]
        if "*" in name:
            continue
        # a public name: what its module calls the site, else the name's first part (relay for relay.example.org)
        short = name[:-len(host.hostname) - 1] if name.endswith("." + host.hostname) else s.get("site") or name.split(".", 1)[0]
        if short and short not in out:
            out[short] = name
            owner[short] = s.get("module") or ""
            if s.get("onion"):
                usable.add(short)
    return (out, usable, owner) if with_usable else out


def _hidden(fleet, roles, ms):
    """Every hidden service of the fleet: [(host, service, address, clients)]."""
    out = []
    for h in fleet.hosts.values():
        if h.role not in roles or h.validate(roles):
            continue
        try:
            rolesmod.resolve_settings(roles[h.role], h, ms)
        except KiwiError:
            continue
        if "onion" not in (h.modules or []):
            continue
        try:
            full = site_names(ms, h, roles[h.role])
        except KiwiError:
            full = {}
        for name, clients in onionmod.services(h).items():
            out.append((h, name, onionmod.address(onionmod.service_key(fleet.seed(), h.name, name,
                                                                      onionmod.rounds(h).get(name, 1))[1]), clients,
                        full.get(name) or "%s.%s" % (name, h.hostname)))
    return out


def cmd_onion(args):
    """Hidden services (docs/design/onion.md): sites of a machine that anyone opens with Tor
    Browser, or only named people. Listed, made, limited to names, taken away."""
    fleet, roles, ms = load(args)
    if not args.what:
        rows = _hidden(fleet, roles, ms)
        if args.porcelain:
            print(json.dumps({"services": [{"host": h.name, "service": s, "site": site,
                                            "address": a, "clients": c} for h, s, a, c, site in rows]}, indent=2))
            return
        for h, s, a, c, _site in rows:
            print("%-22s http://%s  %s" % ("%s.%s" % (s, h.name), a, ("only: " + ", ".join(c)) if c else "anyone with the address"))
        if not rows:
            util.say("no hidden services yet. Make one: kiwi-server onion %s <service>.<machine>, e.g. git.papaya"
                     % os.path.basename(fleet.path))
        return
    service, _, hname = args.what.partition(".")
    host = fleet.hosts.get(hname)
    if host is None or host.role not in roles or not roles[host.role].stack:
        raise KiwiError("which service of which machine? <service>.<machine>, e.g. git.papaya (machines: %s)"
                        % ", ".join(h.name for h in fleet.hosts.values() if h.role in roles and roles[h.role].stack))
    if not onionmod.NAME.match(service) or (args.client and not onionmod.NAME.match(args.client)):
        raise KiwiError("a service and a person are named in small letters, digits and -")
    role = roles[host.role]
    rolesmod.resolve_settings(role, host, ms)
    r = Renderer(fleet, roles, out_dir_for(args, fleet), None, ms)
    r.dns_records()
    sites, usable, _owner = site_names(ms, host, role, with_usable=True)
    sites = sorted(sites)
    if service in sites and service not in usable and not args.remove and not args.anyway and service not in (
            onionmod.services(host) if "onion" in host.modules else {}):
        raise KiwiError("%s.%s is not marked as usable over Tor: its pages were not seen to work at a .onion address "
                        "(they may send a visitor to the name on the VPN, or need that name). Usable on %s: %s. "
                        "To try it all the same: --anyway"
                        % (service, host.name, host.name, ", ".join(sorted(usable)) or "none"))
    if service not in sites:
        raise KiwiError("%s has no site called %s (it has: %s)" % (host.name, service, ", ".join(sites) or "none on the VPN yet"))
    at_home = role.node_type == "master" and bool((host.role_settings or {}).get("at_home"))
    if (role.node_type != "master" or at_home) and "vpn-client" not in host.modules:
        direct = bool(((host.role_settings_raw or {}).get("onion") or {}).get("without_vpn"))
        if not direct:
            raise KiwiError("%s has no VPN client, so its Tor would leave through its own connection, where your internet "
                            "provider sees it. Give it one (the mesh's, or, without a master, a commercial VPN: a vpn-client "
                            "with vpn_provider mullvad, protonvpn, …), or choose Tor without a VPN: onion: { without_vpn: true }"
                            % host.name)
        util.warn("%s: Tor leaves through its own connection (onion.without_vpn): your internet provider can see that "
                  "it uses Tor" % host.name)
    have = onionmod.services(host) if "onion" in host.modules else {}
    rounds = onionmod.rounds(host) if "onion" in host.modules else {}
    if args.new_address:
        if args.remove or args.client:
            raise KiwiError("--new-address changes the address only: without --remove or --client")
        if service not in have:
            raise KiwiError("%s.%s is no hidden service: it has no address to change" % (service, host.name))
        old_address = onionmod.address(onionmod.service_key(fleet.seed(), host.name, service, rounds.get(service, 1))[1])
        rounds[service] = rounds.get(service, 1) + 1
    address = onionmod.address(onionmod.service_key(fleet.seed(), host.name, service, rounds.get(service, 1))[1])
    base = os.path.basename(fleet.path)
    if args.show:
        if args.show not in have.get(service, []):
            raise KiwiError("%s is not a name of %s.%s (names: %s)" % (args.show, service, host.name, ", ".join(have.get(service, [])) or "none"))
        print("%s opens http://%s in Tor Browser. When it asks for a key:\n  %s"
              % (args.show, address, onionmod.browser_line(fleet.seed(), host.name, service, args.show)))
        return
    if args.remove:
        if service not in have:
            raise KiwiError("%s.%s is no hidden service" % (service, host.name))
        if args.client:
            if args.client not in have[service]:
                raise KiwiError("%s is not a name of %s.%s" % (args.client, service, host.name))
            have[service] = [c for c in have[service] if c != args.client]
            said = "%s can no longer open %s.%s%s" % (args.client, service, host.name,
                                                       ": nobody is named now, so anyone with the address gets in. --remove without "
                                                       "--client takes the service away" if not have[service] else "")
        else:
            del have[service]
            said = "%s.%s is no hidden service any more" % (service, host.name)
    else:
        clients = have.setdefault(service, [])
        if args.client and args.client not in clients:
            clients.append(args.client)
        said = "%s.%s is a hidden service: http://%s" % (service, host.name, address)
        if args.new_address:
            said = ("%s.%s has a new address: http://%s. The old one (%s) stops answering with the next apply; "
                    "whoever had it needs the new one" % (service, host.name, address, old_address))
    value = {"services": {s: dict(({"clients": c} if c else {}), **({"round": rounds[s]} if rounds.get(s, 1) > 1 else {}))
                          for s, c in have.items()}}
    mods = list(host.modules) if "onion" in host.modules else list(host.modules) + ["onion"]
    if not have:                # no hidden service left: the machine does not run Tor for nothing
        mods = [x for x in mods if x != "onion"]
    path = os.path.realpath(fleet.path)
    before = util.read_text(path)
    text = before
    for where, v in ((["hosts", host.name, host.role, "modules"], mods), (["hosts", host.name, host.role, "onion"], value)):
        text = fleettext.put(text, where, v) if text is not None else None
    if text is None:            # laid out in a way the text edit does not follow: from the data, comments lost
        doc = formmod.Doc.parse(before)
        block = doc.block(host.name).setdefault(host.role, {})
        block["modules"], block["onion"] = mods, value
        text = doc.dump()
        if any(x.lstrip().startswith("#") for x in before.splitlines()[2:]):
            util.say("%s was written anew: the comments in it are not kept" % base)
    util.write_text(path, text, 0o600)
    errs = config.Fleet.load(fleet.path).errors
    if errs:
        util.write_text(path, before, 0o600)
        raise KiwiError(errs[0])
    util.say(said)
    if args.client and not args.remove:
        util.say("only %s. What %s types when Tor Browser asks for a key:\n  %s"
                 % (", ".join(have[service]), args.client, onionmod.browser_line(fleet.seed(), host.name, service, args.client)))
    elif not args.remove and not have[service]:
        util.say("anyone with the address gets in, and meets the service's own login")
    util.say("bring it to the machine: kiwi-server apply %s %s" % (base, host.name))


SERVICE_NAME = re.compile(r"^k[nm]-[a-z0-9][a-z0-9-]{0,40}$")
SERVICE_ACTIONS = ("restart", "stop", "start", "logs")


def service_script(name, action, lines=200):
    """What runs on the machine for one service of its stack: restart, stop, start, or its last
    lines of log. Only a container of the stack (kn-…, km-…): a podman machine runs each as its
    own systemd unit (kiwi-stack's quadlets), a docker one as a container."""
    if not SERVICE_NAME.match(name or ""):
        raise KiwiError("%r is not a service of a stack (its container's name, like kn-nginx)" % name)
    if action not in SERVICE_ACTIONS:
        raise KiwiError("restart, stop, start or logs")
    lines = max(1, min(int(lines), 2000))
    done = {"restart": "restarted", "stop": "stopped", "start": "started"}.get(action)
    unit = ('journalctl -u "$n.service" -n %d --no-pager -o short-iso' % lines if action == "logs"
            else 'systemctl %s "$n.service"' % action)
    dock = 'docker logs --tail %d "$n" 2>&1' % lines if action == "logs" else 'docker %s "$n" >/dev/null' % action
    return ("n=%s\n"
            'if systemctl cat "$n.service" >/dev/null 2>&1; then %s\n'
            'elif command -v docker >/dev/null && docker inspect "$n" >/dev/null 2>&1; then %s\n'
            'else echo "this machine has no service $n" >&2; exit 3; fi\n' % (shlex.quote(name), unit, dock)
            + ('echo "$n: %s"\n' % done if done else ""))


def game_servers(h):
    """The game servers of a machine (games module): server, game, its container and where players connect."""
    if "games" not in (h.modules or []):
        return []
    g = (h.module_settings or {}).get("games") or {}
    catalog, out = g.get("catalog") or {}, []
    for name, s in sorted((g.get("servers") or {}).items()):
        game = catalog.get((s or {}).get("game")) or {}
        if not game.get("ports"):
            continue
        port = int(s.get("port") or str(game["ports"][0]).split("/")[0])
        out.append({"server": name, "game": s.get("game"), "title": game.get("title") or s.get("game"),
                    "container": "kn-game-%s" % name, "address": "%s:%d" % (h.hostname, port),
                    "sleep": int(s.get("sleep") or 0)})
    return out


def cmd_service(args):
    """One service of a machine's stack, by hand: restarted, stopped, started, or its last lines
    of log. Stopped stays stopped until it is started again or the machine restarts."""
    fleet, _roles, _ms = load(args)
    h = fleet.select([args.host])[0]
    script = service_script(args.name, args.action, args.lines)
    sys.stdout.write(remote.run(remote.reach(h, args.ssh), script).decode(errors="replace"))


def cmd_disks(args):
    """The disks a machine has, as it sees them, and which of them hold data that is not
    mounted: with the line for `mounts` in the fleet file that puts it there."""
    fleet, roles, ms = load(args)
    host = fleet.select([args.host])[0]
    tgt = remote.reach(host, args.ssh)
    out = remote.run(tgt, "lsblk -P -o NAME,SIZE,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINT -e 7,11").decode()
    rows = [dict(re.findall(r'(\w+)="([^"]*)"', line)) for line in out.splitlines() if line.strip()]
    set_up = {m[0]: m[1] for m in config.mounts_of(host.cfg)}
    free = []
    for r in rows:
        if r.get("FSTYPE") in ("xfs", "ext4", "btrfs") and not r.get("MOUNTPOINT") and r.get("LABEL") not in ("boot", "root"):
            dev = ("LABEL=" + r["LABEL"]) if re.fullmatch(r"[A-Za-z0-9._:-]+", r.get("LABEL") or "") else "UUID=" + r["UUID"]
            r["mounts"] = "%s /mnt/%s" % (dev, (r.get("LABEL") or "data").lower() if dev.startswith("LABEL=") else "data")
            if dev not in set_up:
                free.append(r)
    if args.porcelain:
        print(json.dumps({"host": host.name, "disks": rows, "mounts": [list(m) for m in config.mounts_of(host.cfg)]}, indent=2))
        return
    print("%-12s %8s  %-5s %-12s %-12s %s" % ("NAME", "SIZE", "TYPE", "FILESYSTEM", "LABEL", "MOUNTED AT"))
    for r in rows:
        print("%-12s %8s  %-5s %-12s %-12s %s" % (r.get("NAME", ""), r.get("SIZE", ""), r.get("TYPE", ""), r.get("FSTYPE", "") or "-",
                                                 r.get("LABEL", "") or "-", r.get("MOUNTPOINT", "") or "-"))
    for dev, at in set_up.items():
        util.say("in the fleet file: %s at %s" % (dev, at))
    for r in free:
        util.say("%s (%s, %s) holds a filesystem and is not mounted. To use it as a data disk, in %s's settings (Data disks), "
                 "or in the fleet file:\n    mounts: [\"%s\"]" % (r["NAME"], r.get("SIZE", "?"), r["FSTYPE"], host.name, r["mounts"]))


def _federation_side(args, path):
    """(fleet, its name, what a partner needs to know of it) for one fleet file."""
    a = argparse.Namespace(**dict(vars(args), fleet=path))
    fleet, roles, ms = load(a)
    masters = [h for h in fleet.hosts.values() if h.role in roles and roles[h.role].node_type == "master" and not h.standby_of]
    if len(masters) != 1:
        raise KiwiError("%s: federation needs exactly one master, found %d" % (path, len(masters)))
    h = masters[0]
    rolesmod.resolve_settings(roles[h.role], h, ms)
    vs = h.module_settings.get("vpn-server") or {}
    if not vs.get("wg_host") or not vs.get("federation_secret"):
        raise KiwiError("%s: its master %s has no wg_host or no federation key" % (path, h.name))
    groups = {str(n): str(g["subnet"]) for n, g in (vs.get("groups") or {}).items() if isinstance(g, dict) and g.get("subnet")}
    return fleet, os.path.basename(fleet.dir.rstrip("/")) or h.name, {
        "endpoint": str(vs["wg_host"]), "public_key": fedmod.public_key(vs["federation_secret"]),
        "mesh": h.role_settings.get("mesh_subnet") or h.role_settings_raw.get("mesh_subnet") or "10.8.0.0/16",
        "domain": h.domain, "dns": vs.get("server_ip") or "", "groups": groups}


def cmd_federate(args):
    """Link two networks this computer holds both fleets of: each master's link key, address,
    mesh and DNS into the other's federation.yaml. What each shares stays empty until written.
    Between two operators: --invite writes this network's card for the other one, --accept
    takes theirs (and writes this network's card for them, when there is none yet)."""
    if args.invite or args.accept:
        import yaml
        if args.other:
            raise KiwiError("--invite and --accept take one fleet file: the other network is the card")
        fleet, name, entry = _federation_side(args, args.fleet)
        if args.accept:
            them, theirs = fedmod.read_card(args.accept)
            if them == name:
                raise KiwiError("%s: the card is of a network called %s, as this one: one of them renames its folder" % (args.accept, them))
            if theirs["mesh"] == entry["mesh"]:
                raise KiwiError("the two meshes are the same (%s): every network has its own /16" % entry["mesh"])
            fedmod.write(fleet.dir, them, theirs)
            fedmod.partners(fleet.dir)
            util.say("%s is a partner of %s now; nothing is shared yet: share and from in %s, then apply the master"
                     % (them, name, os.path.join(fleet.dir, fedmod.FILE)))
        out = args.invite or os.path.join(os.path.dirname(os.path.abspath(args.accept)), "%s.card.yaml" % name)
        if args.invite or not os.path.isfile(out):
            util.write_text(out, "# %s's card for a partner network (kiwi-server federate FLEET --accept THIS). Nothing secret.\n"
                            % name + yaml.safe_dump(fedmod.card(name, entry), sort_keys=False, default_flow_style=None), 0o644)
            util.say("%s's card: %s. The other network's operator takes it with: kiwi-server federate THEIR-FLEET --accept %s"
                     % (name, out, os.path.basename(out)))
        return
    if not args.other:
        raise KiwiError("federate FLEET OTHER-FLEET, or FLEET --invite CARD / FLEET --accept CARD")
    sides = [_federation_side(args, path) for path in (args.fleet, args.other)]
    (fa, na, ea), (fb, nb, eb) = sides
    if na == nb:
        raise KiwiError("both fleets are called %s: rename one folder" % na)
    if not ea["mesh"] or not eb["mesh"] or ea["mesh"] == eb["mesh"]:
        raise KiwiError("the two meshes must be set and differ (%s, %s): every network has its own /16" % (ea["mesh"], eb["mesh"]))
    nb = fedmod.write(fa.dir, nb, eb)        # the names each side knows the other by
    na = fedmod.write(fb.dir, na, ea)
    if args.exit or args.no_exit:
        # the first network leaves to the internet through the second, over the link: the
        # first says so (exit), the second allows it (internet)
        on = bool(args.exit)
        fedmod.set_flag(fa.dir, nb, "exit", on)
        fedmod.set_flag(fb.dir, na, "internet", on)
        fedmod.partners(fa.dir)             # says so when two partners would be the exit, or the two leave through each other
        fedmod.partners(fb.dir)
        if on:
            util.say("%s leaves to the internet through %s, over their link. Apply %s's master first (it lets %s out), "
                     "then %s's: until both are done, %s's devices have no internet" % (na, nb, nb, na, na, na))
        else:
            util.say("%s leaves by its own way again. Apply %s's master, then %s's" % (na, na, nb))
        return
    util.say("%s and %s are linked; nothing is shared yet: share and from in each federation.yaml, then apply both masters"
             % (na, nb))


def cmd_unlock(args):
    """A machine waiting at boot for a ring it cannot reach by itself: forward the ring in."""
    fleet, roles, ms = load(args)
    hosts = fleet.select([args.host])
    if args.manual or not hosts[0].cfg["unlock"].get("rings"):     # no ring: its key opens it
        unlock.type_key(hosts[0], address=args.address, port=args.port, timeout=args.timeout)
        return
    servers = unlock.ring_servers(fleet, lambda x: rolesmod.resolve_settings(roles[x.role], x, ms))
    unlock.open_disk(hosts[0], servers, ring=args.ring, address=args.address, port=args.port, timeout=args.timeout)


def cmd_prune(args):
    """Old snapshots off the backup nodes. A machine can only add to its backups, so this
    is done from here, where the seed is."""
    if args.every:
        return backupmod.prune_timer(os.path.abspath(args.fleet), args.every)
    fleet, roles, ms = load(args)
    if fleet.errors:
        raise KiwiError(fleet.errors[0])
    by_hostname, servers = {}, []
    for h in fleet.hosts.values():
        try:
            rolesmod.resolve_settings(roles[h.role], h, ms)
        except KiwiError as e:
            util.warn("%s is left out: %s" % (h.name, e))
            continue
        by_hostname[h.hostname] = h
    servers = [h for h in fleet.select(args.hosts) if h.hostname in by_hostname and "backup-server" in h.modules]
    if not servers:
        raise KiwiError("none of these hosts runs the backup-server module")
    if args.ssh and len(servers) != 1:
        raise KiwiError("--ssh goes with exactly one host")
    flags = {"daily": args.keep_daily, "weekly": args.keep_weekly, "monthly": args.keep_monthly}
    failed = []
    for h in servers:
        cfg = h.module_settings["backup-server"]
        keep = {k: v if v is not None else int(cfg.get("keep_" + k, backupmod.KEEP[k]) or 0) for k, v in flags.items()}
        if not any(keep.values()):
            raise KiwiError("%s: keeping 0 days, weeks and months would keep nothing" % h.name)
        data = cfg.get("data_dir") or "%s/kn-backup-server/data" % (h.role_settings.get("docker_dir") or "/home/user/docker")
        tgt = remote.reach(h, args.ssh)
        for name in cfg.get("clients") or []:
            client = by_hostname.get(name)
            if client is None or "backup" not in client.modules:
                util.warn("%s: %s is not a machine of this fleet with the backup module, so its password is not here" % (h.name, name))
                continue
            b = client.module_settings["backup"]
            try:
                r = backupmod.prune_repository(tgt, h.role_settings.get("runtime") or "podman", "%s/%s" % (data, name),
                                               b["image"], b["repo_password"], keep, args.dry_run)
            except KiwiError as e:
                util.warn("%s: %s: %s" % (h.name, name, e))
                failed.append(name)
                continue
            freed = ", freed %s" % r["freed"] if r["freed"] else ""
            util.say("%s: %s: %d snapshots kept, %d %s%s" % (h.name, name, r["kept"], r["removed"],
                                                             "would go" if args.dry_run else "removed", freed))
    if not args.dry_run:
        backupmod.record_prune(fleet, [h.name for h in servers], failed)
    if failed:
        raise KiwiError("not pruned: %s" % ", ".join(failed))


def keyring_way(st, name):
    """A key of its own for this computer, kept in its keyring, as one more way to open the
    open store. The way's name; None when no keyring took a key."""
    ways, sid = st.ways(), st.store_id()
    held = store.keyring_get(sid)
    if held:
        try:
            mine = next((n for n, k in ways if k == store.public_of(held)), None)
        except KiwiError:
            mine = None
        if mine:
            return mine                     # it could open this store before
    secret, public = store.keygen()
    if not store.keyring_put(sid, secret):
        return None
    name = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")[:24] or "computer"
    base, n = name, 2
    while any(w == name for w, _k in ways):
        name, n = "%s-%d" % (base, n), n + 1
    st.add(name, public)
    return name


def restore_copy(args, name, data, into):
    """A fleet with a store, from its copy: the recovery key opens it, and this computer gets
    a key of its own for the store."""
    identity = backupmod.recovery_key(args.recovery_file, args.passphrase_file)
    fleet_name, files = backupmod.restore_copy(data, identity, into, getattr(args, "unsigned", False))
    fleet_path = os.path.join(into, fleet_name)
    util.say("restored %d files from %s into %s" % (len(files), name, into))
    st = store.Store(into)
    if os.path.isdir(st.runtime()):         # one place in memory per store: opening this one would take the other's
        util.say("this network's store is open on this computer already, from another folder, so this one stays locked: "
                 "kiwi-server secrets lock there, then kiwi-server secrets unlock %s" % fleet_path)
        return
    st.unlock(identity)
    way = keyring_way(st, socket.gethostname().split(".")[0])
    if way:
        util.say("the secret store is open, and this computer's keyring holds a key for it now (%s)" % way)
    else:
        util.warn("no keyring took a key: unlocking will ask for the recovery key")
    if any(f.startswith("outside/") for f in files):
        util.say("files that lived outside the fleet directory are under %s/outside — point the settings at them" % into)
    util.say("next: kiwi-server list %s — are these your machines? A copy is not signed" % fleet_path)
    util.say("then: kiwi-server status %s" % fleet_path)


def cmd_restore(args):
    if not args.archive and not args.ssh:
        raise KiwiError("give an archive file, or --from user@node to fetch one from a node")
    if args.archive:
        name, data = os.path.basename(args.archive), util.read_bytes(args.archive)
    else:
        if args.list:
            for n in backupmod.list_remote(args.ssh):
                print(n)
            return
        name, data = backupmod.fetch_remote(args.ssh, args.name)
        util.say("fetched %s from %s" % (name, args.ssh))
    into = os.path.abspath(args.into or ".")
    kind = backupmod.kind(data)
    if kind == "store":
        return restore_copy(args, name, data, into)
    if kind is None:
        raise KiwiError("%s is not a copy of a network" % name)
    if args.recovery_file:
        util.say("this copy is of the older kind: a passphrase opens it, the recovery key is not used")
    pw = backupmod.passphrase(None, args.passphrase_file)
    files = backupmod.restore(data, pw, into)
    util.say("restored %d files from %s into %s" % (len(files), name, into))
    if any(f.startswith("outside/") for f in files):
        util.say("files that lived outside the fleet directory are under %s/outside — point the settings at them" % into)
    util.say("next: kiwi-server validate %s" % os.path.join(into, "fleet.yaml"))


def cmd_render(args):
    _render_or_build(args, build=False)


def cmd_build(args):
    if args.starter:
        build_starter(args)
        return
    if args.output or args.stream or args.base or args.debug_shell:
        raise KiwiError("--output, --stream and --base are for the starter image (--starter); "
                        "a fleet's media go to -o/--output-dir DIR")
    if not args.fleet:
        raise KiwiError("build needs a fleet file: kiwi-server build <fleet> [host...], or the starter image: "
                        "kiwi-server build --starter")
    _render_or_build(args, build=True)


def build_starter(args):
    """The starter image (docs/design/starter.md): no fleet, no secret, the same for everybody."""
    if args.fleet or args.hosts:
        raise KiwiError("the starter image has no fleet in it: kiwi-server build --starter [--output FILE]")
    for flag in ("bare", "offline", "no_backup", "output_dir"):
        if getattr(args, flag, None):
            raise KiwiError("--%s is for a fleet's media, not for the starter image" % flag.replace("_", "-"))
    if args.base and not os.path.isfile(args.base):
        raise KiwiError("no such file: %s" % args.base)
    out = os.path.abspath(args.output or "kiwi-starter.iso")
    util.say("building the starter image -> %s" % out)
    startermod.build_iso(make_tc(args), out, cache_dir_for(args), stream=args.stream or "stable",
                         base=args.base, debug_shell=args.debug_shell)
    util.say("%s (%d MB): write it to a USB stick, start the machine from it" % (out, os.path.getsize(out) >> 20))


def cmd_toolchain(args):
    tc = make_tc(args)
    sub = args.action or "status"
    if sub == "build":
        tc.build_image(os.path.join(util.home_dir(), "container"))
        return
    st = tc.status()
    data = {"runtime": tc.runtime(), "image": tc.image, "image_present": tc.image_present(),
            "mode": tc.mode, "tools": {t: {"how": h, "why": w} for t, (h, w) in st.items()}}
    if args.porcelain:
        print(json.dumps(data, indent=2))
        return
    print("runtime: %s    image: %s (%s)" % (data["runtime"] or "none", tc.image,
                                             "present" if data["image_present"] else "missing"))
    for t, (h, w) in st.items():
        print("  %-17s %s" % (t, h or "MISSING — " + w))


def cmd_doctor(args):
    checks = []

    def add(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    add("python %d.%d" % sys.version_info[:2], sys.version_info >= (3, 9), "3.9 or newer needed")
    try:
        import yaml  # noqa: F401
        add("python3-pyyaml", True)
    except ImportError:
        add("python3-pyyaml", False, "install python3-pyyaml (rpm-ostree install / pip install --user pyyaml)")
    add("python3-jinja2 (renders the modules)", modmod.jinja2 is not None,
        "" if modmod.jinja2 else "pip install --user jinja2  or  rpm-ostree install python3-jinja2")
    add("openssl (fleet CA and host certificates)", bool(util.which("openssl")))
    add("ssh (apply, status, enroll and the fleet backup reach the machines over it)", bool(util.which("ssh")),
        "" if util.which("ssh") else "install openssh-clients (Fedora) / openssh-client (Debian)")
    try:
        util.sha512_crypt("probe", "kiwikiwikiwikiwi")
        add("password hashing", True)
    except KiwiError as e:
        add("password hashing", False, str(e))
    try:
        roles = rolesmod.discover()
        add("roles", True, ", ".join(roles))
    except KiwiError as e:
        add("roles", False, str(e))
    try:
        ms = modmod.discover()
        add("modules", True, ", ".join(ms.names()))
    except KiwiError as e:
        add("modules", False, str(e))
    tc = make_tc(args)
    rt = tc.runtime()
    add("container runtime", bool(rt), rt or "neither podman nor docker — native tools only")
    if rt:
        add("toolchain image %s" % tc.image, tc.image_present(), "" if tc.image_present() else "kiwi-server toolchain build")
    for t, (h, w) in tc.status().items():
        add(t, bool(h), h or w)
    compose = False
    if util.which("docker"):
        compose = subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0
    add("docker compose (optional, validates rendered stacks)", compose)
    add("shellcheck (optional, lints rendered scripts)", bool(util.which("shellcheck")))
    gui = False
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        gui = True
    except (ImportError, ValueError):
        pass
    add("GTK4 + libadwaita (for kiwi-server-gui)", gui, "" if gui else "headless is fine; the CLI does everything")
    optional = ("shellcheck", "GTK4", "container", "toolchain", "docker compose")
    ok = all(c["ok"] for c in checks if not c["name"].startswith(optional))
    if args.porcelain:
        print(json.dumps({"ok": ok, "checks": checks}, indent=2))
        return
    for c in checks:
        print("  %s %s%s" % ("ok " if c["ok"] else "!! ", c["name"], ("  — " + c["detail"]) if c["detail"] else ""))
    print(":: %s" % ("everything needed is here" if ok else "something is missing — see above"))
    if not ok:
        sys.exit(1)


def cmd_version(_args):
    print("kiwi-server %s" % VERSION)


USAGE = """kiwi-server — scripts, configs and unattended ISOs for Kiwi Network machines

  kiwi-server init [fleet.yaml]                 a new fleet: a few questions (--example: the commented example)
  kiwi-server add <fleet> master|node|bare [name]  a new host, named after a fruit dish (master) or a fruit
  kiwi-server names [fleet]                     free names for new machines, as JSON
  kiwi-server validate <fleet> [host...]        check it (exit 2 on errors)
  kiwi-server list <fleet>                      hosts and what has been generated
  kiwi-server show <fleet> <host>               the effective configuration of one host
  kiwi-server render <fleet> [host...]          role scripts, stacks, Butane/Ignition or preseed
  kiwi-server build <fleet> [host...]           render, then the unattended ISO per host: the base system (--offline: the role and its secrets too)
  kiwi-server build --starter [--output FILE]   the starter image: one ISO for any machine, no fleet in it; the network starts on its page
  kiwi-server script <fleet> <host>             print the script for any machine: the base system (--offline: the role and its secrets too)
  kiwi-server ca <fleet>                        the fleet CA (created on first use)
  kiwi-server openwrt <fleet> --wireguard FILE [--full] | --via NODE|IP
                                                a uci script that joins an OpenWrt router to the mesh
  kiwi-server apply <fleet> <host...>           re-render and run the role script on a running machine (SSH)
  kiwi-server take-in <fleet> <address> --code CODE --preset P [--name N]
                                                a machine the starter stick runs, waiting at home: taken in and set up
  kiwi-server serve <fleet> [--listen A:P]      the control node: web pages and API (--set-password first)
  kiwi-server accounts <fleet> invite|requests|accept|deny|create|link|list|delete …
                                                the network's accounts: invite codes, requests, accounts, password links
  kiwi-server signup serve|invite|requests|…   the accounts' sign-up page, in the accounts module's container
  kiwi-server qr <file> [--png|--svg]           a device's config as a QR code for the WireGuard app
  kiwi-server control push|pull <fleet>         the fleet to the control node, or back (encrypted)
  kiwi-server control join <fleet> <link>       this computer joins a fleet with an invite link from the control page
  kiwi-server control invite <fleet>            makes that link here, for one more computer
  kiwi-server control push|pull|join <fleet> --dir FOLDER     the same through a synced folder (Nextcloud, …);
                                                then control requests|accept|deny <fleet> [name]
  kiwi-server watch <fleet> [host...]           send the role to a RAM master that booted (--timer on: every minute)
  kiwi-server status <fleet> [host...]          what the machines report: role applied, stack status
  kiwi-server gone <fleet> [--dry-run]                machines that do not answer: what the fleet allows done about it
  kiwi-server remove <fleet> <host> [--yes]            a machine leaves for good: off the VPN, out of the fleet file
  kiwi-server connect <fleet> [--name N] [--remove]   this computer on the network: VPN connection, names, certificates
  kiwi-server enroll <fleet> <host|name> [--group G] [--split] [--qr]
                                                a WireGuard client on the master's wg-easy; a node's config goes to secrets/
  kiwi-server logins <fleet> [host...]          the services to sign in to: address, user name and password
  kiwi-server secrets show <fleet> <host> [setting]   print a host's password (no setting: their names)
  kiwi-server secrets init|unlock|lock|status <fleet>  secrets/ as one encrypted file, opened into memory
  kiwi-server mtu <fleet> <node|device|address> [--write]   measure a tunnel's real MTU; say what to set
  kiwi-server vps login <fleet>                 sign in to netcup's API, in a browser
  kiwi-server vps list|probe <fleet> [server]   netcup's servers; what one really is, its disk: among it
  kiwi-server vps install <fleet> <host>        wipe a netcup server and install the host's built ISO
  kiwi-server backup <fleet> [host...]          the fleet directory, encrypted, to its nodes (or --local DIR)
  kiwi-server restore --from user@node [--into DIR]   get it back on a fresh machine (or restore FILE)
  kiwi-server prune <fleet> [host...] [--dry-run]    old snapshots off the backup nodes
  kiwi-server plan <fleet> [host...]            what an apply would change and restart, before an update
  kiwi-server check <fleet>                      after an update: every name over HTTPS, DNS, the exit
  kiwi-server backups <fleet> <host> [module...]      a machine's snapshots; --now, --restore [--at TIME]
  kiwi-server recovery <fleet>                  if something breaks: could it come back, and what to do
  kiwi-server federate <fleet> <other fleet>         link two networks (then share with: access)
  kiwi-server access <fleet> [who] [what…]            who on the VPN may reach what: groups and linked networks
  kiwi-server onion <fleet> [service.machine]        hidden services: a site for anyone with Tor, or for named people
  kiwi-server disks <fleet> <host>                    the machine's disks, and the line that mounts a data disk
  kiwi-server sshca <fleet> [init|sign KEY|trust]     the fleet's SSH CA: host certificates, keys let in for a while
  kiwi-server unlock <fleet> <host>             forward a Tang ring into a machine waiting at boot
  kiwi-server roles [-v]                        what a machine can become, and which modules that is
  kiwi-server modules [-v]                      the kiwi-v2 modules and their settings
  kiwi-server targets                           coreos, ucore, debian, armbian, coreos-ram
  kiwi-server toolchain [status|build]          butane / coreos-installer / xorriso, native or container
  kiwi-server doctor                            is everything here?

options:  -o/--output-dir DIR   --cache DIR   --toolchain auto|native|container
          --porcelain (JSON)    --quiet       --verbose
"""


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-o", "--output-dir")
    common.add_argument("--cache")
    common.add_argument("--toolchain", choices=("auto", "native", "container"), default="auto")
    common.add_argument("--image")
    common.add_argument("--porcelain", action="store_true")
    common.add_argument("--quiet", action="store_true")
    common.add_argument("-v", "--verbose", action="store_true")

    p = argparse.ArgumentParser(prog="kiwi-server", add_help=False, usage=USAGE)
    p.add_argument("-h", "--help", action="store_true")
    p.add_argument("--version", action="store_true")
    sp = p.add_subparsers(dest="cmd")

    def sub(name, fn, **kw):
        # `kiwi-server <cmd> -h` shows that command's own line and options
        line = next((ln.strip() for ln in USAGE.splitlines() if ln.strip().startswith("kiwi-server %s " % name)
                     or ln.strip() == "kiwi-server %s" % name), "kiwi-server %s" % name)
        usage, _, desc = line.partition("   ")
        s = sp.add_parser(name, parents=[common], add_help=True, usage=usage.strip() + " [options]",
                          description=desc.strip() or None, **kw)
        s.set_defaults(fn=fn)
        return s

    s = sub("init", cmd_init); s.add_argument("path", nargs="?")
    s.add_argument("--domain", help="the commented example with this domain (default home; .internal, .corp and .mail are the other safe ones)")
    s.add_argument("--example", action="store_true", help="the commented example fleet instead of the questions")
    s.add_argument("--questions", action="store_true", help="the questions as JSON, with what this computer knows filled in (for the app)")
    s.add_argument("--answers", metavar="FILE", help="the answers as JSON (- for stdin) instead of asking")
    s.add_argument("--check", metavar="FILE", help="what is wrong with these answers (JSON, - for stdin), by question: for the app")
    s.add_argument("--provider", default=next(iter(providers.PROVIDERS)), choices=list(providers.PROVIDERS),
                   help="the hoster for the two options below (default %(default)s)")
    s.add_argument("--provider-login", metavar="FILE", help="sign in at the hoster through the browser, the token into FILE: for the app")
    s.add_argument("--provider-servers", metavar="FILE", help="the servers of the account whose token is in FILE, as JSON: for the app")
    s.add_argument("--api", metavar="URL", help=argparse.SUPPRESS)
    s.add_argument("--all", action="store_true", help="also ask what has an answer that works: names, disks, the system, the name ending")
    s.add_argument("--no-store", action="store_true", help="leave the keys as plain files (for a test)")
    s = sub("names", cmd_names); s.add_argument("fleet", nargs="?")
    s = sub("add", cmd_add); s.add_argument("fleet"); s.add_argument("role", choices=["master", "node", "bare"])
    s.add_argument("name", nargs="?", help="default: a free fruit (a node) or fruit dish (a master)")
    s.add_argument("--preset", choices=["gateway", "cloud", "minimal"], help="a node's module list (default minimal)")
    s.add_argument("--target", choices=["ucore", "coreos", "debian"], help="its system (default: the fleet's)")
    s.add_argument("--address", help="a master: its public address or name (vpn-server.wg_host)")
    s = sub("remove", cmd_remove); s.add_argument("fleet"); s.add_argument("host")
    s.add_argument("--yes", action="store_true", help="do not ask")
    s.add_argument("--master", help="which master, when the fleet has more than one")
    s.add_argument("--ssh", metavar="USER@ADDR", help="how to reach the master")
    s.add_argument("--api", metavar="URL", help=argparse.SUPPRESS)   # wg-easy reachable directly (tests)
    s = sub("validate", cmd_validate); s.add_argument("fleet"); s.add_argument("hosts", nargs="*")
    sub("list", cmd_list).add_argument("fleet")
    s = sub("next", cmd_next); s.add_argument("fleet")
    s.add_argument("host", nargs="?", help="this machine, also when it was set up before: it was installed anew")
    s.add_argument("--yes", action="store_true", help="do the step without asking first")
    s = sub("show", cmd_show); s.add_argument("fleet"); s.add_argument("host")
    s = sub("logins", cmd_logins); s.add_argument("fleet"); s.add_argument("hosts", nargs="*")
    for name, fn in (("render", cmd_render), ("build", cmd_build)):
        s = sub(name, fn); s.add_argument("fleet", nargs="?" if name == "build" else None); s.add_argument("hosts", nargs="*")
        s.add_argument("--bare", action="store_true",
                       help="the base system only: the role and its stack's secrets come later with apply (what build does by default)")
        if name == "build":
            s.add_argument("--offline", action="store_true",
                           help="the role and its secrets on the ISO too, for a machine that cannot be reached over SSH")
            s.add_argument("--starter", action="store_true",
                           help="the starter image instead: no fleet file, no secret; installs itself and shows the first-run page")
            s.add_argument("--output", metavar="FILE", help="--starter: where the ISO goes (default ./kiwi-starter.iso)")
            s.add_argument("--stream", choices=("stable", "testing", "next"), help="--starter: the Fedora CoreOS stream (default stable)")
            s.add_argument("--base", metavar="ISO", help="--starter: this Fedora CoreOS live ISO instead of the stream's latest")
            s.add_argument("--debug-shell", action="store_true", help=argparse.SUPPRESS)   # drills: a root shell on ttyS1
        s.add_argument("--no-backup", action="store_true", help="skip the fleet backup to backup.hosts afterwards")
    s = sub("script", cmd_script); s.add_argument("fleet"); s.add_argument("host")
    s.add_argument("--offline", action="store_true", help="the whole role and its secrets, not only the base system")
    sub("ca", cmd_ca).add_argument("fleet")
    s = sub("secrets", cmd_secrets); s.add_argument("action", choices=["show", "init", "unlock", "lock", "status", "join", "add", "remove", "copy-key", "recovery", "autolock", "screen-watch", "password"]); s.add_argument("fleet")
    s.add_argument("host", nargs="?"); s.add_argument("setting", nargs="?", help="e.g. vpn-server.wg_password, or wg_password")
    s.add_argument("--for", default=None, metavar="TIME", help="unlock: lock again after this long unused (default: autolock's idle, 30m; 0: stay open)")
    s.add_argument("--idle", metavar="TIME", help="autolock: lock after this long unused, e.g. 15m, 1h; 0: never")
    s.add_argument("--screen", metavar="on|off", help="autolock: lock with the screen")
    s.add_argument("--password-stdin", action="store_true", help="unlock, password: the password(s) on stdin, one per line")
    s.add_argument("--off", action="store_true", help="password: the keyring alone opens it again")
    s.add_argument("--identity", metavar="FILE", help="unlock: a file with the age secret key, instead of the keyring")
    s.add_argument("--out", metavar="FILE", help="recovery: the new key into this new file (0600), not on the screen")
    s.add_argument("--make", action="store_true", help=argparse.SUPPRESS)        # recovery, for the app: a key, nothing else
    s.add_argument("--key-stdin", action="store_true", help=argparse.SUPPRESS)   # recovery, for the app: that key, kept
    s = sub("mtu", cmd_mtu); s.add_argument("fleet"); s.add_argument("target", help="a node, a device in secrets/devices/, or a mesh address")
    s.add_argument("--master", help="which master, when the fleet has more than one")
    s.add_argument("--ssh", metavar="USER@ADDR", help="how to reach the machine that measures: the node, or for a device the master")
    s.add_argument("--write", action="store_true", help="a node's result: write wg_mtu into the fleet file")
    s = sub("vps", cmd_vps); s.add_argument("action", choices=["login", "list", "probe", "install", "restart"]); s.add_argument("fleet")
    s.add_argument("target", nargs="?", help="a fleet host, or a server's id, address, name or nickname")
    s.add_argument("--server", help="the netcup server, when the host does not say (vps.server in the fleet)")
    s.add_argument("--yes", action="store_true", help="install: wipe the server's disk without asking")
    s.add_argument("--api", metavar="URL", help=argparse.SUPPRESS)   # a fake netcup (tests)
    s = sub("gone", cmd_recover, aliases=["recover"]); s.add_argument("fleet")      # recover: its name in 2.93.0
    s.add_argument("--status", metavar="FILE", help="what `status --porcelain` said, instead of asking the machines again")
    s.add_argument("--dry-run", action="store_true", help="say what would be done, do nothing, remember nothing")
    s.add_argument("--api", metavar="URL", help=argparse.SUPPRESS)   # a fake provider (tests)
    s = sub("notify", cmd_notify); s.add_argument("fleet")
    s.add_argument("--test", action="store_true", help="send one test message on every channel that is set")
    s.add_argument("--status", metavar="FILE", help="what `status --porcelain` said, instead of asking the machines again")
    s.add_argument("--events", metavar="FILE", help="what the control node did by itself in this look (its recover.json)")
    s.add_argument("--state", metavar="FILE", help="where what is wrong is kept (default: .kiwi-health.json beside the fleet file)")
    s.add_argument("--no-send", action="store_true", help="keep the state, send nothing")
    s = sub("openwrt", cmd_openwrt); s.add_argument("fleet")
    s.add_argument("--wireguard", metavar="FILE", help="the client config the master issued for the router")
    s.add_argument("--full", action="store_true", help="route everything through the mesh, not only the mesh subnet")
    s.add_argument("--via", metavar="NODE|IP", help="no tunnel: a static route to a gateway node on the LAN")
    s.add_argument("--name", default="kiwi", help="the interface and zone name on the router (default kiwi)")
    s = sub("check", cmd_check); s.add_argument("fleet"); s.add_argument("hosts", nargs="*")
    s.add_argument("--ssh", metavar="USER@ADDR", help="how to reach the master")
    s = sub("plan", cmd_plan); s.add_argument("fleet"); s.add_argument("hosts", nargs="*")
    s.add_argument("--ssh", metavar="USER@ADDR", help="how to reach the one host given, instead of admin@hostname")
    s = sub("apply", cmd_apply); s.add_argument("fleet"); s.add_argument("hosts", nargs="*")
    s.add_argument("--ssh", metavar="USER@ADDR", help="how to reach the one host given, instead of admin@hostname")
    s.add_argument("--no-run", action="store_true", help="only put the script in place")
    s.add_argument("--new-host-key", action="store_true",
                   help="with --ssh: the machine was installed anew, forget the SSH host key it showed before")
    s.add_argument("--update", action="store_true", help="afterwards, restart the containers a newer image came for (kiwi-stack update)")
    s = sub("qr", cmd_qr); s.add_argument("file")
    s.add_argument("--png", action="store_true", help="a PNG image on standard output")
    s.add_argument("--svg", action="store_true", help="an SVG image on standard output")
    s = sub("control", cmd_control)
    s.add_argument("action", choices=["push", "pull", "join", "invite", "requests", "accept", "deny", "ring", "activity"]); s.add_argument("fleet")
    s.add_argument("link", nargs="?", help="join: the invite link from the control page; accept, deny: the computer's name")
    s.add_argument("--dir", metavar="FOLDER", help="a synced folder (Nextcloud, …) instead of a control node: push, pull, join")
    s.add_argument("--name", help="join: what this computer is called there (default: its hostname)")
    s.add_argument("--address", help="join: where to connect when the control node's name does not resolve here")
    s.add_argument("--to", metavar="HOST", help="which control node, when several hosts have the module")
    s.add_argument("--force", action="store_true", help="overwrite a side that changed since the last sync")
    s.add_argument("--identity", metavar="FILE",
                   help="a file with this computer's age secret key, instead of the keyring (join writes it)")
    s.add_argument("--ssh", metavar="USER@ADDR", help="how to reach the control node")
    s = sub("accounts", cmd_accounts); s.add_argument("fleet"); s.add_argument("what"); s.add_argument("arg", nargs="*")
    s.add_argument("--to", metavar="HOST", help="the machine with the accounts module, when there are several")
    s.add_argument("--jabber", action="store_true", help="Jabber's sign-up page instead: its invite codes and requests")
    s.add_argument("--ssh", metavar="USER@ADDR", help="how to reach it")
    s.add_argument("--uses", type=int, default=1, help="invite: how many accounts the code makes")
    s.add_argument("--days", type=int, default=14, help="invite: how long the code is good")
    s.add_argument("--note", default="", help="invite: for whom")
    s = sub("signup", cmd_signup); s.add_argument("what"); s.add_argument("arg", nargs="?")
    s.add_argument("--state", default="/state", metavar="DIR", help="where the invites and requests are kept")
    s.add_argument("--kanidm", default="", metavar="URL", help="Kanidm's address on the stack network")
    s.add_argument("--kanidm-cert", default="", metavar="FILE",
                   help="the certificate Kanidm shows (its chain file): the only one this trusts there")
    s.add_argument("--token-file", default="", metavar="FILE", help="the sign-up's token for Kanidm")
    s.add_argument("--id-host", default="", metavar="NAME", help="the accounts' public name (the links point there)")
    s.add_argument("--brand", default="", help="the network's name on the page")
    s.add_argument("--terms-file", default="", metavar="FILE", help="the rules shown on the page, a paragraph a line")
    s.add_argument("--listen", default="0.0.0.0:8080", metavar="ADDR:PORT")
    s.add_argument("--ejabberd", default="", metavar="JSON",
                   help='Jabber addresses instead: {"<page name>": {"api": "http://…:5281/api", "host": "<Jabber domain>", "onion": "…"}}')
    s.add_argument("--ways", default="", help="invites,requests,captcha: the ways to an account the page offers")
    s.add_argument("--id-onion", default="", help="the accounts' hidden service: links stay on Tor for who came over Tor")
    s.add_argument("--uses", type=int, default=1, help="invite: how many accounts the code makes")
    s.add_argument("--days", type=int, default=14, help="invite: how long the code is good")
    s.add_argument("--note", default="", help="invite: for whom (shown on the control page)")
    s = sub("serve", cmd_serve); s.add_argument("fleet")
    s.add_argument("--listen", default="127.0.0.1:8740", metavar="ADDR:PORT")
    s.add_argument("--status-minutes", type=int, default=10, metavar="N", help="how often it looks at every host")
    s.add_argument("--set-password", action="store_true",
                   help="set the password that opens the store on the control node (from the terminal or stdin)")
    s.add_argument("--wait", action="store_true", help="wait for the fleet and its password instead of ending (the control module)")
    s.add_argument("--https", metavar="ADDR:PORT",
                   help="the pages over HTTPS there, with a certificate of their own; --listen then only sends the browser on (the starter)")
    s = sub("adopt", cmd_adopt); s.add_argument("fleet"); s.add_argument("host")
    s.add_argument("--as", required=True, metavar="USER@ADDR",
                   help="the board's own login (root or a sudo user, a password works), e.g. odroid@192.168.2.40")
    s.add_argument("--new-host-key", action="store_true",
                   help="the board was flashed again: forget the host key the fleet knows for that address")
    s.add_argument("--key", metavar="FILE", help="target coreos (a machine the starter stick installed): the key that login takes")
    s = sub("take-in", cmd_take_in); s.add_argument("fleet"); s.add_argument("address", help="the address the waiting machine shows")
    s.add_argument("--code", required=True, help="the setup code on its screen")
    from . import setup as _setupmod
    s.add_argument("--preset", required=True, choices=sorted(_setupmod.PRESETS), help="what it does: %s" %
                   "; ".join("%s: %s" % kv for kv in sorted(_setupmod.PRESETS.items())))
    s.add_argument("--name", help="its name in the network (made up when left out)")
    s = sub("watch", cmd_watch); s.add_argument("fleet"); s.add_argument("hosts", nargs="*")
    s.add_argument("--ssh", metavar="USER@ADDR", help="how to reach the one host given, instead of admin@hostname")
    s.add_argument("--once", action="store_true", help="one look, then exit (what the timer runs)")
    s.add_argument("--interval", type=int, default=60, metavar="SECONDS", help="between looks (default %(default)s)")
    s.add_argument("--timer", choices=("on", "off"), help="a user timer on this computer that looks every minute")
    s = sub("status", cmd_status); s.add_argument("fleet"); s.add_argument("hosts", nargs="*")
    s.add_argument("--ssh", metavar="USER@ADDR")
    s = sub("connect", cmd_connect); s.add_argument("fleet")
    s.add_argument("--name", help="what this computer is called on the VPN (default: its own name)")
    s.add_argument("--group", help="a client group of the master")
    s.add_argument("--remove", action="store_true", help="take this computer off the network again")
    s.add_argument("--existing", action="store_true", help="the device of this name is this computer: use its place again")
    s.add_argument("--no-trust", action="store_true", help="leave the browsers' certificate stores alone")
    s.add_argument("--ssh", metavar="USER@ADDR", help="how to reach the master")
    s = sub("enroll", cmd_enroll); s.add_argument("fleet"); s.add_argument("name", help="a fleet host, or a device name")
    s.add_argument("--group", help="a client group of the master (its subnet decides the address)")
    s.add_argument("--address", help="the mesh address to give the client")
    s.add_argument("--split", action="store_true", help="only the mesh through the tunnel (devices that keep their own internet)")
    s.add_argument("--existing", action="store_true", help="fetch the config of a client that already exists")
    s.add_argument("--mtu", type=int, metavar="N",
                   help="the tunnel's packet size in this config (a device gets 1280, which fits mobile networks; a machine the master's wg_mtu)")
    s.add_argument("--remove", action="store_true", help="take a device off the VPN: its key stops working")
    s.add_argument("--master", help="which master, when the fleet has more than one")
    s.add_argument("--ssh", metavar="USER@ADDR", help="how to reach the master")
    s.add_argument("--api", metavar="URL", help=argparse.SUPPRESS)   # wg-easy reachable directly (tests)
    s.add_argument("--qr", action="store_true", help="print the config as a QR code for the WireGuard app")
    s = sub("backup", cmd_backup); s.add_argument("fleet"); s.add_argument("hosts", nargs="*")
    s.add_argument("--local", metavar="DIR", help="also (or only) write the archive here")
    s.add_argument("--ssh", metavar="USER@ADDR", help="how to reach the one host given, instead of admin@hostname")
    s.add_argument("--passphrase-file", metavar="FILE", help="not needed for a fleet with a secret store: its recovery key opens the copy")
    s = sub("backups", cmd_backups); s.add_argument("fleet"); s.add_argument("host"); s.add_argument("modules", nargs="*")
    s.add_argument("--now", action="store_true", help="back up now")
    s.add_argument("--new", action="store_true", help="a rebuilt machine that waits: start a new history instead of restoring")
    s.add_argument("--restore", action="store_true", help="bring these modules back from the backups (none named: all of them)")
    s.add_argument("--at", metavar="TIME", help="with --restore: the newest snapshot from then or before, e.g. '2026-11-10 19:00'")
    s.add_argument("--ssh", metavar="USER@ADDR")
    s = sub("recovery", recoverymod.cmd); s.add_argument("fleet")
    s.add_argument("--status", metavar="FILE", help="what status --porcelain said last: then it also says how old each machine's backup is")
    s = sub("sshca", cmd_sshca); s.add_argument("fleet")
    s.add_argument("action", nargs="?", choices=("init", "sign", "trust"))
    s.add_argument("key", nargs="?", help="sign: the public key to let in")
    s.add_argument("--user", help="sign: the user the key logs in as (default: admin.user)")
    s.add_argument("--days", type=int, default=sshca.USER_DAYS, help="sign: how long it lets the key in (default %(default)s)")
    s.add_argument("--known-hosts", metavar="FILE", help="trust: instead of ~/.ssh/known_hosts")
    s = sub("rotate", cmd_rotate); s.add_argument("fleet")
    s.add_argument("action", nargs="?", choices=("ssh", "seed"), help="without: the list of what can be replaced. "
                   "ssh: the admin key and SSH CA. seed: the secrets inside the services that a restart can change")
    s.add_argument("hosts", nargs="*", help="only these machines (default: all that still wait)")
    s.add_argument("--waive", action="append", metavar="HOST", help="leave this machine out (off for good, or about to be installed anew)")
    s.add_argument("--why", help="a note for the record, e.g. laptop lost")
    s = sub("federate", cmd_federate); s.add_argument("fleet"); s.add_argument("other", nargs="?", help="the partner network's fleet file")
    s.add_argument("--invite", metavar="CARD", help="write this network's card for a partner run by someone else")
    s.add_argument("--accept", metavar="CARD", help="take a partner's card (and write this network's for them)")
    s.add_argument("--exit", action="store_true", help="the first network leaves to the internet through the second, over the link")
    s.add_argument("--no-exit", action="store_true", help="the first network leaves by its own way again")
    s = sub("service", cmd_service); s.add_argument("fleet"); s.add_argument("host")
    s.add_argument("name", help="the service as its container is called, e.g. kn-nginx")
    s.add_argument("action", choices=["restart", "stop", "start", "logs"])
    s.add_argument("--lines", type=int, default=200, help="logs: how many of the last lines")
    s.add_argument("--ssh", metavar="USER@ADDR")
    s = sub("disks", cmd_disks); s.add_argument("fleet"); s.add_argument("host")
    s.add_argument("--ssh", metavar="USER@ADDR")
    s = sub("onion", cmd_onion); s.add_argument("fleet"); s.add_argument("what", nargs="?", help="<service>.<machine>, e.g. git.papaya")
    s.add_argument("--client", metavar="NAME", help="only named people get in: add this name (with --remove: take it away)")
    s.add_argument("--show", metavar="NAME", help="what this person types into Tor Browser")
    s.add_argument("--remove", action="store_true", help="take the hidden service away, or with --client that name")
    s.add_argument("--new-address", action="store_true",
                   help="a new address for this hidden service; the old one stops answering with the next apply")
    s.add_argument("--anyway", action="store_true",
                   help="make it of a site that is not marked as usable over Tor (it may send visitors to its name on the VPN)")
    s = sub("access", cmd_access); s.add_argument("fleet"); s.add_argument("who", nargs="?", help="a group of the VPN or a linked network")
    s.add_argument("what", nargs="*", help="all, nodes, apps (every app, no admin pages), nothing, a machine, service.machine or machine:port")
    s.add_argument("--add", action="store_true", help="give these too, and keep what it has")
    s.add_argument("--remove", action="store_true", help="take these away, and keep the rest")
    s.add_argument("--new", action="store_true", help="make the group, with the next free range of addresses")
    s.add_argument("--peers", choices=["yes", "no"], help="may the group reach other devices on the VPN")
    s.add_argument("--internet", choices=["yes", "no"], help="may the group leave to the internet through the VPN")
    s.add_argument("--admin", choices=["yes", "no"], help="may the group open the admin pages: the control page, Portainer, Pi-hole and the like")
    s = sub("unlock", cmd_unlock); s.add_argument("fleet"); s.add_argument("host")
    s.add_argument("--ring", help="which ring, when unlock.via forwards more than one")
    s.add_argument("--manual", action="store_true", help="hand over the disk's key instead of forwarding a ring")
    s.add_argument("--address", metavar="ADDR", help="where the machine answers, instead of the fleet's addresses for it")
    s.add_argument("--port", type=int, metavar="N", help="the initramfs's SSH port, where something forwards it elsewhere")
    s.add_argument("--timeout", type=int, default=300, metavar="SECONDS", help="how long to wait for its initramfs (default %(default)s)")
    s = sub("prune", cmd_prune); s.add_argument("fleet"); s.add_argument("hosts", nargs="*")
    s.add_argument("--keep-daily", type=int, metavar="N", help="days with a snapshot to keep (default: the server's keep_daily)")
    s.add_argument("--keep-weekly", type=int, metavar="N", help="weeks (default: keep_weekly)")
    s.add_argument("--keep-monthly", type=int, metavar="N", help="months (default: keep_monthly)")
    s.add_argument("--dry-run", action="store_true", help="only say what would go")
    s.add_argument("--ssh", metavar="USER@ADDR", help="how to reach the one host given, instead of admin@hostname")
    s.add_argument("--every", choices=("day", "week", "off"),
                   help="instead of pruning now: a timer on this computer that does it (a missed run comes at the next login), or none")
    s = sub("restore", cmd_restore); s.add_argument("archive", nargs="?")
    s.add_argument("--from", dest="ssh", metavar="USER@ADDR", help="fetch from this node (its LAN address works before any mesh)")
    s.add_argument("--name", help="which archive; default the newest")
    s.add_argument("--list", action="store_true", help="only list what the node keeps")
    s.add_argument("--into", metavar="DIR", help="where the fleet directory is recreated (default .)")
    s.add_argument("--recovery-file", metavar="FILE",
                   help="the recovery key of a fleet with a secret store (or KIWI_RECOVERY_KEY, or typed)")
    s.add_argument("--unsigned", action="store_true",
                   help="open a copy without a signature: one of yours made before 2.170.0 (anyone could make such a copy)")
    s.add_argument("--passphrase-file", metavar="FILE", help="the passphrase of a copy of the older kind")
    for name, fn in (("roles", cmd_roles), ("modules", cmd_modules)):
        sub(name, fn).add_argument("--fleet", dest="fleet_file", metavar="FLEET",
                                   help="with the modules of this fleet's module sources too")
    s = sub("sources", cmd_sources)
    s.add_argument("fleet", nargs="?", help="the fleet file (not for new)")
    s.add_argument("what", nargs="?", default="list", help="list (default), add, update, remove; or `sources new FOLDER`")
    s.add_argument("arg", nargs="*")
    s.add_argument("--ref", help="add, update: a branch, tag or commit instead of the repository's default branch")
    s.add_argument("--name", help="add: the source's name (default: the repository's); new: the example module's")
    s.add_argument("--path", help="add: a folder on this computer instead of a link (while you write a module)")
    sub("targets", cmd_targets)
    sub("toolchain", cmd_toolchain).add_argument("action", nargs="?", choices=("status", "build"))
    sub("doctor", cmd_doctor)
    sub("version", cmd_version)
    sub("help", lambda a: print(USAGE))
    return p


def save_store(args):
    """What this command changed in an open store, encrypted before it exits:
    a disk key that exists only in memory is gone with the next power cut."""
    fleet = getattr(args, "fleet", None)
    if not fleet:
        return
    try:
        store.Store.for_fleet(fleet).sync()
    except (KiwiError, OSError) as e:
        print("warning: changes in secrets/ were not saved to %s: %s" % (store.FILE, e), file=sys.stderr)


def main(argv=None):
    p = build_parser()
    args = p.parse_args(argv)
    if args.version:
        cmd_version(args)
        return 0
    if args.help or not args.cmd:
        print(USAGE)
        return 0
    util.QUIET = bool(getattr(args, "quiet", False))
    try:
        args.fn(args)
        sys.stdout.flush()      # a closed pipe must surface here, not in the interpreter's exit flush
    except KiwiError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        # `kiwi-server roles -v | head`: the reader went away, which is fine
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    finally:
        save_store(args)
    return 0
