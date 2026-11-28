"""Small helpers shared by every module: output, hashing, files."""
import base64
import hashlib
import os
import shlex
import subprocess
import sys
import warnings


from kiwimodules.util import KiwiError, b64, deep_merge, read_bytes, read_text  # noqa: E402,F401


def proxies():
    """The addresses whose X-Real-IP is believed (KIWI_PROXY, comma separated): the reverse proxy in
    front of a page. Anyone else could write any address there and slip past a limit per address."""
    return {x.strip() for x in os.environ.get("KIWI_PROXY", "").split(",") if x.strip()}


def client_address(request, none="?"):
    """Who asks: the address the proxy saw when the request comes through the proxy, else the peer's own."""
    peer = request.client.host if request.client else ""
    if peer and peer in proxies():
        return request.headers.get("x-real-ip") or peer
    return peer or none


def forwarded_allow():
    """uvicorn's forwarded_allow_ips: the proxy only (127.0.0.1 when none is named)."""
    return ",".join(sorted(proxies())) or "127.0.0.1"


_held = __import__("threading").local()


def dir_lock(d):
    """One writer at a time in folder `d`, across processes and threads (flock on d/.kiwi-lock).
    The same thread may take it again inside (a certificate that makes the CA first)."""
    import contextlib
    import fcntl

    @contextlib.contextmanager
    def held():
        key = os.path.realpath(d)
        mine = getattr(_held, "dirs", None)
        if mine is None:
            mine = _held.dirs = set()
        if key in mine:
            yield
            return
        guard_store_dir(d)                  # never the store's folder on disk (a locked store)
        os.makedirs(d, mode=0o700, exist_ok=True)
        fd = os.open(os.path.join(d, ".kiwi-lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            mine.add(key)
            yield
        finally:
            mine.discard(key)
            os.close(fd)
    return held()


def tmp_name(path):
    """A temporary name beside `path` that no other process or thread writing `path` at the same
    moment uses: two writers on one shared ".tmp" interleave, and the one renamed last may be
    half of each (seen: a store that the recovery key no longer opened, after a backup and the
    control node's own look ran together)."""
    import threading
    return "%s.%d-%d.tmp" % (path, os.getpid(), threading.get_ident())


def settle(tmp, path):
    """`tmp` becomes `path`, and stays so through a power cut: its bytes are forced to the
    disk before the name changes, and the folder after. Without this a file written and
    renamed a moment before the power went out came back empty (seen on a control node: the
    whole encrypted store, 0 bytes, a minute after a push)."""
    fd = os.open(tmp, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    try:
        d = os.open(os.path.dirname(os.path.abspath(path)), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(d)
    except OSError:
        pass                    # a file system that does not sync folders
    finally:
        os.close(d)


def guard_store_dir(d):
    """Never make the secret store's folder on disk: with the store locked its link is gone, and
    a write into secrets/ made a plain folder beside secrets.age (an apply that outlived the
    idle lock, 23 Nov 2026). Any folder still to be made whose name plus .age exists is it."""
    d = os.path.abspath(d)
    while d and not os.path.lexists(d):
        if os.path.exists(d + ".age"):
            raise KiwiError("the secret store is locked: kiwi-server secrets unlock <fleet> (nothing was written to %s)"
                            % d)
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent


def _write(path, data, mode, binary):
    if os.path.islink(path):        # the file the link names: a control node's fleet file is a
        path = os.path.realpath(path)   # link into RAM, and a copy beside the link would be on disk
    d = os.path.dirname(path)
    if d:
        guard_store_dir(d)
        os.makedirs(d, exist_ok=True)
    tmp = tmp_name(path)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with (os.fdopen(fd, "wb") if binary else os.fdopen(fd, "w", encoding="utf-8")) as f:
        f.write(data)
    os.chmod(tmp, mode)
    settle(tmp, path)


def write_text(path, text, mode=0o644):
    """Write a file whole or not at all, with the requested mode (secrets get 0600), and
    so that it survives a power cut once this returns."""
    _write(path, text, mode, False)


def write_bytes(path, data, mode=0o644):
    _write(path, data, mode, True)


QUIET = False


def say(msg):
    if not QUIET:
        print(":: %s" % msg)


def log(msg):
    if not QUIET:
        print(msg)


def note(msg):
    """A say on stderr: for what a command notices on the way, so that its own output (JSON with
    --porcelain) stays clean (28 Nov 2026: the note about the control node's home address broke
    `client list --porcelain` for every script reading it)."""
    if not QUIET:
        print(":: %s" % msg, file=sys.stderr)


def warn(msg):
    print("warning: %s" % msg, file=sys.stderr)


def die(msg, code=1):
    print("error: %s" % msg, file=sys.stderr)
    sys.exit(code)


def quote(s):
    return shlex.quote(str(s))


_SALT_ALPHABET = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def derive_salt(*parts):
    """A 16-character crypt salt derived from its inputs.

    Deterministic on purpose: rendering the same fleet twice gives the same
    hash, so outputs can be diffed and an unchanged host shows as unchanged."""
    h = hashlib.sha256(":".join(parts).encode()).digest()
    return "".join(_SALT_ALPHABET[b % len(_SALT_ALPHABET)] for b in h[:16])


def sha512_crypt(password, salt):
    """SHA-512 crypt(3) hash, usable by Ignition (passwd) and d-i (preseed).

    `crypt` left the standard library in Python 3.13; Fedora ships crypt_r in
    its place, and openssl / mkpasswd are the fallbacks on anything else."""
    full = "$6$%s" % salt
    for modname in ("crypt_r", "crypt"):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                mod = __import__(modname)
            out = mod.crypt(password, full)
            if out and out.startswith("$6$"):
                return out
        except ImportError:
            continue
    for argv, stdin in (
        (["openssl", "passwd", "-6", "-salt", salt, "-stdin"], password + "\n"),
        (["mkpasswd", "-m", "sha-512", "-S", salt, "-s"], password + "\n"),
    ):
        try:
            out = subprocess.run(argv, input=stdin, capture_output=True, text=True,
                                 check=True).stdout.strip()
            if out.startswith("$6$"):
                return out
        except (OSError, subprocess.CalledProcessError):
            continue
    raise KiwiError("cannot hash a password: need python crypt_r/crypt, openssl or mkpasswd")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def which(name):
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def home_dir():
    """The directory holding roles/ and container/ — set by bin/kiwi-server."""
    h = os.environ.get("KIWI_SERVER_HOME")
    if h:
        return h
    # running from a checkout: lib/kiwiserver/util.py -> repo root
    return os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def parent(path):
    return os.path.dirname(os.path.abspath(path))


def shown_path(path):
    """A path as a person reads it: relative below the working directory, else from ~."""
    rel = os.path.relpath(path)
    if not rel.startswith(".."):
        return rel
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def typed_path(path):
    """A path for a command a person copies: as shown_path writes it, quoted where it must be,
    a leading ~/ left outside the quotes so the shell still expands it."""
    shown = shown_path(os.path.abspath(path))
    if shown.startswith("~/"):
        return "~/" + shlex.quote(shown[2:])
    return shlex.quote(shown)


def count(n, word, many=None):
    """1 day, 2 days: a number with its word, never "1 days"."""
    n = int(n)
    return "%d %s" % (n, word if n == 1 else (many or word + "s"))
