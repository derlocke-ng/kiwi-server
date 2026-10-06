"""The fleet itself, backed up to its own nodes.

Everything kiwi-server knows lives in one directory: the fleet file,
secrets/ (WireGuard configs, the CA, passwords) and what file settings point
at. Lose that machine and the fleet is unmanageable — no CA key, no way to
render a changed node. So the directory goes, encrypted, to the nodes it
built, into /var/lib/kiwi-server/backups: after every render or build when
`backup.hosts` is set, or by hand with `kiwi-server backup`. It comes back
with `kiwi-server restore` on a fresh machine over plain SSH to a node's LAN
address, before there is any mesh.

The archive is a gzipped tar encrypted by openssl (AES-256-CBC, PBKDF2 key
from a passphrase), so nothing but openssl and tar is needed to open one by
hand:  openssl enc -d -aes-256-cbc -md sha256 -pbkdf2 -iter 600000 -in X | tar xz
"""
import io
import os
import shlex
import subprocess
import sys
import tarfile
import time

from . import util
from .util import KiwiError

REMOTE_DIR = "/var/lib/kiwi-server/backups"
ENC_ARGS = ["-aes-256-cbc", "-md", "sha256", "-pbkdf2", "-iter", "600000"]
EXCLUDE_DIRS = {"output", ".work", "__pycache__", ".git"}
EXCLUDE_SUFFIXES = (".iso", ".tmp", ".part")


# ---- the archive ------------------------------------------------------------------

def _openssl(args, data, passphrase):
    env = {**os.environ, "KIWI_BACKUP_PASS": passphrase}
    try:
        r = subprocess.run(["openssl", "enc"] + args + ["-pass", "env:KIWI_BACKUP_PASS"],
                           input=data, capture_output=True, env=env)
    except FileNotFoundError:
        raise KiwiError("openssl is required for fleet backups")
    if r.returncode != 0:
        lines = (r.stderr or b"").decode(errors="replace").strip().splitlines() or ["failed"]
        raise KiwiError("openssl: %s" % lines[-1])
    return r.stdout


def encrypt(data, passphrase):
    return _openssl(ENC_ARGS + ["-salt"], data, passphrase)


def decrypt(data, passphrase):
    try:
        return _openssl(["-d"] + ENC_ARGS, data, passphrase)
    except KiwiError as e:
        raise KiwiError("cannot decrypt the backup — wrong passphrase? (%s)" % e)


def _walk(path):
    if os.path.isfile(path):
        yield path
        return
    for root, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS)
        for f in sorted(files):
            if not f.endswith(EXCLUDE_SUFFIXES):
                yield os.path.join(root, f)


def fleet_files(fleet):
    """(path, name in the archive) for what the fleet needs: the fleet file,
    secrets/, the CA directory and backup.extra. Never output/ or an ISO."""
    cfg = fleet.defaults.get("backup") or {}
    tls = fleet.defaults.get("tls") or {}
    roots = [fleet.path, fleet.resolve_path("secrets"), fleet.resolve_path(tls.get("ca_dir") or "secrets/ca")]
    roots += [fleet.resolve_path(str(x)) for x in (cfg.get("extra") or [])]
    out, seen = [], set()
    for root in roots:
        if not os.path.exists(root):
            continue
        for f in _walk(root):
            real = os.path.realpath(f)
            if real in seen:
                continue
            seen.add(real)
            rel = os.path.relpath(f, fleet.dir)
            if rel.startswith(".."):   # outside the fleet directory: kept under outside/<path>
                rel = os.path.join("outside", os.path.abspath(f).lstrip("/"))
            out.append((f, rel))
    return sorted(out, key=lambda t: t[1])


def archive_name(fleet, when=None):
    stem = os.path.splitext(os.path.basename(fleet.path))[0]
    return "fleet-%s-%s.tar.enc" % (stem, time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(when)))


def create(fleet, passphrase):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, arc in fleet_files(fleet):
            tar.add(path, arcname=arc, recursive=False)
    return encrypt(buf.getvalue(), passphrase)


def restore(data, passphrase, into):
    """Unpack an archive into a directory (created 0700); every file 0600."""
    raw = decrypt(data, passphrase)
    try:
        tar = tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz")
    except tarfile.TarError:
        raise KiwiError("decrypted, but this is not a kiwi-server fleet backup")
    os.makedirs(into, mode=0o700, exist_ok=True)
    names = []
    kw = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
    with tar:
        for m in tar.getmembers():
            parts = m.name.split("/")
            if m.name.startswith("/") or ".." in parts or not (m.isfile() or m.isdir()):
                raise KiwiError("refusing archive member %r" % m.name)
            tar.extract(m, into, **kw)
            if m.isfile():
                names.append(m.name)
    for root, dirs, files in os.walk(into):
        os.chmod(root, 0o700)
        for f in files:
            os.chmod(os.path.join(root, f), 0o600)
    return names


# ---- the passphrase --------------------------------------------------------------

def passphrase(fleet=None, path=None, confirm=False):
    """From --passphrase-file, the fleet's backup.passphrase_file, the
    KIWI_BACKUP_PASS variable, or a prompt (only on a terminal)."""
    cfg = (fleet.defaults.get("backup") if fleet is not None else None) or {}
    p = path or cfg.get("passphrase_file")
    if p:
        p = fleet.resolve_path(p) if fleet is not None else os.path.expanduser(str(p))
        if not os.path.isfile(p):
            raise KiwiError("backup passphrase file not found: %s" % p)
        lines = util.read_text(p).splitlines()
        if not lines or not lines[0].strip():
            raise KiwiError("backup passphrase file is empty: %s" % p)
        return lines[0].strip()
    if os.environ.get("KIWI_BACKUP_PASS"):
        return os.environ["KIWI_BACKUP_PASS"]
    if not sys.stdin.isatty():
        raise KiwiError("no passphrase: set backup.passphrase_file in the fleet, give --passphrase-file, "
                        "or export KIWI_BACKUP_PASS")
    import getpass
    pw = getpass.getpass("backup passphrase: ")
    if not pw:
        raise KiwiError("an empty passphrase protects nothing")
    if confirm and getpass.getpass("again: ") != pw:
        raise KiwiError("the passphrases differ")
    return pw


# ---- the nodes -------------------------------------------------------------------

def ssh_target(host, override=None):
    return override or "%s@%s" % (host.admin["user"], host.hostname)


def _ssh(target, script, data=None, runner=subprocess.run):
    """Run a shell script as root on a node; the admin user has sudo."""
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", target, "sudo sh -c " + shlex.quote(script)]
    try:
        r = runner(argv, input=data, capture_output=True)
    except FileNotFoundError:
        raise KiwiError("ssh is not installed")
    if r.returncode != 0:
        lines = (r.stderr or b"").decode(errors="replace").strip().splitlines() or ["ssh failed"]
        raise KiwiError("%s: %s" % (target, lines[-1]))
    return r.stdout


def store_local(data, directory, name):
    p = os.path.join(directory, name)
    util.write_bytes(p, data, 0o600)
    return p


def store_remote(data, target, name, keep=10, runner=subprocess.run):
    """Into REMOTE_DIR on the node (root, 0700); the newest `keep` stay.
    Returns what the node keeps afterwards."""
    d, n = REMOTE_DIR, shlex.quote(name)
    script = ("set -e; d=%s; install -d -m 0700 \"$d\"; cat > \"$d\"/%s.tmp; chmod 0600 \"$d\"/%s.tmp; "
              "mv -f \"$d\"/%s.tmp \"$d\"/%s; ls -1t \"$d\"/fleet-*.tar.enc | tail -n +%d | xargs -r rm -f; "
              "ls -1t \"$d\"" % (d, n, n, n, n, int(keep) + 1))
    return _ssh(target, script, data, runner).decode(errors="replace").split()


def list_remote(target, runner=subprocess.run):
    out = _ssh(target, "ls -1t %s/fleet-*.tar.enc 2>/dev/null || true" % REMOTE_DIR, None, runner)
    return [os.path.basename(x) for x in out.decode(errors="replace").split()]


def fetch_remote(target, name=None, runner=subprocess.run):
    """(name, data) of one archive — the newest when no name is given."""
    if not name:
        names = list_remote(target, runner)
        if not names:
            raise KiwiError("%s keeps no fleet backups in %s" % (target, REMOTE_DIR))
        name = names[0]
    if "/" in name or not name.startswith("fleet-") or not name.endswith(".tar.enc"):
        raise KiwiError("not a fleet backup name: %r" % name)
    return name, _ssh(target, "cat %s/%s" % (REMOTE_DIR, shlex.quote(name)), None, runner)
