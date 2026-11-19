"""A machine at another site that joins over the VPN first (`install.over_vpn`).

Behind someone else's router nothing at home reaches a freshly installed machine, so its first
apply cannot come over SSH from home. With `install: { over_vpn: true }` its install media carry
a WireGuard config for a one-time join peer on the master (its own key and address, not the
machine's VPN identity): the bare system comes up on the mesh with it, the control node or
kiwi-server applies it there, and once the machine answers at its own mesh address the join
tunnel goes and the peer is removed from the master. A stick lost after that opens nothing.

The join tunnel routes only what comes from its own address (a table of its own): the role
points the machine's mesh route at its real VPN client in the middle of the apply, and the
apply's session must not follow it.
"""
import ipaddress
import os
import re

from . import remote, util, wgeasy
from .util import KiwiError

TABLE = 4242
CONF = "/etc/wireguard/kiwi-join.conf"
UNIT = "wg-quick@kiwi-join.service"
# detached: the session that runs this goes over the tunnel it takes down
OFF = ("systemd-run --collect --on-active=10 --unit=kiwi-join-off sh -c "
       "'systemctl disable --now %s; rm -f %s' >/dev/null 2>&1 || true" % (UNIT, CONF))


def wanted(host):
    return bool((host.cfg.get("install") or {}).get("over_vpn"))


def path(fleet, host):
    return fleet.resolve_path("secrets/join/%s.conf" % host.hostname)


def peer_name(host):
    return "join-" + host.hostname.split(".", 1)[0]


def address(text):
    """The join peer's address on the mesh, from its config."""
    m = re.search(r"^\s*Address\s*=\s*([0-9.]+)", str(text or ""), re.M)
    return m.group(1) if m else ""


def tunnel_config(text, mesh):
    """wg-easy's config for the join peer as the bare machine uses it: the mesh only, its address
    alone, no DNS (a bare Debian has no resolvconf for wg-quick), 1280 bytes (an unknown line at
    another site), and a routing table of its own for what comes from its address."""
    text = wgeasy.split_tunnel(text, mesh)
    text = wgeasy.with_mtu(text, wgeasy.DEVICE_MTU)
    lines = [ln for ln in text.splitlines() if not re.match(r"\s*DNS\s*=", ln)]
    addr = address(text)
    # its address alone (/32): wg-easy's /24 would put the mesh's first /24 on the join tunnel in the
    # main table, ahead of the machine's own VPN client, and its own address would never answer
    # (kumquat, 19 Nov 2026)
    lines = [re.sub(r"^(\s*Address\s*=\s*)([0-9.]+)/\d+", r"\g<1>\2/32", ln) for ln in lines]
    out = []
    for ln in lines:
        out.append(ln)
        if ln.strip() == "[Interface]":
            out += ["Table = %d" % TABLE,
                    "PostUp = ip -4 rule add from %s table %d priority 4242" % (addr, TABLE),
                    "PreDown = ip -4 rule del from %s table %d priority 4242" % (addr, TABLE)]
    return "# kiwi-server: a one-time way onto the VPN for the first apply (install.over_vpn)\n" + "\n".join(out) + "\n"


def make(fleet, master, wg_password, mesh, host, ssh=None, api=None):
    """The join peer on the master (made once) and its config in the fleet's secrets. Its text."""
    dest = path(fleet, host)
    if os.path.isfile(dest):
        return util.read_text(dest)

    def create(url):
        c_api = wgeasy.WgEasy(url, wg_password).login()
        c = c_api.client(peer_name(host)) or c_api.create(peer_name(host))
        return c_api.configuration(c["id"])
    if api:
        raw = create(api)
    else:
        with remote.Tunnel(remote.reach(master, ssh), 51821) as url:
            raw = create(url)
    text = tunnel_config(raw, mesh)
    if not address(text):
        raise KiwiError("%s: the join peer's config has no address" % host.name)
    util.write_text(dest, text, 0o600)
    util.say("%s: a one-time way onto the VPN for its first apply (%s at %s); its install media carry its key "
             "until then" % (host.name, peer_name(host), address(text)))
    return text


def drop(fleet, master, wg_password, host, ssh=None, api=None):
    """The join peer off the master and its config out of the fleet."""
    def delete(url):
        c_api = wgeasy.WgEasy(url, wg_password).login()
        c = c_api.client(peer_name(host))
        if c is not None:
            c_api.delete(c["id"])
    if api:
        delete(api)
    else:
        with remote.Tunnel(remote.reach(master, ssh), 51821) as url:
            delete(url)
    if os.path.isfile(path(fleet, host)):
        os.remove(path(fleet, host))
    util.say("%s: its one-time way onto the VPN is gone; it is on the mesh with its own address" % host.name)


def joined_at(fleet, host):
    """Its join address, when it has a join peer that has not been removed yet."""
    p = path(fleet, host)
    return address(util.read_text(p)) if os.path.isfile(p) else ""


def on_its_own(host, mesh_ip, answers=None):
    """Does it answer at its own mesh address? Only then the join tunnel may go."""
    answers = answers or remote._answers
    try:
        ipaddress.ip_address(str(mesh_ip))
    except ValueError:
        return False
    return any(answers(str(mesh_ip), p) for p in dict.fromkeys([remote.ssh_port(host), 22]))
