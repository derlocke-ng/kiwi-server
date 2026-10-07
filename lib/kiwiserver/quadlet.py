"""Podman quadlets from the rendered compose file.

The modules describe their containers as compose fragments, and that stays
the source: `docker compose config` validates it and the docker runtime runs
it as it is. For the podman runtime the same compose dict becomes quadlet
units — one .container per service, one .network per network — that systemd
runs on the host: ordering, restarts and logs come from systemd, nothing but
podman is needed on the machine, and `podman auto-update` pulls new images.

Only the compose subset the modules use is translated; anything else is an
error at render time rather than a surprise on the host.
"""
import re

from .util import KiwiError

KNOWN_SERVICE_KEYS = {
    "image", "container_name", "environment", "volumes", "networks", "network_mode", "ports", "expose",
    "cap_add", "cap_drop", "sysctls", "devices", "restart", "logging", "security_opt", "init", "read_only",
    "tmpfs", "user", "shm_size", "stop_grace_period", "hostname", "command", "depends_on", "healthcheck",
    "profiles", "labels",
}


def _esc(value):
    """A systemd assignment value: % is a specifier, so %%; quoted when it
    has whitespace or quotes; compose's $$ (its own escaping) back to $."""
    v = str(value).replace("$$", "$").replace("%", "%%")
    if re.search(r"[\s\"'\\]", v):
        v = '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return v


def _seconds(value):
    s = str(value).strip()
    m = re.match(r"^(\d+)\s*(ms|s|m|h)?$", s)
    if not m:
        raise KiwiError("quadlet: cannot read the duration %r" % value)
    n, unit = int(m.group(1)), m.group(2) or "s"
    return {"ms": max(1, n // 1000), "s": n, "m": n * 60, "h": n * 3600}[unit]


def _env_items(env):
    if isinstance(env, dict):
        return [(str(k), "" if v is None else str(v)) for k, v in env.items()]
    out = []
    for e in env or []:
        e = str(e)
        k, _, v = e.partition("=")
        out.append((k, v))
    return out


def _unit_name(service):
    return service + ".service"


def container_unit(service, body, networks, services):
    """One .container quadlet for a compose service."""
    body = body or {}
    unknown = sorted(set(body) - KNOWN_SERVICE_KEYS)
    if unknown:
        raise KiwiError("quadlet: service %s uses compose keys the podman runtime does not translate: %s"
                        % (service, ", ".join(unknown)))
    unit, container, svc = [], [], []
    unit.append("Description=kiwi stack: %s" % service)
    deps = body.get("depends_on") or {}
    dep_names = list(deps) if isinstance(deps, (dict, list)) else [str(deps)]
    requires = []
    net_mode = body.get("network_mode")
    if net_mode:
        if net_mode == "host":
            container.append("Network=host")
        elif net_mode.startswith("service:"):
            other = net_mode.split(":", 1)[1]
            if other not in services:
                raise KiwiError("quadlet: %s shares the network of %s, which is not in the stack" % (service, other))
            container.append("Network=container:%s" % other)
            requires.append(other)
        elif net_mode.startswith("container:"):
            container.append("Network=%s" % net_mode)
        else:
            raise KiwiError("quadlet: %s: network_mode %r is not translated" % (service, net_mode))
    else:
        nets = body.get("networks") or {}
        names = list(nets) if isinstance(nets, dict) else [str(n) for n in nets]
        for n in names:
            if n not in networks:
                raise KiwiError("quadlet: %s joins network %s, which the stack does not define" % (service, n))
            container.append("Network=%s.network" % n)
            cfg = nets.get(n) if isinstance(nets, dict) else None
            if isinstance(cfg, dict) and cfg.get("ipv4_address"):
                container.append("IP=%s" % cfg["ipv4_address"])
    for d in dep_names:
        if d in services and d not in requires:
            unit.append("After=%s" % _unit_name(d))
            unit.append("Wants=%s" % _unit_name(d))
    for r in requires:
        unit.append("Requires=%s" % _unit_name(r))
        unit.append("After=%s" % _unit_name(r))
    container.insert(0, "Image=%s" % body["image"])
    container.insert(1, "ContainerName=%s" % (body.get("container_name") or service))
    container.append("AutoUpdate=registry")
    if body.get("hostname"):
        container.append("HostName=%s" % body["hostname"])
    if body.get("init"):
        container.append("RunInit=true")
    if body.get("read_only"):
        container.append("ReadOnly=true")
    if body.get("user") is not None:
        container.append("User=%s" % body["user"])
    for c in body.get("cap_add") or []:
        container.append("AddCapability=%s" % c)
    for c in body.get("cap_drop") or []:
        container.append("DropCapability=%s" % c)
    for opt in body.get("security_opt") or []:
        if str(opt) == "label:disable":
            container.append("SecurityLabelDisable=true")
        else:
            container.append("PodmanArgs=--security-opt=%s" % opt)
    for s in body.get("sysctls") or []:
        if isinstance(body["sysctls"], dict):
            s = "%s=%s" % (s, body["sysctls"][s])
        container.append("Sysctl=%s" % s)
    for d in body.get("devices") or []:
        container.append("AddDevice=%s" % d)
    for t in body.get("tmpfs") or []:
        container.append("Tmpfs=%s" % t)
    if body.get("shm_size") is not None:
        container.append("ShmSize=%s" % body["shm_size"])
    if body.get("stop_grace_period"):
        container.append("StopTimeout=%d" % _seconds(body["stop_grace_period"]))
    for k, v in _env_items(body.get("environment")):
        container.append("Environment=%s" % _esc("%s=%s" % (k, v)))
    for v in body.get("volumes") or []:
        if isinstance(v, dict):
            raise KiwiError("quadlet: %s: long-form volumes are not translated" % service)
        container.append("Volume=%s" % v)
    for p in body.get("ports") or []:
        container.append("PublishPort=%s" % p)
    if body.get("command"):
        cmd = body["command"]
        container.append("Exec=%s" % (" ".join(cmd) if isinstance(cmd, list) else cmd))
    hc = body.get("healthcheck")
    if hc and not hc.get("disable"):
        test = hc.get("test")
        if isinstance(test, list):
            if test and test[0] == "CMD-SHELL":
                test = " ".join(test[1:])
            elif test and test[0] == "CMD":
                test = " ".join(test[1:])
            else:
                test = " ".join(test)
        if test:
            container.append("HealthCmd=%s" % _esc(test))
            for key, opt in (("interval", "HealthInterval"), ("timeout", "HealthTimeout"),
                             ("start_period", "HealthStartPeriod")):
                if hc.get(key):
                    container.append("%s=%ds" % (opt, _seconds(hc[key])))
            if hc.get("retries"):
                container.append("HealthRetries=%d" % int(hc["retries"]))
    container.append("LogDriver=journald")
    restart = str(body.get("restart") or "no")
    svc.append("Restart=%s" % ("always" if restart in ("always", "unless-stopped") else
                               "on-failure" if restart.startswith("on-failure") else "no"))
    svc.append("TimeoutStartSec=900")
    text = ["# kiwi-server stack — generated; the fleet file is the place to change it",
            "[Unit]"] + unit + ["", "[Container]"] + container + ["", "[Service]"] + svc + \
           ["", "[Install]", "WantedBy=kiwi-stack.target", ""]
    return "\n".join(text)


def network_unit(name, body):
    body = body or {}
    lines = ["# kiwi-server stack — generated; the fleet file is the place to change it",
             "[Network]", "NetworkName=%s" % (body.get("name") or name), "Driver=%s" % (body.get("driver") or "bridge")]
    ipam = (body.get("ipam") or {}).get("config") or []
    for cfg in ipam:
        if cfg.get("subnet"):
            lines.append("Subnet=%s" % cfg["subnet"])
        if cfg.get("gateway"):
            lines.append("Gateway=%s" % cfg["gateway"])
    opts = body.get("driver_opts") or {}
    if str(opts.get("com.docker.network.enable_ipv6", "")).lower() in ("true", "1"):
        lines.append("IPv6=true")
    if opts.get("com.docker.network.driver.mtu"):
        lines.append("Options=mtu=%s" % opts["com.docker.network.driver.mtu"])
    lines.append("")
    return "\n".join(lines)


def target_unit(description):
    return ("# kiwi-server stack — generated\n[Unit]\nDescription=%s\nAfter=network-online.target\n"
            "Wants=network-online.target\n\n[Install]\nWantedBy=multi-user.target\n" % description)


def from_compose(compose, description="Kiwi Server stack"):
    """{file name: text} — the .container and .network quadlets plus the target."""
    services = compose.get("services") or {}
    networks = compose.get("networks") or {}
    out = {}
    for n, body in networks.items():
        out["%s.network" % n] = network_unit(n, body)
    for svc, body in services.items():
        out["%s.container" % svc] = container_unit(svc, body, networks, services)
    out["kiwi-stack.target"] = target_unit(description)
    return out
