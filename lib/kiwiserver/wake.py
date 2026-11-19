"""Wake-on-LAN: a machine at home that is switched off is woken by another machine on its LAN.

Each apply learns the machine's card, its MAC and its address on the LAN into the store
(secrets/macs.json: nothing secret, but kept with the fleet). `network.wol: true` switches
waking on at the card (a drop-in for systemd's 99-default.link, and ethtool now where it is
there). `kiwi-server wake FLEET HOST` asks a machine of the same LAN to send the magic packet:
to the sleeping machine's address, through a neighbour entry that names its MAC for a moment,
so no broadcast is needed (a switch that has not seen that MAC yet passes it to every port,
as it does with any frame for a card it does not know).
"""
import ipaddress
import json
import os
import re
import shlex
import threading
import time

from . import remote, util
from .util import KiwiError

FILE = "macs.json"
MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
_LOCK = threading.Lock()

# what the machine tells about its card: the one of the default route
LEARN = ("card=$(ip -o -4 route show default 2>/dev/null | awk '{print $5; exit}'); [ -n \"$card\" ] || exit 0; "
         "printf '%s %s %s\\n' \"$card\" \"$(cat /sys/class/net/$card/address)\" "
         "\"$(ip -o -4 addr show dev \"$card\" scope global | awk '{print $4; exit}')\"; "
         "if command -v ethtool >/dev/null 2>&1; then ethtool \"$card\" 2>/dev/null | sed -n 's/^[[:space:]]*Wake-on: /wol /p'; fi")
DROPIN = "/etc/systemd/network/99-default.link.d/kiwi-wol.conf"
WOL_ON = ("install -d -m 0755 %s && printf '# kiwi-server: network.wol (Wake-on-LAN)\\n[Link]\\nWakeOnLan=magic\\n' > %s; "
          "card=$(ip -o -4 route show default 2>/dev/null | awk '{print $5; exit}'); "
          "if [ -n \"$card\" ] && command -v ethtool >/dev/null 2>&1; then ethtool -s \"$card\" wol g 2>/dev/null || true; fi"
          % (os.path.dirname(DROPIN), DROPIN))
WOL_OFF = "rm -f %s" % DROPIN


def path(fleet):
    return os.path.join(fleet.dir, "secrets", FILE)


def known(fleet):
    try:
        data = json.loads(util.read_text(path(fleet)))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def parse(text):
    """{card, mac, address, wol} of what LEARN printed; {} when nothing fits."""
    out = {}
    for line in str(text or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and MAC.match(parts[1].lower()) and not out:
            out = {"card": parts[0], "mac": parts[1].lower(), "address": parts[2] if len(parts) > 2 else ""}
        elif parts[:1] == ["wol"] and out:
            out["wol"] = parts[1] if len(parts) > 1 else ""
    return out


def remember(fleet, name, seen, clock=time.time):
    """What an apply learned of a machine's card, into the store."""
    if not seen:
        return
    with _LOCK:
        data = known(fleet)
        data[name] = dict(seen, seen=int(clock()))
        os.makedirs(os.path.dirname(path(fleet)), exist_ok=True)
        util.write_text(path(fleet), json.dumps(data, indent=1, sort_keys=True) + "\n", 0o600)


def after_apply(host, tgt, runner=None):
    """Learn its card, and switch waking on or off at the card as network.wol says."""
    run = runner or remote.run
    wol = bool(host.cfg["network"].get("wol"))
    try:
        seen = parse(run(tgt, (WOL_ON if wol else WOL_OFF) + "; " + LEARN).decode(errors="replace"))
    except KiwiError as e:
        util.warn("%s: its card was not looked at (Wake-on-LAN): %s" % (host.name, e))
        return
    remember(host.fleet, host.name, seen)
    if wol and seen.get("wol") and "g" not in seen["wol"]:
        util.warn("%s: its card %s does not wake on a magic packet yet (Wake-on: %s): the setting takes at its next "
                  "start; some machines also need it switched on in their firmware" % (host.name, seen["card"], seen["wol"]))


def magic(mac):
    """The magic packet: six 0xff, then the MAC sixteen times."""
    raw = bytes.fromhex(mac.replace(":", ""))
    return b"\xff" * 6 + raw * 16


def send_script(address, mac):
    """For the machine that wakes another one: the packet to its address on the LAN, through a
    neighbour entry naming its MAC for that moment; refused when the address is not on the LAN."""
    esc = "".join("\\x%02x" % b for b in magic(mac))
    body = ("set -e; route=$(ip -o route get %(a)s); case \"$route\" in *' via '*) echo 'not on this LAN' >&2; exit 4;; esac; "
            "dev=$(printf '%%s' \"$route\" | sed -n 's/.* dev \\([^ ]*\\).*/\\1/p'); [ -n \"$dev\" ] || exit 4; "
            "ip neigh replace %(a)s lladdr %(m)s dev \"$dev\" nud permanent; "
            "for i in 1 2 3; do printf '%(p)s' > /dev/udp/%(a)s/9 || true; sleep 0.2; done; "
            "ip neigh del %(a)s dev \"$dev\" 2>/dev/null || true; echo \"sent on $dev\""
            % {"a": address, "m": mac, "p": esc})
    return "bash -c " + shlex.quote(body)


def target(fleet, host, mac=None, address=None):
    """(mac, address on its LAN) of a machine to wake: given, from the fleet file, else as learned."""
    seen = known(fleet).get(host.name) or {}
    net = host.cfg["network"]
    mac = str(mac or net.get("mac") or seen.get("mac") or "").lower()
    if not MAC.match(mac):
        raise KiwiError("%s: its MAC is not known: it is learned at an apply, or give network.mac (or --mac aa:bb:cc:dd:ee:ff)"
                        % host.name)
    addr = address or (str(net.get("address") or "") if not net.get("dhcp", True) else "") or seen.get("address") or ""
    if not addr:
        raise KiwiError("%s: its address on the LAN is not known: give --address" % host.name)
    return mac, str(ipaddress.ip_interface(addr).ip), ipaddress.ip_interface(addr).network if "/" in addr else None


def wakers(fleet, host, net, via=None):
    """The machines that may send it: --via, else those at its site with an address in its LAN."""
    if via:
        if via not in fleet.hosts:
            raise KiwiError("no machine called %s" % via)
        return [fleet.hosts[via]]
    site = str(host.cfg["network"].get("site") or "home")
    seen = known(fleet)
    out = []
    for h in fleet.hosts.values():
        if h.name == host.name or h.standby_of or str(h.cfg["network"].get("site") or "home") != site:
            continue
        a = (str(h.cfg["network"].get("address") or "") if not h.cfg["network"].get("dhcp", True) else "") \
            or (seen.get(h.name) or {}).get("address") or ""
        try:
            if a and (net is None or ipaddress.ip_interface(a).ip in net):
                out.append(h)
        except ValueError:
            continue
    return out


def wake(fleet, host, mac=None, address=None, via=None, reach=None, runner=None):
    """Send the magic packet from the first machine of its LAN that answers. The name of that one."""
    mac, addr, net = target(fleet, host, mac, address)
    run, reach = runner or remote.run, reach or remote.reach
    tried = []
    for w in wakers(fleet, host, net, via):
        try:
            out = run(reach(w), send_script(addr, mac)).decode(errors="replace").strip()
        except KiwiError as e:
            tried.append("%s (%s)" % (w.name, str(e).splitlines()[-1] if str(e) else "no answer"))
            continue
        util.say("%s: woken from %s (%s, %s)" % (host.name, w.name, mac, out or "sent"))
        return w.name
    raise KiwiError("%s: no machine of its LAN sent the packet%s" % (host.name, ": " + "; ".join(tried) if tried else
                                                                     " (none at its site with an address in its LAN: --via NAME)"))
