"""One table per part (docs/design/authority.md, step 3): for every part (a module on a machine),
in one place, what happens to it when its machine stops. One engine reads it; the page shows its
sentence for every part.

    where    here: on its machine only (a gateway, a site's DNS, an unlock ring)
             move: it can go to another machine
    copies   one, or one with a standby (keep_ready); "two at once" is not built
    lost     its machine is lost: switch (the standby takes it by itself), wait (until the machine
             is back), ask (a person switches)
    alone    its machine reaches no other voter: stop (when another could take it), keep
    need     must (the network needs it: told at once), normal, wait (can wait)

The defaults come from the part's kind below, `recovery: switch` for `lost`, and the fleet file
changes them per part:

    defaults:
      parts:
        vault: { lost: ask, need: must }
        games: { need: wait }

`alone` follows from `lost`: a part that is switched by itself stops on a machine that reaches no
voter (it may already run elsewhere); one that waits or is switched by a person keeps running.
"""
from .util import KiwiError

WHERE = ("here", "move")
LOST = ("switch", "wait", "ask")
NEED = ("must", "normal", "wait")
KEYS = ("lost", "need")             # what the fleet file may set per part

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
    """The fleet file's `defaults.parts`, checked: {part: {lost, need}}."""
    raw = (fleet.defaults or {}).get("parts") or {}
    if not isinstance(raw, dict):
        raise KiwiError("defaults.parts: a map of parts, each with lost and need")
    out = {}
    for part, row in raw.items():
        if not isinstance(row, dict):
            raise KiwiError("defaults.parts.%s: lost and need, as a map" % part)
        for k, v in row.items():
            if k not in KEYS:
                raise KiwiError("defaults.parts.%s.%s: only %s can be set" % (part, k, " and ".join(KEYS)))
            if v not in {"lost": LOST, "need": NEED}[k]:
                raise KiwiError("defaults.parts.%s.%s: one of %s" % (part, k, ", ".join({"lost": LOST, "need": NEED}[k])))
        out[str(part)] = dict(row)
    return out


def row(fleet, host, part, standby=None, witness=None):
    """The table's row for `part` on `host`: {part, machine, where, copies, lost, alone, need,
    standby, witness}. `standby`, `witness`: as keep_ready and lease.witness say (None: none)."""
    from . import watchdog
    base = dict(BUILT_IN.get(part, {}))
    switch_on, _after = watchdog.switch_policy(fleet)
    lost = "switch" if switch_on else "ask"
    if not standby:
        lost = "wait"                       # nowhere to go: it waits for its machine
    elif not witness:
        lost = lost                         # switched by the control node, as before permissions
    over = table(fleet).get(part, {})
    if standby and over.get("lost"):
        lost = over["lost"]
    need = over.get("need") or base.get("need") or "normal"
    return {"part": part, "machine": host.name, "where": base.get("where", "move"),
            "copies": "standby" if standby else "one", "lost": lost,
            "alone": "stop" if (lost == "switch" and witness) else "keep", "need": need,
            "standby": standby, "witness": witness}


def sentence(r):
    """What happens to the part when its machine stops, in one short sentence."""
    m, p, sb = r["machine"], r["part"], r["standby"]
    if r["copies"] == "standby" and r["lost"] == "switch" and r["witness"]:
        what = "%s takes it over about four minutes after %s stops (witness: %s)" % (sb, m, r["witness"])
    elif r["copies"] == "standby" and r["lost"] == "switch":
        what = "the control node switches it to %s five minutes after %s stops" % (sb, m)
    elif r["copies"] == "standby" and r["lost"] == "ask":
        what = "kept ready on %s; a person switches it" % sb
    elif r["copies"] == "standby":
        what = "kept ready on %s; it waits for %s" % (sb, m)
    elif r["where"] == "here":
        what = "runs on %s only, away while %s is" % (m, m)
    else:
        what = "away while %s is (no standby)" % m
    return "%s: %s.%s" % (p, what, {"must": " The network needs it.", "wait": " It can wait.", "normal": ""}[r["need"]])


def summary(machine, rows):
    """One sentence for a machine: what its stop does to its parts."""
    moved = ["%s goes to %s%s" % (r["part"], r["standby"], " after about four minutes" if r["lost"] == "switch" and r["witness"]
                                  else " after five minutes" if r["lost"] == "switch" else " when a person switches it" if r["lost"] == "ask"
                                  else " only when it is back")
             for r in rows if r["copies"] == "standby"]
    away = [r for r in rows if r["copies"] != "standby"]
    needed = [r["part"] for r in away if r["need"] == "must"]
    rest = len(away) - len(needed)
    bits = list(moved)
    if needed:
        bits.append("%s (the network needs %s)%s away until it is back" % (
            ", ".join(needed), "it" if len(needed) == 1 else "them",
            " and %d more are" % rest if rest else " is" if len(needed) == 1 else " are"))
    elif rest:
        bits.append("its %d parts are away until it is back" % rest if rest > 1 else "its part is away until it is back")
    return "When %s stops: %s." % (machine, "; ".join(bits) if bits else "nothing runs there")


def rows(fleet, roles):
    """Every part of every machine: [row]."""
    from . import lease as leasemod, partstandby
    out = []
    for h in fleet.hosts.values():
        kept = partstandby.kept(h)
        for part in sorted(h.modules or []):
            standby = kept.get(part) if kept.get(part) in fleet.hosts else None
            witness = leasemod.witness(fleet, h.name, standby, leasemod.master_of(roles)) if standby else None
            out.append(row(fleet, h, part, standby, witness))
    return out
