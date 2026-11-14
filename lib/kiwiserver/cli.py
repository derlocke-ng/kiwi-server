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

from . import VERSION, address as addressmod, adopt as adoptmod, qr as qrmod, backup as backupmod, certs, config, federation as fedmod, iso as isomod, modules as modmod, plan as planmod, roles as rolesmod, sshca, sshport
from . import facts, form as formmod, luks, mtu, netcup, providers, remote, router as routermod, setup as setupmod, store, toolchain as tcmod, unlock, util, wgeasy
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
    try:
        modmod.Renderer(ms).render(rolesmod.stack_spec(host, role))
    except KiwiError as e:
        return [str(e)]
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
        self.ms = modset or modmod.discover()
        self._scanned = False
        self._records = []
        self._node_lan = []
        self._needs_ca = False
        self._master_ip = ""

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
        for h in self.fleet.hosts.values():
            try:
                if h.role not in self.roles or h.validate(self.roles):
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
                lan = static_lan(h)
                if lan and role.node_type != "master":      # a master's stack lives on the mesh, not its LAN
                    node_lan.append((spec.vpn_ip, lan.ip))
                    if "cache" in h.modules:
                        c = h.module_settings["cache"]
                        self._caches.append((lan, h.hostname, list(c.get("registries") or []), bool(c.get("apt"))))
                names = [h.hostname] + modmod.Renderer(self.ms).service_names(spec)
                for n in names:
                    if "*" not in n and (spec.vpn_ip, n) not in recs:
                        recs.append((spec.vpn_ip, n))
            except KiwiError as e:
                if not re.search(r"vpn-client\.wireguard_config: file not found", str(e)):   # not enrolled yet: no mesh name yet
                    util.warn("dns records: skipping %s: %s" % (h.name, e))
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
        spec = rolesmod.stack_spec(host, role, records, master_ip=self._master_ip, ca_cert=self.ca_cert(host),
                                   lan_addresses=self.lan_addresses(host, role))
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
            if unlock.wanted(host):      # the thumbprints go into the Ignition: Ignition binds the disk
                host.unlock_pins = unlock.pins(host, unlock.ring_servers(
                    self.fleet, lambda x: rolesmod.resolve_settings(self.roles[x.role], x, self.ms)))
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
    roles = rolesmod.discover() if need_roles else None
    ms = modmod.discover() if need_roles else None
    return fleet, roles, ms


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
    taken = set((yaml.safe_load(util.read_text(args.fleet)) or {}).get("hosts") or {})
    name = args.name or setupmod.free_name(args.role, taken)
    setupmod.add_host(args.fleet, name, args.role, args.preset or "", args.target or "")
    util.say("added %s as a %s to %s" % (name, args.role, args.fleet))
    fname = os.path.basename(args.fleet)
    if args.role == "master":
        util.say("next: set its wg_host (the public address) in the fleet file, then kiwi-server build %s %s" % (fname, name))
    elif args.role == "node":
        util.say("next: once its master runs, kiwi-server enroll %s %s; then kiwi-server build %s %s" % (fname, name, fname, name))
    else:
        util.say("next: kiwi-server build %s %s" % (fname, name))


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
        rows.append({"name": h.name, "hostname": h.hostname, "target": h.target, "role": h.role,
                     "phase": nxt[0], "next": nxt[1], "provider": (h.cfg.get("vps") or {}).get("provider") or "",
                     "on_done": (h.cfg.get("install") or {}).get("on_done", "poweroff"), "admin_user": h.admin["user"],
                     "applied": (fleet_state(fleet).get("applied") or {}).get(h.name),
                     "modules": list(h.modules), "disk": h.cfg.get("disk"),
                     "address": h.cfg["network"].get("address") if not h.cfg["network"].get("dhcp", True) else "dhcp",
                     "vpn_ip": mesh_ip,
                     "sites": [n for ip, n in records if mesh_ip and ip == mesh_ip and n != h.hostname],
                     "status": o.status(), "dir": o.dir, "iso": o.iso,
                     "updates": h.cfg.get("updates") or {},
                     "errors": errs, "warnings": h.warnings})
    if args.porcelain:
        groups = {}         # the master's client groups: where a new device may go
        for h in fleet.hosts.values():
            if h.role in roles and roles[h.role].node_type == "master" and not groups:
                groups = {g: (v or {}).get("subnet") for g, v in
                          (((h.module_settings or {}).get("vpn-server") or {}).get("groups") or {}).items()}
        print(json.dumps({"fleet": fleet.path, "output_dir": out_dir, "domain": domain, "mesh": mesh,
                          "master_ip": master_ip, "groups": groups, "hosts": rows}, indent=2))
        return
    fmt = "%-12s %-24s %-7s %-11s %-16s %s"
    print(fmt % ("NAME", "HOSTNAME", "TARGET", "ROLE", "ADDRESS", "OUTPUT"))
    for r in rows:
        s = r["status"]
        have = [k for k in ("script", "stack", "config", "ignition", "iso") if s.get(k)]
        state = "INVALID" if r["phase"] == "invalid" else (", ".join(have) if have else "-")
        print(fmt % (r["name"], r["hostname"], r["target"], r["role"], r["address"] or "", state))
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
    # phase == "install": the media exist. Is the machine there already? A machine nobody has
    # met is not looked for by its name (that resolves on the VPN only): its fixed address at
    # home, or a master's address on the Internet, is where the first contact goes.
    net = h.cfg.get("network") or {}
    addr = (str(net["address"]).split("/")[0] if not net.get("dhcp", True) and net.get("address")
            else (h.role_settings_raw.get("vpn-server") or {}).get("wg_host") if is_master(h) else None)
    tgt = "%s@%s" % (h.admin["user"], addr) if addr else None
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
        if not os.path.isfile(fleet.resolve_path(who.token_file())):
            if not go("Sign in to %s (it opens a page in your browser)?" % who.name):
                return
            run("vps", "login", path)
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


def next_step(fleet, roles, host, errors, out):
    """(phase, what to do next) for a machine, from what this computer knows: the fleet file
    and the output. `status` knows whether it runs; the app combines the two."""
    fname = os.path.basename(fleet.path)
    real = [e for e in errors if not not_enrolled(e)]
    if real:
        return "invalid", "fix the fleet file: %s" % real[0]
    if errors:
        masters = [h.name for h in fleet.hosts.values() if h.role in roles and roles[h.role].node_type == "master"]
        return "enroll", "once %s runs: kiwi-server enroll %s %s" % (" or ".join(masters) or "its master", fname, host.name)
    if (fleet_state(fleet).get("applied") or {}).get(host.name):
        return "applied", "kiwi-server status %s %s says how it is" % (fname, host.name)
    if host.role == "bare":
        return "bare", "give it a role in the fleet file, or install it bare: kiwi-server build %s %s" % (fname, host.name)
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


def cmd_roles(args):
    roles = rolesmod.discover()
    ms = modmod.discover()
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
    ms = modmod.discover()
    if args.porcelain:
        print(json.dumps({"modules": [m.as_dict() for m in ms.modules.values()]}, indent=2))
        return
    for m in ms.modules.values():
        print("%-14s %-12s %-12s %s" % (m.name, m.category, "/".join(m.node_types), m.description))
        if m.requires:
            print("               requires: %s" % ", ".join(m.requires))
        if args.verbose:
            for s in m.settings:
                req = " (required)" if s.required else ""
                print("               %-24s %-7s default=%r%s" % (s.key, s.type, s.default, req))


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
        elif args.porcelain:
            print(json.dumps(netcup.probe(client, server), indent=2))
        else:
            print_probe(netcup.probe(client, server))


def cmd_mtu(args):
    """How big a packet really gets through one tunnel, and what to set."""
    fleet, roles, ms = load(args)
    _domain, master_ip, _mesh, master_mtu = fleet_facts(fleet, roles, ms)
    masters = [h for h in fleet.hosts.values() if h.role in roles and roles[h.role].node_type == "master"]
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
        if not st.is_open():
            st.unlock(st.identity(args.identity))
        timed = store.schedule_lock(st, args.fleet, getattr(args, "for"))
        util.say("the secret store is open%s" % (" for %s" % getattr(args, "for") if timed else ""))
    elif args.action == "lock":
        st.lock()
        util.say("the secret store is locked")
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
            print(json.dumps({"store": True, "open": st.is_open(), "locks_at": st.locks_at(), "opens_with": names}))
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
                # the control node applies itself: the role may restart the container this runs in,
                # so it runs as a unit of its own on the machine and outlives the connection
                remote.run(tgt, "systemctl reset-failed kiwi-self-apply 2>/dev/null; systemd-run --collect "
                                "--unit=kiwi-self-apply bash -c %s" % shlex.quote(
                                    "bash /var/lib/kiwi-server/role.sh --force" + (" && " + UPDATE_IMAGES if update else "")))
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
                unlock.bind(h, tgt, servers)
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
    tgt = adoptmod.run(hosts[0], getattr(args, "as"), new_host_key=args.new_host_key)
    util.say("%s: adopted; now its role" % hosts[0].name)
    args.hosts, args.ssh, args.no_run = [hosts[0].name], tgt, False
    cmd_apply(args)


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
    if controlsync.remote_of(fleet_dir) and not args.ssh:      # joined: through the control node's API
        if args.action == "push":
            n = controlsync.push_api(fleet_dir, fleet_name, args.force, key)
            util.say("the control node holds this copy now (%d files)" % n)
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
    util.say("serving %s on http://%s" % (args.fleet, args.listen))
    sys.stdout.flush()      # into a container's log now: without a terminal the setup code above would wait in a buffer
    web.serve(c, args.listen)


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
        if h.role in roles and roles[h.role].node_type == "master":
            errs = h.validate(roles) or settings_errors(roles, ms, h)
            if errs:
                raise KiwiError("%s: the master is not valid: %s" % (h.name, "; ".join(errs)))
            return h
    raise KiwiError("no master in the fleet%s" % (" named %s" % name if name else ""))


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
            backupmod.keep_wireguard(master, remote.reach(master, args.ssh))
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
        backupmod.keep_wireguard(master, remote.reach(master, args.ssh))
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


def cmd_connect(args):
    """This computer becomes a device of the network: its own place on the VPN, a connection
    that carries only the network, and the network's certificates trusted in its browsers."""
    from . import connect as connectmod
    fleet, roles, ms = load(args)
    path = args.fleet
    domain = fleet.defaults.get("domain") or "home"
    name = args.name or re.sub(r"[^a-z0-9-]+", "-", socket.gethostname().split(".")[0].lower()).strip("-") or "computer"
    if name in fleet.hosts:
        raise KiwiError("%s is a machine of this network already: give this computer another name with --name" % name)
    if args.remove:
        if connectmod.have_networkmanager() and connectmod.down(domain):
            util.say("the connection %s is gone from this computer" % connectmod.connection_name(domain))
        rc = main(["enroll", path, name, "--remove"] + (["--ssh", args.ssh] if args.ssh else []))
        if rc:
            raise SystemExit(rc)
        return
    conf = fleet.resolve_path("secrets/devices/%s.conf" % name)
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
        master = next((h for h in fleet.hosts.values() if h.role in roles and roles[h.role].node_type == "master"), None)
        if master:
            for _ in range(10):
                try:
                    socket.getaddrinfo(master.hostname, 443)
                    util.say("%s answers: this computer reaches the network" % master.hostname)
                    break
                except OSError:
                    time.sleep(1)
            else:
                util.warn("%s does not resolve yet. Is the master running? nmcli connection up %s tries again"
                          % (master.hostname, con))
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
    if not hosts and not args.local:
        raise KiwiError("nowhere to back up to: name hosts, set backup.hosts in the fleet, or give --local DIR")
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
    failed = []
    for h in hosts:                         # a machine that is off does not keep the others from their copy
        try:
            kept = backupmod.store_remote(data, backupmod.ssh_target(h, args.ssh), name, int(cfg.get("keep") or 10))
            util.say("%s: %s stored in %s (%d kept)" % (h.name, name, backupmod.REMOTE_DIR, len(kept)))
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


def cmd_federate(args):
    """Link two networks this computer holds both fleets of: each master's link key, address,
    mesh and DNS into the other's federation.yaml. What each shares stays empty until written."""
    sides = []
    for path in (args.fleet, args.other):
        a = argparse.Namespace(**dict(vars(args), fleet=path))
        fleet, roles, ms = load(a)
        masters = [h for h in fleet.hosts.values() if h.role in roles and roles[h.role].node_type == "master"]
        if len(masters) != 1:
            raise KiwiError("%s: federation needs exactly one master, found %d" % (path, len(masters)))
        h = masters[0]
        rolesmod.resolve_settings(roles[h.role], h, ms)
        vs = h.module_settings.get("vpn-server") or {}
        if not vs.get("wg_host") or not vs.get("federation_secret"):
            raise KiwiError("%s: its master %s has no wg_host or no federation key" % (path, h.name))
        sides.append((fleet, os.path.basename(fleet.dir.rstrip("/")) or h.name, {
            "endpoint": str(vs["wg_host"]), "public_key": fedmod.public_key(vs["federation_secret"]),
            "mesh": h.role_settings.get("mesh_subnet") or h.role_settings_raw.get("mesh_subnet") or "10.8.0.0/16",
            "domain": h.domain, "dns": vs.get("server_ip") or ""}))
    (fa, na, ea), (fb, nb, eb) = sides
    if na == nb:
        raise KiwiError("both fleets are called %s: rename one folder" % na)
    if not ea["mesh"] or not eb["mesh"] or ea["mesh"] == eb["mesh"]:
        raise KiwiError("the two meshes must be set and differ (%s, %s): every network has its own /16" % (ea["mesh"], eb["mesh"]))
    fedmod.write(fa.dir, nb, eb)
    fedmod.write(fb.dir, na, ea)
    util.say("%s and %s are linked; nothing is shared yet: share and from in each federation.yaml, then apply both masters"
             % (na, nb))


def cmd_unlock(args):
    """A machine waiting at boot for a ring it cannot reach by itself: forward the ring in."""
    fleet, roles, ms = load(args)
    hosts = fleet.select([args.host])
    if args.manual:
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
    fleet_name, files = backupmod.restore_copy(data, identity, into)
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
    _render_or_build(args, build=True)


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
  kiwi-server script <fleet> <host>             print the script for any machine: the base system (--offline: the role and its secrets too)
  kiwi-server ca <fleet>                        the fleet CA (created on first use)
  kiwi-server openwrt <fleet> --wireguard FILE [--full] | --via NODE|IP
                                                a uci script that joins an OpenWrt router to the mesh
  kiwi-server apply <fleet> <host...>           re-render and run the role script on a running machine (SSH)
  kiwi-server serve <fleet> [--listen A:P]      the control node: web pages and API (--set-password first)
  kiwi-server qr <file> [--png|--svg]           a device's config as a QR code for the WireGuard app
  kiwi-server control push|pull <fleet>         the fleet to the control node, or back (encrypted)
  kiwi-server control join <fleet> <link>       this computer joins a fleet with an invite link from the control page
  kiwi-server control push|pull|join <fleet> --dir FOLDER     the same through a synced folder (Nextcloud, …);
                                                then control requests|accept|deny <fleet> [name]
  kiwi-server watch <fleet> [host...]           send the role to a RAM master that booted (--timer on: every minute)
  kiwi-server status <fleet> [host...]          what the machines report: role applied, stack status
  kiwi-server connect <fleet> [--name N] [--remove]   this computer on the network: VPN connection, names, certificates
  kiwi-server enroll <fleet> <host|name> [--group G] [--split] [--qr]
                                                a WireGuard client on the master's wg-easy; a node's config goes to secrets/
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
  kiwi-server federate <fleet> <other fleet>         link two networks (then share in federation.yaml)
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
    s = sub("validate", cmd_validate); s.add_argument("fleet"); s.add_argument("hosts", nargs="*")
    sub("list", cmd_list).add_argument("fleet")
    s = sub("next", cmd_next); s.add_argument("fleet")
    s.add_argument("host", nargs="?", help="this machine, also when it was set up before: it was installed anew")
    s.add_argument("--yes", action="store_true", help="do the step without asking first")
    s = sub("show", cmd_show); s.add_argument("fleet"); s.add_argument("host")
    for name, fn in (("render", cmd_render), ("build", cmd_build)):
        s = sub(name, fn); s.add_argument("fleet"); s.add_argument("hosts", nargs="*")
        s.add_argument("--bare", action="store_true",
                       help="the base system only: the role and its stack's secrets come later with apply (what build does by default)")
        if name == "build":
            s.add_argument("--offline", action="store_true",
                           help="the role and its secrets on the ISO too, for a machine that cannot be reached over SSH")
        s.add_argument("--no-backup", action="store_true", help="skip the fleet backup to backup.hosts afterwards")
    s = sub("script", cmd_script); s.add_argument("fleet"); s.add_argument("host")
    s.add_argument("--offline", action="store_true", help="the whole role and its secrets, not only the base system")
    sub("ca", cmd_ca).add_argument("fleet")
    s = sub("secrets", cmd_secrets); s.add_argument("action", choices=["show", "init", "unlock", "lock", "status", "join", "add", "remove"]); s.add_argument("fleet")
    s.add_argument("host", nargs="?"); s.add_argument("setting", nargs="?", help="e.g. vpn-server.wg_password, or wg_password")
    s.add_argument("--for", default="8h", metavar="TIME", help="unlock: lock again after this long (default 8h; 0: stay open)")
    s.add_argument("--identity", metavar="FILE", help="unlock: a file with the age secret key, instead of the keyring")
    s = sub("mtu", cmd_mtu); s.add_argument("fleet"); s.add_argument("target", help="a node, a device in secrets/devices/, or a mesh address")
    s.add_argument("--master", help="which master, when the fleet has more than one")
    s.add_argument("--ssh", metavar="USER@ADDR", help="how to reach the machine that measures: the node, or for a device the master")
    s.add_argument("--write", action="store_true", help="a node's result: write wg_mtu into the fleet file")
    s = sub("vps", cmd_vps); s.add_argument("action", choices=["login", "list", "probe", "install"]); s.add_argument("fleet")
    s.add_argument("target", nargs="?", help="a fleet host, or a server's id, address, name or nickname")
    s.add_argument("--server", help="the netcup server, when the host does not say (vps.server in the fleet)")
    s.add_argument("--yes", action="store_true", help="install: wipe the server's disk without asking")
    s.add_argument("--api", metavar="URL", help=argparse.SUPPRESS)   # a fake netcup (tests)
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
    s.add_argument("action", choices=["push", "pull", "join", "requests", "accept", "deny", "ring"]); s.add_argument("fleet")
    s.add_argument("link", nargs="?", help="join: the invite link from the control page; accept, deny: the computer's name")
    s.add_argument("--dir", metavar="FOLDER", help="a synced folder (Nextcloud, …) instead of a control node: push, pull, join")
    s.add_argument("--name", help="join: what this computer is called there (default: its hostname)")
    s.add_argument("--address", help="join: where to connect when the control node's name does not resolve here")
    s.add_argument("--to", metavar="HOST", help="which control node, when several hosts have the module")
    s.add_argument("--force", action="store_true", help="overwrite a side that changed since the last sync")
    s.add_argument("--identity", metavar="FILE",
                   help="a file with this computer's age secret key, instead of the keyring (join writes it)")
    s.add_argument("--ssh", metavar="USER@ADDR", help="how to reach the control node")
    s = sub("serve", cmd_serve); s.add_argument("fleet")
    s.add_argument("--listen", default="127.0.0.1:8740", metavar="ADDR:PORT")
    s.add_argument("--status-minutes", type=int, default=10, metavar="N", help="how often it looks at every host")
    s.add_argument("--set-password", action="store_true",
                   help="set the password that opens the store on the control node (from the terminal or stdin)")
    s.add_argument("--wait", action="store_true", help="wait for the fleet and its password instead of ending (the control module)")
    s = sub("adopt", cmd_adopt); s.add_argument("fleet"); s.add_argument("host")
    s.add_argument("--as", required=True, metavar="USER@ADDR",
                   help="the board's own login (root or a sudo user, a password works), e.g. odroid@192.168.2.40")
    s.add_argument("--new-host-key", action="store_true",
                   help="the board was flashed again: forget the host key the fleet knows for that address")
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
    s = sub("federate", cmd_federate); s.add_argument("fleet"); s.add_argument("other", help="the partner network's fleet file")
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
    s.add_argument("--passphrase-file", metavar="FILE", help="the passphrase of a copy of the older kind")
    sub("roles", cmd_roles)
    sub("modules", cmd_modules)
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
