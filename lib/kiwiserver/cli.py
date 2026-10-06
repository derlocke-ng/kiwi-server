"""The command line. bin/kiwi-server execs this; kiwi-server-gui calls it
with --porcelain and parses JSON, so the CLI stays the single source of truth."""
import argparse
import json
import os
import shutil
import subprocess
import sys

from . import VERSION, backup as backupmod, certs, config, iso as isomod, modules as modmod, roles as rolesmod
from . import remote, router as routermod, toolchain as tcmod, util, wgeasy
from .config import TARGETS
from .targets import coreos as coreos_t, debian as debian_t
from .util import KiwiError

EXIT_INVALID = 2


def cache_dir_for(args):
    return (getattr(args, "cache", None) or os.environ.get("KIWI_SERVER_CACHE")
            or os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "kiwi-server"))


def out_dir_for(args, fleet):
    return os.path.abspath(getattr(args, "output_dir", None) or os.path.join(fleet.dir, "output"))


def make_tc(args):
    return tcmod.Toolchain(mode=getattr(args, "toolchain", "auto") or "auto",
                           image=getattr(args, "image", None), verbose=bool(getattr(args, "verbose", False)))


def mask(value):
    return "********" if value else value


class Outputs:
    """Where a host's files go and which of them exist."""

    def __init__(self, out_dir, host):
        self.dir = os.path.join(out_dir, host.name)
        n = host.name
        self.script = os.path.join(self.dir, n + ".role.sh")
        self.bu = os.path.join(self.dir, n + ".bu")
        self.ign = os.path.join(self.dir, n + ".ign")
        self.preseed = os.path.join(self.dir, n + ".preseed.cfg")
        self.files = os.path.join(self.dir, n + ".kiwi-server")
        self.stack = os.path.join(self.dir, n + ".stack")
        self.iso = os.path.join(self.dir, n + ".iso")
        self.readme = os.path.join(self.dir, "README.txt")
        self.work = os.path.join(self.dir, ".work")
        self.host = host

    def status(self):
        debian = self.host.target == "debian"
        return {
            "script": os.path.isfile(self.script),
            "config": os.path.isfile(self.preseed if debian else self.bu),
            "ignition": (not debian) and os.path.isfile(self.ign),
            "stack": os.path.isfile(os.path.join(self.stack, "docker-compose.yml")),
            "iso": os.path.isfile(self.iso),
            "iso_size": os.path.getsize(self.iso) if os.path.isfile(self.iso) else 0,
        }


def host_readme(host, o, bundle=None):
    disk = host.cfg.get("disk") or "<disk: not set>"
    L = ["%s — %s" % (host.name, host.hostname),
         "target: %s    role: %s    install disk: %s" % (host.target, host.role, disk)]
    if host.modules:
        L.append("modules: %s" % ", ".join(host.modules))
    L += ["",
          "%s.role.sh" % host.name,
          "    The first-boot script: runs once as root on the installed machine",
          "    (kiwi-role.service). Also usable on an existing %s system:" % ("Debian" if host.target == "debian" else "CoreOS/uCore"),
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


def settings_errors(roles, ms, host):
    """Resolve the host's role and module settings; the errors, as strings."""
    if host.role not in roles or host.target not in TARGETS:
        return []
    role = roles[host.role]
    try:
        rolesmod.resolve_settings(role, host, ms)
    except KiwiError as e:
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
    def __init__(self, fleet, roles, out_dir, tc, modset=None):
        self.fleet, self.roles, self.out_dir, self.tc = fleet, roles, out_dir, tc
        self.ms = modset or modmod.discover()
        self._scanned = False
        self._records = []
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
        recs, needs_ca, master_ip = [], False, ""
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
                names = [h.hostname] + modmod.Renderer(self.ms).service_names(spec)
                for n in names:
                    if "*" not in n and (spec.vpn_ip, n) not in recs:
                        recs.append((spec.vpn_ip, n))
            except KiwiError as e:
                util.warn("dns records: skipping %s: %s" % (h.name, e))
        self._records, self._needs_ca, self._master_ip = recs, needs_ca, master_ip

    def dns_records(self):
        self.scan()
        return self._records

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
        spec = rolesmod.stack_spec(host, role, records, master_ip=self._master_ip)
        b = modmod.Renderer(self.ms).render(spec)
        b.no_resolved_stub = any(bool(self.ms.get(m).host.get("disable_resolved_stub")) for m in b.modules)
        return b

    def script(self, host):
        role = self.prepare(host)
        b = self.bundle(host, role)
        return rolesmod.render_script(host, role, VERSION, bundle=b, ca_cert=self.ca_cert(host)), b

    def write_stack(self, o, bundle):
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
        o = Outputs(self.out_dir, host)
        os.makedirs(o.dir, exist_ok=True)
        script, bundle = self.script(host)
        util.write_text(o.script, script, 0o600)
        produced = [o.script]
        if bundle is not None:
            self.write_stack(o, bundle)
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
        else:
            util.write_text(o.bu, coreos_t.render(host, script), 0o600)
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
        if not host.cfg.get("disk"):
            raise KiwiError("%s: disk: is required to build an ISO (it is wiped on boot)" % host.name)
        if host.target == "debian":
            base = isomod.debian_base_iso(self.tc, host.cfg["debian"], cache_dir)
            isomod.debian_iso(self.tc, host, o.preseed, o.files, o.iso, base, o.work)
        else:
            if not os.path.isfile(o.ign):
                raise KiwiError("%s: no Ignition config — butane is needed (see kiwi-server doctor)" % host.name)
            isomod.coreos_iso(self.tc, host, o.ign, o.iso, cache_dir)
        return o


# ---- commands -----------------------------------------------------------------------

def load(args, need_roles=True):
    fleet = config.Fleet.load(args.fleet)
    roles = rolesmod.discover() if need_roles else None
    ms = modmod.discover() if need_roles else None
    return fleet, roles, ms


def check(fleet, roles, ms, hosts, porcelain=False, strict=True):
    errs = list(fleet.errors)
    for h in hosts:
        errs += h.validate(roles)
        errs += settings_errors(roles, ms, h)
    if errs and strict:
        if porcelain:
            print(json.dumps({"ok": False, "errors": errs}, indent=2))
        else:
            for e in errs:
                print("error: %s" % e, file=sys.stderr)
        sys.exit(EXIT_INVALID)
    return errs


def cmd_init(args):
    dest = args.path or "fleet.yaml"
    if os.path.exists(dest):
        raise KiwiError("%s exists — not overwriting it" % dest)
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


def cmd_validate(args):
    fleet, roles, ms = load(args)
    hosts = fleet.select(args.hosts)
    errs = check(fleet, roles, ms, hosts, args.porcelain, strict=False)
    warns = list(fleet.warnings) + ["%s: %s" % (h.name, w) for h in hosts for w in h.warnings]
    if args.porcelain:
        print(json.dumps({"ok": not errs, "errors": errs, "warnings": warns}, indent=2))
    else:
        for e in errs:
            print("error: %s" % e, file=sys.stderr)
        for w in warns:
            util.warn(w)
        if not errs:
            util.say("%s: %d host(s) valid" % (os.path.basename(fleet.path), len(hosts)))
    if errs:
        sys.exit(EXIT_INVALID)


def cmd_list(args):
    fleet, roles, ms = load(args)
    out_dir = out_dir_for(args, fleet)
    rows = []
    for h in fleet.hosts.values():
        errs = h.validate(roles)
        if not errs:
            errs = settings_errors(roles, ms, h)
        o = Outputs(out_dir, h)
        rows.append({"name": h.name, "hostname": h.hostname, "target": h.target, "role": h.role,
                     "modules": list(h.modules), "disk": h.cfg.get("disk"),
                     "address": h.cfg["network"].get("address") if not h.cfg["network"].get("dhcp", True) else "dhcp",
                     "vpn_ip": (h.role_settings or {}).get("vpn_ip") or "",
                     "status": o.status(), "dir": o.dir, "iso": o.iso,
                     "errors": errs, "warnings": h.warnings})
    if args.porcelain:
        print(json.dumps({"fleet": fleet.path, "output_dir": out_dir, "hosts": rows}, indent=2))
        return
    fmt = "%-12s %-24s %-7s %-11s %-16s %s"
    print(fmt % ("NAME", "HOSTNAME", "TARGET", "ROLE", "ADDRESS", "OUTPUT"))
    for r in rows:
        s = r["status"]
        have = [k for k in ("script", "stack", "config", "ignition", "iso") if s.get(k)]
        state = "INVALID" if r["errors"] else (", ".join(have) if have else "-")
        print(fmt % (r["name"], r["hostname"], r["target"], r["role"], r["address"] or "", state))
        for e in r["errors"]:
            print("    error: %s" % e)


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
        print(json.dumps({"roles": [r.as_dict(ms) for r in roles.values()],
                          "modules": [m.as_dict() for m in ms.modules.values()]}, indent=2))
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
                for s in ms.get(m).settings:
                    req = " (required)" if s.required else ""
                    print("               %-24s %-7s default=%r%s" % (m + "." + s.key, s.type, s.default, req))


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
        print("%-8s %s" % (k, v))


def cmd_script(args):
    fleet, roles, ms = load(args)
    h = fleet.select([args.host])[0]
    check(fleet, roles, ms, [h])
    tc = make_tc(args)
    util.QUIET = True   # stdout is the script; the CA / certificate notes would end up inside it
    script, _bundle = Renderer(fleet, roles, out_dir_for(args, fleet), tc, ms).script(h)
    sys.stdout.write(script)


def fleet_facts(fleet, roles, ms):
    """(domain, master's mesh address, mesh subnet): what a router needs to know."""
    r = Renderer(fleet, roles, out_dir_for(argparse.Namespace(output_dir=None), fleet), None, ms)
    r.scan()
    mesh = "10.8.0.0/16"
    for h in fleet.hosts.values():
        if h.role in roles and roles[h.role].node_type == "master" and h.role_settings.get("mesh_subnet"):
            mesh = h.role_settings["mesh_subnet"]
            break
    return str(fleet.defaults.get("domain") or ""), r._master_ip, mesh


def cmd_openwrt(args):
    fleet, roles, ms = load(args)
    domain, master_ip, mesh = fleet_facts(fleet, roles, ms)
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
    sys.stdout.write(routermod.openwrt_script(domain, master_ip, mesh, wg=wg, via=via, full=args.full, name=args.name))


# ---- managing machines that run ----------------------------------------------------

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
    r = Renderer(fleet, roles, out_dir_for(args, fleet), make_tc(args), ms)
    failed = []
    for h in hosts:
        tgt = remote.target(h, args.ssh)
        try:
            script, _b = r.script(h)
            util.say("%s: sending the role script to %s" % (h.name, tgt))
            remote.put(tgt, script.encode(), "/var/lib/kiwi-server/role.sh", "0700")
            if args.no_run:
                util.say("%s: in place at /var/lib/kiwi-server/role.sh — run it with: sudo bash /var/lib/kiwi-server/role.sh --force" % h.name)
                continue
            util.say("%s: applying (the output below is the machine's)" % h.name)
            rc = remote.run_stream(tgt, "bash /var/lib/kiwi-server/role.sh --force")
            if rc != 0:
                raise KiwiError("the role script exited with %d — journalctl -u kiwi-role on the machine" % rc)
            util.say("%s: applied" % h.name)
        except KiwiError as e:
            failed.append(h.name)
            print("error: %s: %s" % (h.name, e), file=sys.stderr)
    if failed:
        raise KiwiError("failed: %s" % ", ".join(failed))


def cmd_status(args):
    """What a running machine says: when its role was applied, and what its
    stack is doing."""
    fleet, roles, ms = load(args)
    hosts = fleet.select(args.hosts)
    if args.ssh and len(hosts) != 1:
        raise KiwiError("--ssh goes with exactly one host")
    out = []
    for h in hosts:
        tgt = remote.target(h, args.ssh)
        script = ("echo \"applied: $(cat /var/lib/kiwi-server/role.done 2>/dev/null || echo never)\"; "
                  "echo \"uptime: $(uptime -p 2>/dev/null || uptime)\"; "
                  "if [ -x /usr/local/bin/kiwi-stack ]; then kiwi-stack status 2>&1 || true; else echo 'no stack'; fi")
        try:
            text = remote.run(tgt, script).decode(errors="replace")
            out.append({"host": h.name, "target": tgt, "ok": True, "output": text})
        except KiwiError as e:
            out.append({"host": h.name, "target": tgt, "ok": False, "output": str(e)})
    if args.porcelain:
        print(json.dumps({"hosts": out}, indent=2))
        return
    for o in out:
        print("== %s (%s)%s" % (o["host"], o["target"], "" if o["ok"] else " — unreachable"))
        print("   " + o["output"].rstrip().replace("\n", "\n   "))


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
    if not vs.get("wg_password"):
        raise KiwiError("%s: vpn-server.wg_password is needed to talk to wg-easy" % master.name)
    name = args.name
    host = fleet.hosts.get(name)
    client_name = host.hostname if host else name
    groups = vs.get("groups") or {}
    if args.group and args.group not in groups:
        raise KiwiError("no client group %r on %s (have: %s)" % (args.group, master.name, ", ".join(groups) or "none"))
    mesh = (master.role_settings or {}).get("mesh_subnet") or "10.8.0.0/16"

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

    if args.api:
        text = enroll(args.api)
    else:
        with remote.Tunnel(remote.target(master, args.ssh), 51821) as url:
            text = enroll(url)
    if args.split:
        text = wgeasy.split_tunnel(text, mesh)
    if host:
        dest = fleet.resolve_path("secrets/%s.conf" % host.hostname)
    else:
        dest = fleet.resolve_path("secrets/devices/%s.conf" % name)
    util.write_text(dest, text if text.endswith("\n") else text + "\n", 0o600)
    util.say("%s" % dest)
    if host:
        util.say("%s's vpn-client picks it up by itself (wireguard_config falls back to secrets/<hostname>.conf) — "
                 "kiwi-server render %s" % (host.name, os.path.basename(fleet.path)))
    if args.qr:
        if util.which("qrencode"):
            subprocess.run(["qrencode", "-t", "ansiutf8"], input=text.encode())
        else:
            util.warn("qrencode is not installed — the wg-easy page shows the QR code too")


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
    check(fleet, roles, ms, hosts)
    tc = make_tc(args)
    out_dir = out_dir_for(args, fleet)
    r = Renderer(fleet, roles, out_dir, tc, ms)
    failed = []
    for h in hosts:
        try:
            if build:
                util.say("building %s (%s, %s) -> %s" % (h.name, h.target, h.role, out_dir))
                o = r.build(h, cache_dir_for(args))
                util.say("%s: %s (%d MB)" % (h.name, o.iso, os.path.getsize(o.iso) >> 20))
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
    if build:
        util.say("done — these ISOs wipe their target disk on boot; label them")
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
    if not (cfg.get("passphrase_file") or os.environ.get("KIWI_BACKUP_PASS")):
        util.warn("backup.hosts is set but backup.passphrase_file is not — the fleet was not backed up "
                  "(kiwi-server backup asks for a passphrase)")
        return
    try:
        pw = backupmod.passphrase(fleet)
        data = backupmod.create(fleet, pw)
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
    pw = backupmod.passphrase(fleet, args.passphrase_file, confirm=not args.passphrase_file)
    data = backupmod.create(fleet, pw)
    name = backupmod.archive_name(fleet)
    n = len(backupmod.fleet_files(fleet))
    if args.local:
        util.say("%s (%d files, %d KB)" % (backupmod.store_local(data, args.local, name), n, len(data) >> 10))
    for h in hosts:
        kept = backupmod.store_remote(data, backupmod.ssh_target(h, args.ssh), name, int(cfg.get("keep") or 10))
        util.say("%s: %s stored in %s (%d kept)" % (h.name, name, backupmod.REMOTE_DIR, len(kept)))


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
    pw = backupmod.passphrase(None, args.passphrase_file)
    into = os.path.abspath(args.into or ".")
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

  kiwi-server init [fleet.yaml]                 write an example fleet file
  kiwi-server validate <fleet> [host...]        check it (exit 2 on errors)
  kiwi-server list <fleet>                      hosts and what has been generated
  kiwi-server show <fleet> <host>               the effective configuration of one host
  kiwi-server render <fleet> [host...]          role scripts, stacks, Butane/Ignition or preseed
  kiwi-server build <fleet> [host...]           render, then the unattended ISO per host
  kiwi-server script <fleet> <host>             print the role script (run it on any machine)
  kiwi-server ca <fleet>                        the fleet CA (created on first use)
  kiwi-server openwrt <fleet> --wireguard FILE [--full] | --via NODE|IP
                                                a uci script that joins an OpenWrt router to the mesh
  kiwi-server apply <fleet> <host...>           re-render and run the role script on a running machine (SSH)
  kiwi-server status <fleet> [host...]          what the machines report: role applied, stack status
  kiwi-server enroll <fleet> <host|name> [--group G] [--split] [--qr]
                                                a WireGuard client on the master's wg-easy; a node's config goes to secrets/
  kiwi-server backup <fleet> [host...]          the fleet directory, encrypted, to its nodes (or --local DIR)
  kiwi-server restore --from user@node [--into DIR]   get it back on a fresh machine (or restore FILE)
  kiwi-server roles [-v]                        what a machine can become, and which modules that is
  kiwi-server modules [-v]                      the kiwi-v2 modules and their settings
  kiwi-server targets                           coreos, ucore, debian
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
        s = sp.add_parser(name, parents=[common], add_help=False, **kw)
        s.set_defaults(fn=fn)
        return s

    s = sub("init", cmd_init); s.add_argument("path", nargs="?")
    s.add_argument("--domain", help="the fleet's domain (default home; .internal, .corp and .mail are the other safe ones)")
    s = sub("validate", cmd_validate); s.add_argument("fleet"); s.add_argument("hosts", nargs="*")
    sub("list", cmd_list).add_argument("fleet")
    s = sub("show", cmd_show); s.add_argument("fleet"); s.add_argument("host")
    s = sub("render", cmd_render); s.add_argument("fleet"); s.add_argument("hosts", nargs="*")
    s.add_argument("--no-backup", action="store_true", help="skip the fleet backup to backup.hosts afterwards")
    s = sub("build", cmd_build); s.add_argument("fleet"); s.add_argument("hosts", nargs="*")
    s.add_argument("--no-backup", action="store_true", help="skip the fleet backup to backup.hosts afterwards")
    s = sub("script", cmd_script); s.add_argument("fleet"); s.add_argument("host")
    sub("ca", cmd_ca).add_argument("fleet")
    s = sub("openwrt", cmd_openwrt); s.add_argument("fleet")
    s.add_argument("--wireguard", metavar="FILE", help="the client config the master issued for the router")
    s.add_argument("--full", action="store_true", help="route everything through the mesh, not only the mesh subnet")
    s.add_argument("--via", metavar="NODE|IP", help="no tunnel: a static route to a gateway node on the LAN")
    s.add_argument("--name", default="kiwi", help="the interface and zone name on the router (default kiwi)")
    s = sub("apply", cmd_apply); s.add_argument("fleet"); s.add_argument("hosts", nargs="*")
    s.add_argument("--ssh", metavar="USER@ADDR", help="how to reach the one host given, instead of admin@hostname")
    s.add_argument("--no-run", action="store_true", help="only put the script in place")
    s = sub("status", cmd_status); s.add_argument("fleet"); s.add_argument("hosts", nargs="*")
    s.add_argument("--ssh", metavar="USER@ADDR")
    s = sub("enroll", cmd_enroll); s.add_argument("fleet"); s.add_argument("name", help="a fleet host, or a device name")
    s.add_argument("--group", help="a client group of the master (its subnet decides the address)")
    s.add_argument("--address", help="the mesh address to give the client")
    s.add_argument("--split", action="store_true", help="only the mesh through the tunnel (devices that keep their own internet)")
    s.add_argument("--existing", action="store_true", help="fetch the config of a client that already exists")
    s.add_argument("--master", help="which master, when the fleet has more than one")
    s.add_argument("--ssh", metavar="USER@ADDR", help="how to reach the master")
    s.add_argument("--api", metavar="URL", help=argparse.SUPPRESS)   # wg-easy reachable directly (tests)
    s.add_argument("--qr", action="store_true", help="print the config as a QR code (qrencode)")
    s = sub("backup", cmd_backup); s.add_argument("fleet"); s.add_argument("hosts", nargs="*")
    s.add_argument("--local", metavar="DIR", help="also (or only) write the archive here")
    s.add_argument("--ssh", metavar="USER@ADDR", help="how to reach the one host given, instead of admin@hostname")
    s.add_argument("--passphrase-file", metavar="FILE")
    s = sub("restore", cmd_restore); s.add_argument("archive", nargs="?")
    s.add_argument("--from", dest="ssh", metavar="USER@ADDR", help="fetch from this node (its LAN address works before any mesh)")
    s.add_argument("--name", help="which archive; default the newest")
    s.add_argument("--list", action="store_true", help="only list what the node keeps")
    s.add_argument("--into", metavar="DIR", help="where the fleet directory is recreated (default .)")
    s.add_argument("--passphrase-file", metavar="FILE")
    sub("roles", cmd_roles)
    sub("modules", cmd_modules)
    sub("targets", cmd_targets)
    sub("toolchain", cmd_toolchain).add_argument("action", nargs="?", choices=("status", "build"))
    sub("doctor", cmd_doctor)
    sub("version", cmd_version)
    sub("help", lambda a: print(USAGE))
    return p


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
    except KiwiError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        # `kiwi-server roles -v | head`: the reader went away, which is fine
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    return 0
