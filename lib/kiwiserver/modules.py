"""Modules: the kiwi-v2 module system, rendered at build time.

A module is a directory under modules/ with a module.yaml and Jinja templates
(docker-compose.yml.j2 plus whatever config files the service needs). A
role picks a set of modules; this renders them for one host into a *stack
bundle* — a docker-compose.yml, the per-module config files, the directories
and host firewall ports they need, and the systemd units a module wants on
the host — which the role script embeds and unpacks on first boot. The
target machine needs docker and nothing else: no Python, no Jinja.

This is the kiwi-v2 renderer (scripts/kiwi-render/kiwi-render.py) ported
and finished: each module's compose fragment is parsed as YAML and merged
(instead of re-indented as text), the reverse proxy's server blocks are
aggregated from the modules' `nginx:` sections, and the modules' firewall
rules end up in the VPN client's post-rules.
"""
import copy
import ipaddress
import os
import re

import yaml

from . import quadlet, util
from .schema import load_settings
from .util import KiwiError

try:
    import jinja2
except ImportError:  # pragma: no cover - reported by doctor and at render time
    jinja2 = None

# the v1 address plan, kept so a migrated machine keeps its addresses
IP_PLAN = {
    "vpn-client": 2, "vpn-server": 3, "dns": 4, "reverse-proxy": 5, "cloud": 6,
    "vault": 7, "tor": 8, "downloader": 9, "gateway": 10, "sftp": 251, "portainer": 250,
}
DEFAULT_SUBNET = {"master": "172.64.0.0/24", "node": "172.128.0.0/24"}
NETWORK_NAME = {"master": "knet-master", "node": "knet-node"}
PREFIX = {"master": "km", "node": "kn"}
_VAR = re.compile(r"\$\{([A-Za-z0-9_]+)\}")


def modules_dir():
    return os.environ.get("KIWI_SERVER_MODULES") or os.path.join(util.home_dir(), "modules")


def _jinja():
    if jinja2 is None:
        raise KiwiError("python3 jinja2 is required to render modules — "
                        "pip install --user jinja2  (or rpm-ostree install python3-jinja2)")
    return jinja2.Environment(trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)


class Module:
    def __init__(self, path):
        self.dir = path
        self.name = os.path.basename(path)
        meta = yaml.safe_load(util.read_text(os.path.join(path, "module.yaml"))) or {}
        m = meta.get("module") or {}
        if m.get("name") and m["name"] != self.name:
            raise KiwiError("modules/%s/module.yaml: name %r does not match the directory" % (self.name, m["name"]))
        self.meta = meta
        self.title = str(m.get("title") or self.name)
        self.description = str(m.get("description") or "")
        self.category = str(m.get("category") or "")
        self.version = str(m.get("version") or "")
        types = m.get("node_types")
        if types is None:
            types = ["master"] if m.get("master_only") else ["master", "node"]
        self.node_types = [str(t) for t in types]
        deps = meta.get("dependencies") or {}
        self.requires = [str(d) for d in (deps.get("required") or [])]
        self.optional = [str(d) for d in (deps.get("optional") or [])]
        self.templates = {str(k): str(v) for k, v in (meta.get("templates") or {}).items()}
        self.outputs = meta.get("outputs") or {}
        env = meta.get("env") or {}
        self.env_optional = {str(k): v for k, v in (env.get("optional") or {}).items()}
        self.settings = load_settings("modules/%s" % self.name, meta.get("settings"))
        net = meta.get("network") or {}
        self.needs_vpn_dnat = bool(net.get("needs_vpn_dnat", False))
        self.open_ports = [str(p) for p in (net.get("open_ports") or [])]
        fw = meta.get("firewall") or {}
        self.firewall_rules = [str(r) for r in (fw.get("rules") or [])]
        self.firewall_scope = str(fw.get("scope") or "vpn-client")
        st = meta.get("storage") or {}
        self.bind_mounts = list(st.get("bind_mounts") or [])
        self.volumes = [str(v) for v in (st.get("volumes") or [])]
        self.nginx = meta.get("nginx") or None
        self.host = meta.get("host_integration") or {}
        self.dns = meta.get("dns") or {}
        self.checks = []   # [{"when": <jinja expression>, "error": text}]
        for c in meta.get("validate") or []:
            if not isinstance(c, dict) or not c.get("when") or not c.get("error"):
                raise KiwiError("modules/%s/module.yaml: every validate: entry needs when: and error:" % self.name)
            self.checks.append({"when": str(c["when"]), "error": str(c["error"])})

    def template(self, key):
        name = self.templates.get(key)
        if not name:
            return None
        p = os.path.join(self.dir, name)
        if not os.path.isfile(p):
            raise KiwiError("modules/%s: template %s is missing" % (self.name, name))
        return util.read_text(p)

    def as_dict(self):
        return {"name": self.name, "title": self.title, "description": self.description,
                "category": self.category, "node_types": self.node_types, "requires": self.requires,
                "optional": self.optional, "settings": [s.as_dict() for s in self.settings]}


class ModuleSet:
    def __init__(self, path=None):
        self.dir = path or modules_dir()
        if not os.path.isdir(self.dir):
            raise KiwiError("modules directory not found: %s" % self.dir)
        self.modules = {}
        for name in sorted(os.listdir(self.dir)):
            d = os.path.join(self.dir, name)
            if os.path.isfile(os.path.join(d, "module.yaml")):
                self.modules[name] = Module(d)
        if not self.modules:
            raise KiwiError("no modules found in %s" % self.dir)
        nets = os.path.join(self.dir, "networks.yml.j2")
        self.networks_template = util.read_text(nets) if os.path.isfile(nets) else None

    def names(self):
        return list(self.modules)

    def get(self, name):
        if name not in self.modules:
            raise KiwiError("unknown module %r (have: %s)" % (name, ", ".join(self.modules)))
        return self.modules[name]

    def resolve(self, names, node_type=None):
        """Dependencies first, requested order otherwise. A required module
        that was not asked for is added; a cycle or an unknown name is an error."""
        order, seen, visiting = [], set(), set()

        def visit(n, chain):
            if n in seen:
                return
            if n in visiting:
                raise KiwiError("module dependency cycle: %s -> %s" % (" -> ".join(chain), n))
            m = self.get(n)
            visiting.add(n)
            for d in m.requires:
                visit(d, chain + [n])
            visiting.discard(n)
            seen.add(n)
            order.append(n)
        for n in names:
            visit(str(n), [])
        if node_type:
            for n in order:
                if node_type not in self.get(n).node_types:
                    raise KiwiError("module %s is only for %s nodes, not %s" % (
                        n, "/".join(self.get(n).node_types), node_type))
        return order


class StackSpec:
    """Everything the renderer needs for one host."""

    def __init__(self, node_type, hostname, modules, docker_dir="/home/user/docker",
                 docker_subnet=None, mtu=1412, timezone="UTC", vpn_ip="", pub_iface="",
                 variables=None, module_config=None, files=None, dns_records=None,
                 service_user="user", master_ip="", mesh_subnet="10.8.0.0/16", domain="", runtime="podman"):
        if node_type not in ("master", "node"):
            raise KiwiError("node_type must be master or node")
        self.node_type = node_type
        self.hostname = hostname
        self.modules = list(modules)
        self.docker_dir = docker_dir.rstrip("/")
        self.docker_subnet = docker_subnet or DEFAULT_SUBNET[node_type]
        self.mtu = int(mtu)
        self.timezone = timezone
        self.vpn_ip = vpn_ip or ""
        self.pub_iface = pub_iface or ""
        self.variables = dict(variables or {})
        self.module_config = {k: dict(v or {}) for k, v in (module_config or {}).items()}
        self.files = dict(files or {})        # "module/key" -> bytes (embedded file settings)
        self.dns_records = list(dns_records or [])  # (ip, name) pairs for the dns module
        self.service_user = service_user
        self.master_ip = master_ip or ""        # the master's mesh address: the nodes' DNS upstream
        self.mesh_subnet = mesh_subnet or "10.8.0.0/16"
        self.domain = domain or ""              # the fleet's domain: forwarded to the master, never upstream
        if runtime not in ("podman", "docker"):
            raise KiwiError("runtime must be podman or docker, not %r" % runtime)
        self.runtime = runtime


class Bundle:
    def __init__(self):
        self.files = {}        # relpath under docker_dir -> (bytes|str, mode)
        self.dirs = []         # relpaths under docker_dir, or absolute paths
        self.ports = []        # "80/tcp"
        self.units = {}        # unit name -> text (host systemd units)
        self.sysctl = {}       # key -> value
        self.kernel_modules = []
        self.container_ips = {}
        self.services = []     # container names, for the summary
        self.urls = []         # what the operator can open afterwards
        self.compose = None
        self.vpn_dependents = []   # containers inside the VPN client's network namespace
        self.mesh_via = ""         # the container the host routes the mesh through
        self.mesh_subnet = ""
        self.hosts = []            # (ip, name): the fleet's names for /etc/hosts
        self.runtime = "podman"
        self.quadlets = {}         # podman: file name -> unit text (.container, .network, the target)

    def add_file(self, rel, content, mode=0o644):
        rel = rel.strip("/")
        if rel in self.files:
            raise KiwiError("two modules want to write %s" % rel)
        self.files[rel] = (content, mode)

    def add_dir(self, path):
        if path and path not in self.dirs:
            self.dirs.append(path)


def _subnet(cidr, what="docker_subnet"):
    try:
        net = ipaddress.IPv4Network(cidr, strict=False)
    except ValueError:
        raise KiwiError("%s: %r is not an IPv4 network like 172.128.0.0/24" % (what, cidr))
    if net.prefixlen > 29:
        raise KiwiError("%s: %s is too small for a stack (at most /29)" % (what, net))
    return net


def _subnet_parts(cidr):
    net = _subnet(cidr)
    base = str(net.network_address).rsplit(".", 1)[0]   # ${DOCKER_SUBNET2}, the v1 convention
    return str(net), str(net.network_address + 1), base


def _compose_safe(v):
    """docker compose interpolates $VAR in the compose file; a password with a
    dollar sign must reach the container intact, so $ becomes $$ for the
    compose template only."""
    if isinstance(v, str):
        return v.replace("$", "$$")
    if isinstance(v, dict):
        return {k: _compose_safe(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_compose_safe(x) for x in v]
    if isinstance(v, tuple):
        return tuple(_compose_safe(x) for x in v)
    return v


def _lower_keys(d):
    return {str(k).lower(): v for k, v in d.items()}


class Renderer:
    def __init__(self, modset=None):
        self.ms = modset or ModuleSet()
        self.env = _jinja()

    # ---- context ----------------------------------------------------------------
    def container_ips(self, spec, ordered):
        """One fixed address per module: the v1 plan's offset from the network
        address, or the host's container_ip override — checked to be inside
        the subnet, not the gateway, and unique."""
        net = _subnet(spec.docker_subnet)
        ips, taken = {}, {net.network_address, net.network_address + 1, net.broadcast_address}
        for i, name in enumerate(ordered):
            cfg = spec.module_config.get(name) or {}
            if cfg.get("container_ip"):
                try:
                    ip = ipaddress.IPv4Address(str(cfg["container_ip"]))
                except ValueError:
                    raise KiwiError("%s.container_ip: %r is not an IPv4 address" % (name, cfg["container_ip"]))
                if ip not in net:
                    raise KiwiError("%s.container_ip: %s is outside the stack network %s" % (name, ip, net))
                if ip in taken:
                    raise KiwiError("%s.container_ip: %s is the gateway or already used by another module" % (name, ip))
            else:
                off = IP_PLAN.get(name, 100 + i)
                ip = net.network_address + off
                if ip not in net or ip in taken:
                    raise KiwiError("docker_subnet %s has no room for %s's address (.%d) — "
                                    "use a /24 or set %s.container_ip" % (net, name, off, name))
            taken.add(ip)
            ips[name] = str(ip)
        return ips

    def base_context(self, spec, ordered):
        subnet, gateway, base = _subnet_parts(spec.docker_subnet)
        ips = self.container_ips(spec, ordered)
        ctx = {
            "node_name": spec.hostname, "hostname": spec.hostname, "node_type": spec.node_type,
            "container_prefix": PREFIX[spec.node_type], "network_name": NETWORK_NAME[spec.node_type],
            "docker_subnet": subnet, "docker_gateway": gateway, "docker_subnet_base": base,
            "docker_dir": spec.docker_dir, "mtu": str(spec.mtu), "timezone": spec.timezone,
            "vpn_ip": spec.vpn_ip, "pub_iface": spec.pub_iface, "service_user": spec.service_user,
            "container_ips": ips, "modules": list(ordered),
            "has_nextcloud": "cloud" in ordered,
            "proxy_ip": ips.get("reverse-proxy", ""),
            "vpn_client_ip": ips.get("vpn-client", ""),
            "dns_ip": ips.get("dns", ""),
            "module_config": {m: dict(v) for m, v in spec.module_config.items()},
            "mesh_ips": sorted({ip for ip, _n in spec.dns_records}),
            "dns_records": list(spec.dns_records),
            "master_ip": spec.master_ip, "mesh_subnet": spec.mesh_subnet, "domain": spec.domain,
            "runtime": spec.runtime,
            # the mesh is behind the WireGuard server on a master, behind the VPN client on a node
            "mesh_via": ips.get("vpn-server" if spec.node_type == "master" else "vpn-client", ""),
        }
        for n in ordered:
            ctx["has_" + n.replace("-", "_")] = True
        ctx.update(_lower_keys(spec.variables))
        return ctx

    def module_context(self, spec, ordered, base, name):
        mod = self.ms.get(name)
        ctx = dict(base)
        ctx["module"] = name
        ctx["container_ip"] = base["container_ips"].get(name, "")
        for k, v in mod.env_optional.items():
            ctx.setdefault(k.lower(), v)
        for s in mod.settings:
            ctx.setdefault(s.key, s.default)
        ctx.update(_lower_keys(spec.module_config.get(name) or {}))
        for key, data in spec.files.items():
            m, _, k = key.partition("/")
            if m == name:
                ctx[k + "_present"] = True
        return ctx

    def render_text(self, text, ctx, what):
        try:
            return self.env.from_string(text).render(**ctx)
        except jinja2.TemplateError as e:
            raise KiwiError("%s: template error: %s" % (what, e))

    def expand(self, s, ctx, what):
        """${VAR} placeholders from module.yaml (the v2 convention) resolve
        against the lowercased context; then the string is Jinja-rendered so
        {{ container_prefix }} works too. Unknown ${VAR} is an error."""
        def repl(m):
            k = m.group(1).lower()
            if k == "dockerdir":
                return ctx["docker_dir"]
            if k == "docker_subnet2":
                return ctx["docker_subnet_base"]
            if k == "lan_iface":
                k = "pub_iface"
            if k not in ctx or ctx[k] in (None, ""):
                if k == "vpn_ip":
                    raise KiwiError("%s: ${VPN_IP} is not set — the stack setting vpn_ip "
                                    "(this host's mesh address) is required here" % what)
                raise KiwiError("%s: ${%s} is not set" % (what, m.group(1)))
            return str(ctx[k])
        return self.render_text(_VAR.sub(repl, str(s)), ctx, what)

    # ---- compose ------------------------------------------------------------------
    def render_compose(self, spec, ordered, base, bundle):
        services, volumes, networks = {}, {}, {}
        for name in ordered:
            mod = self.ms.get(name)
            text = mod.template("compose")
            if text is None:
                continue
            ctx = _compose_safe(self.module_context(spec, ordered, base, name))
            rendered = self.render_text(text, ctx, "modules/%s/%s" % (name, mod.templates["compose"]))
            try:
                doc = yaml.safe_load(rendered) or {}
            except yaml.YAMLError as e:
                raise KiwiError("modules/%s: rendered compose is not valid YAML: %s" % (name, e))
            for svc, body in (doc.get("services") or {}).items():
                if svc in services:
                    raise KiwiError("service %s is defined by two modules" % svc)
                services[svc] = body
                bundle.services.append(svc)
            for vol, body in (doc.get("volumes") or {}).items():
                volumes.setdefault(vol, body or {"name": vol})
            for vol in mod.volumes:
                volumes.setdefault(vol, {"name": vol})
            for net, body in (doc.get("networks") or {}).items():
                networks.setdefault(net, body)
        if self.ms.networks_template:
            nets = yaml.safe_load(self.render_text(self.ms.networks_template, base, "modules/networks.yml.j2")) or {}
            for net, body in (nets.get("networks") or {}).items():
                networks.setdefault(net, body)
        self.check_ports(services)
        # containers inside the VPN client's network namespace must be restarted
        # with it (kiwi-stack vpn-restart), or they keep a namespace that is gone
        vpn = "service:%s-vpn-client" % base["container_prefix"]
        bundle.vpn_dependents = [str((body or {}).get("container_name") or svc)
                                 for svc, body in services.items() if (body or {}).get("network_mode") == vpn]
        compose = {"services": services}
        if volumes:
            compose["volumes"] = volumes
        if networks:
            compose["networks"] = networks
        header = ("# kiwi-server stack for %s (%s) — modules: %s\n"
                  "# Generated; edit the fleet file and render again instead of this file.\n"
                  % (spec.hostname, spec.node_type, ", ".join(ordered)))
        bundle.compose = compose
        return header + yaml.safe_dump(compose, sort_keys=False, default_flow_style=False, width=1000)

    @staticmethod
    def check_ports(services):
        """Two services publishing the same host port fail at the second
        `up`, not at render — so it is an error here, naming both."""
        seen = {}
        for svc, body in services.items():
            for p in (body or {}).get("ports") or []:
                p = str(p)
                proto = p.rsplit("/", 1)[1] if "/" in p else "tcp"
                parts = p.rsplit("/", 1)[0].split(":")
                host_port = parts[-2] if len(parts) >= 2 else parts[0]
                bind = parts[0] if len(parts) == 3 else "0.0.0.0"
                for other_bind, other in seen.get((host_port, proto), []):
                    if bind == other_bind or "0.0.0.0" in (bind, other_bind):
                        raise KiwiError("host port %s/%s is published by both %s and %s — change one "
                                        "(the modules' *_port / *_bind settings)" % (host_port, proto, other, svc))
                seen.setdefault((host_port, proto), []).append((bind, svc))

    # ---- nginx aggregation ---------------------------------------------------------
    def nginx_blocks(self, spec, ordered, base):
        upstreams, servers = [], []
        for name in ordered:
            mod = self.ms.get(name)
            if not mod.nginx or (isinstance(mod.nginx, dict) and mod.nginx.get("aggregator")):
                continue
            ctx = self.module_context(spec, ordered, base, name)
            what = "modules/%s/module.yaml nginx" % name
            nx = mod.nginx
            if not isinstance(nx, dict):
                raise KiwiError("%s: must be a mapping" % what)
            alias = {}
            if nx.get("upstream"):
                u = nx["upstream"]
                if not isinstance(u, dict) or not u.get("name") or not u.get("server"):
                    raise KiwiError("%s: upstream needs name: and server:" % what)
                uname, userver = self.expand(u["name"], ctx, what), self.expand(u["server"], ctx, what)
                upstreams.append({"name": uname, "server": userver})
                alias[uname] = userver
            entries = list(nx.get("servers") or [])
            if nx.get("server_name"):
                entries.insert(0, {k: v for k, v in nx.items() if k not in ("upstream", "servers")})
            for e in entries:
                if not isinstance(e, dict) or not e.get("server_name") or not e.get("proxy_pass"):
                    raise KiwiError("%s: every server block needs server_name: and proxy_pass:" % what)
                if e.get("when") and not ctx.get(str(e["when"])):
                    continue
                proxy_pass = self.expand(e["proxy_pass"], ctx, what)
                # nginx resolves an upstream block once, at startup, and refuses to start
                # when the name does not exist yet (Nextcloud AIO's containers appear
                # later). The generated config resolves names per request instead, so
                # the upstream's server goes straight into proxy_pass.
                for uname, userver in alias.items():
                    proxy_pass = proxy_pass.replace("://" + uname, "://" + userver)
                hsts = e.get("hsts")
                try:
                    s = {"server_name": self.expand(e["server_name"], ctx, what),
                         "proxy_pass": proxy_pass,
                         "websocket": bool(e.get("websocket", False)),
                         "hsts": None if hsts is None else bool(hsts),   # None: the module setting decides
                         "comment": self.expand(e.get("comment") or ("%s (%s)" % (name, e["server_name"])), ctx, what),
                         "client_max_body_size": e.get("client_max_body_size"),
                         "proxy_read_timeout": e.get("proxy_read_timeout"),
                         "proxy_ssl": bool(e.get("proxy_ssl", proxy_pass.startswith("https://"))),
                         "extra_directives": [self.expand(x, ctx, what) for x in (e.get("extra_directives") or [])],
                         "extra_locations": [{"path": self.expand(x["path"], ctx, what),
                                              "config": self.expand(x["config"], ctx, what)}
                                             for x in (e.get("extra_locations") or [])]}
                except (KeyError, TypeError, AttributeError) as err:
                    raise KiwiError("%s: malformed server block for %s (%s)" % (what, e.get("server_name"), err))
                servers.append(s)
        return upstreams, servers

    def service_names(self, spec):
        """The names the reverse proxy would answer for — what the fleet's DNS
        records must carry for this host. Cheap: no compose rendering."""
        ordered = self.ms.resolve(spec.modules, spec.node_type)
        base = self.base_context(spec, ordered)
        _upstreams, servers = self.nginx_blocks(spec, ordered, base)
        return [s["server_name"] for s in servers]

    # ---- firewall rules for the VPN client ----------------------------------------
    def vpn_rules(self, spec, ordered, base):
        rules = []
        for name in ordered:
            mod = self.ms.get(name)
            if mod.firewall_scope != "vpn-client" or name == "vpn-client":
                continue
            ctx = self.module_context(spec, ordered, base, name)
            for r in mod.firewall_rules:
                r = r.strip()
                if r.startswith("-"):
                    rule = self.expand(r, ctx, "modules/%s firewall" % name)
                    if rule not in rules:
                        rules.append(rule)
        return rules

    # ---- the bundle --------------------------------------------------------------------
    def render(self, spec):
        ordered = self.ms.resolve(spec.modules, spec.node_type)
        base = self.base_context(spec, ordered)
        bundle = Bundle()
        bundle.container_ips = dict(base["container_ips"])
        prefix = base["container_prefix"]

        # cross-module facts first: the compose fragments read them too
        upstreams, servers = self.nginx_blocks(spec, ordered, base)
        base["upstreams"], base["servers"] = upstreams, servers
        base["module_rules"] = self.vpn_rules(spec, ordered, base)
        base["enable_vpn_dnat"] = bool(base["proxy_ip"]) and any(
            self.ms.get(n).needs_vpn_dnat for n in ordered)
        if not spec.vpn_ip:
            at_mesh = [n for n in ordered if self.ms.get(n).needs_vpn_dnat] if base["enable_vpn_dnat"] else []
            if (spec.module_config.get("vpn-client") or {}).get("extra_dnat_rules"):
                at_mesh.append("vpn-client.extra_dnat_rules")
            if at_mesh:
                raise KiwiError("%s: the stack setting vpn_ip (this host's mesh address) is required — "
                                "%s are reachable at the mesh address" % (spec.hostname, ", ".join(at_mesh)))
        bundle.add_file("docker-compose.yml", self.render_compose(spec, ordered, base, bundle), 0o600)
        bundle.runtime = spec.runtime
        if spec.runtime == "podman":
            bundle.quadlets = quadlet.from_compose(bundle.compose, "Kiwi Server stack on %s (%s)" % (
                spec.hostname, ", ".join(ordered)))

        for name in ordered:
            mod = self.ms.get(name)
            ctx = self.module_context(spec, ordered, base, name)
            for c in mod.checks:
                try:
                    hit = self.env.compile_expression(c["when"])(**ctx)
                except jinja2.TemplateError as e:
                    raise KiwiError("modules/%s/module.yaml validate: %s: %s" % (name, c["when"], e))
                if hit:
                    raise KiwiError("%s: %s: %s" % (spec.hostname, name, self.render_text(c["error"], ctx, name)))
            # config templates (everything but compose)
            for key, fname in mod.templates.items():
                if key in ("compose", "env_example"):
                    continue
                out = mod.outputs.get(key) or {}
                if isinstance(out, str):
                    out = {"path": out}
                text = self.render_text(mod.template(key), ctx, "modules/%s/%s" % (name, fname))
                where = "modules/%s/module.yaml outputs.%s" % (name, key)
                if out.get("kind") == "host_unit":
                    unit = self.expand(out.get("name") or fname.replace(".j2", ""), ctx, where)
                    if unit in bundle.units:
                        raise KiwiError("two modules want to install the host unit %s" % unit)
                    bundle.units[unit] = text
                    continue
                rel = self.expand(out.get("path") or "%s/%s" % (name, fname.replace(".j2", "")), ctx, where)
                bundle.add_file(rel, text, int(out.get("mode", 0o644)))
            # embedded files (file-type settings): where the module says they go
            for s in mod.settings:
                if s.type != "file":
                    continue
                data = spec.files.get("%s/%s" % (name, s.key))
                if data is None:
                    continue
                out = mod.outputs.get(s.key)
                if not out:
                    raise KiwiError("modules/%s: file setting %s has no outputs: entry" % (name, s.key))
                if isinstance(out, str):
                    out = {"path": out}
                bundle.add_file(self.expand(out["path"], ctx, "modules/%s/module.yaml outputs.%s" % (name, s.key)),
                                data, int(out.get("mode", 0o600)))
            # directories, ports, host integration
            for bm in mod.bind_mounts:
                host = str(bm.get("host") or "")
                if not host or bm.get("type", "dir") != "dir":
                    continue
                try:
                    bundle.add_dir(self.expand(host, ctx, "modules/%s/module.yaml storage.bind_mounts" % name))
                except KiwiError as e:
                    # an optional mount may point at a setting nobody set; a broken
                    # template is an error either way
                    if bm.get("required", True) or "is not set" not in str(e):
                        raise
            for p in mod.open_ports:
                p = self.expand(p, ctx, "modules/%s/module.yaml network.open_ports" % name).strip()
                if p and p not in bundle.ports:
                    bundle.ports.append(p)
            for k, v in (mod.host.get("sysctl") or {}).items():
                bundle.sysctl[str(k)] = str(v)
            for km in mod.host.get("kernel_modules") or []:
                if km not in bundle.kernel_modules:
                    bundle.kernel_modules.append(str(km))
            for url in mod.meta.get("urls") or []:
                bundle.urls.append(self.expand(url, ctx, "modules/%s/module.yaml urls" % name))
        if base["enable_vpn_dnat"] and "vpn-client" in ordered:
            bundle.ports += [p for p in ("80/tcp", "443/tcp") if p not in bundle.ports]
        # every file's parent exists before compose mounts it
        for rel in list(bundle.files):
            d = os.path.dirname(rel)
            if d and not os.path.isabs(d):
                bundle.add_dir(d)
        # paths inside the stack directory are kept relative to it
        dd = spec.docker_dir.rstrip("/") + "/"
        dirs = set()
        for d in bundle.dirs:
            if d.startswith(dd):
                d = d[len(dd):]
            elif d == spec.docker_dir.rstrip("/"):
                continue
            dirs.add(d.rstrip("/"))
        bundle.dirs = sorted(dirs, key=lambda p: (os.path.isabs(p), p))
        bundle.prefix = prefix
        bundle.modules = ordered
        bundle.vpn_dependents = list(getattr(bundle, "vpn_dependents", []))
        bundle.mesh_via = base["mesh_via"]
        bundle.mesh_subnet = spec.mesh_subnet if base["mesh_via"] else ""
        # the host's own names point at itself: its services are published on the host
        bundle.hosts = [("127.0.0.1" if spec.vpn_ip and ip == spec.vpn_ip else ip, n) for ip, n in spec.dns_records]
        return bundle


def discover(path=None):
    return ModuleSet(path)
