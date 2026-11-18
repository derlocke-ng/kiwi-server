"""Module sources: modules besides the built-in ones (kiwi-modules), from a git repository of
your own or a community one, or a folder on this computer while you write a module.

They are listed by hand in the fleet file, never found by themselves:

    module_sources:
      - name: community
        url: https://github.com/derlocke-ng/kiwi-modules-community
        commit: 0123456789abcdef0123456789abcdef01234567
      - name: mine
        path: ~/src/smartfritz/module          # this computer only

A repository is fetched at exactly that commit, into a cache, by whatever renders the fleet
(this computer, another one, the control node): the same modules everywhere, and nothing
changes until `kiwi-server sources FLEET update NAME` moves the pin. Its modules are a folder
each, in `modules/` or at the top. They render like built-in ones, and their templates and
scripts run as root on your machines: add only sources you trust.
"""
import hashlib
import os
import re
import shutil
import subprocess
import tempfile

from . import util
from .util import KiwiError

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
URL = re.compile(r"^(https://[A-Za-z0-9.-]+(:[0-9]+)?/[A-Za-z0-9._~/-]+|file:///[^\s]+)$")


def check(raw):
    """(sources, errors) of a fleet file's `module_sources`: each {name, url, commit} or {name, path}."""
    out, errs = [], []
    if raw in (None, []):
        return out, errs
    if not isinstance(raw, list):
        return out, ["module_sources must be a list"]
    names = set()
    for i, s in enumerate(raw):
        where = "module_sources[%d]" % i
        if not isinstance(s, dict):
            errs.append("%s: name, and url with commit or path" % where)
            continue
        name = str(s.get("name") or "")
        if not NAME.match(name):
            errs.append("%s: name is lowercase letters, digits and dashes: %r" % (where, name))
            continue
        if name in names:
            errs.append("module_sources: two are called %s" % name)
        names.add(name)
        unknown = sorted(set(s) - {"name", "url", "commit", "path", "note", "tag"})
        if unknown:
            errs.append("%s (%s): unknown key %s" % (where, name, ", ".join(unknown)))
        if s.get("path") and s.get("url"):
            errs.append("%s (%s): url or path, not both" % (where, name))
        elif s.get("path"):
            out.append({"name": name, "path": str(s["path"])})
        elif s.get("url"):
            if not URL.match(str(s["url"])):
                errs.append("%s (%s): url is an https address of a git repository" % (where, name))
            elif not COMMIT.match(str(s.get("commit") or "")):
                errs.append("%s (%s): commit is the 40 hex digits of the commit to take "
                            "(kiwi-server sources FLEET add writes it)" % (where, name))
            else:
                out.append(dict({"name": name, "url": str(s["url"]), "commit": str(s["commit"])},
                                **({"tag": str(s["tag"])} if s.get("tag") else {})))
        else:
            errs.append("%s (%s): url with commit, or path" % (where, name))
    return out, errs


def cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "kiwi-server", "module-sources")


def _git(*argv, cwd=None, timeout=300):
    if not shutil.which("git"):
        raise KiwiError("module sources need git on this computer (dnf install git-core, apt install git)")
    r = subprocess.run(["git"] + list(argv), cwd=cwd, capture_output=True, text=True, timeout=timeout,
                       env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    if r.returncode:
        raise KiwiError("git %s: %s" % (argv[0], (r.stderr.strip().splitlines() or ["failed"])[-1]))
    return r.stdout


def _modules_in(folder):
    sub = os.path.join(folder, "modules")
    return sub if os.path.isdir(sub) else folder


def fetch(src, cache=None):
    """The folder with a source's modules: a path as it is; a repository at its commit, fetched
    into the cache once (a commit never changes, so what is there is it)."""
    if src.get("path"):
        folder = os.path.expanduser(src["path"])
        if not os.path.isdir(folder):
            raise KiwiError("module source %s: %s is not a folder on this computer" % (src["name"], folder))
        return _modules_in(folder)
    cache = cache or cache_dir()
    key = hashlib.sha256(src["url"].encode()).hexdigest()[:10]
    dest = os.path.join(cache, "%s-%s-%s" % (src["name"], key, src["commit"][:12]))
    if not os.path.isdir(dest):
        os.makedirs(cache, exist_ok=True)
        work = tempfile.mkdtemp(prefix=".fetch-", dir=cache)
        try:
            _git("init", "-q", cwd=work)
            _git("fetch", "-q", "--depth", "1", src["url"], src["commit"], cwd=work)
            _git("checkout", "-q", "--detach", "FETCH_HEAD", cwd=work)
            got = _git("rev-parse", "HEAD", cwd=work).strip()
            if got != src["commit"]:
                raise KiwiError("module source %s: %s gave commit %s, not %s" % (src["name"], src["url"], got, src["commit"]))
            shutil.rmtree(os.path.join(work, ".git"))
            os.rename(work, dest)
        finally:
            if os.path.isdir(work):
                shutil.rmtree(work, ignore_errors=True)
    return _modules_in(dest)


def folders(fleet, cache=None):
    """[(name, folder)] of a fleet's module sources, fetched where needed: for kiwimodules' discover()."""
    return [(s["name"], fetch(s, cache)) for s in getattr(fleet, "module_sources", None) or []]


def _semver(tag):
    m = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag)
    return tuple(int(x) for x in m.groups()) if m else None


def latest(url):
    """(tag, commit) of a repository's newest version tag (v1.2.3), or None when it has none."""
    out = _git("ls-remote", "--tags", url, "v*", timeout=60)
    found = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[1].startswith("refs/tags/"):
            continue
        name = parts[1][len("refs/tags/"):]
        peeled = name.endswith("^{}")
        name = name[:-3] if peeled else name
        if _semver(name) and (peeled or name not in found):     # an annotated tag's commit wins over the tag object
            found[name] = parts[0]
    if not found:
        return None
    tag = max(found, key=_semver)
    return tag, found[tag]


def pin(url, ref=None):
    """(commit, tag or "") to pin: the newest version tag when the repository has some and nothing
    else is asked for, else what `ref` (a branch, tag or commit) or the default branch is now."""
    if not ref:
        got = latest(url)
        if got:
            return got[1], got[0]
    commit = resolve(url, ref)
    return commit, (ref if ref and _semver(ref) else "")


def resolve(url, ref=None):
    """The commit a branch, tag or commit of a repository is now."""
    if ref and COMMIT.match(ref):
        return ref
    out = _git("ls-remote", url, ref or "HEAD", timeout=60)
    rows = [line.split() for line in out.splitlines() if line.strip()]
    if ref and not rows:
        raise KiwiError("%s has no branch or tag called %s" % (url, ref))
    # a tag's own object and the commit it points at: the commit (^{}) when there is one
    peeled = [r[0] for r in rows if r[1].endswith("^{}")]
    if peeled:
        return peeled[0]
    if not rows:
        raise KiwiError("%s answered nothing: is it a git repository?" % url)
    return rows[0][0]


def write(fleet_path, sources):
    """The fleet file with these module sources (before `hosts:`, which stays last)."""
    from . import form
    path = os.path.realpath(fleet_path)
    doc = form.Doc.parse(util.read_text(path))
    data = {k: v for k, v in doc.data.items() if k not in ("hosts", "module_sources")}
    if sources:
        data["module_sources"] = sources
    data["hosts"] = doc.data["hosts"]
    doc.data = data
    util.write_text(path, doc.dump(), 0o600)


def raw(fleet):
    return list((getattr(fleet, "raw", None) or {}).get("module_sources") or [])


SCAFFOLD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scaffold")


def scaffold(folder, name="hello"):
    """A new module source to start from: the layout, the example module (renamed to `name`), the
    check that renders every module, and its CI. Never over something that is there."""
    if not NAME.match(name):
        raise KiwiError("a module's name is lowercase letters, digits and dashes: %r" % name)
    dest = os.path.abspath(os.path.expanduser(folder))
    if os.path.exists(dest) and os.listdir(dest):
        raise KiwiError("%s is not empty: a new module source goes into a new folder" % dest)
    shutil.copytree(SCAFFOLD, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    if name != "hello":
        old, new = os.path.join(dest, "modules", "hello"), os.path.join(dest, "modules", name)
        os.rename(old, new)
        for f in os.listdir(new):
            p = os.path.join(new, f)
            text = util.read_text(p).replace("hello", name).replace("Hello", name.capitalize())
            util.write_text(p, text)
    os.chmod(os.path.join(dest, "tools", "check-modules.py"), 0o755)
    return dest
