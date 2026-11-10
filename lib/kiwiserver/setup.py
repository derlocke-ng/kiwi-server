"""Starting a fleet: the questions `kiwi-server init` asks, in a terminal or through the app,
and the fleet file the answers make.

A fleet made here validates as far as it can before anything is installed: its admin key
exists, nothing points at a placeholder. What cannot exist yet (a node's WireGuard config,
which its master issues once it runs) is a next step, not an error. The app asks the same
questions: `init --questions` prints them as JSON with what this computer already knows
filled in, and `init --answers FILE` takes them back.
"""
import ipaddress
import json
import os
import random
import re
import subprocess

from . import config, util
from .util import KiwiError

# design/control-plane.md, Names: fruit dishes for masters, fruits for nodes (DNS-safe, no "kiwi")
MASTERS = ["sorbet", "crumble", "trifle", "parfait", "compote", "pavlova", "strudel", "cobbler",
           "sundae", "clafoutis", "galette", "tartlet", "fool", "jelly", "mousse"]
NODES = ["papaya", "lychee", "mango", "quince", "guava", "damson", "medlar", "loquat", "feijoa",
         "rambutan", "longan", "pomelo", "tamarind", "salak", "jujube", "cherimoya"]
PRESETS = {
    "gateway": "the home's DNS and the route into the mesh, a download station",
    "cloud": "Nextcloud and Vaultwarden",
    "minimal": "only the VPN client and the reverse proxy; add modules later",
}


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


def add_host(path, name, role, preset="", target=""):
    """A new host block at the end of the fleet file, written as text so its comments stay."""
    text = util.read_text(path)
    import yaml
    data = yaml.safe_load(text) or {}
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
                  "      vpn-server: { wg_host: <its public address or name> }"]
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


def _run(argv):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout if r.returncode == 0 else ""


def detect():
    """What this computer knows that a new fleet needs: its LAN, time zone, keyboard, SSH key."""
    found = {"subnet": "", "gateway": "", "timezone": "UTC", "keyboard": "us", "ssh_key": ""}
    try:
        routes = json.loads(_run(["ip", "-j", "route", "show", "default"]) or "[]")
    except ValueError:
        routes = []
    if routes:
        found["gateway"] = routes[0].get("gateway", "")
        dev = routes[0].get("dev", "")
        try:
            addrs = json.loads(_run(["ip", "-j", "-4", "addr", "show", "dev", dev]) or "[]")
        except ValueError:
            addrs = []
        for a in (addrs[0].get("addr_info", []) if addrs else []):
            if a.get("family") == "inet":
                found["subnet"] = str(ipaddress.ip_interface("%s/%s" % (a["local"], a["prefixlen"])).network)
                break
    link = os.path.realpath("/etc/localtime")
    if "zoneinfo/" in link:
        found["timezone"] = link.split("zoneinfo/", 1)[1]
    status = _run(["localectl", "status"])
    m = re.search(r"X11 Layout:\s*([a-z][a-z0-9_-]*)", status) or re.search(r"VC Keymap:\s*([a-z]+)", status)
    if m:
        found["keyboard"] = m.group(1)
    for name in ("id_ed25519.pub", "id_ecdsa.pub", "id_rsa.pub"):
        p = os.path.expanduser("~/.ssh/" + name)
        if os.path.isfile(p):
            found["ssh_key"] = p
            break
    return found


def suggest(subnet, n, gateway=""):
    """A fixed address to suggest for the n-th machine: .30 and up in the router's own /24,
    which routers rarely hand out. The person checks it is free."""
    try:
        net = ipaddress.ip_network(subnet)
        near = ipaddress.ip_network("%s/24" % gateway, strict=False) if gateway and net.prefixlen < 24 else net
    except ValueError:
        return ""
    addr = ipaddress.ip_address(int(near.network_address) + (30 if near.prefixlen <= 24 else 2) + n)
    return "%s/%d" % (addr, net.prefixlen) if addr in net else ""


def questions(found=None):
    """The steps of a new fleet, each a list of questions with what this computer suggests."""
    found = found or detect()
    keys = [{"value": "generate", "label": "A new key for this fleet, kept with its secrets (recommended)"}]
    if found["ssh_key"]:
        keys.append({"value": found["ssh_key"], "label": "My own key, %s" % found["ssh_key"].replace(os.path.expanduser("~"), "~", 1)})
    return [
        {"step": "The fleet", "intro": "Names, time and keyboard of every machine in it.", "questions": [
            {"id": "domain", "label": "Domain", "default": "home",
             "help": "Machines are called <name>.<domain>, services <service>.<name>.<domain>. home can never become a "
                     "public top-level domain; internal, corp and mail are the other safe ones. Not local (that is mDNS)."},
            {"id": "timezone", "label": "Time zone", "default": found["timezone"]},
            {"id": "keyboard", "label": "Keyboard layout", "default": found["keyboard"],
             "help": "The console's layout: it matters when someone types a disk key there."}]},
        {"step": "Logging in", "intro": "How kiwi-server reaches the machines: by SSH, with a key, never a password.",
         "questions": [
            {"id": "admin_key", "label": "SSH key", "type": "choice", "options": keys, "default": "generate",
             "help": "A key of the fleet's own goes with the fleet to another computer and opens the secret store's "
                     "machines from there too."}]},
        {"step": "The master", "intro": "The machine everything connects to: on the Internet, usually a small VPS.",
         "questions": [
            {"id": "master_name", "label": "Name", "default": random.choice(MASTERS)},
            {"id": "master_where", "label": "Where it runs", "type": "choice", "default": "netcup", "options": [
                {"value": "netcup", "label": "A VPS at netcup (kiwi-server installs it through their API)"},
                {"value": "vps", "label": "A VPS elsewhere (you install Debian 13, or boot its ISO there)"},
                {"value": "home", "label": "A machine at home with a port open to the Internet"}]},
            {"id": "master_address", "label": "Its public address or name", "required": True,
             "help": "What phones, laptops and the other machines connect to, e.g. 203.0.113.7 or vpn.example.org. "
                     "netcup shows it in its control panel."},
            {"id": "master_disk", "label": "Its disk", "default": "/dev/vda",
             "help": "Wiped by the ISO. A VPS usually has /dev/vda or /dev/sda; `kiwi-server vps install` says when it is "
                     "the other one."},
            {"id": "exit", "label": "The Internet for the mesh", "type": "choice", "default": "direct", "options": [
                {"value": "direct", "label": "Through the master's own connection (nothing else needed)"},
                {"value": "mullvad", "label": "Through Mullvad (a WireGuard key from your Mullvad account)"}]},
            {"id": "mullvad_key", "label": "Mullvad private key", "secret": True, "when": "exit == 'mullvad'",
             "help": "PrivateKey from a WireGuard config made on mullvad.net. It goes into the secret store's fleet file."},
            {"id": "mullvad_address", "label": "Mullvad address", "when": "exit == 'mullvad'",
             "help": "Address from the same config, e.g. 10.66.1.2/32."},
            {"id": "mullvad_country", "label": "Exit country", "default": "Germany", "when": "exit == 'mullvad'"}]},
        {"step": "At home", "intro": "Machines on your LAN: each joins the mesh through the master.",
         "questions": [
            {"id": "lan_subnet", "label": "Your LAN", "default": found["subnet"],
             "help": "As this computer sees it. Machines with a fixed address get one in it."},
            {"id": "lan_gateway", "label": "Your router", "default": found["gateway"]},
            {"id": "nodes", "label": "Machines at home", "type": "machines", "default": [],
             "help": "Each with a name, what it does (%s), a fixed address you know is free and, for a gateway, "
                     "where downloads go; none is fine too, they can be added later."
                     % "; ".join("%s: %s" % kv for kv in PRESETS.items()),
             "fields": [{"id": "name", "label": "Name", "default_from": "fruits"},
                        {"id": "preset", "label": "What it does", "type": "choice", "default": "gateway",
                         "options": [{"value": k, "label": "%s: %s" % (k, v)} for k, v in PRESETS.items()]},
                        {"id": "target", "label": "System", "type": "choice", "default": "ucore",
                         "options": [{"value": "ucore", "label": "uCore (updates itself, rolls back)"},
                                     {"value": "debian", "label": "Debian 13"}]},
                        {"id": "address", "label": "Fixed address (empty: DHCP)", "default_from": "suggest"},
                        {"id": "disk", "label": "Its disk, wiped by the ISO", "default": "/dev/nvme0n1"},
                        {"id": "download_dir", "label": "Where downloads go", "default": "/home/user/downloads",
                         "when": "preset == 'gateway'"}]}]},
        {"step": "Secrets", "intro": "Keys, passwords and certificates live in the fleet folder's secrets/.",
         "questions": [
            {"id": "store", "label": "Keep them encrypted (recommended)", "type": "bool", "default": True,
             "help": "secrets/ becomes one encrypted file, opened into memory with your desktop's keyring. You get a "
                     "recovery key once: write it down."}]},
    ]


def _answers_valid(a):
    d = str(a.get("domain") or "home").strip(".").lower()
    if not d or not all(config._LABEL.match(x) for x in d.split(".")):
        raise KiwiError("the domain must be DNS labels: home, internal, my.corp")
    adv = config.domain_advice(d)
    if adv and adv[0] == "error":
        raise KiwiError(adv[1])
    names = [a.get("master_name") or ""] + [n.get("name", "") for n in a.get("nodes") or []]
    for n in names:
        if not config._LABEL.match(n or ""):
            raise KiwiError("%r is not a machine name: lowercase letters, digits and dashes" % n)
    if len(set(names)) != len(names):
        raise KiwiError("two machines are called the same")
    if not (a.get("master_address") or "").strip():
        raise KiwiError("the master's public address or name is needed: what phones and the machines at home connect to")
    if a.get("exit") == "mullvad" and not (a.get("mullvad_key") and a.get("mullvad_address")):
        raise KiwiError("Mullvad needs the private key and the address from a WireGuard config made on mullvad.net")
    for n in a.get("nodes") or []:
        if n.get("preset", "gateway") not in PRESETS:
            raise KiwiError("%s: %s is not one of %s" % (n.get("name"), n.get("preset"), ", ".join(PRESETS)))
        if n.get("address"):
            try:
                ipaddress.ip_interface(n["address"])
            except ValueError:
                raise KiwiError("%s: %s is not an address with its prefix, e.g. 192.168.1.30/24" % (n["name"], n["address"]))
    return d


def make(answers, fleet_dir):
    """The fleet file's data for these answers, and what was made beside it."""
    a = dict(answers)
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
        "timezone": a.get("timezone") or "UTC", "locale": "en_US.UTF-8", "keyboard": a.get("keyboard") or "us",
        "admin": {"user": "core", "ssh_keys": [ssh_key], "groups": ["docker"], "ssh_password_auth": False},
        "network": {"dhcp": True},
        "updates": {"enabled": True, "days": ["Sun"], "time": "03:30", "length_minutes": 90},
        "install": {"on_done": "poweroff"},
        "tls": {"auto": True, "ca_dir": "secrets/ca", "name_constraints": [domain]},
    }
    m = a["master_name"]
    master = {"modules": ["vpn-server", "dns", "tor", "reverse-proxy"], "dns": {"mullvad_socks": False}}
    if a.get("master_address"):
        master["vpn-server"] = {"wg_host": a["master_address"]}
    if a.get("exit") == "mullvad":
        master = {"vpn-client": {"vpn_provider": "mullvad", "wireguard_private_key": a["mullvad_key"],
                                 "wireguard_addresses": a["mullvad_address"],
                                 "server_countries": a.get("mullvad_country") or "Germany"}}
        if a.get("master_address"):
            master["vpn-server"] = {"wg_host": a["master_address"]}
    hosts = {m: {"role": "master", "target": "debian", "disk": a.get("master_disk") or "/dev/vda", "master": master}}
    for n in a.get("nodes") or []:
        name = n["name"]
        node = {"preset": n.get("preset") or "gateway",
                "vpn-client": {"wireguard_config": "secrets/%s.%s.conf" % (name, domain)}}
        if node["preset"] == "gateway":
            node["downloader"] = {"download_dir": n.get("download_dir") or "/home/user/downloads"}
        h = {"role": "node", "target": n.get("target") or "ucore", "disk": n.get("disk") or "/dev/nvme0n1", "node": node}
        if n.get("address"):
            h["network"] = {"dhcp": False, "address": n["address"]}
            if a.get("lan_gateway"):
                h["network"]["gateway"] = a["lan_gateway"]
        hosts[name] = h
    return {"defaults": defaults, "hosts": hosts}, made


def write(path, data):
    head = ("# A Kiwi Network fleet, made by `kiwi-server init`. Every machine is a host below;\n"
            "# what a host does not set comes from defaults. kiwi-server validate checks it,\n"
            "# kiwi-server list says what each machine needs next. Docs: README.md, docs/modules.md\n")
    import yaml
    util.write_text(path, head + yaml.safe_dump(data, sort_keys=False, default_flow_style=False), 0o600)


def next_steps(fleet_name, answers):
    """What to do after init, in order, as lines of text."""
    m = answers["master_name"]
    lines = ["The master, %s:" % m]
    if answers.get("master_where") == "netcup":
        lines += ["  kiwi-server vps login %s          # once: netcup's API, through your browser" % fleet_name,
                  "  kiwi-server build %s %s" % (fleet_name, m),
                  "  kiwi-server vps install %s %s    # wipes it and installs from its ISO" % (fleet_name, m)]
    elif answers.get("master_where") == "vps":
        lines += ["  kiwi-server build %s %s           # boot output/%s/%s.iso at your provider," % (fleet_name, m, m, m),
                  "                                            # or install Debian 13 with the fleet's key yourself"]
    else:
        lines += ["  kiwi-server build %s %s           # write output/%s/%s.iso to a stick, boot it" % (fleet_name, m, m, m)]
    lines += ["  kiwi-server apply %s %s --ssh core@%s" % (fleet_name, m, answers.get("master_address") or "<its address>")]
    for n in answers.get("nodes") or []:
        name = n["name"]
        lines += ["%s, once the master runs:" % name,
                  "  kiwi-server enroll %s %s          # its WireGuard config from the master" % (fleet_name, name),
                  "  kiwi-server build %s %s           # then: sudo dd if=output/%s/%s.iso of=/dev/sdX bs=4M" % (fleet_name, name, name, name),
                  "  kiwi-server apply %s %s --ssh core@%s" % (fleet_name, name, (n.get("address") or "<its address>").split("/")[0])]
    lines += ["kiwi-server list %s says what each machine needs next, kiwi-server status how they are." % fleet_name]
    return lines


def ask(found=None, read=input):
    """The questions in a terminal: Enter takes what is in brackets."""
    answers = {}

    def when_ok(q):
        w = q.get("when")
        if not w:
            return True
        key, _, val = w.partition(" == ")
        return str(answers.get(key)) == val.strip("'")
    for step in questions(found):
        print("\n== %s — %s" % (step["step"], step["intro"]))
        for q in step["questions"]:
            if not when_ok(q):
                continue
            if q.get("help"):
                print("   %s" % q["help"])
            kind = q.get("type", "string")
            if kind == "choice":
                for i, o in enumerate(q["options"], 1):
                    print("   %d) %s" % (i, o["label"]))
                default = next((i for i, o in enumerate(q["options"], 1) if o["value"] == q.get("default")), 1)
                got = read("%s [%d]: " % (q["label"], default)).strip() or str(default)
                if not got.isdigit() or not 1 <= int(got) <= len(q["options"]):
                    raise KiwiError("%s: a number from 1 to %d" % (q["label"], len(q["options"])))
                answers[q["id"]] = q["options"][int(got) - 1]["value"]
            elif kind == "bool":
                got = read("%s [%s]: " % (q["label"], "Y/n" if q.get("default") else "y/N")).strip().lower()
                answers[q["id"]] = q.get("default", False) if not got else got.startswith("y")
            elif kind == "machines":
                nodes, taken = [], set(answers.values())
                while (read("Add a machine at home? [%s]: " % ("Y/n" if not nodes else "y/N")).strip().lower()
                       or ("y" if not nodes else "n")).startswith("y"):
                    name = read("  Name [%s]: " % (pick := next(n for n in NODES if n not in taken))).strip() or pick
                    taken.add(name)
                    print("  What it does: " + "; ".join("%s: %s" % kv for kv in PRESETS.items()))
                    preset = read("  Preset [gateway]: ").strip() or "gateway"
                    downloads = (read("  Where its downloads go [/home/user/downloads]: ").strip()
                                 or "/home/user/downloads") if preset == "gateway" else ""
                    target = read("  System, ucore or debian [ucore]: ").strip() or "ucore"
                    addr = suggest(answers.get("lan_subnet", ""), len(nodes), answers.get("lan_gateway", ""))
                    address = read("  Fixed address you know is free [%s] (- for DHCP): " % addr).strip() or addr
                    disk = read("  Its disk, wiped by the ISO [/dev/nvme0n1]: ").strip() or "/dev/nvme0n1"
                    nodes.append({"name": name, "preset": preset, "target": target, "download_dir": downloads,
                                  "address": "" if address == "-" else address, "disk": disk})
                answers[q["id"]] = nodes
            else:
                prompt = "%s%s: " % (q["label"], " [%s]" % q["default"] if q.get("default") else "")
                answers[q["id"]] = (read(prompt).strip() if not q.get("secret") else __import__("getpass").getpass(prompt).strip()) \
                    or q.get("default", "")
    return answers
