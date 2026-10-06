"""Routers: what a LAN router needs so its clients reach the mesh.

Two ways in. A WireGuard client on the router, with the mesh routed through
it (a *split* tunnel: only the mesh) or everything (a *full* tunnel: the
whole LAN leaves through the master's exit). Or, for a router without
WireGuard, a static route to a gateway node on the LAN. In both cases one
dnsmasq line sends the fleet's names to the master's Pi-hole, which replaces
the per-host overrides people keep by hand.

OpenWrt gets a uci script from `kiwi-server openwrt`; other routers get the
same facts as instructions (docs/routers.md).
"""
import ipaddress
import re

from .util import KiwiError

_LIST_KEYS = ("address", "allowedips", "dns")


def parse_wireguard(text):
    """A wg-quick style client config, as wg-easy issues it:
    {"interface": {...}, "peers": [{...}]}; keys lowercased, list-valued
    keys (address, allowedips, dns) as lists."""
    sections, cur = [], None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            cur = {"_section": line[1:-1].strip().lower()}
            sections.append(cur)
            continue
        if cur is None or "=" not in line:
            raise KiwiError("not a WireGuard config: %r" % raw.strip())
        k, v = [x.strip() for x in line.split("=", 1)]
        k = k.lower()
        if k in _LIST_KEYS:
            cur.setdefault(k, []).extend(x.strip() for x in v.split(",") if x.strip())
        else:
            cur[k] = v
    iface = next((s for s in sections if s["_section"] == "interface"), None)
    peers = [s for s in sections if s["_section"] == "peer"]
    if iface is None or not peers:
        raise KiwiError("the WireGuard config needs an [Interface] and a [Peer] section")
    for key in ("privatekey", "address"):
        if not iface.get(key):
            raise KiwiError("the WireGuard config's [Interface] has no %s" % key)
    for key in ("publickey", "endpoint"):
        if not peers[0].get(key):
            raise KiwiError("the WireGuard config's [Peer] has no %s" % key)
    if ":" not in peers[0]["endpoint"]:
        raise KiwiError("the [Peer] Endpoint needs a port: host:51820")
    return {"interface": iface, "peers": peers}


def _q(v):
    return "'" + str(v).replace("'", "'\\''") + "'"


def openwrt_script(domain, master_ip, mesh_subnet, wg=None, via=None, full=False, name="kiwi"):
    """A POSIX sh script of uci commands. Re-runnable: every section it
    creates is named, deleted first and written again."""
    if not re.match(r"^[a-z][a-z0-9_]{0,14}$", name):
        raise KiwiError("the interface name must be a short lowercase word: %r" % name)
    if (wg is None) == (via is None):
        raise KiwiError("give a WireGuard config or a gateway address, not both")
    try:
        ipaddress.IPv4Network(mesh_subnet)
    except ValueError:
        raise KiwiError("mesh_subnet %r is not an IPv4 network" % mesh_subnet)
    L = ["#!/bin/sh",
         "# kiwi-server: join the mesh from an OpenWrt router. Run as root on the router:",
         "#   scp this-file root@openwrt:/tmp/kiwi.sh && ssh root@openwrt sh /tmp/kiwi.sh",
         "# Re-running it is safe: the sections it writes are named and replaced.",
         "set -e", ""]
    dns_line = None
    if wg is not None:
        i, p = wg["interface"], wg["peers"][0]
        host, port = p["endpoint"].rsplit(":", 1)
        allowed = ["0.0.0.0/0"] if full else [mesh_subnet]
        mode = "everything (full tunnel)" if full else "the mesh %s (split tunnel)" % mesh_subnet
        L += ["# WireGuard: the router is a mesh client; LAN clients reach %s through it" % mode,
              "command -v wg >/dev/null 2>&1 || opkg install wireguard-tools luci-proto-wireguard 2>/dev/null || apk add wireguard-tools luci-proto-wireguard",
              "uci -q delete network.%s" % name,
              "uci -q delete network.%s_peer" % name,
              "uci set network.%s=interface" % name,
              "uci set network.%s.proto='wireguard'" % name,
              "uci set network.%s.private_key=%s" % (name, _q(i["privatekey"]))]
        for a in i["address"]:
            L.append("uci add_list network.%s.addresses=%s" % (name, _q(a)))
        L.append("uci set network.%s.mtu=%s" % (name, _q(i.get("mtu") or "1412")))
        L += ["uci set network.%s_peer=wireguard_%s" % (name, name),
              "uci set network.%s_peer.description='kiwi master'" % name,
              "uci set network.%s_peer.public_key=%s" % (name, _q(p["publickey"]))]
        if p.get("presharedkey"):
            L.append("uci set network.%s_peer.preshared_key=%s" % (name, _q(p["presharedkey"])))
        L += ["uci set network.%s_peer.endpoint_host=%s" % (name, _q(host)),
              "uci set network.%s_peer.endpoint_port=%s" % (name, _q(port)),
              "uci set network.%s_peer.persistent_keepalive=%s" % (name, _q(p.get("persistentkeepalive") or "25")),
              "uci set network.%s_peer.route_allowed_ips='1'" % name]
        for a in allowed:
            L.append("uci add_list network.%s_peer.allowed_ips=%s" % (name, _q(a)))
        L += ["",
              "# firewall: the LAN may talk into the tunnel, nothing comes in unasked",
              "uci -q delete firewall.%s" % name,
              "uci -q delete firewall.%s_fwd" % name,
              "uci set firewall.%s=zone" % name,
              "uci set firewall.%s.name=%s" % (name, _q(name)),
              "uci add_list firewall.%s.network=%s" % (name, _q(name)),
              "uci set firewall.%s.input='REJECT'" % name,
              "uci set firewall.%s.output='ACCEPT'" % name,
              "uci set firewall.%s.forward='REJECT'" % name,
              "uci set firewall.%s.masq='1'" % name,
              "uci set firewall.%s.mtu_fix='1'" % name,
              "uci set firewall.%s_fwd=forwarding" % name,
              "uci set firewall.%s_fwd.src='lan'" % name,
              "uci set firewall.%s_fwd.dest=%s" % (name, _q(name))]
        if full:
            dns_line = master_ip   # every lookup through the mesh, like the tunnel
    else:
        try:
            ipaddress.IPv4Address(via)
        except ValueError:
            raise KiwiError("the gateway node's LAN address must be an IPv4 address, got %r" % via)
        L += ["# static route: LAN clients reach the mesh %s through the gateway node at %s" % (mesh_subnet, via),
              "uci -q delete network.%s_route" % name,
              "uci set network.%s_route=route" % name,
              "uci set network.%s_route.interface='lan'" % name,
              "uci set network.%s_route.target=%s" % (name, _q(mesh_subnet)),
              "uci set network.%s_route.gateway=%s" % (name, _q(via))]
    L.append("")
    if dns_line:
        L += ["# DNS: every lookup goes to the master's Pi-hole, through the tunnel",
              "uci -q del_list dhcp.@dnsmasq[0].server=%s" % _q(dns_line),
              "uci add_list dhcp.@dnsmasq[0].server=%s" % _q(dns_line),
              "uci set dhcp.@dnsmasq[0].noresolv='1'"]
    elif domain and master_ip:
        fwd = "/%s/%s" % (domain, master_ip)
        L += ["# DNS: the fleet's names (*.%s) come from the master's Pi-hole — no per-host overrides needed" % domain,
              "uci -q del_list dhcp.@dnsmasq[0].server=%s" % _q(fwd),
              "uci add_list dhcp.@dnsmasq[0].server=%s" % _q(fwd),
              "uci set dhcp.@dnsmasq[0].rebind_domain=%s" % _q(domain)]
    L += ["", "uci commit network", "uci commit firewall", "uci commit dhcp",
          "/etc/init.d/network reload", "/etc/init.d/firewall reload", "/etc/init.d/dnsmasq restart",
          "echo 'kiwi: done — try: nslookup gate.%s' " % (domain or "home"), ""]
    return "\n".join(L)
