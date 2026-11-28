"""What an apply would change on a running machine, read-only: the stack's files, quadlets and
host units rendered here, compared by hash with the machine's, and the containers that would
restart for it, by the rule kiwi-stack itself applies (roles/common/lib.sh,
ks_stack_changed_services: a container restarts when its quadlet changed, or a file it mounts
or reads; one whose quadlet changed only in the names it gives the container, its AddHost lines,
takes them while it runs; a file its part reads again by itself, a module's `live` output like
the DNS names, restarts nothing), and the kiwi-stack tool and library. The rest of the role script
(packages, sshd, sysctl) is not compared.
"""
import hashlib
import shlex

from . import remote

QUADLETS = "/etc/containers/systemd"
UNITS = "/etc/systemd/system"
STACK_ENV = "/etc/kiwi-server/stack.env"
CLI = "/usr/local/bin/kiwi-stack"
LIB = "/usr/local/lib/kiwi-stack/lib.sh"
NAMELESS = "without its names:"         # a key of `have`: a container quadlet's hash without its AddHost lines


def _sha(content):
    return hashlib.sha256(content if isinstance(content, bytes) else content.encode()).hexdigest()


def _nameless(text):
    """A quadlet's hash without the names it gives its container (kiwi-stack: ks_nameless)."""
    return _sha("".join(line + "\n" for line in text.splitlines() if not line.startswith("AddHost=")))


def wanted(host, bundle):
    """{path on the machine: sha256} of what an apply would write there."""
    d = (host.role_settings or {}).get("docker_dir") or "/home/user/docker"
    out = {"%s/%s" % (d, rel): _sha(content) for rel, (content, _mode) in bundle.files.items()}
    out.update({"%s/%s" % (UNITS if n.endswith(".target") else QUADLETS, n): _sha(t)     # the target is a unit
                for n, t in (getattr(bundle, "quadlets", None) or {}).items()})
    out.update({"%s/%s" % (UNITS, n): _sha(t) for n, t in bundle.units.items()})
    # the stack's own tool and library: a new release changes how the stack starts, stops and reports
    from . import roles as rolesmod
    out[CLI] = _sha(rolesmod.stack_cli())
    out[LIB] = _sha(rolesmod.common_lib())
    return out


def live(host, bundle):
    """The paths on the machine whose change restarts nothing: their part reads them again by
    itself (module.yaml `live`; kiwi-stack: KS_STACK_LIVE)."""
    d = (host.role_settings or {}).get("docker_dir") or "/home/user/docker"
    return {"%s/%s" % (d, rel) for rel in getattr(bundle, "live", None) or ()}


def machine(tgt, paths):
    """(release that wrote the stack, {path: sha256}, [services]) as the machine has them."""
    script = ("head -n1 %s 2>/dev/null | sed 's/^/V /'\n" % STACK_ENV +
              "for p in %s; do [ -f \"$p\" ] && sha256sum \"$p\" | sed 's/^/H /'; done\n" % " ".join(shlex.quote(p) for p in paths) +
              "for p in %s; do [ -f \"$p\" ] && echo \"N $(grep -v '^AddHost=' \"$p\" | sha256sum | cut -d' ' -f1)  $p\"; done\n"
              % (" ".join(shlex.quote(p) for p in paths if p.endswith(".container")) or "''") +
              "sed -n 's/^STACK_SERVICES=\"\\(.*\\)\"$/S \\1/p' %s 2>/dev/null\n" % STACK_ENV)
    version, have, services = "", {}, []
    for line in remote.run(tgt, script).decode(errors="replace").splitlines():
        kind, _sp, rest = line.partition(" ")
        if kind == "V":
            version = rest.rsplit("kiwi-server ", 1)[-1].split(" ")[0] if "kiwi-server " in rest else ""
        elif kind == "H":
            digest, _sp, path = rest.partition("  ")
            have[path] = digest
        elif kind == "N":
            digest, _sp, path = rest.partition("  ")
            have[NAMELESS + path] = digest
        elif kind == "S":
            services = rest.split()
    return version, have, services


def compare(bundle, want, have, services_before, live=()):
    changed = sorted(p for p in want if have.get(p) != want[p])
    counted = [p for p in changed if p not in live]       # what a container restarts for
    quadlets = getattr(bundle, "quadlets", None) or {}
    restart, new, names = [], [], []
    for svc in bundle.services:
        q = "%s/%s.container" % (QUADLETS, svc)
        if q in want and q not in have:
            new.append(svc)
            continue
        hit = q in changed
        named = hit and have.get(NAMELESS + q) == _nameless(quadlets.get(svc + ".container") or "")
        if named:           # only its names changed: unless a file it mounts changed too, it goes on running
            hit = False
        for line in (quadlets.get(svc + ".container") or "").splitlines():
            if hit:
                break
            if line.startswith("Volume=/"):
                src = line[len("Volume="):].split(":", 1)[0]
            elif line.startswith("EnvironmentFile=/"):
                src = line[len("EnvironmentFile="):]
            else:
                continue
            hit = any(p == src or p.startswith(src.rstrip("/") + "/") for p in counted)
        if hit:
            restart.append(svc)
        elif named:
            names.append(svc)
    removed = [s for s in services_before if s not in bundle.services]
    return {"changed": [dict({"path": p, "new": p not in have}, **({"live": True} if p in live else {})) for p in changed],
            "restart": restart, "names": names,
            "new_services": new, "removed_services": removed, "quadlets": bool(quadlets)}
