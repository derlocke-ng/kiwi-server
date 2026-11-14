"""The settings a person can edit, as data: which fields there are, what a machine inherits
when it says nothing, and how the text of a form becomes YAML. The control page builds its
settings form from here (control.py, web.py).

The app (gui/kiwi-server-gui) still carries its own copy of BASE_GROUPS, SECTION_TARGETS,
to_text and from_text. A test (TestForm) holds the two copies of the data equal until the app
reads them from `kiwi-server roles --porcelain` ("base") instead.
"""
import yaml

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
             help="Hashed at render time. For the console; SSH uses keys"),
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
        dict(path=("network", "interface"), type="string", label="Interface", default="",
             help="Empty: the first wired interface"),
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
        else:
            d[path[-1]] = value
        self._prune(self.block(host))

    def _prune(self, d):
        for k in list(d):
            if isinstance(d[k], dict):
                self._prune(d[k])
                if not d[k] and k not in ("hosts",):
                    del d[k]

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
        for m in on:
            if m not in mods:
                continue
            fields = [_from_schema(doc, host, (name, m, st["key"]), st, (defaults.get(m) or {}).get(st["key"], st.get("default")))
                      for st in mods[m]["settings"]]
            out.append({"id": "mod:" + m, "title": mods[m]["title"], "help": mods[m].get("description", ""), "kind": "module",
                        "advanced": True, "fields": fields})
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
    for g, f in fields:
        name, path = f["name"], tuple(f["path"])
        if name not in posted:
            continue
        got = posted[name]
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
