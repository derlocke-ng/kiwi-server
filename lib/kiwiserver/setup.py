"""Starting a fleet: the questions `kiwi-server init` asks, in a terminal or through the app,
and the fleet file the answers make.

A fleet made here validates as far as it can before anything is installed: its admin key
exists, nothing points at a placeholder. What cannot exist yet (a node's WireGuard config,
which its master issues once it runs) is a next step, not an error. The app asks the same
questions: `init --questions` prints them as JSON with what this computer already knows
filled in, and `init --answers FILE` takes them back.
"""
import fcntl
import ipaddress
import json
import os
import random
import re
import socket
import struct
import subprocess

import yaml

from . import config, providers, util
from .util import KiwiError

# design/control-plane.md, Names: fruit dishes for masters, fruits for nodes (DNS-safe, no "kiwi")
MASTERS = ["sorbet", "crumble", "trifle", "parfait", "compote", "pavlova", "strudel", "cobbler",
           "sundae", "clafoutis", "galette", "tartlet", "fool", "jelly", "mousse"]
NODES = ["papaya", "lychee", "mango", "quince", "guava", "damson", "medlar", "loquat", "feijoa",
         "rambutan", "longan", "pomelo", "tamarind", "salak", "jujube", "cherimoya"]
PRESETS = {         # what a machine at home does, in the words the wizard shows; the first is the suggestion
    "cloud": "Keep my files, photos and passwords (Nextcloud and a password manager)",
    "gateway": "Be my home's ad blocker, VPN gateway and download station (it takes over DNS and DHCP at home)",
    "minimal": "Nothing yet: only its place on the VPN",
}


def add_home_machine(path, preset, address="", name="", target=""):
    """A machine at home, added to the fleet file as the first run's questions add one: its name
    (made up when none is given). `address`: its fixed address at home, with the prefix and gateway
    of the machines the file has. target coreos: a starter's system as it is (no install)."""
    import ipaddress
    import yaml
    if preset not in PRESETS:
        raise KiwiError("what should it do?")
    if target not in ("", "ucore", "debian", "coreos"):
        raise KiwiError("the system is ucore or debian")
    data = yaml.safe_load(util.read_text(path)) or {}
    taken = set(data.get("hosts") or {})
    name = (name or "").strip().lower() or free_name("node", taken)
    nets = [h.get("network") or {} for h in (data.get("hosts") or {}).values() if isinstance(h, dict)]
    gateway = next((str(n["gateway"]) for n in nets if n.get("gateway")), "")     # as the machines it has
    like = next((str(n["address"]) for n in nets if "/" in str(n.get("address") or "")), "")
    address = (address or "").strip()
    net = None
    if address:
        try:
            ip = ipaddress.ip_interface(address)
        except ValueError:
            raise KiwiError("An address looks like 192.168.1.30.")
        prefix = ip.network.prefixlen if "/" in address else int(like.rsplit("/", 1)[1]) if like else 24
        net = "{ dhcp: false, address: %s/%d%s }" % (ip.ip, prefix, ", gateway: %s" % gateway if gateway else "")
    if alone(data):         # no master: off the VPN, so its address at home is where it answers
        if not net:
            raise KiwiError("Without a master, a machine needs its fixed address at home: its services answer there.")
        if preset == "gateway":
            raise KiwiError("A gateway leads your home into the VPN: it comes with a master.")
    add_host(path, name, "node", preset, target or "ucore", network=not net)
    disk = "" if target == "coreos" else "\n    disk: auto"        # a starter's system is installed already
    text = util.read_text(path).rstrip("\n") + disk
    util.write_text(path, text + ("\n    network: %s\n" % net if net else "\n"), 0o600)
    return name


def remove_host(path, name):
    """A host's block out of the fleet file, as text, so the comments of the others stay."""
    import yaml
    text = util.read_text(path)
    lines = text.splitlines(keepends=True)
    start = next((i for i, x in enumerate(lines) if x.rstrip() == "  %s:" % name or x.startswith("  %s: " % name)), None)
    data = yaml.safe_load(text) or {}
    if start is None or name not in (data.get("hosts") or {}):
        raise KiwiError("there is no host called %s in %s" % (name, path))
    while start > 0 and lines[start - 1].startswith("  #"):     # the comment right above a host is that host's
        start -= 1
    end = start + 1
    while end < len(lines) and lines[end].startswith("  #"):    # (past its own comment, to its name)
        end += 1
    end += 1
    while end < len(lines):
        x = lines[end]
        if x.strip() and not x.startswith("   ") and not x.startswith("  #"):      # the next host, or a new section
            break
        end += 1
    while end > start + 1 and lines[end - 1].lstrip().startswith("#") and lines[end - 1].startswith("  #"):
        end -= 1                                # a comment right above the next host is that host's
    new = "".join(lines[:start] + lines[end:])
    left = yaml.safe_load(new) or {}
    want = {k: v for k, v in (data.get("hosts") or {}).items() if k != name}
    if (left.get("hosts") or {}) != want:
        raise KiwiError("%s could not be taken out of %s as text without touching another host: remove its block by hand"
                        % (name, path))
    if not want:                                # hosts: with nothing under it is not a mapping any more
        new = new.rstrip("\n").rsplit("hosts:", 1)[0] + "hosts: {}\n"
    util.write_text(path, new, 0o600)


def free_name(role, taken=()):
    """A name for a new machine nobody uses yet: a fruit dish for a master, a fruit otherwise."""
    pool = MASTERS if role == "master" else NODES
    for name in pool:
        if name not in taken:
            return name
    n = 2
    while "%s%d" % (pool[0], n) in taken:
        n += 1
    return "%s%d" % (pool[0], n)


def alone(data):
    """A fleet's data without a master (docs/design/no-master.md)?"""
    hosts = (data or {}).get("hosts") or {}
    return bool(hosts) and not any((h or {}).get("role") == "master" for h in hosts.values())


def onto_the_vpn(path):
    """A network without a master gets one: each machine at home that is off the VPN (`vpn: false`)
    goes back on it. Its VPN client comes back (`enroll` fetches its config once the master runs)
    and leads its own module list when it has one. Changed in the text, so comments stay; where
    the text is laid out otherwise, the file is written from its data. The machines that changed."""
    import yaml
    from . import fleettext
    text = util.read_text(path)
    data = yaml.safe_load(text) or {}
    domain = (data.get("defaults") or {}).get("domain") or "home"
    edits, changed = [], []
    hosts = data.get("hosts") or {}
    masters = [n for n, h in hosts.items() if (h or {}).get("role") == "master"]
    exit_taken = any(isinstance((hosts[m] or {}).get("master"), dict) and hosts[m]["master"].get("vpn-client")
                     for m in masters)
    for name, h in hosts.items():
        node = (h or {}).get("node")
        if not isinstance(node, dict) or node.get("vpn") is not False:
            continue
        changed.append(name)
        # a commercial VPN as this machine's exit: its one VPN client now goes to the network, so the
        # provider moves to the new master and is the whole network's exit there (once: a WireGuard
        # key of a provider is good for one device)
        own = node.get("vpn-client") if isinstance(node.get("vpn-client"), dict) else {}
        if own.get("vpn_provider") and own["vpn_provider"] != "custom" and masters:
            if not exit_taken:
                m = masters[0]
                edits.append((["hosts", m, "master", "vpn-client"], dict(own)))
                mods = (hosts[m].get("master") or {}).get("modules")
                if isinstance(mods, list) and "vpn-client" not in mods:
                    edits.append((["hosts", m, "master", "modules"], ["vpn-client"] + list(mods)))
                exit_taken = True
                util.say("%s's VPN provider (%s) is the exit of %s now, for the whole network" % (name, own["vpn_provider"], m))
            else:
                util.warn("%s's VPN provider (%s) is not used any more: the network leaves through its master's exit; "
                          "its key may go back to the provider" % (name, own["vpn_provider"]))
        edits += [(["hosts", name, "node", "vpn"], True),
                  (["hosts", name, "node", "vpn-client"], {"wireguard_config": "secrets/%s.%s.conf" % (name, domain)})]
        if isinstance(node.get("modules"), list) and "vpn-client" not in node["modules"]:
            edits.append((["hosts", name, "node", "modules"], ["vpn-client"] + list(node["modules"])))
    for keys, value in edits:
        d = data
        for k in keys[:-1]:
            d = d[k]
        d[keys[-1]] = value
        if text is not None:
            text = fleettext.put(text, keys, value)
    if changed:
        util.write_text(path, text if text is not None else
                        yaml.safe_dump(data, sort_keys=False, default_flow_style=False), 0o600)
    return changed


def add_host(path, name, role, preset="", target="", address="", network=True):
    """A new host block at the end of the fleet file, written as text so its comments stay.
    `address`: a master's public address or name (its vpn-server.wg_host)."""
    text = util.read_text(path)
    import yaml
    data = yaml.safe_load(text) or {}
    off = alone(data)       # a network without a master: a new node is off the VPN too
    if name in (data.get("hosts") or {}):
        raise KiwiError("there is already a host called %s" % name)
    if not config._LABEL.match(name):
        raise KiwiError("%r is not a machine name: lowercase letters, digits and dashes" % name)
    lines = ["  %s:" % name, "    role: %s" % role]
    if target:
        lines.append("    target: %s" % target)
    if role == "master":
        lines += ["" if target else "    target: debian", "    master:",
                  "      modules: [vpn-server, dns, tor, reverse-proxy]",
                  "      vpn-server: { wg_host: %s }" % (address or "<its public address or name>")]
    elif role == "node" and off:
        lines += ["    node:", "      preset: %s" % (preset or "minimal"), "      vpn: false"]
        if network:         # the caller writes its own
            lines += ["    network:", "      dhcp: false", '      address: ""        # its fixed address at home, like 192.168.1.31/24']
    elif role == "node":
        domain = (data.get("defaults") or {}).get("domain") or "home"
        lines += ["    node:", "      preset: %s" % (preset or "minimal"),
                  "      vpn-client: { wireguard_config: secrets/%s.%s.conf }" % (name, domain)]
    block = "\n".join(x for x in lines if x) + "\n"
    last_top = [line for line in text.splitlines() if line and not line[0].isspace() and not line.startswith("#")]
    if last_top and last_top[-1].startswith("hosts:") and last_top[-1].strip() != "hosts: {}":
        util.write_text(path, text.rstrip("\n") + "\n" + block, 0o600)
    else:
        raise KiwiError("hosts: is not the fleet file's last section, so the block cannot be added without losing "
                        "its comments; add it under hosts: by hand:\n" + block)


def _run(argv, timeout=10):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout if r.returncode == 0 else ""


def free_addresses(subnet, gateway="", want=6, run=_run):
    """Addresses at home that do not answer right now, so they look free: .30 and up in the
    router's own /24, which routers rarely hand out. Each is pinged once, all at the same
    time, and the neighbour table is asked as well (a machine may not answer a ping)."""
    import threading
    cands = [a for a in (suggest(subnet, n, gateway) for n in range(want * 3)) if a]
    taken = set()

    def look(addr):
        ip = addr.split("/")[0]
        if run(["ping", "-c", "1", "-W", "1", ip], 3):
            taken.add(addr)
            return
        seen = run(["ip", "neigh", "show", ip])
        if seen.strip() and "FAILED" not in seen and "INCOMPLETE" not in seen:
            taken.add(addr)
    threads = [threading.Thread(target=look, args=(a,), daemon=True) for a in cands]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    return [a for a in cands if a not in taken][:want]


def _from_proc(table="/proc/net/route"):
    """((address, prefix length) or None, gateway) of the interface the default route leaves by,
    from the kernel's own table: for where the `ip` command is not installed."""
    try:
        with open(table) as fh:
            rows = [x.split() for x in fh.read().splitlines()[1:]]
        dev, gw = next((r[0], r[2]) for r in rows if len(r) > 7 and r[1] == "00000000" and int(r[3], 16) & 2)
        gateway = socket.inet_ntoa(struct.pack("<L", int(gw, 16)))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            name = struct.pack("256s", dev[:15].encode())
            address = socket.inet_ntoa(fcntl.ioctl(sock.fileno(), 0x8915, name)[20:24])        # SIOCGIFADDR
            mask = socket.inet_ntoa(fcntl.ioctl(sock.fileno(), 0x891b, name)[20:24])           # SIOCGIFNETMASK
        return (address, ipaddress.ip_network("0.0.0.0/%s" % mask).prefixlen), gateway
    except (OSError, StopIteration, ValueError, struct.error):
        return None, ""


def detect(probe=True):
    """What this computer knows that a new fleet needs: its LAN with addresses that look free,
    time zone, keyboard, language, SSH key."""
    found = {"subnet": "", "gateway": "", "timezone": "UTC", "keyboard": "us", "locale": "en_US.UTF-8",
             "ssh_key": "", "addresses": []}
    try:
        routes = json.loads(_run(["ip", "-j", "route", "show", "default"]) or "[]")
    except ValueError:
        routes = []
    own = None
    if routes:
        found["gateway"] = routes[0].get("gateway", "")
        dev = routes[0].get("dev", "")
        try:
            addrs = json.loads(_run(["ip", "-j", "-4", "addr", "show", "dev", dev]) or "[]")
        except ValueError:
            addrs = []
        for a in (addrs[0].get("addr_info", []) if addrs else []):
            if a.get("family") == "inet":
                own = (a["local"], a["prefixlen"])
                break
    elif os.environ.get("KIWI_STARTER") == "1":
        # the control image has no `ip`, and on a starter its container has the machine's own network
        # (in any other container this would find the container's, not the home's)
        own, found["gateway"] = _from_proc()
    if own:
        found["subnet"] = str(ipaddress.ip_interface("%s/%s" % own).network)
        if os.environ.get("KIWI_STARTER") == "1":
            # on a machine the starter stick installed, the network's first node is this machine
            found["this"] = own[0]
    link = os.path.realpath("/etc/localtime")
    if "zoneinfo/" in link:
        found["timezone"] = link.split("zoneinfo/", 1)[1]
    status = _run(["localectl", "status"])
    m = re.search(r"X11 Layout:\s*([a-z][a-z0-9_-]*)", status) or re.search(r"VC Keymap:\s*([a-z]+)", status)
    if m:
        found["keyboard"] = m.group(1)
    lang = os.environ.get("LC_ALL") or os.environ.get("LANG") or ""
    if re.fullmatch(r"[a-z]{2,3}_[A-Z]{2}\.(UTF-8|utf8)", lang):
        found["locale"] = lang.replace("utf8", "UTF-8")
    for name in ("id_ed25519.pub", "id_ecdsa.pub", "id_rsa.pub"):
        p = os.path.expanduser("~/.ssh/" + name)
        if os.path.isfile(p):
            found["ssh_key"] = p
            break
    if probe and found["subnet"]:
        found["addresses"] = free_addresses(found["subnet"], found["gateway"])
    return found


def suggest(subnet, n, gateway=""):
    """A fixed address to suggest for the n-th machine: .30 and up in the router's own /24,
    which routers rarely hand out."""
    try:
        net = ipaddress.ip_network(subnet)
        near = ipaddress.ip_network("%s/24" % gateway, strict=False) if gateway and net.prefixlen < 24 else net
    except ValueError:
        return ""
    addr = ipaddress.ip_address(int(near.network_address) + (30 if near.prefixlen <= 24 else 2) + n)
    return "%s/%d" % (addr, net.prefixlen) if addr in net else ""


def questions(found=None):
    """The steps of a new fleet: what the terminal asks and what the app shows. A question
    marked `advanced` has an answer that works; it is asked only on request (`init --all`,
    the app's More settings). `check()` says what is wrong with an answer."""
    found = found or detect()
    have_lan = bool(found["subnet"] and found["gateway"])
    keys = [{"value": "generate", "label": "Make a new key for this network (recommended)"}]
    if found["ssh_key"]:
        keys.append({"value": found["ssh_key"], "label": "My own key, %s" % found["ssh_key"].replace(os.path.expanduser("~"), "~", 1)})
    lan = ("your home network %s behind the router %s" % (found["subnet"], found["gateway"]) if have_lan
           else "no home network (this computer has no connection right now)")
    return [
        {"step": "Before you start", "id": "start",
         "intro": "Kiwi Server builds your own private network: your files, photos and passwords on machines of your "
                  "own, reachable from anywhere through your own VPN. You need three things.",
         "notes": ["A master: the one machine that is reachable from the Internet and that everything connects to. A "
                   "small rented server for a few euros a month is the usual choice. None of your data is kept on it. "
                   "It can come later: until then your services open at home only.",
                   "A machine at home that stays on, for your files: a mini PC or an old laptop. It can come later.",
                   "A USB stick (4 GB or more) for each machine at home, and about an hour.",
                   "Found on this computer: time zone %s, keyboard %s, %s."
                   % (found["timezone"], found["keyboard"], lan)],
         "questions": [
            {"id": "timezone", "label": "Time zone", "default": found["timezone"], "advanced": True},
            {"id": "keyboard", "label": "Keyboard of the machines", "default": found["keyboard"], "advanced": True,
             "help": "de, us, fr …: it matters when you type on one of them."},
            {"id": "locale", "label": "Language of the machines", "default": found["locale"], "advanced": True,
             "help": "Like de_DE.UTF-8 or en_US.UTF-8."},
            {"id": "domain", "label": "Name ending", "default": "home", "advanced": True,
             "help": "Your machines are called <name>.home. Keep home unless you know you need another; internal, corp "
                     "and mail are the other safe ones (not local)."},
            {"id": "admin_key", "label": "Key to your machines", "type": "choice", "options": keys, "default": "generate",
             "advanced": True,
             "help": "This computer reaches your machines with a key file instead of a password. One of the network's "
                     "own travels with it when you later use another computer."},
            {"id": "lan_subnet", "label": "Your home network", "default": found["subnet"], "advanced": have_lan,
             "help": "Like 192.168.1.0/24. Your router's settings page shows it."},
            {"id": "lan_gateway", "label": "Your router's address", "default": found["gateway"], "advanced": have_lan,
             "help": "Like 192.168.1.1."}]},
        {"step": "The master", "id": "master",
         "intro": "The master is the machine in the middle. It only passes your connections on. Where does it run?",
         "questions": [
            {"id": "master_where", "label": "Where it runs", "type": "choice", "default": next(iter(providers.PROVIDERS)),
             "options": [{"value": p.id, "label": "A rented server at %s (Kiwi Server installs it for you)" % p.name}
                         for p in providers.PROVIDERS.values()] + [
                {"value": "vps", "label": "A rented server somewhere else (you start it from the install media)"},
                {"value": "home", "label": "A machine at home (your router must pass a port on to it)"},
                {"value": "none", "label": "No master yet: my services open at home only (a master can come later)"}]},
            {"id": "provider_server", "label": "Your server there", "type": "lookup", "lookup": "master_where",
             "when": "master_where in %s" % ",".join(providers.PROVIDERS),
             "names": {p.id: p.name for p in providers.PROVIDERS.values()},
             "help": "Sign in at the hoster in your browser. Kiwi Server then lists your servers and takes the address "
                     "of the one you pick; nothing is changed there yet. Without it, type the address below."},
            {"id": "master_address", "label": "Its address on the Internet", "required": True, "when": "master_where in %s" % ",".join(list(providers.PROVIDERS) + ["vps", "home"]),
             "help": "A rented server: the IPv4 address your hoster shows for it, like 203.0.113.7. At home: a name "
                     "from a dynamic DNS service that points at your router, like myhome.example.org. Your phones and "
                     "machines connect to it."},
            {"id": "exit", "label": "How your devices reach the Internet on the VPN", "type": "choice", "default": "direct", "when": "master_where in %s" % ",".join(list(providers.PROVIDERS) + ["vps", "home"]),
             "options": [{"value": "direct", "label": "Through the master's own connection (simplest)"}] + [
                {"value": k, "label": ("Through " + v[0] + " (more private; needs an account there)") if k != "custom" else
                 "Through another VPN provider (its WireGuard config)"} for k, v in EXITS.items()]},
            {"id": "exit_key", "label": "WireGuard private key", "secret": True, "required": True, "when": "exit in mullvad,protonvpn,nordvpn,surfshark,airvpn,windscribe,ivpn,fastestvpn,custom",
             "help": "The PrivateKey line of a WireGuard config from your provider: Mullvad at mullvad.net, Proton at "
                     "account.proton.me, NordVPN in your Nord Account, the others on their account's WireGuard or manual "
                     "setup page. It is kept encrypted with your other keys."},
            {"id": "exit_address", "label": "Tunnel address", "required": True, "when": "exit in mullvad,surfshark,airvpn,windscribe,ivpn,fastestvpn,custom",
             "help": "The Address line of the same config, its IPv4 part, like 10.66.1.2/32."},
            {"id": "exit_psk", "label": "Preshared key", "secret": True, "when": "exit in airvpn,windscribe,custom",
             "help": "The PresharedKey line of the same config. AirVPN and Windscribe have one; another provider's config may."},
            {"id": "exit_peer", "label": "Server public key", "required": True, "when": "exit == 'custom'",
             "help": "The PublicKey line under [Peer] in the same config."},
            {"id": "exit_endpoint", "label": "Server address and port", "required": True, "when": "exit == 'custom'",
             "help": "The Endpoint line under [Peer], like 185.65.135.1:51820. An address, not a name."},
            {"id": "exit_country", "label": "Exit country", "default": "Germany", "when": "exit in mullvad,protonvpn,nordvpn,surfshark,airvpn,windscribe,ivpn,fastestvpn"},
            {"id": "master_name", "label": "Its name", "default": random.choice(MASTERS), "advanced": True, "when": "master_where in %s" % ",".join(list(providers.PROVIDERS) + ["vps", "home"])},
            {"id": "master_disk", "label": "Its disk", "default": "auto", "advanced": True, "when": "master_where in %s" % ",".join(list(providers.PROVIDERS) + ["vps", "home"]),
             "help": "auto: the install finds the machine's one disk. A device like /dev/vda when it has several. "
                     "Everything on it is erased."}]},
        {"step": "At home", "id": "home",
         "intro": ("The machines at your place. This machine is the first: it keeps your files. More can come later."
                   if found.get("this") else
                   "The machines at your place. Add the one that will keep your files; more can come later."),
         "questions": [
            {"id": "nodes", "label": "Machines at home", "type": "machines", "default": [],
             "add": "Add a machine at home",
             "none": "Without a machine at home you get the VPN and the ad blocker, and no place for your files yet.",
             "fields": [{"id": "preset", "label": "What it does", "type": "choice", "default": "cloud",
                         "options": [{"value": k, "label": v} for k, v in PRESETS.items()]},
                        {"id": "address", "label": "Its address at home", "default_from": "suggest",
                         "help": "A fixed address in your home network. The one suggested does not answer right now, "
                                 "so it looks free. Empty: your router gives it one."},
                        {"id": "name", "label": "Name", "default_from": "fruits", "advanced": True},
                        {"id": "target", "label": "System", "type": "choice", "default": "ucore", "advanced": True,
                         "options": [{"value": "ucore", "label": "uCore (updates itself, rolls back)"},
                                     {"value": "debian", "label": "Debian 13"}]},
                        {"id": "disk", "label": "Its disk", "default": "auto", "advanced": True,
                         "help": "auto: the install finds the machine's one disk. A device like /dev/nvme0n1 when it has "
                                 "several. Everything on it is erased."},
                        {"id": "download_dir", "label": "Where downloads go", "default": "/home/user/downloads",
                         "when": "preset == 'gateway'", "advanced": True}]}]},
        {"step": "Your keys", "id": "keys",
         "intro": "Kiwi Server now makes every key and password of your network and keeps them encrypted on this "
                  "computer. You get a recovery key, shown once: write it down. It is the way back in if this "
                  "computer is lost.",
         "notes": ["After that you get a list of steps, one machine after the other: the master first (if there is one), "
                   "then the machines at home, then your phone."],
         "questions": []},
    ]


# addresses that only exist inside a home or a provider's network: no master can be reached there
_AT_HOME = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")


def _name_ok(n):
    return bool(config._LABEL.match(n or ""))


def master_problem(addr):
    """What is wrong with a master's public address or name, in a sentence, or ""."""
    addr = str(addr or "").strip()
    if not addr:
        return "The master's address is needed: it is what your phones and machines connect to."
    try:
        ip = ipaddress.ip_address(addr)
        if any(ip in ipaddress.ip_network(n) for n in _AT_HOME if ip.version == 4) or ip.is_loopback or ip.is_link_local:
            return ("%s is an address inside a home network: nothing on the Internet can reach it. "
                    "Give the address your hoster shows, or a dynamic DNS name for a master at home." % addr)
    except ValueError:
        if not re.fullmatch(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", addr.lower()):
            return "That is neither an address like 203.0.113.7 nor a name like vpn.example.org."
    return ""


def check(a):
    """{question id: what is wrong, in a sentence} for these answers; a machine's fields are
    `nodes.<number>.<field>`. Empty when all is well."""
    bad = {}
    d = str(a.get("domain") or "home").strip(".").lower()
    if not d or not all(config._LABEL.match(x) for x in d.split(".")):
        bad["domain"] = "The name ending is made of letters, digits and dashes, like home or my.corp."
    else:
        adv = config.domain_advice(d)
        if adv and adv[0] == "error":
            bad["domain"] = adv[1]
    addr = str(a.get("master_address") or "").strip()
    if a.get("master_where") == "none":     # a network without a master (yet): docs/design/no-master.md
        if not [n for n in a.get("nodes") or [] if n.get("address")]:
            bad["nodes"] = ("Without a master, the network is the machines at home: add one, with its address. "
                            "Its services answer there.")
        for i, n in enumerate(a.get("nodes") or []):
            if not n.get("address"):
                bad["nodes.%d.address" % i] = "Without a master, each machine needs its fixed address at home."
            if n.get("preset") == "gateway":
                bad["nodes.%d.preset" % i] = ("A gateway leads your home into the VPN and out through it: it comes "
                                              "with a master. Take cloud for now.")
    elif master_problem(addr):
        bad["master_address"] = master_problem(addr)
    if a.get("exit") in EXITS:
        _exit_answers(a)
        name, needs_addr, needs_psk, where = EXITS[a["exit"]]
        if not a.get("exit_key"):
            bad["exit_key"] = "The PrivateKey line of a WireGuard config is needed: %s." % where
        if needs_addr:
            try:
                ipaddress.ip_interface(str(a.get("exit_address") or ""))
            except ValueError:
                bad["exit_address"] = "The Address line of the same config is needed, like 10.66.1.2/32."
        if needs_psk and not a.get("exit_psk"):
            bad["exit_psk"] = "%s needs the PresharedKey line of the same config." % name
        if a["exit"] == "custom":
            if not re.fullmatch(r"[A-Za-z0-9+/]{42,43}=?", str(a.get("exit_peer") or "").strip()):
                bad["exit_peer"] = "The PublicKey line under [Peer] is needed: 44 letters and digits ending in =."
            host, _, port = str(a.get("exit_endpoint") or "").strip().rpartition(":")
            try:
                ipaddress.ip_address(host.strip("[]"))
                if not 0 < int(port) < 65536:
                    raise ValueError
            except ValueError:
                bad["exit_endpoint"] = "The Endpoint line under [Peer] is needed as an address and port, like 185.65.135.1:51820."
    names = [a.get("master_name") or ""]
    if not _name_ok(names[0]) and a.get("master_where") != "none":
        bad["master_name"] = "A name is made of lowercase letters, digits and dashes."
    net = None
    try:
        net = ipaddress.ip_network(str(a.get("lan_subnet") or ""))
    except ValueError:
        if a.get("nodes"):
            bad["lan_subnet"] = "Your home network is needed for a machine at home, like 192.168.1.0/24."
    if a.get("lan_gateway"):
        try:
            ipaddress.ip_address(str(a["lan_gateway"]))
        except ValueError:
            bad["lan_gateway"] = "Your router's address looks like 192.168.1.1."
    seen = set()
    for i, n in enumerate(a.get("nodes") or []):
        name = n.get("name") or ""
        if not _name_ok(name):
            bad["nodes.%d.name" % i] = "A name is made of lowercase letters, digits and dashes."
        elif name in names:
            bad["nodes.%d.name" % i] = "Two machines are called %s." % name
        names.append(name)
        if n.get("preset", "cloud") not in PRESETS:
            bad["nodes.%d.preset" % i] = "%s is not one of %s." % (n.get("preset"), ", ".join(PRESETS))
        if n.get("address"):
            try:
                iface = ipaddress.ip_interface(str(n["address"]) if "/" in str(n["address"]) or net is None
                                               else "%s/%d" % (n["address"], net.prefixlen))
                if net is not None and iface.ip not in net:
                    bad["nodes.%d.address" % i] = "%s is not in your home network %s." % (iface.ip, net)
                elif str(iface.ip) == str(a.get("lan_gateway") or ""):
                    bad["nodes.%d.address" % i] = "%s is your router's address." % iface.ip
                elif str(iface.ip) in seen:
                    bad["nodes.%d.address" % i] = "Two machines have the address %s." % iface.ip
                seen.add(str(iface.ip))
            except ValueError:
                bad["nodes.%d.address" % i] = "An address looks like 192.168.1.30."
        for key in ("disk",):
            v = str(n.get(key) or "auto")
            if v != "auto" and not v.startswith("/dev/"):
                bad["nodes.%d.%s" % (i, key)] = "auto, or a device like /dev/nvme0n1."
    v = str(a.get("master_disk") or "auto")
    if v != "auto" and not v.startswith("/dev/"):
        bad["master_disk"] = "auto, or a device like /dev/vda."
    if a.get("this_address") and [str(n.get("address") or "").split("/")[0] for n in a.get("nodes") or []].count(
            str(a["this_address"])) != 1:
        bad["nodes"] = "This machine is the first one at home, at %s: it cannot be left out." % a["this_address"]
    return bad


def _answers_valid(a):
    bad = check(a)
    if bad:
        raise KiwiError(next(iter(bad.values())))
    return str(a.get("domain") or "home").strip(".").lower()


def preset_modules(preset):
    """The parts a preset of the node role stands for."""
    from . import roles as rolesmod
    with open(os.path.join(rolesmod.roles_dir(), "node", "role.yaml")) as fh:
        return list(yaml.safe_load(fh)["presets"][preset]["modules"])


def make(answers, fleet_dir):
    """The fleet file's data for these answers, and what was made beside it. With `this_address`
    (a machine the starter stick installed), the node at that address is the machine this runs
    on: it is not installed again and carries the control part."""
    a = dict(answers)
    a.setdefault("master_name", random.choice(MASTERS))
    domain = _answers_valid(a)
    made = []
    if a.get("admin_key", "generate") == "generate":
        key = os.path.join(fleet_dir, "secrets", "admin_ed25519")
        if not os.path.isfile(key):
            os.makedirs(os.path.dirname(key), mode=0o700, exist_ok=True)
            r = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "kiwi-%s" % domain, "-f", key],
                               capture_output=True, text=True)
            if r.returncode != 0:
                raise KiwiError("ssh-keygen: %s" % r.stderr.strip()[:200])
            made.append(key)
        ssh_key = "secrets/admin_ed25519.pub"
    else:
        ssh_key = a["admin_key"]
    defaults = {
        "target": "ucore", "role": "bare", "domain": domain,
        "timezone": a.get("timezone") or "UTC", "locale": a.get("locale") or "en_US.UTF-8", "keyboard": a.get("keyboard") or "us",
        "admin": {"user": "core", "ssh_keys": [ssh_key], "groups": ["docker"], "ssh_password_auth": False},
        "network": {"dhcp": True},
        "updates": {"enabled": True, "days": ["Sun"], "time": "03:30", "length_minutes": 90},
        "install": {"on_done": "poweroff"},
        "tls": {"auto": True, "ca_dir": "secrets/ca", "name_constraints": [domain]},
    }
    m = a["master_name"]
    addr = str(a.get("master_address") or "").strip()
    master = {"modules": ["vpn-server", "dns", "tor", "reverse-proxy"], "dns": {"mullvad_socks": False},
              "vpn-server": {"wg_host": addr}}
    if a.get("exit") in EXITS:
        _exit_answers(a)
        name, needs_addr, _psk, _where = EXITS[a["exit"]]
        vc = {"vpn_provider": a["exit"], "wireguard_private_key": str(a["exit_key"]).strip()}
        if needs_addr:
            vc["wireguard_addresses"] = str(a["exit_address"]).strip()
        if a.get("exit_psk"):
            vc["wireguard_preshared_key"] = str(a["exit_psk"]).strip()
        if a["exit"] == "custom":
            host, _, port = str(a["exit_endpoint"]).strip().rpartition(":")
            vc.update(wireguard_public_key=str(a["exit_peer"]).strip(), wireguard_endpoint_ip=host.strip("[]"),
                      vpn_endpoint_port=int(port))
        else:   # Windscribe sorts its servers by region, which are countries there
            vc["server_regions" if a["exit"] == "windscribe" else "server_countries"] = a.get("exit_country") or "Germany"
        master = {"vpn-client": vc, "vpn-server": {"wg_host": addr}}
    alone = a.get("master_where") == "none"
    if a.get("master_where") == "home":     # its own connection is the home line: Tor only through a VPN exit there
        master["at_home"] = True
    hosts = {} if alone else {m: {"role": "master", "target": "debian", "disk": a.get("master_disk") or "auto", "master": master}}
    if a.get("master_where") in providers.PROVIDERS:
        hosts[m]["vps"] = {"provider": a["master_where"]}
        if a.get("provider_server"):    # picked from the hoster's list: the install knows which one
            hosts[m]["vps"]["server"] = a["provider_server"]
    try:
        prefix = ipaddress.ip_network(str(a.get("lan_subnet") or "")).prefixlen
    except ValueError:
        prefix = 24
    for n in a.get("nodes") or []:
        name = n["name"]
        node = {"preset": n.get("preset") or "cloud",
                "vpn-client": {"wireguard_config": "secrets/%s.%s.conf" % (name, domain)}}
        if alone:       # no VPN: its home address stands in, and the first machine answers for the names
            del node["vpn-client"]
            node["vpn"] = False
            if not [x for x in hosts.values() if "dns" in x["node"].get("modules", [])]:
                node["modules"] = ["dns"] + [x for x in preset_modules(node["preset"]) if x not in ("vpn-client", "dns")]
        if node["preset"] == "gateway":
            node["downloader"] = {"download_dir": n.get("download_dir") or "/home/user/downloads"}
        h = {"role": "node", "target": n.get("target") or "ucore", "disk": n.get("disk") or "auto", "node": node}
        if n.get("address"):
            address = str(n["address"]) if "/" in str(n["address"]) else "%s/%d" % (n["address"], prefix)
            h["network"] = {"dhcp": False, "address": address}
            if a.get("lan_gateway"):
                h["network"]["gateway"] = a["lan_gateway"]
            if a.get("this_address") and address.split("/")[0] == str(a["this_address"]):
                # the starter's system as it is, its address as the router gave it, and the control part:
                # `modules` replaces the preset's list, so it is that list and one more
                h["target"] = "coreos"
                del h["disk"]
                node["modules"] = (node.get("modules") or [x for x in preset_modules(node["preset"])
                                                          if not (alone and x == "vpn-client")]) + ["control"]
                if a.get("lan_gateway"):
                    h["network"]["dns"] = [a["lan_gateway"]]
        hosts[name] = h
    return {"defaults": defaults, "hosts": hosts}, made


def write(path, data):
    head = ("# A Kiwi Network fleet, made by `kiwi-server init`. Every machine is a host below;\n"
            "# what a host does not set comes from defaults. kiwi-server validate checks it,\n"
            "# kiwi-server list says what each machine needs next. Docs: README.md, docs/modules.md\n")
    import yaml
    util.write_text(path, head + yaml.safe_dump(data, sort_keys=False, default_flow_style=False), 0o600)


def next_steps(fleet_path):
    """What to say after the wizard: one command does the steps, one after the other."""
    return ["Your network is described. Now it gets built, one machine after the other:",
            "",
            "  kiwi-server next %s" % fleet_path,
            "",
            "It does the next step and says what it did. Run it again until it says everything runs.",
            "In the Kiwi Server app the same steps are buttons."]


def _wrap(text, indent=""):
    import textwrap
    return "\n".join(textwrap.wrap(text, 78, initial_indent=indent, subsequent_indent=indent))


# The exits a master can take: every VPN provider gluetun runs with WireGuard, and any other
# provider's WireGuard config ("custom"). (name, needs the Address line, needs a PresharedKey, where the config is made)
EXITS = {
    "mullvad": ("Mullvad", True, False, "mullvad.net: Account, WireGuard configuration"),
    "protonvpn": ("Proton VPN", False, False, "account.proton.me: VPN, WireGuard configuration"),
    "nordvpn": ("NordVPN", False, False, "your Nord Account: NordVPN, Manual setup"),
    "surfshark": ("Surfshark", True, False, "your Surfshark account: VPN, Manual setup, WireGuard"),
    "airvpn": ("AirVPN", True, True, "the AirVPN Client Area: Config Generator"),
    "windscribe": ("Windscribe", True, True, "windscribe.com: Config Generator, WireGuard"),
    "ivpn": ("IVPN", True, False, "ivpn.net: Account, WireGuard"),
    "fastestvpn": ("FastestVPN", True, False, "a WireGuard config FastestVPN's support sends you"),
    "custom": ("Another provider", True, False, "your provider's website: a WireGuard config file"),
}


def _exit_answers(a):
    """The answers of an earlier release (exit: mullvad, mullvad_key, …) as today's."""
    for old, new in (("mullvad_key", "exit_key"), ("mullvad_address", "exit_address"), ("mullvad_country", "exit_country")):
        if a.get(old) and not a.get(new):
            a[new] = a[old]
    return a


def _when(q, values):
    """Is this question asked? `key == 'value'`, or `key in a,b,c`."""
    w = q.get("when")
    if not w:
        return True
    if " in " in w:
        key, _, vals = w.partition(" in ")
        return str(values.get(key)) in vals.split(",")
    key, _, val = w.partition(" == ")
    return str(values.get(key)) == val.strip("'")


def ask(found=None, read=input, show=print, everything=False, lookup=None):
    """The questions in a terminal: Enter takes what is in brackets, a wrong answer is asked
    again. `everything` also asks what has an answer that works (the advanced ones).
    `lookup(provider, show)` looks a hoster account's servers up instead of asking for an
    address: it returns (rows of {id, address, label}, where the login's token is)."""
    found = found or detect()
    answers = {}

    def one(q, values, key, problems):
        """Ask until the answer passes. `problems()` is check() for the answers as they would be."""
        kind = q.get("type", "string")
        if q.get("help"):
            show(_wrap(q["help"], "   "))
        while True:
            if kind == "choice":
                opts = q["options"]
                for n, o in enumerate(opts, 1):
                    show("   %d) %s" % (n, o["label"]))
                default = next((n for n, o in enumerate(opts, 1) if o["value"] == q.get("default")), 1)
                got = read("%s [%d]: " % (q["label"], default)).strip() or str(default)
                if not got.isdigit() or not 1 <= int(got) <= len(opts):
                    show("   A number from 1 to %d, please." % len(opts))
                    continue
                value = opts[int(got) - 1]["value"]
            elif kind == "bool":
                got = read("%s [%s]: " % (q["label"], "Y/n" if q.get("default") else "y/N")).strip().lower()
                value = q.get("default", False) if not got else got.startswith(("y", "j"))
            else:
                prompt = "%s%s: " % (q["label"], " [%s]" % q["default"] if q.get("default") else "")
                got = (__import__("getpass").getpass(prompt) if q.get("secret") else read(prompt)).strip()
                value = "" if got == "-" else (got or q.get("default", ""))
                if q.get("required") and not value:
                    show("   This one is needed.")
                    continue
            values[q["id"]] = value
            msg = problems().get(key)
            if not msg:
                return value
            show(_wrap(msg, "   "))

    for step in questions(found):
        show("\n== %s ==" % step["step"])
        show(_wrap(step["intro"]))
        for note in step.get("notes") or []:
            show(_wrap(note, "  - ").replace("\n  - ", "\n    "))
        for q in step["questions"]:
            if not _when(q, answers):
                continue
            kind = q.get("type", "string")
            if kind == "machines":
                nodes = answers[q["id"]] = []
                taken = {answers.get("master_name")}
                while (read("\n%s? [%s]: " % (q.get("add", "Add a machine"), "Y/n" if not nodes else "y/N")).strip().lower()
                       or ("y" if not nodes else "n")).startswith(("y", "j")):
                    node = {}
                    nodes.append(node)
                    free = [x for x in found.get("addresses") or [] if x not in [m.get("address") for m in nodes]]
                    for f in q["fields"]:
                        f = dict(f)
                        if f.get("default_from") == "fruits":
                            f["default"] = next((x for x in NODES if x not in taken), "node%d" % len(nodes))
                        elif f.get("default_from") == "suggest":
                            f["default"] = free[0] if free else ""
                            f["label"] += " (- for none)" if f["default"] else ""
                        if not _when(f, node):
                            continue
                        if f.get("advanced") and not everything:
                            node[f["id"]] = f.get("default", "")
                            continue
                        one(f, node, "nodes.%d.%s" % (len(nodes) - 1, f["id"]), lambda: check(answers))
                    taken.add(node["name"])
                    show("   %s: %s%s" % (node["name"], PRESETS.get(node.get("preset"), node.get("preset")),
                                          ", at %s" % node["address"] if node.get("address") else ""))
                if not nodes and q.get("none"):
                    show(_wrap(q["none"], "   "))
                continue
            if kind == "lookup":
                if lookup is None:
                    continue
                if q.get("help"):
                    show(_wrap(q["help"], "   "))
                who = providers.get(answers[q["lookup"]])
                if not (read("Sign in to %s now and pick your server from a list? [Y/n]: " % who.name).strip().lower()
                        or "y").startswith(("y", "j")):
                    continue
                try:
                    rows, token = lookup(who.id, show)
                except KiwiError as e:
                    show(_wrap("That did not work (%s). Type the address instead." % e, "   "))
                    continue
                if not rows:
                    show("   %s lists no server for this account. Type the address instead." % who.name)
                    continue
                pick = 0
                if len(rows) > 1:
                    for n, r in enumerate(rows, 1):
                        show("   %d) %s" % (n, r["label"]))
                    while True:
                        got = read("Which one becomes the master [1]: ").strip() or "1"
                        if got.isdigit() and 1 <= int(got) <= len(rows):
                            pick = int(got) - 1
                            break
                        show("   A number from 1 to %d, please." % len(rows))
                answers.update({q["id"]: rows[pick]["id"], "master_address": rows[pick]["address"], "provider_token": token})
                show("   %s: its address is %s" % (rows[pick]["label"], rows[pick]["address"]))
                continue
            if q["id"] in answers:              # a lookup gave the answer already
                continue
            if (q.get("advanced") and not everything) or (kind == "choice" and len(q["options"]) == 1):
                answers[q["id"]] = q["options"][0]["value"] if kind == "choice" and len(q["options"]) == 1 else q.get("default", "")
                continue
            one(q, answers, q["id"], lambda: check(dict(answers, nodes=[])))
    return answers
