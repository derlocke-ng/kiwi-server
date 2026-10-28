"""One machine without a fleet: the input of `kiwi-modules render`.

    name: sh3                       # the stack's name for the machine
    hostname: sh3.home              # default: name.domain
    domain: home
    target: ucore                   # coreos | ucore | debian (what the stack runs on)
    timezone: Europe/Berlin
    admin: { user: core }           # the service user default
    role: node                      # the profile: master, or node with a preset
    node:
      preset: cloud
      vpn_ip: 10.8.0.25
      vpn-client: { wireguard_config: sh3.conf }
      cloud: { admin_password: ... }
    dns_records: [[10.8.0.1, gate.home], [10.8.0.25, sh3.home]]   # the network's names (optional)
    master_ip: 10.8.0.1             # the nodes' DNS upstream and the mesh route (optional)
    ca_cert: ca/kiwiCA.pem          # the network's CA, trusted inside the containers (optional)
    seed: seed                      # 32 random bytes: generated secrets derive from it (optional)

Paths are relative to the file. Generated secrets (database passwords and the
like) need the seed; without one every secret-typed setting must be given.
"""
import hashlib
import hmac
import os

import yaml

from .util import KiwiError, deep_merge, read_bytes, read_text

DEFAULTS = {"domain": "home", "target": "ucore", "timezone": "UTC", "admin": {"user": "core"}}
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def seed_id(seed):
    """Twelve hex digits that name a seed without revealing it: the machine
    side compares them so a stack from another seed is refused."""
    return hashlib.sha256(b"kiwi-seed-id:" + seed).hexdigest()[:12]


def derive_secret(seed, *parts, length=32):
    """A generated secret: HMAC-SHA256 of the seed over the parts (joined by
    ':'), letters and digits only (AIO forbids @ and : in passwords); the same
    seed and parts give the same value in kiwi-server and here. Frozen: a
    change here rotates every generated secret of every machine."""
    out, counter = "", 0
    while len(out) < length:
        digest = hmac.new(seed, (":".join(str(p) for p in parts) + ":%d" % counter).encode(),
                          hashlib.sha256).digest()
        out += "".join(ALPHABET[b % len(ALPHABET)] for b in digest)
        counter += 1
    return out[:length]


class Secrets:
    """Where generated secrets and file settings come from: next to the file."""

    def __init__(self, directory, seed_path=None):
        self.dir = directory
        self.seed_path = seed_path

    def resolve_path(self, p):
        p = os.path.expanduser(str(p))
        return p if os.path.isabs(p) else os.path.join(self.dir, p)

    def has_seed(self):
        return bool(self.seed_path) and os.path.isfile(self.resolve_path(self.seed_path))

    def seed(self):
        if not self.has_seed():
            raise KiwiError("a generated secret is needed and there is no seed: give every secret, "
                            "or `seed: <file>` with 32 random bytes (head -c 32 /dev/urandom > seed)")
        data = read_bytes(self.resolve_path(self.seed_path))
        if len(data) < 16:
            raise KiwiError("%s is too short to be a seed" % self.seed_path)
        return data

    def seed_id(self):
        return seed_id(self.seed())

    def derive_secret(self, *parts, length=32):
        return derive_secret(self.seed(), *parts, length=length)


class Machine:
    def __init__(self, data, directory):
        if not isinstance(data, dict) or not data.get("name"):
            raise KiwiError("the machine file needs at least name: and role:")
        self.cfg = deep_merge(DEFAULTS, {k: v for k, v in data.items()
                                         if k not in ("dns_records", "master_ip", "ca_cert", "seed")})
        self.name = str(data["name"])
        self.hostname = str(data.get("hostname") or (self.name + ("." + self.cfg["domain"] if self.cfg.get("domain") else "")))
        self.target = str(self.cfg.get("target") or "ucore")
        self.role = str(data.get("role") or "")
        if not self.role:
            raise KiwiError("%s: role: is missing (master, or node with a preset)" % self.name)
        self.role_settings_raw = dict(data.get(self.role) or {})
        self.role_settings, self.role_files, self.modules, self.module_settings, self.module_files = {}, {}, [], {}, {}
        self.warnings = []
        self.secrets = Secrets(directory, data.get("seed"))
        self.dns_records = [(str(ip), str(n)) for ip, n in (data.get("dns_records") or [])]
        self.master_ip = str(data.get("master_ip") or "")
        self.ca_cert = read_text(self.secrets.resolve_path(data["ca_cert"])) if data.get("ca_cert") else None

    @property
    def domain(self):
        return self.hostname.split(".", 1)[1] if "." in self.hostname else self.cfg.get("domain") or ""

    @property
    def admin(self):
        return self.cfg["admin"]


def load(path):
    try:
        data = yaml.safe_load(read_text(path))
    except yaml.YAMLError as e:
        raise KiwiError("%s: not valid YAML: %s" % (path, e))
    return Machine(data, os.path.dirname(os.path.abspath(path)))
