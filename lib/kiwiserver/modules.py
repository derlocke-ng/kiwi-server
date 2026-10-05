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

from . import util
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
                 service_user="user"):
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

    def add_file(self, rel, content, mode=0o644):
        rel = rel.strip("/")
        if rel in self.files:
            raise KiwiError("two modules want to write %s" % rel)
        self.files[rel] = (content, mode)

    def add_dir(self, path):
        if path and path not in self.dirs:
            self.dirs.append(path)


def _subnet_parts(cidr):
    net = ipaddress.ip_network(cidr, strict=False)
    base = str(net.network_address).rsplit(".", 1)[0]
    return str(net), str(net.network_address + 1), base


def _lower_keys(d):
    return {str(k).lower(): v for k, v in d.items()}


class Renderer:
    def __init__(self, modset=None):
        self.ms = modset or ModuleSet()
        self.env = _jinja()

    # ---- context ----------------------------------------------------------------
    def base_context(self, spec, ordered):
        subnet, gateway, base = _subnet_parts(spec.docker_subnet)
        ips = {}
        for i, name in enumerate(ordered):
            cfg = spec.module_config.get(name) or {}
            if cfg.get("container_ip"):
                ips[name] = str(cfg["container_ip"])
            elif name in IP_PLAN:
                ips[name] = "%s.%d" % (base, IP_PLAN[name])
            else:
                ips[name] = "%s.%d" % (base, 100 + i)
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
            ctx = self.module_context(spec, ordered, base, name)
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

    # ---- nginx aggregation ---------------------------------------------------------
    def nginx_blocks(self, spec, ordered, base):
        upstreams, servers = [], []
        for name in ordered:
            mod = self.ms.get(name)
            if not mod.nginx or mod.nginx.get("aggregator"):
                continue
            ctx = self.module_context(spec, ordered, base, name)
            what = "modules/%s nginx" % name
            nx = mod.nginx
            if nx.get("upstream"):
                u = nx["upstream"]
                upstreams.append({"name": self.expand(u["name"], ctx, what),
                                  "server": self.expand(u["server"], ctx, what)})
            entries = list(nx.get("servers") or [])
            if nx.get("server_name"):
                entries.insert(0, {k: v for k, v in nx.items() if k not in ("upstream", "servers")})
            for e in entries:
                if e.get("when") and not ctx.get(str(e["when"])):
                    continue
                s = {"server_name": self.expand(e["server_name"], ctx, what),
                     "proxy_pass": self.expand(e["proxy_pass"], ctx, what),
                     "websocket": bool(e.get("websocket", False)),
                     "hsts": bool(e.get("hsts", False)),
                     "comment": self.expand(e.get("comment") or ("%s (%s)" % (name, e["server_name"])), ctx, what),
                     "client_max_body_size": e.get("client_max_body_size"),
                     "proxy_read_timeout": e.get("proxy_read_timeout"),
                     "proxy_ssl": bool(e.get("proxy_ssl", False)),
                     "extra_directives": [self.expand(x, ctx, what) for x in (e.get("extra_directives") or [])],
                     "extra_locations": [{"path": self.expand(x["path"], ctx, what),
                                          "config": self.expand(x["config"], ctx, what)}
                                         for x in (e.get("extra_locations") or [])]}
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
        bundle.add_file("docker-compose.yml", self.render_compose(spec, ordered, base, bundle), 0o600)

        for name in ordered:
            mod = self.ms.get(name)
            ctx = self.module_context(spec, ordered, base, name)
            # config templates (everything but compose)
            for key, fname in mod.templates.items():
                if key in ("compose", "env_example"):
                    continue
                out = mod.outputs.get(key) or {}
                if isinstance(out, str):
                    out = {"path": out}
                text = self.render_text(mod.template(key), ctx, "modules/%s/%s" % (name, fname))
                if out.get("kind") == "host_unit":
                    unit = self.expand(out.get("name") or fname.replace(".j2", ""), ctx, name)
                    bundle.units[unit] = text
                    continue
                rel = self.expand(out.get("path") or "%s/%s" % (name, fname.replace(".j2", "")), ctx, name)
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
                bundle.add_file(self.expand(out["path"], ctx, name), data, int(out.get("mode", 0o600)))
            # directories, ports, host integration
            for bm in mod.bind_mounts:
                host = str(bm.get("host") or "")
                if not host or bm.get("type", "dir") != "dir":
                    continue
                try:
                    bundle.add_dir(self.expand(host, ctx, "modules/%s storage" % name))
                except KiwiError:
                    if bm.get("required", True):
                        raise
            for p in mod.open_ports:
                p = self.expand(p, ctx, name).strip()
                if p and p not in bundle.ports:
                    bundle.ports.append(p)
            for k, v in (mod.host.get("sysctl") or {}).items():
                bundle.sysctl[str(k)] = str(v)
            for km in mod.host.get("kernel_modules") or []:
                if km not in bundle.kernel_modules:
                    bundle.kernel_modules.append(str(km))
            for url in mod.meta.get("urls") or []:
                bundle.urls.append(self.expand(url, ctx, name))
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
        return bundle


def discover(path=None):
    return ModuleSet(path)
