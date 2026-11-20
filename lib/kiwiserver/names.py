"""Names by service (docs/design/scale.md): what a part is called on the network.

By default a part is named after its machine: `paste.quince.home`. With `names: service` under
`defaults:` (or on one machine) a part that runs on one machine only is named for itself:
`paste.home`. The name then stays when the part moves to another machine, and nothing people
keep (a link, a client's server address) changes with the move. The machine's name for it
stays as a second name where the module keeps one.

A module says how it is named with `named_by: {setting, word, url}` (kiwi-modules 0.103): the
setting that holds its name, its own word, and whether the setting holds a URL. A name set by
hand in that setting always counts.
"""
from .util import KiwiError

KINDS = ("machine", "service")
# these keep their machine's name: computers that joined point at the control page's, and the
# backups at the backup server's
STAY = ("control", "backup-server")


def kind(host):
    return str(host.cfg.get("names") or "machine")


def _named(ms, module):
    m = ms.modules.get(module)
    return getattr(m, "named_by", None) if m is not None else None


def _listed(host):
    """The modules the fleet file lists for this machine (the ones a person put there)."""
    return [str(x) for x in (host.role_settings_raw or {}).get("modules") or []]


def _by_hand(host, module, nb):
    v = ((host.role_settings_raw or {}).get(module) or {}).get(nb["setting"])
    return str(v).strip() if v else ""


def _bare(value):
    """A name without its scheme and path: https://vault.home/ -> vault.home"""
    v = str(value).strip()
    for scheme in ("https://", "http://"):
        if v.startswith(scheme):
            v = v[len(scheme):]
    return v.split("/", 1)[0].lower()


def name_of(fleet, host, module, ms, given=None):
    """The name this part answers to: set by hand, by service, or after its machine. None for a
    module that has no name of its own."""
    nb = _named(ms, module)
    if nb is None:
        return None
    hand = _by_hand(host, module, nb)
    if hand:
        return _bare(hand)
    if given is not None and (host.name, module) in given:
        return given[(host.name, module)]
    return "%s.%s" % (nb["word"], host.hostname)


def plan(fleet, ms):
    """{(machine, module): name} of the parts that are named by service: the machine wants it
    (`names: service`), the part's name is not set by hand, and no other machine has the part."""
    where = {}
    for h in fleet.hosts.values():
        for module in _listed(h):
            if _named(ms, module) is not None:
                where.setdefault(module, []).append(h)
    out = {}
    for module, hosts in where.items():
        if len(hosts) != 1 or module in STAY:
            continue                    # the same part twice: each keeps its machine's name
        h, nb = hosts[0], _named(ms, module)
        domain = str(h.cfg.get("domain") or "")
        if kind(h) == "service" and domain and not _by_hand(h, module, nb):
            out[(h.name, module)] = "%s.%s" % (nb["word"], domain)
    return out


def errors(fleet, ms):
    """What is wrong with the names: an unknown kind, or one name for two parts."""
    errs = ["%s: names: %r is not one of %s" % (h.name, h.cfg.get("names"), ", ".join(KINDS))
            for h in fleet.hosts.values() if kind(h) not in KINDS]
    if errs:
        return errs
    given, seen = plan(fleet, ms), {}
    for h in fleet.hosts.values():
        for module in _listed(h):
            name = name_of(fleet, h, module, ms, given)
            if name is None:
                continue
            if name in seen and seen[name] != (h.name, module):
                errs.append("the name %s is wanted twice: by %s on %s and by %s on %s. Give one of them another "
                            "name (its setting %s)" % (name, seen[name][1], seen[name][0], module, h.name,
                                                       _named(ms, module)["setting"]))
            seen.setdefault(name, (h.name, module))
    return errs


def assign(fleet, ms):
    """Write the names by service into the machines' settings as they are held in memory (never
    into the fleet file): every command then renders, checks and lists the part under that name.
    Returns {(machine, module): name}. Raises on names that clash."""
    errs = errors(fleet, ms)
    if errs:
        raise KiwiError("\n".join(errs))
    given = plan(fleet, ms)
    for (host, module), name in given.items():
        h, nb = fleet.hosts[host], _named(ms, module)
        raw = h.role_settings_raw.setdefault(module, {})
        if not isinstance(raw, dict):
            continue
        raw[nb["setting"]] = ("https://" + name) if nb["url"] else name
    return given
