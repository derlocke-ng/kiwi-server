"""Small helpers shared by the package (and by kiwi-server, which re-exports them)."""
import base64
import copy
import os


class KiwiError(Exception):
    """A user-facing error: printed as `error: ...`, exit 1, no traceback."""


def deep_merge(base, over, path=(), extend=()):
    """base overridden by over, mapping by mapping; lists replace, except the
    key paths in `extend`, where they are extended without duplicates."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        p = path + (k,)
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v, p, extend)
        elif isinstance(v, list) and isinstance(out.get(k), list) and p in extend:
            out[k] = out[k] + [x for x in v if x not in out[k]]
        else:
            out[k] = copy.deepcopy(v)
    return out


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


def repo_root():
    """The checkout or install that holds modules/ and the profiles: two levels
    above this package (lib/kiwimodules/ in both kiwi-modules and kiwi-server)."""
    return os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def modules_root():
    return os.environ.get("KIWI_MODULES_HOME") or os.environ.get("KIWI_SERVER_MODULES") or os.path.join(_home(), "modules")


def profiles_root():
    """profiles/ in kiwi-modules; roles/ in kiwi-server (the same role.yaml format)."""
    if os.environ.get("KIWI_MODULES_PROFILES"):
        return os.environ["KIWI_MODULES_PROFILES"]
    if os.environ.get("KIWI_SERVER_ROLES"):
        return os.environ["KIWI_SERVER_ROLES"]
    for name in ("profiles", "roles"):
        p = os.path.join(_home(), name)
        if os.path.isdir(p):
            return p
    return os.path.join(_home(), "profiles")


def _home():
    return os.environ.get("KIWI_SERVER_HOME") or repo_root()
