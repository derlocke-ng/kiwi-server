"""The settings a person can edit, as data: which fields there are, what a machine inherits
when it says nothing, and how the text of a form becomes YAML. The control page builds its
settings form from here (control.py, web.py).

The app (gui/kiwi-server-gui) still carries its own copy of BASE_GROUPS, SECTION_TARGETS,
to_text and from_text. A test (TestForm) holds the two copies of the data equal until the app
reads them from `kiwi-server roles --porcelain` ("base") instead.
"""
import re
import yaml

from . import util
from .util import KiwiError

# ---------------------------------------------------------------------------
# base fields: (path, type, label, help, scope)   scope: all | defaults | host
# `default` is the built-in value kiwi-server uses when neither the fleet's
# defaults nor the host say anything — shown as the inherited value.
# ---------------------------------------------------------------------------
BASE_GROUPS = [
    ("Machine", [
        dict(path=("hostname",), type="string", label="Hostname", scope="host",
             help="Empty: <name>.<domain> from the defaults", default=""),
        dict(path=("target",), type="enum", label="Target", options=[], default="ucore",
             help="What gets installed"),
        dict(path=("role",), type="enum", label="Role", options=[], default="bare",
             help="What the machine becomes on first boot"),
        dict(path=("disk",), type="string", label="Install disk", default="",
             help="WIPED by the ISO without asking — /dev/sda, /dev/nvme0n1, /dev/vda"),
        dict(path=("mounts",), type="lines", label="Data disks", scope="host", default=[],
             help="A second disk for the data, one per line: the disk, then where it goes, like LABEL=data /mnt/data. "
                  "The disk by its label (LABEL=…), its UUID=… or /dev/disk/by-id/…; kiwi-server disks <fleet> <machine> "
                  "lists what the machine has. It is mounted before the services start and never erased"),
        dict(path=("quiet",), type="list", label="Warnings left out", scope="host", default=[],
             help="Warnings this machine does not show, by their key: disk-plain (its disk is not encrypted), "
                  "data-disk-plain, clock, memory, failed-units, unhealthy, not-applied. They stay as notes. "
                  "A warning's own button does the same: Leave it out"),
        dict(path=("install", "on_done"), type="enum", label="When the install is done", options=["poweroff", "reboot"],
             default="poweroff",
             help="poweroff: the machine turns off, you pull the stick and switch it on again. reboot: it starts the "
                  "installed system by itself; for a virtual machine, or when its own disk comes first in the boot order"),
        dict(path=("domain",), type="string", label="Domain", scope="defaults", default="home",
             help="Hosts are <name>.<domain>, services <service>.<name>.<domain>. .home, .internal, .corp, .mail stay private; not .local, not a public TLD"),
        dict(path=("timezone",), type="string", label="Time zone", default="UTC", help="An IANA name, e.g. Europe/Berlin: the clock of logs, backups and the reboot time"),
        dict(path=("locale",), type="string", label="Locale", default="en_US.UTF-8", help="Language and formats of the system, e.g. de_DE.UTF-8"),
        dict(path=("keyboard",), type="string", label="Keyboard layout", default="us",
             help="Debian installer keymap, e.g. de, us, fr"),
        dict(path=("motd",), type="text", label="Message of the day", default="", help="Shown at every SSH login, e.g. what the machine is for"),
    ]),
    ("Admin user", [
        dict(path=("admin", "user"), type="string", label="User name", default="core",
             help="Passwordless sudo on every target; must not be root"),
        dict(path=("admin", "password"), type="secret", label="Password", default="",
             help="For the machine's screen; SSH uses keys. Kept only as a hash: write it down"),
        dict(path=("admin", "password_hash"), type="string", label="…or a crypt hash", default="",
             help="$6$… — instead of a password"),
        dict(path=("admin", "ssh_keys"), type="lines", label="SSH public keys", default=[],
             help="One per line: a key, or a path to a .pub file (~/.ssh/id_ed25519.pub)"),
        dict(path=("admin", "groups"), type="list", label="Extra groups", default=[],
             help="Space separated, e.g. docker"),
        dict(path=("admin", "ssh_port"), type="int", label="SSH port", default=22,
             help="Where sshd listens. Another port than 22 sees far fewer bots; moved without a lockout at the next apply"),
        dict(path=("admin", "ssh_password_auth"), type="bool", label="Allow SSH password login", default=False, help="Off: SSH takes keys only, the password is for the console. On only while a machine has no key yet"),
    ]),
    ("Network", [
        dict(path=("network", "dhcp"), type="bool", label="DHCP", default=True, help="On: the router gives the address. Off: the address below, or the next free one from the range in the defaults"),
        dict(path=("network", "address"), type="string", label="Static address (CIDR)", default="",
             help="192.168.1.20/24 — or leave empty and set an address range in the defaults"),
        dict(path=("network", "gateway"), type="string", label="Gateway", default="", help="The router's address, e.g. 192.168.1.1. Only without DHCP"),
        dict(path=("network", "dns"), type="list", label="DNS servers", default=[], help="Space separated"),
        dict(path=("network", "site"), type="string", label="Site", default="home",
             help="Where the machine stands. Machines of one site with fixed addresses reach each other on their LAN; "
                  "a machine at another site (a friend's, an office) over the VPN, also when its LAN has the same numbers "
                  "as home's. A name of your choice, e.g. office"),
        dict(path=("network", "interface"), type="string", label="Interface", default="",
             help="Empty: the first wired interface"),
        dict(path=("network", "wol"), type="bool", label="Wake-on-LAN", default=False,
             help="On: its card wakes on the signal another machine of its LAN sends (Wake it, kiwi-server wake). "
                  "Some machines also need it switched on in their firmware; it takes at the next start"),
        dict(path=("network", "mac"), type="string", label="…its card's MAC", scope="host", default="",
             help="Empty: learned at each apply. Only for a machine that has not been applied yet, like aa:bb:cc:dd:ee:ff"),
        dict(path=("network", "vlans"), type="object", label="Tagged VLANs", scope="host", default=[],
             help="Only with a managed switch that sends this machine tagged frames. One line per VLAN, like "
                  "- {id: 20, address: 192.168.20.5/24, name: iot}. The machine gets an interface on it with no "
                  "default route; a name of a group of the home network makes that group's rules hold there",
             doc="docs/design/lans.md"),
        dict(path=("network", "iprange"), type="string", label="Address range", scope="defaults", default="",
             help="192.168.1.20-192.168.1.99 — static hosts without an address get the next free one"),
        dict(path=("network", "mask"), type="string", label="Prefix length for the range", scope="defaults",
             default="", help="Empty: the smallest network covering the range"),
    ]),
    ("Updates", [
        dict(path=("updates", "enabled"), type="bool", label="Automatic updates", default=True, help="On: the system updates itself and reboots in the window below when it must"),
        dict(path=("updates", "days"), type="list", label="Reboot days", default=["Sun"],
             help="Mon Tue … Sun, space separated; empty = any day. Updates install continuously, this is when a reboot may happen"),
        dict(path=("updates", "time"), type="string", label="Reboot time", default="03:30", help="HH:MM local time"),
        dict(path=("updates", "length_minutes"), type="int", label="Window length (minutes)", default=90, help="How long after the reboot time a reboot may still start"),
        dict(path=("watchdog",), type="bool", label="Hardware watchdog", default=True,
             help="On: a machine that hangs is reset by its hardware watchdog after a minute, one that hangs in a "
                  "restart after ten. Nothing is set where the machine has none"),
    ]),
    ("When a machine is gone", [
        dict(path=("recovery", "master", "do"), type="enum", label="A master that does not answer", options=["tell", "restart"],
             scope="defaults", default="tell",
             help="tell: the control node says so on its page. restart: it also switches the server off and on at its "
                  "provider; nothing on it is touched. Needs a control node that is signed in to the provider"),
        dict(path=("recovery", "master", "after"), type="int", label="…after this many minutes", scope="defaults", default=15,
             help="How long a master must be gone before that happens"),
        dict(path=("recovery", "node", "do"), type="enum", label="A node that does not answer", options=["tell", "restart"],
             scope="defaults", default="tell", help="restart works for a node at a provider; one at home can only be told about"),
        dict(path=("recovery", "node", "after"), type="int", label="…after this many minutes", scope="defaults", default=60,
             help="How long a node must be gone before that happens"),
        dict(path=("recovery", "unlock"), type="bool", label="Open a disk that waits at boot", scope="defaults", default=True,
             help="A machine that restarted and waits with its disk locked gets its unlock ring forwarded by the control "
                  "node. Off: someone presses Unlock it"),
    ]),
    ("Disk unlock", [
        dict(path=("unlock", "rings"), type="list", label="Rings that open its disk", default=[],
             help="The unlock rings (the tang module on other machines) that open this machine's encrypted disk at boot, "
                  "by name, space separated. Empty: someone types the passphrase or presses Unlock",
             doc="docs/security.md"),
        dict(path=("unlock", "threshold"), type="int", label="…how many of them", default=1,
             help="1: any one ring opens it. 2: two of them have to answer"),
        dict(path=("unlock", "via"), type="map", label="…reached through", default={},
             help="One line per ring that is not on the LAN, like away=http://10.8.0.1:7501 (a machine at another site)"),
        dict(path=("unlock", "ssh"), type="bool", label="SSH while it waits", default=False,
             help="An SSH server in the waiting system, so that Unlock (or kiwi-server unlock) can open it from here"),
        dict(path=("unlock", "ssh_from"), type="list", label="…only from", default=[],
             help="Addresses or networks, space separated. CoreOS only: Debian's SSH server at boot has no such limit"),
        dict(path=("unlock", "control"), type="list", label="Rings that open the control node", default=[],
             help="A control node: these rings open its key store after a restart, so it works again before anyone "
                  "logs in after a power cut. Logging in to the page still takes the password. Empty: the store opens "
                  "at the first login"),
        dict(path=("unlock", "control_threshold"), type="int", label="…how many of them", default=1,
             help="1: any one of them opens it. 2 or more: that many have to answer together"),
        dict(path=("unlock", "leash"), type="int", label="Leash, in minutes", default=0,
             help="Carried off, it locks itself: when the rings of its own site have not answered for this long, after it "
                  "saw them since it started, it restarts and waits locked. 0: off; at least 5. A whole house without its "
                  "network for that long restarts too"),
    ]),
    ("Messages when something is wrong", [
        dict(path=("notify", "mail", "to"), type="list", label="Mail to", scope="defaults", default=[],
             help="Who gets a mail when something has been wrong for a while, and when it is fine again. Several: with spaces"),
        dict(path=("notify", "mail", "host"), type="string", label="Mail server", scope="defaults", default="",
             help="The server that sends your mail (SMTP), like smtp.example.org: your mail provider names it"),
        dict(path=("notify", "mail", "user"), type="string", label="…its user name", scope="defaults", default="",
             help="What you log in to that server with, often your mail address. Empty: it takes mail without a login"),
        dict(path=("notify", "mail", "password"), type="secret", label="…its password", scope="defaults", default="",
             help="Many providers want a password made for one app, not your own"),
        dict(path=("notify", "mail", "from"), type="string", label="…sent as", scope="defaults", default="",
             help="The sender's address. Empty: the user name"),
        dict(path=("notify", "mail", "tls"), type="enum", label="…encryption", options=["starttls", "tls", "none"],
             scope="defaults", default="starttls", help="starttls (port 587) is what most servers want; tls is port 465"),
        dict(path=("notify", "mail", "port"), type="int", label="…port", scope="defaults", default=0,
             help="0: the usual one for the encryption"),
        dict(path=("notify", "ntfy", "url"), type="string", label="Push message (ntfy)", scope="defaults", default="",
             help="A topic of ntfy, like https://ntfy.sh/a-name-only-you-know: the ntfy app on your phone shows what is sent there"),
        dict(path=("notify", "ntfy", "token"), type="secret", label="…its access token", scope="defaults", default="",
             help="Only when the topic is protected"),
        dict(path=("notify", "webhook", "url"), type="string", label="Webhook", scope="defaults", default="",
             help="An address that takes the message as JSON (title, text, level, network): an SMS gateway, a chat room"),
        dict(path=("notify", "heartbeat"), type="string", label="Heartbeat", scope="defaults", default="",
             help="An address asked after every look at the machines. A service like healthchecks.io tells you when it "
                  "stops hearing from your network: the one case the network cannot report itself"),
        dict(path=("notify", "after"), type="int", label="Tell after this many minutes", scope="defaults", default=10,
             help="How long something must be wrong before a message goes out. A restart for an update is shorter"),
    ]),
    ("uCore", [
        dict(path=("ucore", "image"), type="string", label="Image", default="ghcr.io/ublue-os/ucore:stable",
             help="ucore-minimal, ucore, ucore-hci — with -nvidia / -zfs variants; :stable or :testing"),
    ]),
    ("RAM", [
        dict(path=("ram", "copy_to_ram"), type="bool", label="Copy the image into RAM", default=False,
             help="Off: the system reads its image from the medium, which stays attached; whatever it writes is in RAM. On: the image too, about 900 MB more, 3 GiB at least, and the medium may go",
             doc="docs/design/ram-master.md"),
    ]),
    ("Debian", [
        dict(path=("debian", "release"), type="string", label="Release", default="trixie", help="The Debian release the installer installs, e.g. trixie"),
        dict(path=("debian", "mirror"), type="string", label="Mirror", default="deb.debian.org", help="Where packages come from; deb.debian.org picks a near one"),
        dict(path=("debian", "partitioning"), type="enum", label="Partitioning", options=["regular", "lvm", "crypto"],
             default="lvm", help="lvm: volumes that can grow later. regular: plain partitions. crypto: LVM inside LUKS, as Encrypt does"),
        dict(path=("debian", "encrypt"), type="bool", label="Encrypt the disk (LUKS)", default=False, help="Full-disk encryption with a one-time key the first apply replaces; unlocked at boot by the fleet (Tang) or by hand"),
        dict(path=("debian", "passphrase"), type="secret", label="Disk passphrase", default="", help="Empty: a one-time key from the fleet, replaced at the first apply. Set: this passphrase stays a way in"),
        dict(path=("debian", "packages"), type="list", label="Extra packages", default=[], help="Space separated"),
        dict(path=("debian", "proxy"), type="string", label="APT proxy", default="", help="An HTTP proxy for the installer's downloads, e.g. http://192.168.1.5:3142. Empty: none"),
    ]),
    ("CoreOS (advanced)", [
        dict(path=("coreos", "stream"), type="string", label="Stream", default="stable", help="Fedora CoreOS' update stream: stable, testing or next"),
        dict(path=("coreos", "kernel_arguments"), type="list", label="Kernel arguments", default=[], help="Added to the kernel command line, space separated, e.g. mitigations=auto"),
        dict(path=("coreos", "services"), type="list", label="Services to enable", default=[],
             help="docker podman cockpit tailscale nfs samba libvirtd, or any unit name"),
        dict(path=("coreos", "console"), type="string", label="Console", default="", help="e.g. ttyS0,115200n8"),
    ]),
    ("After the role", [
        dict(path=("post_script",), type="text", label="Post script", default="",
             help="Shell lines run as root at the end of the role script"),
    ]),
]


# the target groups a host shows only for its own target
SECTION_TARGETS = {"uCore": ("ucore",), "RAM": ("coreos-ram",), "Debian": ("debian",),
                   "CoreOS (advanced)": ("coreos", "ucore", "coreos-ram")}
# the groups a settings page shows open; the others are for the rare case
MAIN_GROUPS = ("Machine", "Network", "Updates")
# a module's or role's settings under these headings are tucked away as "more"
MORE_GROUPS = ("Advanced", "Generated")
TEXT_TYPES = ("list", "lines", "map", "text", "object")


def to_text(ftype, value):
    if value is None:
        return ""
    if ftype in ("list",):
        return " ".join(str(v) for v in value) if isinstance(value, list) else str(value)
    if ftype == "lines":
        return "\n".join(str(v) for v in value) if isinstance(value, list) else str(value)
    if ftype == "map":
        return "\n".join("%s=%s" % (k, v) for k, v in value.items()) if isinstance(value, dict) else str(value)
    if ftype == "object":
        return yaml.safe_dump(value, sort_keys=False, default_flow_style=False).rstrip("\n") if value else ""
    return str(value)


def from_text(ftype, text):
    text = text.strip() if ftype != "text" else text.rstrip()
    if text == "":
        return None
    if ftype == "list":
        return text.split()
    if ftype == "lines":
        return [ln.strip() for ln in text.splitlines() if ln.strip()]
    if ftype == "map":
        out = {}
        for ln in text.splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1)
                out[k.strip()] = v.strip()
        return out or None
    if ftype == "int":
        try:
            return int(text)
        except ValueError:
            return None
    if ftype == "object":
        try:
            v = yaml.safe_load(text)
        except yaml.YAMLError:
            return None   # the app keeps it as typed until it parses; changes() says so instead
        return v if isinstance(v, (dict, list)) and v else None
    return text


# ---------------------------------------------------------------------------
# the document
# ---------------------------------------------------------------------------
class Doc:
    """The fleet file as a dict, read and written by path. A machine is named by its host
    name; None is the defaults, which every machine inherits."""
    HEAD = ("# fleet.yaml — written by kiwi-server's control page. Comments are not kept here;\n"
            "# `kiwi-server init` writes a commented example to read along.\n")

    def __init__(self, data=None):
        data = {} if data is None else data
        if not isinstance(data, dict):
            raise KiwiError("the fleet file is not a mapping")
        if data.get("defaults") is None:
            data = dict({"defaults": {}}, **{k: v for k, v in data.items() if k != "defaults"})
        if data.get("hosts") is None:       # stays the last section: `add_host` appends to it as text
            data = dict({k: v for k, v in data.items() if k != "hosts"}, hosts={})
        self.data = data

    @classmethod
    def parse(cls, text):
        try:
            return cls(yaml.safe_load(text))
        except yaml.YAMLError as e:
            raise KiwiError("the fleet file is not valid YAML: %s" % str(e).splitlines()[0])

    def hosts(self):
        return list(self.data["hosts"])

    def block(self, host):
        if host is None:
            return self.data["defaults"]
        if host not in self.data["hosts"]:
            raise KiwiError("no such machine: %s" % host)
        if not isinstance(self.data["hosts"][host], dict):
            self.data["hosts"][host] = {}
        return self.data["hosts"][host]

    def get(self, host, path):
        """What this machine (or the defaults) sets itself, or None."""
        d = self.block(host)
        for p in path:
            if not isinstance(d, dict) or p not in d:
                return None
            d = d[p]
        return d

    def inherited(self, host, path, default=None):
        """What the value is when the machine does not set it: the defaults', else the built-in one."""
        if host is not None:
            v = self.get(None, path)
            if v is not None:
                return v
        return default

    def value(self, host, path, default=None):
        v = self.get(host, path)
        return self.inherited(host, path, default) if v is None else v

    def set(self, host, path, value, default=None):
        """An override, where the value is another than the inherited one. None, or the
        inherited value itself, takes the override away: the machine follows the defaults again."""
        if value is not None and value == self.inherited(host, path, default):
            value = None
        d = self.block(host)
        for p in path[:-1]:
            if not isinstance(d.get(p), dict):
                if value is None:
                    return
                d[p] = {}
            d = d[p]
        if value is None:
            d.pop(path[-1], None)
            self._prune(host, path[:-1])
        else:
            d[path[-1]] = value

    def _prune(self, host, parents):
        """The blocks an override that went leaves empty, from the inside out, and nothing else:
        an empty {} elsewhere can mean something (a hidden service with its defaults,
        `onion: services: {git: {}}`, was dropped by any other change of its machine until 2.179.1)."""
        for i in range(len(parents), 0, -1):
            parent = self.get(host, parents[:i - 1]) if i > 1 else self.block(host)
            if isinstance(parent, dict) and parent.get(parents[i - 1]) == {}:
                del parent[parents[i - 1]]
            else:
                break

    def apply(self, host, changes):
        """{path: value or None} into the document; the dotted paths that are different now."""
        keys = []
        for path, value in changes.items():
            path = tuple(path.split(".")) if isinstance(path, str) else tuple(path)
            was = self.get(host, path)
            self.set(host, path, value)
            if self.get(host, path) != was:
                keys.append(".".join(path))
        return keys

    def dump(self):
        return self.HEAD + yaml.safe_dump(self.data, sort_keys=False, allow_unicode=True, default_flow_style=False)


# ---------------------------------------------------------------------------
# what a form shows
# ---------------------------------------------------------------------------
def _options(names, labels=None):
    return [{"value": str(n), "label": str((labels or {}).get(n) or n)} for n in names]


def _field(doc, host, path, spec, default):
    """One field of a form. A secret's value never leaves the document: only whether there is one."""
    ftype = spec["type"]
    secret = ftype == "secret"
    cur, inh = doc.get(host, path), doc.inherited(host, path, default)
    return {"path": list(path), "name": ".".join(path), "type": ftype, "label": spec["label"], "help": spec.get("help", ""),
            "options": _options(spec.get("options") or []), "placeholder": spec.get("placeholder", ""),
            "value": None if secret else cur, "inherited": None if secret else inh, "secret": secret,
            "set": cur is not None, "inherited_set": bool(inh), "more": False}


def _from_schema(doc, host, path, s, default):
    """A role's or module's setting (`roles --porcelain`) as a field."""
    f = _field(doc, host, path, {"type": s["type"], "label": s["label"] + (" *" if s.get("required") else ""),
                                 "help": s.get("help", ""), "options": s.get("options") or [],
                                 "placeholder": s.get("placeholder", "")}, default)
    if s.get("targets"):
        f["help"] = (f["help"] + " (%s only)" % "/".join(s["targets"])).strip()
    f["more"] = (s.get("group") or "") in MORE_GROUPS
    return f


def _resolve(wanted, needs):
    """The modules with what they need, the needed ones first."""
    out = []

    def visit(m, seen):
        if m in out or m in seen:
            return
        seen.add(m)
        for d in needs.get(m) or []:
            visit(d, seen)
        out.append(m)
    for m in wanted:
        visit(m, set())
    return out


def _as_list(v):
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str) and v.strip():
        return v.split()
    return None


def _open_fields(doc, host, role_name, module, schema):
    """Open to: for each web site of a module on a node, a switch for Tor (a hidden service of the
    machine's onion part) and a public name (its DNS record at the master, the node's proxy
    open through it). The VPN always. An admin page is offered neither."""
    domain = str(doc.value(host, ("domain",), "") or doc.value(None, ("domain",), "") or "home")
    services = doc.value(host, (role_name, "onion", "services"), None) or {}
    out = []
    for site in schema.get("sites") or []:
        if site.get("admin"):
            continue
        key, label = site["key"], "%s.%s" % (site["key"], host)
        if site.get("tor"):
            out.append({"path": [role_name, "onion", "services", key], "name": "open.%s.%s.tor" % (module, key),
                        "type": "bool", "open": "tor", "label": "%s over Tor" % label,
                        "help": "A hidden service: anyone with its address opens it in Tor Browser, without the VPN. "
                                "The address: on the machine's page once it is applied.",
                        "options": [], "placeholder": "", "value": key in services, "inherited": False, "secret": False,
                        "set": key in services, "inherited_set": False, "more": False})
        if site.get("setting"):
            now = doc.value(host, (role_name, module, site["setting"]), None)
            name = str(now or "").split("://")[-1].strip("/")
            public = name if name and not (name + ".").endswith("." + domain + ".") else ""
            out.append({"path": [role_name, module, site["setting"]], "name": "open.%s.%s.public" % (module, key),
                        "type": "string", "open": "public", "url": bool(site.get("url")),
                        "label": "%s from the Internet" % label,
                        "help": "A name outside your network's domain, like %s.example.org, whose DNS record points at the "
                                "master's address: the master hands it on to this machine, which holds its certificate. "
                                "Empty: on the VPN only." % key,
                        "options": [], "placeholder": "%s.example.org" % key, "value": public or None, "inherited": None,
                        "secret": False, "set": bool(public), "inherited_set": False, "more": False})
    return out


def _role_groups(doc, host, role, mods):
    """The role's part of a form: what the machine runs (preset, module list), one group per
    module it runs, then the role's own settings."""
    name = role["name"]
    presets = role.get("presets") or {}
    main, out = [], []
    preset = None
    if presets:
        preset = doc.value(host, (name, "preset"))
        preset = preset if preset in presets else next(iter(presets))
    for st in role["settings"]:
        if st["key"] == "preset" and presets:
            f = _from_schema(doc, host, (name, "preset"), st, st.get("default"))
            f["options"] = _options(presets, {p: "%s (%s)" % (presets[p].get("title") or p, p) for p in presets})
            f["label"], f["more"] = "What it is for", False
            f["help"] = "The list of parts to start from. Choosing another one sets the list below anew."
            main.append(f)
    if role.get("stack"):
        needs = {m: list(s.get("requires") or []) for m, s in mods.items()}
        usual = list(presets[preset].get("modules") or []) if preset else list(role.get("modules") or [])
        inherited = (_as_list(doc.get(None, (name, "modules"))) if host is not None else None) or usual
        own = _as_list(doc.get(host, (name, "modules")))
        wanted = own or inherited
        on = _resolve(wanted, needs)
        kind = role.get("node_type") or "node"
        offered = [m for m in mods if kind in (mods[m].get("node_types") or [kind]) or m in on]
        main.append({"path": [name, "modules"], "name": "%s.modules" % name, "type": "modules", "label": "Its parts",
                     "help": "What runs on it. A part that another one needs comes along by itself.",
                     "options": [{"value": m, "label": mods[m]["title"], "help": mods[m].get("description", ""),
                                  "on": m in on, "needs": needs.get(m) or []} for m in offered],
                     "placeholder": "", "value": own, "inherited": inherited, "wanted": wanted, "secret": False,
                     "set": own is not None, "inherited_set": True, "more": False})
        defaults = dict(role.get("module_defaults") or {})
        if preset:      # the preset's own defaults win over the role's
            for m, d in (presets[preset].get("module_defaults") or {}).items():
                defaults[m] = dict(defaults.get(m) or {}, **d)
        # every part on offer has its group, the ones not running hidden: switched on, a page shows
        # its settings at once, as the app does; untouched, a hidden group changes nothing
        for m in list(on) + [m for m in offered if m not in on]:
            if m not in mods:
                continue
            fields = [_from_schema(doc, host, (name, m, st["key"]), st, (defaults.get(m) or {}).get(st["key"], st.get("default")))
                      for st in mods[m]["settings"]]
            if host is not None and kind == "node":
                fields = _open_fields(doc, host, name, m, mods[m]) + fields
            out.append({"id": "mod:" + m, "title": mods[m]["title"], "help": mods[m].get("description", ""), "kind": "module",
                        "advanced": True, "fields": fields, "off": m not in on, "module": m})
    by_group = {}
    for st in role["settings"]:
        if not (st["key"] == "preset" and presets):
            by_group.setdefault(st.get("group") or "Settings", []).append(st)
    for gname, items in by_group.items():
        fields = [_from_schema(doc, host, (name, st["key"]), st, st.get("default")) for st in items]
        for f in fields:
            f["more"] = False       # the whole group is tucked away already
        out.append({"id": "role:" + gname, "title": role["title"] if gname == "Settings" else gname,
                    "help": role.get("description", "") if gname == "Settings" else "", "kind": "role", "advanced": True,
                    "fields": fields})
    if main:
        out.insert(0, {"id": "role", "title": "What it does", "help": "", "kind": "role", "advanced": False, "fields": main})
    return out


def groups(schema, targets, data, host=None):
    """What a settings form needs for one machine (its host name) or for the defaults (None):
    the groups in the order a page shows them, the everyday ones first. Each group: id, title,
    help, kind (base, role or module), advanced, fields. Each field: path, name (the path with
    dots), type, label, help, options, value (what is set here, or None), inherited (what
    counts when nothing is set here), secret."""
    doc = data if isinstance(data, Doc) else Doc(data)
    roles = {r["name"]: r for r in schema.get("roles") or []}
    mods = {m["name"]: m for m in schema.get("modules") or []}
    target = doc.value(host, ("target",), "ucore")
    out = []
    for title, fields in BASE_GROUPS:
        if host is not None and target not in SECTION_TARGETS.get(title, (target,)):
            continue
        rows = []
        for f in fields:
            scope = f.get("scope", "all")
            if scope != "all" and (scope == "defaults") != (host is None):
                continue
            row = _field(doc, host, f["path"], f, f.get("default"))
            if f["path"] == ("target",):
                row["options"] = _options(targets)
            elif f["path"] == ("role",):
                row["options"] = _options(roles)
            rows.append(row)
        if rows:
            out.append({"id": "base:" + title, "title": title, "help": "", "kind": "base",
                        "advanced": title not in MAIN_GROUPS, "fields": rows})
    role = roles.get(doc.value(host, ("role",), "bare"))
    if role:
        out[1:1] = _role_groups(doc, host, role, mods)          # right after Machine, as in the app
    for g in out:
        for f in g["fields"]:       # what is set stays in the list: a form sent back untouched must not drop it
            if f["type"] == "enum" and f["value"] is not None and str(f["value"]) not in [o["value"] for o in f["options"]]:
                f["options"].append({"value": str(f["value"]), "label": str(f["value"])})
    return [g for g in out if not g["advanced"]] + [g for g in out if g["advanced"]]


# ---------------------------------------------------------------------------
# what a posted form changes
# ---------------------------------------------------------------------------
def _typed(f, text):
    """The value a text stands for; KiwiError says what is wrong with it in plain words."""
    ftype = f["type"]
    text = text.replace("\r\n", "\n")
    if text.strip() == "":
        return None
    if ftype == "bool":
        if text not in ("yes", "no"):
            raise KiwiError("yes or no")
        return text == "yes"
    if ftype == "enum":
        if text not in [o["value"] for o in f["options"]]:
            raise KiwiError("choose one from the list")
        return text
    if ftype == "int":
        try:
            return int(text.strip())
        except ValueError:
            raise KiwiError("a whole number is needed, like 22")
    if ftype == "map":
        if any("=" not in ln for ln in text.splitlines() if ln.strip()):
            raise KiwiError("one NAME=value per line")
    if ftype == "object":
        try:
            v = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise KiwiError("this is not valid YAML: %s" % str(e).splitlines()[0])
        if not isinstance(v, (dict, list)):
            raise KiwiError("a YAML list or mapping is needed")
    return from_text(ftype, text)


def changes(found, posted):
    """What a posted form changes: ({path: value, None takes the override away}, {field name:
    what is wrong with it}). `found` is what groups() gave for the form; `posted` maps a
    field's name to the text typed, the module list's to the names ticked, a secret's to None
    when it is to be removed. Only what differs from what the form showed is a change: a
    field left alone is not written, and neither is one that is not in `posted`."""
    out, errors = {}, {}
    fields = [(g, f) for g in found for f in g["fields"]]
    mods = next((f for _g, f in fields if f["type"] == "modules"), None)
    picked = set(posted.get(mods["name"]) or ()) if mods and mods["name"] in posted else \
        {o["value"] for o in (mods or {}).get("options", ()) if o["on"]}
    for g, f in fields:
        name, path = f["name"], tuple(f["path"])
        if name not in posted or (mods and g.get("module") and g["module"] not in picked):
            continue            # a module not ticked: the page sends its hidden settings, they wait
        got = posted[name]
        if f.get("open") == "tor":
            want = str(got).strip().lower() in ("yes", "true", "1", "on")
            if want != bool(f["value"]):
                out[path] = {} if want else None
                if want:        # the machine's onion part comes along
                    mf = next((x for gg, x in fields if x["type"] == "modules"), None)
                    if mf and "onion" not in [o["value"] for o in mf["options"] if o["on"]]:
                        out[tuple(mf["path"])] = list(mf["wanted"]) + ["onion"]
            continue
        if f.get("open") == "public":
            text = str(got or "").strip().lower().split("://")[-1].strip("/")
            if text == (f["value"] or ""):
                continue
            if text and not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+", text):
                errors[name] = "%s: a name like %s" % (f["label"], f["placeholder"])
                continue
            out[path] = ("https://" + text if f.get("url") else text) if text else None
            if text:            # the node's proxy takes it in through the master
                out[(path[0], "reverse-proxy", "public")] = True
            continue
        if f["type"] == "modules":
            needs = {o["value"]: o["needs"] for o in f["options"]}
            picked = [m for m in got if m in needs]
            if set(picked) == {o["value"] for o in f["options"] if o["on"]}:
                continue
            new = [m for m in f["wanted"] if m in picked] + [m for m in picked if m not in f["wanted"]]
            if set(_resolve(new, needs)) == set(_resolve(f["inherited"], needs)):
                new = None          # the usual list again
            if new != f["value"]:
                out[path] = new
            continue
        if f["secret"] and path[-2:] == ("admin", "password") and got is not None and got.strip():
            # the console password: only its hash goes into the fleet file (no secret in the clear on a disk)
            import secrets as pysecrets
            out[path[:-1] + ("password_hash",)] = util.sha512_crypt(got.strip(), pysecrets.token_hex(8))
            out[path] = None
            continue
        if f["secret"]:
            if got is None:
                if f["set"]:
                    out[path] = None
            elif got.strip():
                out[path] = got.strip()
            continue
        try:
            new = _typed(f, got or "")
        except KiwiError as e:
            errors[name] = "%s: %s" % (f["label"], e)
            continue
        if f["type"] == "bool":     # as the form showed it: anything set counts as yes or no
            same = new == (None if f["value"] is None else bool(f["value"]))
        else:
            same = to_text(f["type"], new) == to_text(f["type"], f["value"])
        if same:                    # left alone, also where the file says what it would inherit anyway
            continue
        if new is not None and new == f["inherited"]:
            new = None              # the inherited value itself: no override
        if new is not None or f["value"] is not None:
            out[path] = new
    by_path = {tuple(f["path"]): (g, f) for g, f in fields}
    for path in list(out):
        if path not in by_path:     # a value "Open to" brings along (the proxy open, the onion part)
            continue
        g, f = by_path[path]
        if ("role",) in out and g["kind"] != "base":       # another role: these were the old one's settings
            del out[path]
        elif f["name"].endswith(".preset") and g["id"] == "role":     # another preset: its list of parts counts
            modules = path[:-1] + ("modules",)
            out.pop(modules, None)
            if modules in by_path and by_path[modules][1]["set"]:
                out[modules] = None
    return out, errors


def plain(error, found, host=None):
    """One of `validate`'s errors for a person at the page: without the machine's own name in
    front, and with the field's label where the message names a path of the fleet file."""
    if host and error.startswith(host + ": "):
        error = error[len(host) + 2:]
    names = sorted(((f["name"], f["label"]) for g in found for f in g["fields"]), key=lambda x: -len(x[0]))
    for name, label in names:
        # a role's or module's setting is named without the role in front (immich.library_dir)
        for key in (name, name.split(".", 1)[-1]):
            if "." in key and key in error:
                return error.replace(key, "“%s”" % label.rstrip(" *"), 1)
    return error
