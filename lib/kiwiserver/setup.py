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
PRESETS = {         # what a machine at home does, in the words the wizard shows; the first is the suggestion
    "cloud": "Keep my files, photos and passwords (Nextcloud and a password manager)",
    "gateway": "Be my home's ad blocker, VPN gateway and download station (it takes over DNS and DHCP at home)",
    "minimal": "Nothing yet: only its place on the VPN",
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


def detect(probe=True):
    """What this computer knows that a new fleet needs: its LAN with addresses that look free,
    time zone, keyboard, language, SSH key."""
    found = {"subnet": "", "gateway": "", "timezone": "UTC", "keyboard": "us", "locale": "en_US.UTF-8",
             "ssh_key": "", "addresses": []}
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
                   "small rented server for a few euros a month is the usual choice. None of your data is kept on it.",
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
            {"id": "master_where", "label": "Where it runs", "type": "choice", "default": "netcup", "options": [
                {"value": "netcup", "label": "A rented server at netcup (Kiwi Server installs it for you)"},
                {"value": "vps", "label": "A rented server somewhere else (you start it from the install media)"},
                {"value": "home", "label": "A machine at home (your router must pass a port on to it)"}]},
            {"id": "master_address", "label": "Its address on the Internet", "required": True,
             "help": "A rented server: the IPv4 address your hoster shows for it, like 203.0.113.7. At home: a name "
                     "from a dynamic DNS service that points at your router, like myhome.example.org. Your phones and "
                     "machines connect to it."},
            {"id": "exit", "label": "How your devices reach the Internet on the VPN", "type": "choice", "default": "direct",
             "options": [
                {"value": "direct", "label": "Through the master's own connection (simplest)"},
                {"value": "mullvad", "label": "Through Mullvad (more private; needs a Mullvad account)"}]},
            {"id": "mullvad_key", "label": "Mullvad private key", "secret": True, "when": "exit == 'mullvad'", "required": True,
             "help": "PrivateKey from a WireGuard config made on mullvad.net. It is kept encrypted with your other keys."},
            {"id": "mullvad_address", "label": "Mullvad address", "when": "exit == 'mullvad'", "required": True,
             "help": "Address from the same config, like 10.66.1.2/32."},
            {"id": "mullvad_country", "label": "Exit country", "default": "Germany", "when": "exit == 'mullvad'"},
            {"id": "master_name", "label": "Its name", "default": random.choice(MASTERS), "advanced": True},
            {"id": "master_disk", "label": "Its disk", "default": "auto", "advanced": True,
             "help": "auto: the install finds the machine's one disk. A device like /dev/vda when it has several. "
                     "Everything on it is erased."}]},
        {"step": "At home", "id": "home",
         "intro": "The machines at your place. Add the one that will keep your files; more can come later.",
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
         "notes": ["After that you get a list of steps, one machine after the other: the master first, then the machines "
                   "at home, then your phone."],
         "questions": []},
    ]


# addresses that only exist inside a home or a provider's network: no master can be reached there
_AT_HOME = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")


def _name_ok(n):
    return bool(config._LABEL.match(n or ""))


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
    if not addr:
        bad["master_address"] = "The master's address is needed: it is what your phones and machines connect to."
    else:
        try:
            ip = ipaddress.ip_address(addr)
            if any(ip in ipaddress.ip_network(n) for n in _AT_HOME if ip.version == 4) or ip.is_loopback or ip.is_link_local:
                bad["master_address"] = ("%s is an address inside a home network: nothing on the Internet can reach it. "
                                         "Give the address your hoster shows, or a dynamic DNS name for a master at home." % addr)
        except ValueError:
            if not re.fullmatch(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", addr.lower()):
                bad["master_address"] = "That is neither an address like 203.0.113.7 nor a name like vpn.example.org."
    if a.get("exit") == "mullvad":
        if not a.get("mullvad_key"):
            bad["mullvad_key"] = "The PrivateKey line of a WireGuard config made on mullvad.net is needed."
        try:
            ipaddress.ip_interface(str(a.get("mullvad_address") or ""))
        except ValueError:
            bad["mullvad_address"] = "The Address line of the same config is needed, like 10.66.1.2/32."
    names = [a.get("master_name") or ""]
    if not _name_ok(names[0]):
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
    return bad


def _answers_valid(a):
    bad = check(a)
    if bad:
        raise KiwiError(next(iter(bad.values())))
    return str(a.get("domain") or "home").strip(".").lower()


def make(answers, fleet_dir):
    """The fleet file's data for these answers, and what was made beside it."""
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
    addr = str(a["master_address"]).strip()
    master = {"modules": ["vpn-server", "dns", "tor", "reverse-proxy"], "dns": {"mullvad_socks": False},
              "vpn-server": {"wg_host": addr}}
    if a.get("exit") == "mullvad":
        master = {"vpn-client": {"vpn_provider": "mullvad", "wireguard_private_key": a["mullvad_key"],
                                 "wireguard_addresses": a["mullvad_address"],
                                 "server_countries": a.get("mullvad_country") or "Germany"},
                  "vpn-server": {"wg_host": addr}}
    hosts = {m: {"role": "master", "target": "debian", "disk": a.get("master_disk") or "auto", "master": master}}
    try:
        prefix = ipaddress.ip_network(str(a.get("lan_subnet") or "")).prefixlen
    except ValueError:
        prefix = 24
    for n in a.get("nodes") or []:
        name = n["name"]
        node = {"preset": n.get("preset") or "cloud",
                "vpn-client": {"wireguard_config": "secrets/%s.%s.conf" % (name, domain)}}
        if node["preset"] == "gateway":
            node["downloader"] = {"download_dir": n.get("download_dir") or "/home/user/downloads"}
        h = {"role": "node", "target": n.get("target") or "ucore", "disk": n.get("disk") or "auto", "node": node}
        if n.get("address"):
            address = str(n["address"]) if "/" in str(n["address"]) else "%s/%d" % (n["address"], prefix)
            h["network"] = {"dhcp": False, "address": address}
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


def _when(q, values):
    w = q.get("when")
    if not w:
        return True
    key, _, val = w.partition(" == ")
    return str(values.get(key)) == val.strip("'")


def ask(found=None, read=input, show=print, everything=False):
    """The questions in a terminal: Enter takes what is in brackets, a wrong answer is asked
    again. `everything` also asks what has an answer that works (the advanced ones)."""
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
            if (q.get("advanced") and not everything) or (kind == "choice" and len(q["options"]) == 1):
                answers[q["id"]] = q["options"][0]["value"] if kind == "choice" and len(q["options"]) == 1 else q.get("default", "")
                continue
            one(q, answers, q["id"], lambda: check(dict(answers, nodes=[])))
    return answers
