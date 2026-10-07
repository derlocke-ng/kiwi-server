"""Fleet configuration: load fleet.yaml, merge defaults into hosts, validate.

A fleet file has two sections:

    defaults:   applies to every host
    hosts:      name -> overrides (same keys as defaults, plus hostname)

Everything a role needs beyond the base system lives under a key named after
the role (`node-cloud:`, `master:`) and is described by that role's role.yaml —
this module only merges those blocks; roles.py validates them.
"""
import copy
import ipaddress
import os
import re

import yaml

from . import util
from .util import KiwiError

TARGETS = {
    "coreos": "Fedora CoreOS, stable stream — Zincati applies updates in your window",
    "ucore":  "uCore — Fedora CoreOS that rebases itself onto a ghcr.io/ublue-os/ucore image",
    "debian": "Debian stable (trixie) netinst with a preseed — unattended-upgrades",
}

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

DEFAULTS = {
    "target": "ucore",
    "role": "bare",
    "hostname": None,
    "domain": "home",      # names are <service>.<host>.<domain>; see domain_advice() for the choice
    "timezone": "UTC",
    "locale": "en_US.UTF-8",
    "keyboard": "us",
    "motd": "",
    "disk": None,
    "admin": {
        "user": "core",
        "password": None,
        "password_hash": None,
        "ssh_keys": [],
        "groups": [],
        "ssh_password_auth": False,
    },
    "network": {
        "interface": "",
        "dhcp": True,
        "address": None,
        "gateway": None,
        "dns": [],
        "iprange": None,
        "mask": None,
    },
    "updates": {
        # When an update may REBOOT the machine. Updates themselves are fetched
        # continuously on every target; the window only gates the reboot.
        "enabled": True,
        "days": ["Sun"],
        "time": "03:30",
        "length_minutes": 90,
    },
    "coreos": {
        "stream": "stable",
        "console": None,
        "kernel_arguments": [],
        "services": [],
        "files": [],
        "directories": [],
        "units": [],
        "boot_device": None,
        "luks": None,
    },
    "ucore": {
        "image": "ghcr.io/ublue-os/ucore:stable",
    },
    "debian": {
        "release": "trixie",
        "mirror": "deb.debian.org",
        "directory": "/debian",
        "proxy": "",
        "packages": [],
        "partitioning": "lvm",
        "encrypt": False,
        "passphrase": None,
        "kernel_arguments": [],
        "non_free_firmware": True,
        "iso_url": None,
    },
    "tls": {
        # certificates for hosts that run the reverse proxy and bring none:
        # one fleet CA in ca_dir (next to the fleet file), one wildcard
        # certificate per host, the CA trusted on every machine built
        "auto": True,
        "ca_dir": "secrets/ca",
        "days": 3650,          # the CA
        "cert_days": 825,      # each host certificate (what browsers still accept)
        "name_constraints": [],  # e.g. [kiwi]: the CA may only sign names under these domains
    },
    "post_script": "",
    "backup": {
        # the fleet directory itself (fleet file, secrets/, the CA), encrypted,
        # to these hosts after every render or build — kiwi-server restore
        # brings it back on a fresh machine
        "hosts": [],
        "passphrase_file": "",
        "keep": 10,
        "extra": [],
    },
}

# The top-level label of the fleet's domain decides whether a name can leak or
# be registered by a stranger:
#   home corp mail   ICANN will not delegate these — too much private use already
#   internal         reserved for private use (2024); arpa: home.arpa is the IETF's
#   test             reserved for testing, never delegated
#   local            mDNS (RFC 6762): Apple devices and systemd-resolved never ask a DNS server
#   lan              unreserved, and what OpenWrt and many routers call their own LAN
#   anything public  a lookup that escapes the mesh reaches the registry, and anyone can
#                    register the name with a browser-trusted certificate (.kiwi, .dev, .box …)
PRIVATE_TLDS = frozenset(("home", "corp", "mail", "internal", "arpa", "test"))
PUBLIC_TLDS = frozenset(
    "com net org io dev app page kiwi box cloud host site online tech xyz me one world link life zone "
    "network blog shop store info biz pro name mobi tv cc co ai gg sh im".split())


def domain_advice(domain):
    """(level, message) about a fleet domain, or None when it is a safe choice."""
    d = str(domain or "").strip().strip(".").lower()
    if not d:
        return None
    tld = d.rsplit(".", 1)[-1]
    if tld in PRIVATE_TLDS:
        return None
    if tld == "local":
        return ("error", "domain %r: .local is mDNS — Apple devices and systemd-resolved never ask a DNS "
                         "server for it; use .home (the default), .internal, .corp or .mail" % d)
    if tld == "lan":
        return ("warning", "domain %r: .lan is what OpenWrt and many routers use for their own LAN names; "
                           "such a router answers *.lan itself and never forwards the fleet's names to the "
                           "master unless its local domain is changed" % d)
    if tld in PUBLIC_TLDS or len(tld) == 2:
        return ("warning", "domain %r: .%s is a public top-level domain — a lookup that escapes the mesh "
                           "reaches its registry, and anyone can register %s with a browser-trusted "
                           "certificate; .home, .internal, .corp and .mail can never be delegated" % (d, tld, d))
    return ("warning", "domain %r: .%s is not reserved for private use — if it is ever delegated as a "
                       "public TLD the fleet's names can leak or be registered by others; .home, .internal, "
                       ".corp and .mail are safe" % (d, tld))

# Lists where a host ADDS to the defaults instead of replacing them.
EXTEND_LISTS = {
    ("admin", "ssh_keys"),
    ("coreos", "kernel_arguments"), ("coreos", "services"), ("coreos", "files"),
    ("coreos", "directories"), ("coreos", "units"),
    ("debian", "packages"), ("debian", "kernel_arguments"),
}

_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", re.I)
_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_KEY_PREFIXES = ("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-", "sk-ssh-ed25519@", "sk-ecdsa-sha2-")


def deep_merge(base, over, path=()):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        p = path + (k,)
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v, p)
        elif isinstance(v, list) and isinstance(out.get(k), list) and p in EXTEND_LISTS:
            out[k] = out[k] + [x for x in v if x not in out[k]]
        else:
            out[k] = copy.deepcopy(v)
    return out


def ip_range(spec):
    """'192.168.1.10-192.168.1.100' -> iterator of addresses, inclusive."""
    if not spec or "-" not in str(spec):
        raise KiwiError("network.iprange must look like 192.168.1.10-192.168.1.100")
    a, b = [s.strip() for s in str(spec).split("-", 1)]
    start, end = ipaddress.IPv4Address(a), ipaddress.IPv4Address(b)
    if end < start:
        raise KiwiError("network.iprange: end is before start")
    cur = int(start)
    while cur <= int(end):
        yield str(ipaddress.IPv4Address(cur))
        cur += 1


def smallest_supernet(spec):
    a, b = [s.strip() for s in str(spec).split("-", 1)]
    start, end = ipaddress.IPv4Address(a), ipaddress.IPv4Address(b)
    for prefix in range(32, -1, -1):
        net = ipaddress.IPv4Network("%s/%d" % (a, prefix), strict=False)
        if net.network_address <= start and net.broadcast_address >= end:
            return net
    raise KiwiError("cannot find a network covering %s" % spec)


def is_ssh_key(s):
    return isinstance(s, str) and s.strip().startswith(_KEY_PREFIXES)


class Host:
    """One resolved machine: defaults merged with its overrides, plus the
    values computed from them (hostname, password hash, assigned address)."""

    def __init__(self, fleet, name, overrides):
        if not isinstance(overrides, dict):
            raise KiwiError("hosts.%s must be a mapping (use {} for no overrides)" % name)
        self.fleet = fleet
        self.name = name
        self.overrides = overrides
        self.cfg = deep_merge(fleet.defaults, overrides)
        c = self.cfg
        hn = c.get("hostname") or (name + ("." + c["domain"] if c.get("domain") else ""))
        self.hostname = str(hn)
        self.target = str(c.get("target") or "")
        self.role = str(c.get("role") or "")
        self.role_settings_raw = deep_merge(fleet.defaults.get(self.role) or {},
                                            overrides.get(self.role) or {}) if self.role else {}
        self.role_settings = {}   # filled by roles.resolve_settings
        self.role_files = {}      # key -> bytes, embedded into the role script
        self.modules = []         # module roles: the resolved module list
        self.module_settings = {}  # module -> {key: value}
        self.module_files = {}    # "module/key" -> bytes
        self.warnings = []

    # ---- derived values -------------------------------------------------
    @property
    def short_name(self):
        return self.hostname.split(".", 1)[0]

    @property
    def domain(self):
        return self.hostname.split(".", 1)[1] if "." in self.hostname else self.cfg.get("domain") or ""

    @property
    def admin(self):
        return self.cfg["admin"]

    def password_hash(self):
        a = self.admin
        if a.get("password_hash"):
            return str(a["password_hash"])
        if a.get("password"):
            salt = util.derive_salt("kiwi-server", self.hostname, str(a["password"]))
            return util.sha512_crypt(str(a["password"]), salt)
        return None

    def ssh_keys(self):
        """Keys as given, or read from files (`~/.ssh/id_ed25519.pub`)."""
        out = []
        for k in self.admin.get("ssh_keys") or []:
            if is_ssh_key(k):
                out.append(k.strip())
                continue
            p = self.fleet.resolve_path(str(k))
            if not os.path.isfile(p):
                raise KiwiError("admin.ssh_keys entry is neither a key nor a readable file: %s" % k)
            for line in util.read_text(p).splitlines():
                if is_ssh_key(line):
                    out.append(line.strip())
        seen, uniq = set(), []
        for k in out:
            if k not in seen:
                seen.add(k)
                uniq.append(k)
        return uniq

    def static_network(self):
        """None for DHCP, else dict(address=cidr, gateway, dns=[...], interface)."""
        n = self.cfg["network"]
        if n.get("dhcp", True):
            return None
        return {
            "interface": n.get("interface") or "",
            "address": n.get("address"),
            "gateway": n.get("gateway"),
            "dns": list(n.get("dns") or []),
        }

    def updates(self):
        u = dict(self.cfg["updates"])
        u["days"] = [str(d) for d in (u.get("days") or [])]
        return u

    def role_setting(self, key, default=None):
        return self.role_settings.get(key, default)

    # ---- validation -----------------------------------------------------
    def validate(self, roles):
        errs = []
        self.warnings = []
        c = self.cfg
        if self.target not in TARGETS:
            errs.append("target must be one of %s (got %r)" % (", ".join(TARGETS), self.target))
        if not self.role:
            errs.append("role is missing")
        elif roles is not None and self.role not in roles:
            errs.append("unknown role %r (known: %s)" % (self.role, ", ".join(sorted(roles))))
        elif roles is not None and self.target in TARGETS and self.target not in roles[self.role].targets:
            errs.append("role %s does not support target %s" % (self.role, self.target))
        for label in self.hostname.split("."):
            if not _LABEL.match(label):
                errs.append("hostname %r is not a valid DNS name" % self.hostname)
                break
        a = c["admin"]
        if not _USER.match(str(a.get("user") or "")):
            errs.append("admin.user %r is not a valid unix user name" % a.get("user"))
        if a.get("password") and a.get("password_hash"):
            errs.append("admin: give password OR password_hash, not both")
        if a.get("password_hash") and not str(a["password_hash"]).startswith("$"):
            errs.append("admin.password_hash does not look like a crypt hash")
        try:
            keys = self.ssh_keys()
        except KiwiError as e:
            errs.append(str(e))
            keys = []
        if not keys and not (a.get("password") or a.get("password_hash")):
            errs.append("admin: no ssh_keys and no password — nobody could log in")
        if self.target == "debian" and a.get("user") == "root":
            errs.append("admin.user must not be root on debian (root login is disabled by the preseed)")
        n = c["network"]
        if not n.get("dhcp", True):
            if n.get("address"):
                try:
                    ipaddress.ip_interface(str(n["address"]))
                except ValueError:
                    errs.append("network.address must be CIDR like 192.168.1.20/24")
            elif not self.fleet.defaults["network"].get("iprange"):
                errs.append("network: static (dhcp: false) needs address: or a defaults network.iprange")
            if not n.get("gateway"):
                errs.append("network: static needs gateway:")
            for d in n.get("dns") or []:
                try:
                    ipaddress.ip_address(str(d))
                except ValueError:
                    errs.append("network.dns entry %r is not an IP address" % d)
        u = c["updates"]
        for d in u.get("days") or []:
            if str(d) not in DAYS:
                errs.append("updates.days entries must be %s" % "/".join(DAYS))
        if not _TIME.match(str(u.get("time") or "")):
            errs.append("updates.time must be HH:MM (24h)")
        try:
            if int(u.get("length_minutes") or 0) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            errs.append("updates.length_minutes must be a positive integer")
        if self.cfg.get("disk") and not str(self.cfg["disk"]).startswith("/dev/"):
            errs.append("disk must be a device path like /dev/sda or /dev/nvme0n1")
        if not self.cfg.get("disk"):
            self.warnings.append("no disk: set — configs render, but an ISO cannot be built")
        d = c["debian"]
        if d.get("partitioning") not in ("regular", "lvm", "crypto"):
            errs.append("debian.partitioning must be regular, lvm or crypto")
        if (d.get("encrypt") or d.get("partitioning") == "crypto") and not d.get("passphrase"):
            errs.append("debian.encrypt needs debian.passphrase")
        if self.target == "ucore" and "/" not in str(c["ucore"].get("image") or ""):
            errs.append("ucore.image must be a full image reference like ghcr.io/ublue-os/ucore:stable")
        for key in ("files", "directories", "units"):
            if not isinstance(c["coreos"].get(key) or [], list):
                errs.append("coreos.%s must be a list" % key)
        if "systemd_units" in c["coreos"]:
            errs.append("coreos.systemd_units: the key is coreos.units")
        t = c.get("tls") or {}
        if not isinstance(t.get("name_constraints") or [], list):
            errs.append("tls.name_constraints must be a list of domains")
        for k in ("days", "cert_days"):
            try:
                if int(t.get(k) or 0) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                errs.append("tls.%s must be a positive integer" % k)
        return ["%s: %s" % (self.name, e) for e in errs]


class Fleet:
    def __init__(self, path, data):
        if not isinstance(data, dict):
            raise KiwiError("%s: top level must be a mapping with defaults: and hosts:" % path)
        self.path = os.path.abspath(path)
        self.dir = os.path.dirname(self.path)
        self.raw = data
        self.defaults = deep_merge(DEFAULTS, data.get("defaults") or {})
        hosts = data.get("hosts")
        if not isinstance(hosts, dict) or not hosts:
            raise KiwiError("%s: hosts: must be a mapping with at least one host" % path)
        self.hosts = {}
        for name, over in hosts.items():
            if not _LABEL.match(str(name)):
                raise KiwiError("host name %r must be a plain DNS label (letters, digits, dashes)" % name)
            self.hosts[str(name)] = Host(self, str(name), over or {})
        self._assign_addresses()
        # fleet-wide findings: the domain choice, the backup block
        self.errors, self.warnings = [], []
        for d in sorted({h.domain for h in self.hosts.values()} | {str(self.defaults.get("domain") or "")}):
            adv = domain_advice(d)
            if adv:
                (self.errors if adv[0] == "error" else self.warnings).append(adv[1])
        b = self.defaults.get("backup") or {}
        if not isinstance(b.get("hosts") or [], list):
            self.errors.append("backup.hosts must be a list of host names")
        else:
            for h in b.get("hosts") or []:
                if str(h) not in self.hosts:
                    self.errors.append("backup.hosts: no such host %r" % h)
        keep = b.get("keep")
        try:
            if int(10 if keep is None else keep) < 1:
                raise ValueError
        except (TypeError, ValueError):
            self.errors.append("backup.keep must be a positive integer")

    @classmethod
    def load(cls, path):
        if not os.path.isfile(path):
            raise KiwiError("fleet file not found: %s" % path)
        try:
            data = yaml.safe_load(util.read_text(path))
        except yaml.YAMLError as e:
            raise KiwiError("%s: not valid YAML: %s" % (path, e))
        return cls(path, data)

    def resolve_path(self, p):
        p = os.path.expanduser(str(p))
        return p if os.path.isabs(p) else os.path.join(self.dir, p)

    def seed(self):
        """32 random bytes in secrets/seed, made on first use: what generated
        secrets (database passwords and the like) are derived from, so every
        render of the fleet gives the same values. Backed up with the fleet;
        a new seed rotates every generated secret."""
        p = self.resolve_path("secrets/seed")
        if not os.path.isfile(p):
            os.makedirs(os.path.dirname(p), mode=0o700, exist_ok=True)
            util.write_bytes(p, os.urandom(32), 0o600)
        data = util.read_bytes(p)
        if len(data) < 16:
            raise KiwiError("%s is too short to be a seed — remove it to make a new one" % p)
        return data

    def derive_secret(self, *parts, length=32):
        """A generated secret: letters and digits only (AIO forbids @ and : in
        passwords), stable for the same seed and the same parts."""
        import hashlib
        import hmac
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        out, counter = "", 0
        while len(out) < length:
            digest = hmac.new(self.seed(), (":".join(parts) + ":%d" % counter).encode(), hashlib.sha256).digest()
            out += "".join(alphabet[b % len(alphabet)] for b in digest)
            counter += 1
        return out[:length]

    def names(self):
        return list(self.hosts)

    def select(self, names):
        if not names:
            return list(self.hosts.values())
        out = []
        for n in names:
            if n not in self.hosts:
                raise KiwiError("no such host in %s: %s (have: %s)" % (
                    os.path.basename(self.path), n, ", ".join(self.hosts)))
            out.append(self.hosts[n])
        return out

    def _assign_addresses(self):
        """Static hosts without an address take the next free one from
        defaults.network.iprange, in file order. Explicit addresses are
        reserved first so a later host never collides with an earlier one."""
        spec = self.defaults["network"].get("iprange")
        if not spec:
            return
        used = set()
        for h in self.hosts.values():
            a = h.cfg["network"].get("address")
            if a:
                used.add(str(a).split("/", 1)[0])
        mask = self.defaults["network"].get("mask") or smallest_supernet(spec).prefixlen
        pool = ip_range(spec)
        for h in self.hosts.values():
            n = h.cfg["network"]
            if n.get("dhcp", True) or n.get("address"):
                continue
            for ip in pool:
                if ip not in used:
                    used.add(ip)
                    n["address"] = "%s/%s" % (ip, n.get("mask") or mask)
                    break
            else:
                raise KiwiError("network.iprange %s has no free address left for %s" % (spec, h.name))

    def validate(self, roles=None):
        errs = []
        for h in self.hosts.values():
            errs += h.validate(roles)
        return errs
