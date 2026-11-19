"""If something breaks (docs/recovery.md): could this network be brought back, and what to do
when a part of it is lost. `kiwi-server recovery <fleet>`; the control page's "If something
breaks" and the app's dialog of that name show the same report.

It reads what is on this computer: the fleet file, the fleet's state file (.kiwi-state.json,
where `backup` notes where and when it stored the network's copy) and, when given, the last
`status --porcelain`. It contacts no machine, so it answers at once, with every machine off.

Two parts:
- checks: [{id, ok, text, fix}], ok true, false (a gap) or None (it cannot tell from here);
- cases: [{id, title, by_itself, you, how: [{label, command, page, app}]}], a fixed list
  filled with this network's names. `command` is one or more command lines (one per line),
  `page` a path of the control page, `app` what the app does for it (RecoveryDialog in
  gui/kiwi-server-gui): snapshots:<host>, rebuild:<host>, unlock:<host>, copy, restore-copy,
  rotate or device.
"""
import json
import os
import shlex
import textwrap
import time
import urllib.parse

from . import backup as backupmod, providers, store as storemod, unlock as unlockmod, util
from .targets import ram as ram_t
from .util import KiwiError

COPY_LATE = 14 * 86400          # the network's copy: older than this is a gap
DATA_LATE = 36 * 3600           # a machine's nightly backup that missed a night (facts.BACKUP_LATE)
ORDER = {False: 0, None: 1, True: 2}    # gaps first


# ---- where the network's copy went ------------------------------------------------------
def record_copy(fleet, hosts=(), local=None, fetched=False, now=None):
    """Note where the network's copy was just stored: on these machines, in a folder
    (`backup --local`), or handed to a browser ("Fetch a copy"). Names and times only.
    A note that cannot be written costs the copy nothing."""
    from . import cli
    now = int(now if now is not None else time.time())
    state = cli.fleet_state(fleet)
    rec = state.setdefault("copy", {})
    for name in hosts:
        rec.setdefault("hosts", {})[name] = now
    if local:
        rec["local"] = {"time": now, "dir": os.path.abspath(local)}
    if fetched:
        rec["fetched"] = now
    try:
        util.write_text(cli._state_path(fleet), json.dumps(state, indent=1, sort_keys=True) + "\n", 0o644)
    except OSError as e:
        util.warn("where the copy went is not noted (%s): kiwi-server recovery will not know it" % e)


def age(seconds):
    s = max(0, int(seconds or 0))
    return ("%d minutes" % (s // 60) if s < 5400 else "%d hours" % (s // 3600) if s < 172800
            else "%d days" % (s // 86400))


def names(items):
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""


# ---- what a machine is ------------------------------------------------------------------
REMADE = ("tor",)       # modules whose saved state is made anew without loss (tor: its guards and cache)


def keeps_data(mod):
    """Does this module keep something worth a backup? Its `backup:` block names paths or
    volumes. Not one whose snapshots are not brought back (vpn-server: the fleet keeps that),
    nor a path a template gives only with some settings (reverse-proxy's certificates)."""
    b = getattr(mod, "backup", None) or {}
    if not b or b.get("no_restore") or getattr(mod, "name", "") in REMADE:
        return False
    frozen = b.get("frozen") or {}
    items = [x for key in ("paths", "volumes") for x in list(b.get(key) or []) + list(frozen.get(key) or [])]
    return any(isinstance(x, dict) or not str(x).lstrip().startswith("{%") for x in items)


def _resolved(fleet, roles, ms):
    """{name: host with its modules and settings}. A machine whose settings do not resolve yet
    (not on the VPN yet: its WireGuard file is missing) gets its modules and their settings
    as written, over the modules' defaults: enough to say what it keeps and where it sends it."""
    from . import roles as rolesmod
    from .config import deep_merge
    out = {}
    for h in fleet.hosts.values():
        if h.role not in roles:
            continue
        role = roles[h.role]
        try:
            rolesmod.resolve_settings(role, h, ms)
        except (KiwiError, KeyError, TypeError, ValueError, OSError):
            raw = h.role_settings_raw or {}
            if not role.stack or not isinstance(raw, dict):
                continue
            plain = {k: v for k, v in raw.items() if not isinstance(v, (dict, list))}
            try:
                wanted = raw.get("modules")
                wanted = role.default_modules(plain) if wanted is None else str(wanted).split() if isinstance(wanted, str) else wanted
                # a machine off the VPN (vpn: false): its presets' VPN client is left out, as at an apply
                without = ("vpn-client",) if plain.get("vpn") is False and role.node_type != "master" else ()
                h.modules = ms.resolve([str(m) for m in wanted], role.node_type, without)
                h.module_settings = {m: deep_merge(deep_merge({x.key: x.default for x in ms.get(m).settings},
                                                              role.defaults_for(m, plain)), raw.get(m) or {})
                                     for m in h.modules}
            except (KiwiError, KeyError, TypeError, ValueError, AttributeError):
                continue
        out[h.name] = h
    return out


def _is_master(h, roles):
    return h.role in roles and roles[h.role].node_type == "master"


def _servers(h):
    """The backup servers of a machine, as its backup settings name them."""
    s = h.module_settings.get("backup") or {}
    return [str(u) for u in list(s.get("servers") or []) + ([s["server"]] if s.get("server") else [])]


def _server_host(fleet, url):
    """The machine of a backup server's address (https://backup.lychee.home), or that address."""
    at = urllib.parse.urlsplit(url).hostname or url
    at = at[len("backup."):] if at.startswith("backup.") else at
    return next((h.name for h in fleet.hosts.values() if h.hostname == at), None) or next(
        (h.name for h in fleet.hosts.values() if h.name == at.split(".")[0]), at)


def _lan_ip(h):
    net = h.cfg.get("network") or {}
    if net.get("dhcp", True) or not net.get("address"):
        return ""
    return str(net["address"]).split("/")[0]




def rebuild_commands(h, fleet_arg):
    """What installs a machine anew from a terminal: one command per line. A machine its
    router gives an address is set up at the address it has right now."""
    f = fleet_arg
    if (h.cfg.get("vps") or {}).get("provider") in providers.PROVIDERS:
        return "kiwi-server next %s %s --yes" % (f, h.name)        # makes the media and installs it there
    if _lan_ip(h) or h.role == "master":
        last = "kiwi-server next %s %s --yes" % (f, h.name)
    else:
        last = "kiwi-server apply %s %s --ssh %s@<its address> --new-host-key" % (f, h.name, h.admin["user"])
    return "kiwi-server build %s %s\n%s" % (f, h.name, last)


# ---- the checks -------------------------------------------------------------------------
def _check(cid, ok, text, fix=""):
    return {"id": cid, "ok": ok, "text": text, "fix": fix}


def copy_check(fleet, state, applied, fleet_arg, now):
    rec = state.get("copy") or {}
    places = [(t, "on " + n) for n, t in sorted((rec.get("hosts") or {}).items()) if n in fleet.hosts]
    if (rec.get("local") or {}).get("time"):
        places.append((rec["local"]["time"], "in " + str(rec["local"].get("dir") or "a folder")))
    if rec.get("fetched"):
        places.append((rec["fetched"], "downloaded from the control page"))
    f = fleet_arg
    fix = ("Save it now: \"Save it now\" or \"Fetch a copy\" on the Backups page, \"Back up the network now\" in the "
           "app, or kiwi-server backup %s (--local <folder> for a USB stick)." % f)
    if not places:
        if applied or (fleet.defaults.get("backup") or {}).get("hosts"):
            return _check("copy", None, "No copy of the network is noted here. One made by an older version, or on "
                                        "another computer, is not counted.", fix)
        return _check("copy", False, "No copy of the network has been made yet.", fix)
    newest = max(t for t, _w in places)
    where = names(w for t, w in places if t == newest)
    if not any(not w.startswith("on ") for _t, w in places):
        where += ". Every copy is on your own machines: keep one somewhere else too"
    if now - newest > COPY_LATE:
        return _check("copy", False, "The newest copy of the network is %s old (%s)." % (age(now - newest), where), fix)
    return _check("copy", True, "The network's copy was stored %s ago, %s." % (age(now - newest), where), fix)


def data_checks(fleet, ms, hosts, applied, said, looked, fleet_arg, now):
    out, f = [], fleet_arg
    for h in hosts.values():
        mods = [m for m in h.modules if keeps_data(ms.get(m))]
        if not mods:
            continue
        cid, n = "data:" + h.name, h.name
        if "backup" not in h.modules:
            out.append(_check(cid, False, "%s keeps data (%s) and has no backup. If it is lost, that data is gone."
                              % (n, ", ".join(mods)),
                              "Give %s the backup part, with a machine that has the backup-server part as its backup "
                              "server: its Settings, then Apply." % n))
            continue
        servers = [_server_host(fleet, u) for u in _servers(h)]
        others = [s for s in servers if s != n]
        if not servers:
            out.append(_check(cid, False, "%s has the backup part but no backup server." % n,
                              "Name one in its settings (backup: servers): a machine with the backup-server part."))
            continue
        if not others:
            out.append(_check(cid, False, "%s keeps its backups only on itself. If it is lost, they are gone too." % n,
                              "Add another machine with the backup-server part to its backup servers, then Apply."))
            continue
        refusing = [s for s in others if s in hosts and "backup-server" in hosts[s].modules
                    and h.hostname not in [str(c) for c in (hosts[s].module_settings.get("backup-server") or {}).get("clients") or []]]
        if refusing:
            out.append(_check(cid, False, "%s does not take the backups of %s." % (names(refusing), n),
                              "Add %s to the clients in the backup-server settings of %s, then Apply %s."
                              % (h.hostname, names(refusing), names(refusing))))
            continue
        to = names(others)
        o = said.get(n) if said is not None else None
        b = ((o or {}).get("facts") or {}).get("backup") if o and o.get("ok") else None
        late = "Is %s on? \"Save now\" on the Backups page, or kiwi-server backups %s %s --now." % (to, f, n)
        if n not in applied and not b:
            out.append(_check(cid, None, "%s is not set up yet. It backs up to %s once it is." % (n, to)))
        elif said is None:
            out.append(_check(cid, True, "%s saves its data every night, to %s." % (n, to)))
        elif o and not o.get("ok"):
            out.append(_check(cid, None, "%s did not answer at the last look: when it last saved its data is not known."
                              % n, "Check that it is on, then look again (Check now)."))
        elif not b:
            out.append(_check(cid, None, "The last look said nothing about the backups of %s." % n,
                              "Look again (Check now), or Apply %s." % n))
        elif b.get("held"):
            out.append(_check(cid, False, "%s holds its backups: %s" % (n, b["held"]),
                              "Its page in the app: Restore all, or Start new; or kiwi-server backups %s %s --restore." % (f, n)))
        elif not b.get("last") or b.get("age") is None:
            out.append(_check(cid, False, "%s has no backup yet." % n, late))
        else:
            ago = b["age"] + max(0, now - looked) if looked else b["age"]
            if ago > DATA_LATE:
                out.append(_check(cid, False, "The last backup of %s is %s old." % (n, age(ago)), late))
            else:
                out.append(_check(cid, True, "%s saved its data %s ago, to %s." % (n, age(ago), to)))
    return out


def unlock_checks(fleet, hosts, fleet_arg):
    want = [h for h in fleet.hosts.values() if ((h.cfg.get("unlock") or {}).get("rings"))]
    if not want:
        return []
    f = fleet_arg

    def resolve(x):
        if x.name not in hosts:
            raise KiwiError("not resolved")
    try:
        servers = unlockmod.ring_servers(fleet, resolve)
    except KiwiError as e:
        return [_check("unlock:" + h.name, None, "Which machines open the disk of %s is not clear: %s." % (h.name, e))
                for h in want]
    out = []
    for h in want:
        cfg = h.cfg.get("unlock") or {}
        rings = [str(r) for r in cfg.get("rings") or []]
        need = int(cfg.get("threshold") or 1)
        missing = [r for r in rings if r not in servers]
        where = {r: servers[r][0].name for r in rings if r in servers and not isinstance(servers[r][0], unlockmod.OperatorRing)}
        machines = sorted(set(where.values()))
        weak = [m for m in machines if sum(1 for r in rings if r not in missing and where.get(r) != m) < need]
        cid, key = "unlock:" + h.name, "kiwi-server unlock %s %s --manual" % (f, h.name)
        if missing:
            out.append(_check(cid, False, "No machine serves the ring %s of %s: its disk does not open by itself."
                              % (names(missing), h.name),
                              "Give a machine the tang part with that ring, or take the ring out of its unlock settings."))
        elif weak:
            out.append(_check(cid, False, "If %s is lost, %s stays locked at its next start until someone types its key."
                              % (" or ".join(weak), h.name),
                              "Give %s a ring on one more machine (unlock: rings), then Apply. Until then: %s." % (h.name, key)))
        elif machines:
            out.append(_check(cid, True, "The disk of %s still opens by itself when one of %s is lost."
                              % (h.name, names(machines))))
        else:
            out.append(_check(cid, True, "The disk of %s opens with the help of this computer: kiwi-server unlock."
                              % h.name))
    return out


def start_alone(fleet, hosts, servers, off=()):
    """Which machines come up by themselves when every machine of the fleet starts at once,
    as after a power cut: (the ones that do, the ones that stay locked). A machine starts
    alone when its disk needs no ring, or when enough of its rings are on machines that
    start alone. A ring that has to be forwarded needs the control node up and open. `off`:
    machines that stay off."""
    control = next((h for h in hosts.values() if "control" in (h.modules or [])), None)
    opens_control = set()       # the machines whose rings open the control node's keys after a restart (any one)
    if control is not None:
        for ring in unlockmod.control_rings(control):
            if ring in servers and not isinstance(servers[ring][0], unlockmod.OperatorRing):
                opens_control.add(servers[ring][0].name)
    needs = {}
    for h in fleet.hosts.values():
        cfg = h.cfg.get("unlock") or {}
        rings = [str(r) for r in cfg.get("rings") or []]
        if not rings:
            continue
        via, givers = cfg.get("via") or {}, []
        for r in rings:
            if r not in servers or isinstance(servers[r][0], unlockmod.OperatorRing):
                continue                        # nobody serves it, or a person's computer does
            who, any_of = {servers[r][0].name}, set()
            if str(via.get(r) or "").startswith("http://127.0.0.1:"):
                if control is None or not opens_control:
                    continue                    # forwarded by hand: not by itself
                who |= {control.name}
                any_of = set(opens_control)     # and one machine whose ring opens the control node
            givers.append((who, any_of))
        needs[h.name] = (int(cfg.get("threshold") or 1), givers)
    up = {h.name for h in fleet.hosts.values() if h.name not in needs and h.name not in off}
    grew = True
    while grew:
        grew = False
        for name, (need, givers) in needs.items():
            if name not in up and name not in off and \
                    sum(1 for g, a in givers if g <= up and (not a or a & up)) >= need:
                up.add(name)
                grew = True
    return up, sorted(n for n in needs if n not in up and n not in off)


def power_cut_checks(fleet, hosts, fleet_arg):
    """After a power cut every machine of a home starts at once. Do they all come back?"""
    if not any((h.cfg.get("unlock") or {}).get("rings") for h in fleet.hosts.values()):
        return []

    def resolve(x):
        if x.name not in hosts:
            raise KiwiError("not resolved")
    try:
        servers = unlockmod.ring_servers(fleet, resolve)
    except KiwiError:
        return []                   # the unlock checks say what is unclear
    up, stuck = start_alone(fleet, hosts, servers)
    fix = ("Give one of them a ring on a machine whose own disk needs no ring: a small board without disk encryption "
           "that is always on does. Its settings: unlock, rings.")
    if stuck:
        return [_check("power-cut", False, "After a power cut %s wait%s locked: each needs a ring on a machine that is "
                       "locked too. Someone has to type a key." % (names(stuck), "s" if len(stuck) == 1 else ""), fix)]
    # the machines that hold it together: with one of them off, others stay locked
    ringed = {s[0].name for s in servers.values() if not isinstance(s[0], unlockmod.OperatorRing)}
    weak = {}
    for m in sorted(ringed):
        lost = start_alone(fleet, hosts, servers, off=(m,))[1]
        if lost:
            weak[m] = lost
    if weak:
        m, lost = sorted(weak.items(), key=lambda x: -len(x[1]))[0]
        return [_check("power-cut", False, "After a power cut every machine comes back by itself, as long as %s starts too. "
                       "If it is off or broken then, %s stay%s locked." % (m, names(lost), "s" if len(lost) == 1 else ""),
                       "Give %s a ring on one more machine that starts without a ring itself." % names(lost))]
    return [_check("power-cut", True, "After a power cut every machine comes back by itself, also with one of the "
                   "machines that serve a ring off.")]


def master_checks(fleet, roles, hosts, applied, fleet_arg):
    out, f = [], fleet_arg
    control = any("control" in h.modules for h in hosts.values())
    for h in fleet.hosts.values():
        if not _is_master(h, roles):
            continue
        n, m = h.name, hosts.get(h.name)
        if m is not None and backupmod.carries_wireguard(m):
            if os.path.isfile(backupmod._wireguard_copy(m)):
                out.append(_check("master-vpn:" + n, True, "The fleet keeps a copy of the VPN state of %s: set up again, "
                                                           "it knows every device and machine." % n))
            elif n not in applied:
                out.append(_check("master-vpn:" + n, None, "%s is not set up yet. The fleet keeps its VPN state from its "
                                                           "first set-up on." % n))
            else:
                out.append(_check("master-vpn:" + n, False,
                                  "The fleet has no copy of the VPN state of %s. Set up again, it would not know your "
                                  "devices: each would need its code again." % n,
                                  "Apply %s once: it keeps a copy. Apply on its page, or kiwi-server apply %s %s." % (n, f, n)))
        if h.target == "coreos-ram":
            timer = os.path.isfile(os.path.join(backupmod._user_units(), ram_t._unit(fleet.path) + ".timer"))
            if control or timer:
                out.append(_check("master-watch:" + n, True, "%s runs in memory. After a restart %s sends it its role "
                                  "by itself." % (n, "the control node" if control else "this computer's timer")))
            else:
                out.append(_check("master-watch:" + n, False,
                                  "%s runs in memory. After a restart it waits empty until kiwi-server watch sends its "
                                  "role, and nothing does that by itself." % n,
                                  "On a computer that is always on: kiwi-server watch %s --timer on. A control node does "
                                  "it too." % f))
        # a master that stops answering: told about, or restarted at its provider (watchdog.py)
        from . import watchdog
        try:
            after, do = watchdog.policy(fleet, "master")
        except KiwiError:
            after, do = 15, "tell"
        if n in applied and not control:
            out.append(_check("master-gone:" + n, False, "Nothing looks whether %s still answers: this network has no "
                              "control node." % n, "A home server with the control module looks every ten minutes and "
                              "says so on its page: “A home server that looks after it…” in the app."))
        elif n in applied and do == "tell":
            out.append(_check("master-gone:" + n, None, "When %s stops answering, the control node says so on its page "
                              "after %d minutes. It does nothing else by itself." % (n, after),
                              "It can restart the server at its provider: Settings for every machine, “When a machine "
                              "is gone”, restart. It has to be signed in there (kiwi-server vps login %s)." % f))
        elif n in applied:
            known = os.path.isfile(fleet.resolve_path("secrets/netcup.token"))
            out.append(_check("master-gone:" + n, True if known else False,
                              ("When %s stops answering for %d minutes, the control node restarts it at its provider, at "
                               "most three times a day." % (n, after)) if known else
                              ("The control node may restart %s when it stops answering, but nobody is signed in at its "
                               "provider from here." % n),
                              "" if known else "Sign in once: kiwi-server vps login %s." % f))
        where = (h.cfg.get("vps") or {}).get("provider")
        if where in providers.PROVIDERS:
            out.append(_check("master-install:" + n, True, "%s can be installed again at %s from here, with one button."
                              % (n, providers.get(where).name)))
        else:
            out.append(_check("master-install:" + n, True, "%s is installed again from its install media by hand: at "
                                                           "your hoster, or from a USB stick." % n))
    return out


WAY_WORDS = {"control": "the control node", "desktop": "the computer the network was set up with"}


def ways_check(fleet, fleet_arg):
    st = storemod.Store.for_fleet(fleet.path)
    if not st.exists():
        return _check("keys", False, "The network's keys are plain files in its folder, and there is no recovery key.",
                      "kiwi-server secrets init %s puts them in one encrypted file and gives you a recovery key."
                      % fleet_arg)
    others = [n for n, _k in st.ways() if n != "recovery"]
    fix = ("Let a second computer manage it: Computers, Add a computer on the control page, or Join a network in the "
           "app on that computer. A home server that looks after it (in the app) is a control node.")
    if not others:
        return _check("keys", False, "Only the recovery key opens the network's keys.", fix)
    said = names(WAY_WORDS.get(n, n) for n in others)
    if len(others) == 1:
        return _check("keys", False, "Only %s opens the network's keys, besides the recovery key. If it is lost, the "
                                     "network comes back only from a copy and the recovery key." % said, fix)
    return _check("keys", True, "Besides the recovery key, %s open the network's keys." % said, fix)


def key_check():
    return _check("recovery-key", None, "Your recovery key cannot be checked from here. Without it no copy opens.",
                  "Make sure you can find it: on paper or in a password manager, not on your machines. It starts "
                  "with AGE-SECRET-KEY-.")


# ---- the cases --------------------------------------------------------------------------
def _how(label, command="", page=None, app=None):
    return {"label": label, "command": command, "page": page, "app": app}


def rebuild_order(fleet, roles, hosts):
    """The order machines come back in after a whole home is gone: the master, then the
    machines that keep the backups, then the ones that open disks, then the others."""
    def rank(h):
        m = hosts.get(h.name)
        mods = m.modules if m is not None else []
        return (0 if _is_master(h, roles) else 1 if "backup-server" in mods else 2 if "tang" in mods else 3)
    return sorted(fleet.hosts.values(), key=rank)


def cases(fleet, roles, ms, hosts, state, fleet_arg):
    f = fleet_arg
    masters = [h for h in fleet.hosts.values() if _is_master(h, roles)]
    others = [h for h in fleet.hosts.values() if not _is_master(h, roles)]
    backed = [h for h in hosts.values() if "backup" in h.modules]
    ring_hosts = [h.name for h in fleet.hosts.values() if (h.cfg.get("unlock") or {}).get("rings")]
    st = storemod.Store.for_fleet(fleet.path)
    ways = [n for n, _k in st.ways()] if st.exists() else []
    computers = [n for n in ways if n not in ("recovery", "control")]
    rec = state.get("copy") or {}
    kept = sorted(((t, n) for n, t in (rec.get("hosts") or {}).items() if n in fleet.hosts), reverse=True)
    out = []

    # a service's data
    out.append({"id": "data", "title": "A service's data is wrong or was deleted",
                "by_itself": "Every machine with the backup part saves its data every night, to its backup machines. "
                             "Nothing is brought back by itself." + ("" if backed else
                             " No machine of this network has the backup part yet."),
                "you": "For one file, look in the service first: Nextcloud, Immich and the others keep their own trash "
                       "and versions. For a whole service as it was on an earlier day, bring that part back from a "
                       "snapshot. What it has now is replaced.",
                "how": [_how("Bring something back on %s" % h.name,
                             "kiwi-server backups %s %s <part> --restore --at '<time>'" % (f, h.name),
                             "/backups/%s" % h.name, "snapshots:%s" % h.name) for h in backed]})

    # a machine
    out.append({"id": "machine", "title": "A machine is dead or was replaced",
                "by_itself": "Nothing installs it again by itself; the other machines keep running. Once it is "
                             "installed anew and set up, a machine with the backup part finds itself in its backups and "
                             "brings its data back by itself, then backs up as before. A machine without backups starts "
                             "empty.",
                "you": "Install it anew under the same name: make its install media, start the machine from them (its "
                       "disk is erased), then Set it up. A machine that only waits at start with a locked disk is not "
                       "lost: Unlock it on its page." + (" Encrypted here: %s." % names(ring_hosts) if ring_hosts else ""),
                "how": [_how("Rebuild %s" % h.name, rebuild_commands(h, fleet_arg), "/hosts/%s#rebuild" % h.name,
                             "rebuild:%s" % h.name) for h in others]
                + [_how("Unlock %s" % n, "kiwi-server unlock %s %s" % (f, n), None, "unlock:%s" % n) for n in ring_hosts]})

    # the master
    ram = [h.name for h in masters if h.target == "coreos-ram"]
    kept_vpn = [h.name for h in masters if h.name in hosts and backupmod.carries_wireguard(hosts[h.name])
                and os.path.isfile(backupmod._wireguard_copy(hosts[h.name]))]
    at = [(h, (h.cfg.get("vps") or {}).get("provider")) for h in masters]
    by_provider = [h.name for h, p in at if p in providers.PROVIDERS]
    you = []
    if by_provider:
        you.append("%s: install it again at its hoster, with one button. Everything on that server is erased; then it "
                   "is set up as the first time." % names(by_provider))
    if [h for h, p in at if p not in providers.PROVIDERS]:
        you.append("Make its install media, start the server from them (at a hoster: upload them as an ISO), then Set "
                   "it up.")
    you.append("A new server with another address: change the master's address in its settings first. Your devices "
               "find it again by themselves only when that address is a name you point to the new server.")
    out.append({"id": "master", "title": "The master is dead",
                "by_itself": "The machines at home keep running and keep their data. Until the master is back, nothing "
                             "reaches them from outside, and your devices have no VPN. A control node says so on "
                             "its page, and restarts the server at its provider when the settings allow it (“When a "
                             "machine is gone”)."
                + (" %s runs in memory: after a restart it gets its role back by itself when a control node or a timer "
                   "watches it." % names(ram) if ram else "")
                + (" The fleet keeps a copy of the VPN state of %s: set up again, your devices and machines connect as "
                   "before, with no new codes." % names(kept_vpn) if kept_vpn else ""),
                "you": " ".join(you),
                "how": [_how(("Install %s again at %s" % (h.name, providers.get(p).name)) if p in providers.PROVIDERS
                             else "Rebuild %s" % h.name, rebuild_commands(h, fleet_arg),
                             "/hosts/%s#rebuild" % h.name, "rebuild:%s" % h.name) for h, p in at]})

    # a device
    out.append({"id": "device", "title": "A phone or laptop was lost or stolen",
                "by_itself": "Nothing. It stays on your VPN until you remove it.",
                "you": "Remove it under Devices. From that moment its key no longer works, even with the WireGuard app "
                       "still on it. If it also managed the network, do what the next case says too.",
                "how": [_how("Devices", "kiwi-server enroll %s <device> --remove" % f, "/devices", "device")]})

    # a computer that manages the network
    out.append({"id": "computer", "title": "A computer that manages the network was lost or stolen",
                "by_itself": "Nothing. It can still open the network's keys, and it knows what it saw.",
                "you": "Remove it under Computers, so it can no longer open the keys or get anything new. Then replace "
                       "the keys that open your machines, and the secrets inside your services. If it knew the password "
                       "of the control page, change that too."
                + (" The computers that can open the keys now: %s." % names(computers) if computers else ""),
                "how": [_how("Computers", "kiwi-server secrets remove %s %s" % (f, computers[0] if len(computers) == 1
                                                                                 else "<computer>"), "/controllers"),
                        _how("Replace the keys", "kiwi-server rotate %s ssh\nkiwi-server rotate %s seed" % (f, f),
                             None, "rotate")]})

    # the network itself
    holders = [WAY_WORDS.get(n, n) for n in ways if n != "recovery"]
    first = fleet.hosts.get(kept[0][1]) if kept else None
    if first is not None and _lan_ip(first):
        back = "kiwi-server restore --from %s@%s --into ~/kiwi" % (first.admin["user"], _lan_ip(first))
    else:
        back = "kiwi-server restore <copy>.tar --into ~/kiwi"
    out.append({"id": "network", "title": "The computer or control node that holds the network is gone",
                "by_itself": "The machines keep running and keep saving their data; nothing on them changes. Nobody can "
                             "change the network until it is back."
                + (" %s can open it too: there it goes on as before." % names(holders) if len(holders) > 1 else ""),
                "you": "Bring the network back from a copy, with the recovery key: on a new control node on its first "
                       "page (I already have a network), or on a computer with the app (Restore a network from a "
                       "copy) or the command below. Then remove the lost one under Computers and replace the keys."
                + (" The newest copy noted here is on %s." % kept[0][1] if kept else ""),
                "how": [_how("Bring it back from a copy", back, None, "restore-copy"),
                        _how("Fetch a copy now", "kiwi-server backup %s --local <folder>" % f, "/backups/network.tar", "copy")]})

    # a whole home
    order = rebuild_order(fleet, roles, hosts)
    out.append({"id": "home", "title": "A whole home is gone: new hardware, a new connection",
                "by_itself": "Nothing at home runs any more. A rented master keeps running. Backups and copies kept on "
                             "machines in that home are gone with them; the ones kept elsewhere are not.",
                "you": "Go in this order. First the network itself: from a copy kept elsewhere, with the recovery key, "
                       "on a new computer or control node. With a new connection, change the machines' home addresses "
                       "in their settings before you make their install media. Then the machines, one after the "
                       "other, in the order below: each one whose backups are left brings its data back by itself.",
                "how": [_how("Bring it back from a copy", back, None, "restore-copy")]
                + [_how("%d. %s" % (i + 1, h.name), rebuild_commands(h, fleet_arg), "/hosts/%s#rebuild" % h.name,
                        "rebuild:%s" % h.name) for i, h in enumerate(order)]})
    return out


# ---- the report -------------------------------------------------------------------------
def report(fleet, roles, ms, status=None, fleet_arg=None, now=None):
    from . import cli
    now = now if now is not None else time.time()
    fleet_arg = cli.typed_path(fleet_arg or fleet.path)      # as a person types it: ~/kiwi/fleet.yaml
    state = cli.fleet_state(fleet)
    applied = state.get("applied") or {}
    hosts = _resolved(fleet, roles, ms)
    said = None if status is None else {o.get("host"): o for o in status.get("hosts") or [] if isinstance(o, dict)}
    looked = (status or {}).get("time")
    checks = ([copy_check(fleet, state, applied, fleet_arg, now)]
              + data_checks(fleet, ms, hosts, applied, said, looked, fleet_arg, now)
              + unlock_checks(fleet, hosts, fleet_arg)
              + power_cut_checks(fleet, hosts, fleet_arg)
              + master_checks(fleet, roles, hosts, applied, fleet_arg)
              + [ways_check(fleet, fleet_arg), key_check()])
    checks.sort(key=lambda c: ORDER[c["ok"]])
    return {"fleet": fleet.path, "looked": looked, "checks": checks,
            "cases": cases(fleet, roles, ms, hosts, state, fleet_arg)}


MARK = {False: "gap", None: "?", True: "ok"}


def human(rep, width=96):
    def wrap(text, first, rest):
        return textwrap.fill(text, width, initial_indent=first, subsequent_indent=rest)
    lines = ["Ready?"]
    for c in rep["checks"]:
        lines.append(wrap(c["text"], "  %-4s " % MARK[c["ok"]], " " * 7))
        if c["fix"] and c["ok"] is not True:
            lines.append(wrap(c["fix"], " " * 7 + "→ ", " " * 9))
    gaps = sum(1 for c in rep["checks"] if c["ok"] is False)
    lines.append("  %s" % ("%d gap%s: the lines marked gap say what to do." % (gaps, "" if gaps == 1 else "s")
                           if gaps else "No gaps."))
    lines += ["", "If something breaks. What happens by itself in each case: the control page, the app, or --porcelain."]
    for k in rep["cases"]:
        lines += ["", "  " + k["title"]]
        for h in k["how"]:
            if h["command"]:
                cmds = h["command"].splitlines()
                lines.append("    %-28s %s" % (h["label"], cmds[0]))
                lines += ["    %-28s %s" % ("", c) for c in cmds[1:]]
    return "\n".join(lines)


def cmd(args):
    """`kiwi-server recovery <fleet> [--status FILE] [--porcelain]`."""
    from . import cli
    fleet, roles, ms = cli.load(args)
    status = None
    if args.status:
        try:
            status = json.loads(util.read_text(args.status))
        except (OSError, ValueError) as e:
            raise KiwiError("%s is not what status --porcelain gives: %s" % (args.status, e))
        if not isinstance(status, dict):
            raise KiwiError("%s is not what status --porcelain gives" % args.status)
    rep = report(fleet, roles, ms, status, args.fleet)
    if args.porcelain:
        print(json.dumps(rep, indent=1))
    else:
        print(human(rep))
