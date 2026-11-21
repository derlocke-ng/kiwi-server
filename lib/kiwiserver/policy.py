"""One table per part (docs/design/authority.md, step 3): for every part (a module on a machine),
in one place, what happens to it when its machine stops. The page shows its sentence for every part.

    where    here: on its machine only (a gateway, a site's DNS, an unlock ring)
             move: it can go to another machine (`move`, or `switch` to a copy kept ready)
    copies   one, or one with a copy kept ready on another machine (keep_ready)
    need     must (the network needs it: an emergency when its machine is gone), normal, wait

No part goes to another machine by itself (the owner, 22 Nov 2026): what its machine held when it
went silent is on no other one. It comes back from its backups when that machine is installed
anew, or it is moved while its machine runs. The kinds of parts bring their rows; the fleet file changes `need`:

    defaults:
      parts:
        vault: { need: must }
        games: { need: wait }
"""
from .util import KiwiError

WHERE = ("here", "move")
NEED = ("must", "normal", "wait")
KEYS = ("need",)                    # what the fleet file may set per part

# what the kinds of parts bring; everything else can move and is needed as usual
BUILT_IN = {
    "gateway": {"where": "here", "need": "must"},
    "dns": {"where": "here", "need": "must"},
    "dhcp-relay": {"where": "here", "need": "must"},
    "vpn-server": {"where": "here", "need": "must"},
    "vpn-client": {"where": "here"},
    "reverse-proxy": {"where": "here"},
    "tang": {"where": "here", "need": "must"},
    "control": {"where": "here", "need": "must"},
    "backup": {"where": "here"},
    "backup-server": {"where": "here"},
    "fail2ban": {"where": "here"},
    "cache": {"where": "here", "need": "wait"},
    "networks": {"where": "here"},
    "games": {"need": "wait"},
    "downloader": {"need": "wait"},
    "portainer": {"need": "wait"},
}


def table(fleet):
    """The fleet file's `defaults.parts`, checked: {part: {need}}."""
    raw = (fleet.defaults or {}).get("parts") or {}
    if not isinstance(raw, dict):
        raise KiwiError("defaults.parts: a map of parts, each with need")
    out = {}
    for part, row in raw.items():
        if not isinstance(row, dict):
            raise KiwiError("defaults.parts.%s: need, as a map" % part)
        for k, v in row.items():
            if k == "lost":         # an older release's switch by itself: gone, the key does nothing now
                continue
            if k not in KEYS:
                raise KiwiError("defaults.parts.%s.%s: only need can be set" % (part, k))
            if v not in NEED:
                raise KiwiError("defaults.parts.%s.%s: one of %s" % (part, k, ", ".join(NEED)))
        out[str(part)] = {k: v for k, v in row.items() if k in KEYS}
    return out


def row(fleet, host, part, standby=None):
    """The table's row for `part` on `host`: {part, machine, where, copies, need, standby}.
    `standby`: the machine that keeps a copy of it ready (keep_ready), or None."""
    base = dict(BUILT_IN.get(part, {}))
    need = table(fleet).get(part, {}).get("need") or base.get("need") or "normal"
    return {"part": part, "machine": host.name, "where": base.get("where", "move"),
            "copies": "standby" if standby else "one", "need": need, "standby": standby}


def sentence(r):
    """What happens to the part when its machine stops, in one short sentence."""
    m, p = r["machine"], r["part"]
    if r["where"] == "here":
        what = "runs on %s only, away while %s is" % (m, m)
    elif r["copies"] == "standby":
        what = "away while %s is; a copy is kept ready on %s, to move it there while %s runs" % (m, r["standby"], m)
    else:
        what = "away while %s is; back from its backups if %s is installed anew" % (m, m)
    return "%s: %s.%s" % (p, what, {"must": " The network needs it.", "wait": " It can wait.", "normal": ""}[r["need"]])


def summary(machine, rows):
    """One sentence for a machine: what its stop does to its parts."""
    needed = [r["part"] for r in rows if r["need"] == "must"]
    rest = len(rows) - len(needed)
    if not rows:
        return "When %s stops: nothing runs there." % machine
    if needed:
        return "When %s stops: %s (the network needs %s)%s away until it is back." % (
            machine, ", ".join(needed), "it" if len(needed) == 1 else "them",
            " and %d more are" % rest if rest else " is" if len(needed) == 1 else " are")
    return "When %s stops: %s away until it is back." % (machine, "its part is" if rest == 1 else "its %d parts are" % rest)


def rows(fleet, roles):
    """Every part of every machine: [row]."""
    from . import partstandby
    out = []
    for h in fleet.hosts.values():
        kept = partstandby.kept(h)
        for part in sorted(h.modules or []):
            standby = kept.get(part) if kept.get(part) in fleet.hosts else None
            out.append(row(fleet, h, part, standby))
    return out
