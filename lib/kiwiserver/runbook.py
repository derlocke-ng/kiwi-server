"""The runbook (docs/design/runbook.md): what happens in which case, as the admins wrote it
beforehand in the fleet file. Fixed lists of cases and actions, no rule language; the first line
that fits a machine (and a part) wins; a case no line covers is told, and nothing is done.

    defaults:
      runbook:
        - { case: part-down, for: "role:node", do: restart, after: 2, tries: 3 }
        - { case: machine-gone, for: sorbet, do: restart, after: 15 }

Step 1 of the design: the table, checked, and its sentences per machine. Nothing acts by it yet."""
from .util import KiwiError

# case: (who sees it, its actions, what it is in words)
CASES = {
    "part-down": ("machine", ("restart", "tell"), "a part is down"),
    "disk-full": ("machine", ("prune-images", "prune-snapshots", "tell"), "its disk fills up"),
    "update-failed": ("machine", ("roll-back", "tell"), "an update fails"),
    "cut-off": ("machine", ("keep-running", "stop", "tell"), "it is cut off from the network"),
    "machine-gone": ("control", ("restart", "tell"), "it does not answer"),
    "backup-overdue": ("control", ("backup-now", "tell"), "its backup is overdue"),
    "certificate-due": ("control", ("apply", "tell"), "a certificate runs out"),
}
KEYS = ("case", "for", "do", "after", "tries", "at")
DOING = {"restart": "restarted", "tell": "you are told", "prune-images": "old images are removed",
         "prune-snapshots": "old snapshots are removed", "roll-back": "it goes back to the image before",
         "keep-running": "it keeps running", "stop": "its parts with a permission stop",
         "backup-now": "a backup starts", "apply": "it is applied"}


def lines(cfg, where="runbook"):
    """The lines of a machine's (or the defaults') `runbook`, checked: [dict]."""
    raw = (cfg or {}).get("runbook") or []
    if not isinstance(raw, list):
        raise KiwiError("%s: a list of lines, each with case, for and do" % where)
    out = []
    for i, line in enumerate(raw):
        at = "%s[%d]" % (where, i)
        if not isinstance(line, dict):
            raise KiwiError("%s: a line is a map with case, for and do" % at)
        bad = [k for k in line if k not in KEYS]
        if bad:
            raise KiwiError("%s: unknown key %s (%s)" % (at, ", ".join(map(str, bad)), ", ".join(KEYS)))
        case = line.get("case")
        if case not in CASES:
            raise KiwiError("%s: case is one of %s" % (at, ", ".join(CASES)))
        do = line.get("do")
        if do not in CASES[case][1]:
            raise KiwiError("%s: for %s, do is one of %s" % (at, case, ", ".join(CASES[case][1])))
        who = line.get("for", "all")
        if not isinstance(who, str) or not who.strip():
            raise KiwiError("%s: for is all, a machine's name, role:NAME, site:NAME or part:NAME" % at)
        if who.partition(":")[0] not in ("role", "site", "part") and ":" in who:
            raise KiwiError("%s: for is all, a machine's name, role:NAME, site:NAME or part:NAME" % at)
        for k in ("after", "tries", "at"):
            if k in line and (not isinstance(line[k], int) or isinstance(line[k], bool) or line[k] < 0):
                raise KiwiError("%s: %s is a whole number" % (at, k))
        if "at" in line and (case != "disk-full" or line["at"] > 100):
            raise KiwiError("%s: at is for disk-full, a percentage of the disk" % at)
        out.append(dict(line, **{"for": who.strip()}))
    return out


def fits(line, host, part=None):
    """Does `line` cover `host` (and `part`, for a case about a part)?"""
    who = line["for"]
    kind, _sep, name = who.partition(":")
    if who == "all":
        return True
    if kind == "role":
        return host.role == name
    if kind == "site":
        return str(((host.cfg or {}).get("network") or {}).get("site") or "home") == name
    if kind == "part":
        return part == name if part is not None else name in (host.modules or [])
    return host.name == who


def for_machine(host):
    """{case: line or None} for `host`: the first line that fits it, for each case; its own lines
    (hosts.NAME.runbook) before the defaults'. A line `for: part:NAME` fits a machine that runs that part."""
    mine = (lines(host.overrides or {}, "hosts.%s.runbook" % host.name)
            + lines(host.fleet.defaults or {}, "defaults.runbook"))
    return {case: next((l for l in mine if l["case"] == case and fits(l, host)), None) for case in CASES}


def sentence(case, line):
    """What happens in this case, in words."""
    what = CASES[case][2]
    if line is None:
        return "When %s: you are told; nothing is done." % what
    do = DOING[line["do"]]
    parts = [do]
    if line.get("after"):
        parts[0] = "%s after %d minute%s" % (do, line["after"], "" if line["after"] == 1 else "s")
    if line.get("at") is not None:
        parts[0] += " at %d %%" % line["at"]
    if line.get("tries") and line["do"] != "tell":
        parts.append("at most %d time%s, then you are told" % (line["tries"], "" if line["tries"] == 1 else "s"))
    return "When %s: %s." % (what, ", ".join(parts))


def errors(fleet):
    """What is wrong in the runbook lines of the defaults and of every machine."""
    errs = []
    try:
        lines(fleet.defaults or {}, "defaults.runbook")
    except KiwiError as e:
        errs.append(str(e))
    for h in fleet.hosts.values():
        own = (h.overrides or {}).get("runbook")
        if own is None:
            continue
        try:
            lines({"runbook": own}, "hosts.%s.runbook" % h.name)
        except KiwiError as e:
            errs.append(str(e))
    return errs
