"""The fleet itself, backed up to its own nodes.

Everything kiwi-server knows lives in one directory: the fleet file,
secrets/ (WireGuard configs, the CA, passwords) and what file settings point
at. Lose that machine and the fleet is unmanageable — no CA key, no way to
render a changed node. So the directory goes, encrypted, to the nodes it
built, into /var/lib/kiwi-server/backups: after every render or build when
`backup.hosts` is set, or by hand with `kiwi-server backup`. It comes back
with `kiwi-server restore` on a fresh machine over plain SSH to a node's LAN
address, before there is any mesh.

Two kinds of archive. A fleet with a secret store (secrets.age) gets a copy
that its recovery key opens: a plain tar of the store as it is and of the fleet
file, encrypted with age for the store's keys (see "the copy" below). Nothing
but tar and age is needed to open one by hand:
  tar xf X && age -d -i KEYFILE fleet.yaml.age

A fleet without a store keeps the older kind: a gzipped tar encrypted by
openssl (AES-256-CBC, PBKDF2 key from a passphrase):
  openssl enc -d -aes-256-cbc -md sha256 -pbkdf2 -iter 600000 -in X | tar xz
"""
import io
import json
import os
import re
import shutil
import hashlib
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time

from . import VERSION, remote, store as storemod, util
from .util import KiwiError

REMOTE_DIR = "/var/lib/kiwi-server/backups"
SUFFIX = ".tar"             # a fleet with a store: opened by its recovery key
OLD_SUFFIX = ".tar.enc"     # a fleet without one: opened by a passphrase
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
    except KiwiError:
        raise KiwiError("the passphrase does not open this copy")


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
    return "fleet-%s-%s%s" % (stem, time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(when)),
                              SUFFIX if has_store(fleet) else OLD_SUFFIX)


def is_name(name):
    """Is this what an archive on a node is called (either kind)?"""
    return "/" not in name and name.startswith("fleet-") and name.endswith((SUFFIX, OLD_SUFFIX))


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


# ---- the copy of a fleet that has a secret store ---------------------------------------
#
# The store is encrypted already (secrets.age, for the keys in secrets.recipients), so the
# copy is the store as it is, plus the fleet file and what else is a plain file, each
# encrypted with age for the same keys. What opens the store opens the copy: the recovery
# key above all, so there is no passphrase to set or to lose.
#
#   MANIFEST.json          what kind of copy, when, by which version, every file's sha256
#   secrets.age            the store, as it rests in the fleet directory
#   secrets.recipients     the public keys it is encrypted for
#   <fleet file>.age       the fleet file
#   federation.yaml.age    when there is one
#   <path>.age             backup.extra, and a CA directory outside secrets/
#
# It is not signed. The list finds a damaged copy, not a forged one: whoever can replace
# the file where it is kept can hand back another network, encrypted for the same public
# keys (ROADMAP, problem 21).

MANIFEST = "MANIFEST.json"
FORMAT = 2                  # (1: the passphrase kind, which has no list)
_KEY = re.compile(r"age1[0-9a-z]{20,200}")
_STORE_ID = re.compile(r"# store: [0-9a-f]{16}")


def has_store(fleet):
    return storemod.Store.for_fleet(fleet.path).exists()


def copy_files(fleet):
    """(path, name in the copy) of the plain files a copy takes, each encrypted on the way:
    the fleet file, federation.yaml, backup.extra, and a CA kept outside secrets/. What is in
    secrets/ travels in the store."""
    from . import federation
    st = storemod.Store.for_fleet(fleet.path)
    inside = os.path.realpath(st.link) + os.sep
    cfg = fleet.defaults.get("backup") or {}
    tls = fleet.defaults.get("tls") or {}
    roots = [fleet.path, os.path.join(fleet.dir, federation.FILE), fleet.resolve_path(tls.get("ca_dir") or "secrets/ca")]
    roots += [fleet.resolve_path(str(x)) for x in (cfg.get("extra") or [])]
    out, seen = [], {os.path.realpath(st.file), os.path.realpath(st.recipients)}
    for root in roots:
        if not os.path.exists(root):
            continue
        for f in _walk(root):
            real = os.path.realpath(f)
            if real in seen or real.startswith(inside):
                continue
            seen.add(real)
            rel = os.path.relpath(f, fleet.dir)
            if rel.startswith(".."):   # outside the fleet directory: kept under outside/<path>
                rel = os.path.join("outside", os.path.abspath(f).lstrip("/"))
            if rel.split("/")[0] == "secrets":
                continue                # (the store's place: nothing plain belongs there)
            out.append((f, rel))
    return out


def pack(fleet_name, files, when=None):
    """The copy from {name: bytes}, its list in front."""
    now = time.time() if when is None else when
    manifest = {"format": FORMAT, "what": "a kiwi-server network: its secret store and its fleet file, for the store's keys",
                "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "kiwi-server": VERSION, "fleet": fleet_name,
                "files": [{"name": n, "size": len(b), "sha256": hashlib.sha256(b).hexdigest()} for n, b in files.items()]}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for name, blob in [(MANIFEST, json.dumps(manifest, indent=1).encode() + b"\n")] + list(files.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(blob), 0o600, int(now)
            tar.addfile(info, io.BytesIO(blob))
    return buf.getvalue()


def create_copy(fleet, when=None):
    """The copy of a fleet that has a store, as it is now. Needs the public keys only."""
    st = storemod.Store.for_fleet(fleet.path)
    if not st.exists():
        raise KiwiError("this fleet has no secret store: its backup is locked with a passphrase")
    st.sync()                   # what changed in the open store, into secrets.age first
    files = {storemod.FILE: util.read_bytes(st.file), storemod.RECIPIENTS: util.read_bytes(st.recipients)}
    for path, name in copy_files(fleet):
        files[name + ".age"] = storemod.encrypt_bytes(st.recipients, util.read_bytes(path))
    return pack(os.path.basename(fleet.path), files, when)


def kind(data):
    """"store" (the recovery key opens it), "passphrase" (the older kind), or None."""
    if data[:8] == b"Salted__":         # how openssl enc -salt begins
        return "passphrase"
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
            return "store" if MANIFEST in tar.getnames() else None
    except (tarfile.TarError, EOFError):
        return None


def _safe(name):
    return (isinstance(name, str) and 0 < len(name) < 1000 and "\\" not in name and "\0" not in name
            and all(p and p not in (".", "..") for p in name.split("/")))


def _check_recipients(blob):
    """secrets.recipients as the store writes it: comments and public keys. The store's id in
    it names a directory, so it is hex and nothing else."""
    try:
        lines = blob.decode().splitlines()
    except UnicodeDecodeError:
        lines = ["?"]
    keys = 0
    for line in lines:
        if _KEY.fullmatch(line.strip()):
            keys += 1
        elif (line.startswith("# store: ") and not _STORE_ID.fullmatch(line)) or (line.strip() and not line.startswith("#")):
            keys = 0
            break
    if not keys:
        raise KiwiError("the copy's list of keys (%s) is not one a store writes" % storemod.RECIPIENTS)


def read_copy(data):
    """(its list, {name: bytes}) of a copy, checked: only plain files with names that stay
    in one directory, exactly the ones the list names, each with the list's sha256."""
    files = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
            for m in tar.getmembers():
                if not m.isreg() or not _safe(m.name) or m.name in files or m.size > len(data):
                    raise KiwiError("the copy holds something that is not allowed: %r" % m.name[:80])
                files[m.name] = tar.extractfile(m).read()
    except (tarfile.TarError, EOFError):
        raise KiwiError("this is not a copy of a network, or it is cut short")
    try:
        manifest = json.loads(files.pop(MANIFEST))
        fmt, fleet_name = manifest["format"], manifest["fleet"]
        listed = {f["name"]: f["sha256"] for f in manifest["files"]}
    except (KeyError, ValueError, TypeError):
        raise KiwiError("this is not a copy of a network: it has no list of its files")
    if fmt != FORMAT:
        raise KiwiError("this copy is of a kind this version does not know (%r): a newer kiwi-server opens it" % (fmt,))
    if set(listed) != set(files):
        raise KiwiError("the copy is damaged: its files are not the ones its list names")
    for name, blob in files.items():
        if hashlib.sha256(blob).hexdigest() != listed[name]:
            raise KiwiError("the copy is damaged: %s is not what its list says" % name)
    needed = (storemod.FILE, storemod.RECIPIENTS)
    if not _safe(fleet_name) or "/" in fleet_name or not all(n in files for n in needed + (fleet_name + ".age",)):
        raise KiwiError("this is not a copy of a network: the store or the fleet file is missing")
    for name in files:
        plain = name[:-4]
        if name not in needed and (not name.endswith(".age") or not plain or plain in needed
                                   or plain.split("/")[0] == "secrets"):
            raise KiwiError("the copy holds something that is not allowed: %r" % name[:80])
    _check_recipients(files[storemod.RECIPIENTS])
    return manifest, files


def open_copy(data, identity):
    """(the fleet file's name, {name: plain bytes}, the store's two files) of a copy, opened
    with a key of its store. Nothing reaches a disk: what age reads is staged in memory."""
    manifest, files = read_copy(data)
    plain = {}
    tmp = tempfile.mkdtemp(prefix="kiwi-copy-", dir=os.environ.get("XDG_RUNTIME_DIR") or "/dev/shm")
    try:
        staged = os.path.join(tmp, "one.age")
        for name in [storemod.FILE] + sorted(n for n in files if n not in (storemod.FILE, storemod.RECIPIENTS)):
            util.write_bytes(staged, files[name], 0o600)
            try:
                opened = storemod.decrypt_file(identity, staged)
            except KiwiError:
                raise KiwiError("that key does not open this copy" if name == storemod.FILE else
                                "the copy is damaged: the key opens its store but not %s" % name)
            if name != storemod.FILE:
                plain[name[:-4]] = opened
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return manifest["fleet"], plain, {n: files[n] for n in (storemod.FILE, storemod.RECIPIENTS)}


def restore_copy(data, identity, into):
    """A copy into a directory (created 0700), as a desktop keeps a fleet: the store as it is,
    the fleet file and the others as plain files 0600. A key that does not open it, or a
    directory that holds a network already: nothing is written. Returns (the fleet file's
    name, the files written)."""
    fleet_name, plain, kept = open_copy(data, identity)
    there = [n for n in (storemod.FILE, storemod.RECIPIENTS, "secrets", fleet_name) if os.path.lexists(os.path.join(into, n))]
    if there:
        raise KiwiError("%s holds a network already (%s): give an empty directory with --into" % (into, ", ".join(there)))
    os.makedirs(into, mode=0o700, exist_ok=True)
    util.write_bytes(os.path.join(into, storemod.FILE), kept[storemod.FILE], 0o600)
    util.write_bytes(os.path.join(into, storemod.RECIPIENTS), kept[storemod.RECIPIENTS], 0o644)
    for name, blob in plain.items():
        path = os.path.join(into, name)
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        util.write_bytes(path, blob, 0o600)
    return fleet_name, sorted(list(kept) + list(plain))


def recovery_key(path=None, passphrase_path=None):
    """The key that opens a copy: from --recovery-file, the KIWI_RECOVERY_KEY variable, or a
    prompt (only on a terminal). A --passphrase-file that holds the key is taken too: the
    app's dialog has one box for either kind of copy."""
    if path or passphrase_path:
        p = os.path.expanduser(path or passphrase_path)
        if not os.path.isfile(p):
            raise KiwiError("recovery key file not found: %s" % p)
        key = next((ln for ln in util.read_text(p).splitlines() if ln.strip().upper().startswith("AGE-SECRET-KEY-")), "")
        if not key:
            raise KiwiError("this copy opens with the network's recovery key (AGE-SECRET-KEY-…), not with a passphrase"
                            if not path else "%s holds no recovery key (a line that starts with AGE-SECRET-KEY-)" % p)
    elif os.environ.get("KIWI_RECOVERY_KEY"):
        key = os.environ["KIWI_RECOVERY_KEY"]
    elif not sys.stdin.isatty():
        raise KiwiError("this copy opens with the network's recovery key: give --recovery-file FILE, "
                        "or export KIWI_RECOVERY_KEY")
    else:
        import getpass
        key = getpass.getpass("recovery key: ")
    key = "".join(key.split()).upper()
    if not key.startswith("AGE-SECRET-KEY-"):
        raise KiwiError("a recovery key starts with AGE-SECRET-KEY-")
    return key


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
    return remote.target(host, override)


def _ssh(target, script, data=None, runner=subprocess.run):
    return remote.run(target, script, data, runner)


def store_local(data, directory, name):
    p = os.path.join(directory, name)
    util.write_bytes(p, data, 0o600)
    return p


# both kinds, newest first; a kind the node keeps none of is not an error
_LIST = "ls -1t %s/fleet-*" + SUFFIX + " %s/fleet-*" + OLD_SUFFIX + " 2>/dev/null"


def store_remote(data, target, name, keep=10, runner=subprocess.run):
    """Into REMOTE_DIR on the node (root, 0700); the newest `keep` stay.
    Returns what the node keeps afterwards."""
    d, n = REMOTE_DIR, shlex.quote(name)
    script = ("set -e; d=%s; install -d -m 0700 \"$d\"; cat > \"$d\"/%s.tmp; chmod 0600 \"$d\"/%s.tmp; "
              "mv -f \"$d\"/%s.tmp \"$d\"/%s; %s | tail -n +%d | xargs -r rm -f; "
              "ls -1t \"$d\"" % (d, n, n, n, n, _LIST % ('"$d"', '"$d"'), int(keep) + 1))
    return _ssh(target, script, data, runner).decode(errors="replace").split()


def list_remote(target, runner=subprocess.run):
    out = _ssh(target, "%s || true" % (_LIST % (REMOTE_DIR, REMOTE_DIR)), None, runner)
    return [os.path.basename(x) for x in out.decode(errors="replace").split()]


def fetch_remote(target, name=None, runner=subprocess.run):
    """(name, data) of one archive — the newest when no name is given."""
    if not name:
        names = list_remote(target, runner)
        if not names:
            raise KiwiError("%s keeps no fleet backups in %s" % (target, REMOTE_DIR))
        name = names[0]
    if not is_name(name):
        raise KiwiError("not a fleet backup name: %r" % name)
    return name, _ssh(target, "cat %s/%s" % (REMOTE_DIR, shlex.quote(name)), None, runner)


# ---- old snapshots of the machines' data (the backup and backup-server modules) ----------

KEEP = {"daily": 7, "weekly": 4, "monthly": 6}


def prune_repository(tgt, runtime, repo, image, password, keep=None, dry_run=False, runner=subprocess.run):
    """Forget what the rules no longer keep in one machine's repository and free the space.

    A machine can only add to its repository, so this happens on the backup node, on the
    files themselves, from the operator's machine: the node does not hold the password. It
    comes in on stdin and lives in the container for as long as this takes."""
    keep = keep or KEEP
    restic = "%s run --rm -i --network none --security-opt label=disable -v %s:/repo %s -r /repo --no-cache --retry-lock 5m" % (
        shlex.quote(runtime), shlex.quote(repo), shlex.quote(image))
    rules = " ".join("--keep-%s %d" % (k, n) for k, n in keep.items())
    script = "[ -d %s ] || { echo '[]'; exit 0; }\n%s forget --json --group-by host,tags %s%s" % (
        shlex.quote(repo), restic, rules, " --dry-run" if dry_run else "")
    data = (password + "\n").encode()
    if not dry_run:
        # A restic killed half way, a power cut say, leaves its lock and this would wait on it
        # for nothing. Only those go: a lock in use is refreshed and is never old enough.
        remote.run(tgt, "[ -d %s ] || exit 0\n%s unlock" % (shlex.quote(repo), restic), data, runner=runner)
    lines = remote.run(tgt, script, data, runner=runner).decode(errors="replace").splitlines()
    try:
        groups = json.loads(next((ln for ln in reversed(lines) if ln.startswith("[")), "[]"))
    except ValueError:
        raise KiwiError("%s: restic's answer about %s could not be read" % (tgt, repo))
    result = {"kept": sum(len(g.get("keep") or []) for g in groups),
              "removed": sum(len(g.get("remove") or []) for g in groups), "freed": ""}
    if result["removed"] and not dry_run:
        out = remote.run(tgt, restic + " prune", data, runner=runner).decode(errors="replace")
        result["freed"] = " ".join(ln.split(":", 1)[1].strip() for ln in out.splitlines() if ln.startswith("total prune:"))
    return result


# ---- pruning on a timer --------------------------------------------------------------------
#
# Only this computer holds the keys that may delete, so the timer lives here, as a systemd user
# unit per fleet. The PC may be off or the store locked: Persistent= runs a missed prune at the
# next login, and a failed one is in the journal and in the record `status` reads.

PRUNE_LATE = 21 * 86400
_EVERY = {"day": "daily", "week": "weekly"}


def _prune_unit(fleet_path):
    return "kiwi-prune-%s" % hashlib.sha256(fleet_path.encode()).hexdigest()[:10]


def _user_units():
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "systemd", "user")


def _systemctl_user(*args):
    return subprocess.run(["systemctl", "--user"] + list(args), capture_output=True, text=True)


def prune_timer(fleet_path, every, systemctl=_systemctl_user):
    """A timer on this computer that prunes the fleet's backup nodes every day or week, or none."""
    unit, d = _prune_unit(fleet_path), _user_units()
    if every == "off":
        systemctl("disable", "--now", unit + ".timer")
        for ext in (".service", ".timer"):
            if os.path.isfile(os.path.join(d, unit + ext)):
                os.remove(os.path.join(d, unit + ext))
        systemctl("daemon-reload")
        util.say("no timer prunes %s any more" % fleet_path)
        return
    cli = shutil.which("kiwi-server") or os.path.abspath(sys.argv[0])
    os.makedirs(d, exist_ok=True)
    util.write_bytes(os.path.join(d, unit + ".service"), (
        "[Unit]\nDescription=Kiwi Network: old snapshots off the backup nodes of %s\n\n"
        "[Service]\nType=oneshot\nExecStart=%s prune %s\n" % (fleet_path, shlex.quote(cli), shlex.quote(fleet_path))).encode(), 0o644)
    util.write_bytes(os.path.join(d, unit + ".timer"), (
        "[Unit]\nDescription=Kiwi Network: prune the backup nodes of %s every %s\n\n"
        "[Timer]\nOnCalendar=%s\nRandomizedDelaySec=1h\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n"
        % (fleet_path, every, _EVERY[every])).encode(), 0o644)
    systemctl("daemon-reload")
    r = systemctl("enable", "--now", unit + ".timer")
    if r.returncode != 0:
        raise KiwiError("the timer did not start: %s" % (r.stderr.strip().splitlines() or ["systemctl failed"])[-1])
    util.say("%s.timer prunes %s every %s (a missed run comes at the next login); journalctl --user -u %s"
             % (unit, fleet_path, every, unit))


def timer_on(fleet_path):
    return os.path.isfile(os.path.join(_user_units(), _prune_unit(fleet_path) + ".timer"))


def _prune_record(fleet):
    return fleet.resolve_path("output/prune.json")


def record_prune(fleet, nodes, failed, now=None):
    path = _prune_record(fleet)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    util.write_bytes(path, json.dumps({"time": int(now if now is not None else time.time()), "nodes": nodes,
                                       "failed": list(failed)}).encode(), 0o600)


def prune_note(fleet, host, now=None):
    """(level or None, text) about when this backup node was last pruned from here."""
    path, now = _prune_record(fleet), now if now is not None else time.time()
    try:
        rec = json.loads(util.read_bytes(path))
    except (OSError, ValueError):
        rec = None
    timer = "a weekly or daily timer prunes it" if timer_on(fleet.path) else "no timer prunes it: kiwi-server prune --every week"
    if not rec or host not in rec.get("nodes", []):
        return ("warn", "never pruned from here; %s" % timer)
    days = (now - rec["time"]) / 86400
    if rec.get("failed"):
        return ("warn", "the last prune %d days ago failed for %s; %s" % (days, ", ".join(rec["failed"]), timer))
    return ("warn" if days * 86400 > PRUNE_LATE else None, "pruned %d days ago; %s" % (days, timer))


# ---- a master's WireGuard identity ------------------------------------------------------
#
# Its data backup goes over the mesh, and the mesh is that identity: a new master could
# not fetch it. So the fleet keeps a copy with its other secrets, refreshed by apply and
# enroll, and a master that has none gets it back before its stack starts.

def carries_wireguard(host):
    return "vpn-server" in (host.modules or [])


def _wireguard_copy(host):
    return host.fleet.resolve_path("secrets/wg-easy/%s.json" % host.name)


def _wireguard_file(host):
    return "%s/km-vpn-server/wg0.json" % ((host.role_settings or {}).get("docker_dir") or "/home/user/docker")


def put_back_wireguard(host, tgt, runner=subprocess.run):
    """Only where the machine has none: what is there is newer than any copy."""
    path = _wireguard_copy(host)
    if not os.path.isfile(path):
        return False
    f = shlex.quote(_wireguard_file(host))
    script = ('[ -s %s ] && { echo present; exit 0; }\nmkdir -p "$(dirname %s)"\numask 027\ncat > %s.new && mv %s.new %s\necho restored'
              % (f, f, f, f, f))
    with open(path, "rb") as stored:
        out = remote.run(tgt, script, stored.read(), runner=runner)
    return b"restored" in out


def keep_wireguard(host, tgt, runner=subprocess.run):
    """The machine's state into the fleet's secrets, when there is one worth keeping."""
    f = shlex.quote(_wireguard_file(host))
    out = remote.run(tgt, "[ -s %s ] && cat %s || true" % (f, f), runner=runner)
    try:
        if not json.loads(out)["server"]["privateKey"]:
            return False
    except (ValueError, KeyError, TypeError):
        return False                        # not started yet, or not wg-easy's
    path = _wireguard_copy(host)
    if os.path.isfile(path) and util.read_bytes(path) == out:
        return False
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    util.write_bytes(path, out, 0o600)
    return True
