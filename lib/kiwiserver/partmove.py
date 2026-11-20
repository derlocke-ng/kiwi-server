"""Move a part to another machine (docs/design/scale.md): its data, its secrets, its name.

    kiwi-server move FLEET PART FROM TO          the plan: what would happen, and what stands in the way
    kiwi-server move FLEET PART FROM TO --yes    do it

What it does, in this order:

1. The part stops on the machine it leaves, and that machine makes one last backup: the copy
   the part is restored from holds everything.
2. The part's generated secrets are pinned to the new machine (secrets/pins.json): a database
   password stays what the data was made with.
3. The fleet file: the part and its settings move to the new machine, and it keeps its name
   (`cloud.papaya.home` stays `cloud.papaya.home`, unless names are by service already).
4. The new machine is applied, and gets the part's data from the old machine's backups
   (`kiwi-stack restore --from`, kiwi-modules 0.104): the old machine's way into them goes
   over for that one run.
5. The old machine is applied (the part is gone there; its data stays on its disk until someone
   deletes it), then every machine the change reaches (the ones that answer for names).
6. The new machine starts a backup: from then on the part is in the new machine's own store.
"""
import copy
import json
import os

import yaml

from . import config, names as namesmod
from .util import KiwiError

PINS = "secrets/pins.json"


def _listed(host):
    return [str(x) for x in (host.role_settings_raw or {}).get("modules") or []]


def _docker_dir(host):
    return str((host.role_settings_raw or {}).get("docker_dir") or "")


PROXY = "reverse-proxy"
LAST_PUBLIC = "no site has a name outside the fleet's domain"     # the proxy's own check, when `public` has nothing left
OPEN_KEYS = ("public", "acme_email", "acme_staging")       # what makes a public name answer: carried to the new machine


def is_public(name, host):
    """A name outside the fleet's domain: the Internet's, answered through the master with a
    certificate from Let's Encrypt (reverse-proxy.public)."""
    domain = str(host.cfg.get("domain") or "")
    return bool(name) and not (name == host.hostname or (domain and name.endswith("." + domain)))


def changed_data(raw, part, src, dst, name_setting=None, name_value=None, open_too=False, swap_ready=False):
    """The fleet file's data with the part moved: off `src`'s list and settings, onto `dst`'s. A
    standby the part had (keep_ready) ends with the move: the part is elsewhere now. `swap_ready`
    (a switch to the standby): the machine it leaves keeps it ready from now on, so the two stay
    a pair; it fetches the part's data once it is back."""
    data = copy.deepcopy(raw)
    hosts = data["hosts"]
    ready = hosts[src.name].get("keep_ready")
    if isinstance(ready, dict) and part in ready:
        ready.pop(part)
        if not ready:
            hosts[src.name].pop("keep_ready")
    if swap_ready:
        back = hosts[dst.name].get("keep_ready")
        back = dict(back) if isinstance(back, dict) else {}
        back[part] = src.name
        hosts[dst.name]["keep_ready"] = back
    s_role, d_role = src.role, dst.role
    s_block = hosts[src.name].setdefault(s_role, {})
    d_block = hosts[dst.name].setdefault(d_role, {})
    s_mods = list(s_block.get("modules") or _listed(src))
    d_mods = list(d_block.get("modules") or _listed(dst))
    s_block["modules"] = [m for m in s_mods if m != part]
    d_block["modules"] = d_mods + ([part] if part not in d_mods else [])
    settings = s_block.pop(part, None)
    if not isinstance(settings, dict):
        settings = {}
    settings = dict(settings)
    if name_setting and name_value:
        settings[name_setting] = name_value
    if settings:
        d_block[part] = settings
    if open_too:                        # a public name: the new machine's proxy opens to the Internet as the old one's is
        was = s_block.get(PROXY) if isinstance(s_block.get(PROXY), dict) else {}
        was = dict((src.role_settings_raw or {}).get(PROXY) or {}, **was)
        now = d_block.setdefault(PROXY, {})
        if isinstance(now, dict):
            for k in OPEN_KEYS:
                if k in was and k not in now:
                    now[k] = was[k]
            now["public"] = True
    return data


def plan(fleet, roles, ms, part, src_name, dst_name, applied=None, render=None, standby=False):
    """{part, from, to, name, carry, pins, steps, stops, notes, data}: what a move would do.
    `stops` non-empty: it cannot be done as things are. `data`: the fleet file's data after it.
    `render(fleet, host)`: renders a machine as an apply would and raises KiwiError on what an
    apply would refuse; with it the plan tries both machines as they would be after the move
    (20 Nov 2026: a hidden service on the old machine still named the part that left, and the
    move found out half way)."""
    stops, notes = [], []
    out = {"part": part, "from": src_name, "to": dst_name, "name": "", "carry": False, "pins": [], "steps": [],
           "stops": stops, "notes": notes, "data": None, "name_setting": None, "name_value": None}
    src, dst = fleet.hosts.get(src_name), fleet.hosts.get(dst_name)
    for n, h in ((src_name, src), (dst_name, dst)):
        if h is None:
            stops.append("there is no machine %s" % n)
    m = ms.modules.get(part)
    if m is None:
        stops.append("there is no part %s" % part)
    if stops:
        return out
    if src is dst:
        stops.append("%s is on %s already" % (part, src_name))
        return out
    if part not in _listed(src):
        stops.append("%s is not among %s's parts (%s)" % (part, src_name, ", ".join(_listed(src)) or "none"))
    if part in _listed(dst):
        stops.append("%s has %s already" % (dst_name, part))
    for h in (src, dst):
        if h.role not in roles:
            stops.append("%s has no role a part runs under" % h.name)
    if stops:
        return out
    if roles[src.role].node_type != roles[dst.role].node_type:
        stops.append("%s is a %s and %s a %s: their parts keep their data under different names, the copy "
                     "would not land where the part looks for it" % (src_name, roles[src.role].node_type,
                                                                     dst_name, roles[dst.role].node_type))
    if _docker_dir(src) != _docker_dir(dst):
        stops.append("the two machines keep their stacks in different folders (docker_dir): the copy would not "
                     "land where the part looks for it")
    backup = getattr(m, "backup", None)
    out["carry"] = bool(backup)
    if backup and backup.get("no_restore"):
        stops.append("%s is not restored from a backup: %s" % (part, backup.get("no_restore")))
    if backup and "backup" not in _listed(src) and not standby:
        stops.append("%s keeps no backups (the backup part): there is nothing to carry %s's data with" % (src_name, part))
    if backup and "backup" not in _listed(dst) and not standby:
        stops.append("%s has no backup part: %s's data comes over through it, and is backed up there afterwards"
                     % (dst_name, part))
    if applied is not None:
        for h in (src, dst):
            if h.name not in applied:
                stops.append("%s is not set up yet" % h.name)
    # its name stays
    nb = getattr(m, "named_by", None)
    if nb:
        name = namesmod.name_of(fleet, src, part, ms, namesmod.plan(fleet, ms))
        out["name"] = name
        by_service = namesmod.kind(dst) == "service" and name == "%s.%s" % (nb["word"], dst.cfg.get("domain") or "")
        hand = ((src.role_settings_raw or {}).get(part) or {}).get(nb["setting"])
        if not by_service or hand:
            out["name_setting"] = nb["setting"]
            out["name_value"] = str(hand) if hand else (("https://" + name) if nb["url"] else name)
    out["pins"] = [s.key for s in m.settings if getattr(s, "generate", False)]
    # a public name answers only where the proxy is open to the Internet (20 Nov 2026: the pastebin
    # moved, that setting stayed behind, and its public name was dead)
    opened = lambda h: bool(((h.role_settings_raw or {}).get(PROXY) or {}).get("public"))
    open_too = bool(nb) and is_public(out["name"], src) and opened(src) and not opened(dst)
    out["open"] = open_too
    data = changed_data(fleet.raw, part, src, dst, out["name_setting"], out["name_value"], open_too, swap_ready=standby)
    out["data"] = data
    def tried(data):
        """What is in the way with the fleet as `data` has it: does the new machine take the part,
        and does the old one stand without it?"""
        found = []
        try:
            after = config.Fleet(fleet.path, copy.deepcopy(data))
            for e in after.hosts[dst_name].validate(roles) + after.hosts[src_name].validate(roles):
                found.append(e)
            namesmod.assign(after, ms)
            if render is not None and not found:
                for n in (dst_name, src_name):
                    try:
                        render(after, after.hosts[n])
                    except KiwiError as e:
                        more = (" (something else on %s still refers to %s: take it out there first)" % (src_name, part)
                                if n == src_name else "")
                        found.append("after the move %s%s" % (e, more))
        except KiwiError as e:
            found.append(str(e))
        return found

    found = tried(data)
    closes = False
    if found and nb and is_public(out["name"], src) and opened(src) and any(LAST_PUBLIC in e for e in found):
        # the part was the last public name there: the old machine's proxy closes to the Internet,
        # it would refuse to be open with nothing to show
        closed = copy.deepcopy(data)
        closed["hosts"][src_name].setdefault(src.role, {}).setdefault(PROXY, {})["public"] = False
        again = tried(closed)
        if not again:
            data, found, closes = closed, again, True
            out["data"] = data
    stops.extend(found)
    out["closes"] = closes
    if not backup:
        notes.append("%s keeps no data of its own to carry: only its settings and secrets move" % part)
    notes.append("%s's data stays on %s's disk after the move, unused: delete it there when the part runs well on %s"
                 % (part, src_name, dst_name))
    if nb and is_public(out["name"], src):
        notes.append("%s is a public name: it answers from %s once the master is applied too (the move does that) and "
                     "%s has its certificate from Let's Encrypt, a minute or two" % (out["name"], dst_name, dst_name))
    out["steps"] = [
        "%s stops on %s%s" % (part, src_name, ", and %s makes one last backup" % src_name if backup else ""),
        ("its %d generated secret%s stay%s what they are, pinned to %s" % (
            len(out["pins"]), "" if len(out["pins"]) == 1 else "s", "s" if len(out["pins"]) == 1 else "", dst_name))
        if out["pins"] else "it has no generated secrets",
        "the fleet file: %s and its settings move from %s to %s%s%s" % (
            part, src_name, dst_name, "; it stays %s" % out["name"] if out["name"] else "",
            "; %s's proxy opens to the Internet for that name, as %s's is" % (dst_name, src_name) if open_too else ""),
        "%s is applied%s" % (dst_name, ", and gets %s's data from %s's backups" % (part, src_name) if backup else ""),
        "%s is applied (the part is gone there%s), then every machine the change reaches" % (
            src_name, "; its proxy closes to the Internet, no public name is left there" if closes else ""),
    ] + (["%s starts a backup: from then on %s is in its own store" % (dst_name, part)] if backup else [])
    return out


def say(p, out=print):
    out("Move %s from %s to %s%s" % (p["part"], p["from"], p["to"], ": it stays %s" % p["name"] if p["name"] else ""))
    if p["stops"]:
        out("")
        out("It cannot be done as things are:")
        for s in p["stops"]:
            out("  - %s" % s)
        return
    out("")
    for i, s in enumerate(p["steps"], 1):
        out("  %d. %s" % (i, s))
    for n in p["notes"]:
        out("  note: %s" % n)


def pin_secrets(fleet, part, src, dst, keys):
    """The part's generated secrets as `src` has them, pinned under `dst`'s name. Returns how many."""
    path = fleet.resolve_path(PINS)
    try:
        with open(path) as fh:
            pins = json.load(fh)
    except (OSError, ValueError):
        pins = {}
    for key in keys:
        pins["%s/%s/%s" % (dst.hostname, part, key)] = fleet.derive_secret(src.hostname, part, key)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".new"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        fh.write(json.dumps(pins, indent=1, sort_keys=True) + "\n")
    os.replace(tmp, path)
    return len(keys)


def way_in(backup_settings, src):
    """What `kiwi-stack restore --from` reads on standard input: the old machine's way into its
    backups, from its backup part's settings as they resolve (the secrets from the seed)."""
    b = backup_settings or {}
    servers = list(dict.fromkeys(([b["server"]] if b.get("server") else []) + list(b.get("servers") or [])))
    if not servers or not b.get("repo_password") or not b.get("server_password"):
        raise KiwiError("%s's backup part does not say where its backups go" % src.name)
    return ("RESTIC_PASSWORD=%s\nRESTIC_REST_USERNAME=%s\nRESTIC_REST_PASSWORD=%s\nKIWI_TARGETS=%s\n"
            % (b["repo_password"], src.hostname, b["server_password"], " ".join(str(s) for s in servers)))


def write_fleet(fleet, data):
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=1000)
    head = ""
    try:
        with open(fleet.path) as fh:
            first = fh.readline()
        if first.startswith("#"):
            head = first
            with open(fleet.path) as fh:
                for line in fh.readlines()[1:]:
                    if not line.startswith("#"):
                        break
                    head += line
    except OSError:
        pass
    tmp = fleet.path + ".new"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        fh.write(head + text)
    os.replace(tmp, fleet.path)
