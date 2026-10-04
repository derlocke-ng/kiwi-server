"""Small helpers shared by every module: output, hashing, files."""
import base64
import hashlib
import os
import shlex
import subprocess
import sys
import warnings


class KiwiError(Exception):
    """A user-facing error: printed as `error: ...`, exit 1, no traceback."""


QUIET = False


def say(msg):
    if not QUIET:
        print(":: %s" % msg)


def log(msg):
    if not QUIET:
        print(msg)


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


def b64(data):
    if isinstance(data, str):
        data = data.encode()
    return base64.b64encode(data).decode()


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write_text(path, text, mode=0o644):
    """Write a file atomically with the requested mode (secrets get 0600)."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def write_bytes(path, data, mode=0o644):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


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
