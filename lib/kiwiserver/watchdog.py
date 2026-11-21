"""The control node looks after machines that are gone (design/recovery.md, step 2).

`recovery:` in the fleet file's defaults says, per kind of machine, how long it must be
unreachable and what may then be done without a person:

    recovery:
      master: { after: 15, do: restart }     # minutes; tell | restart | reinstall
      node:   { after: 60, do: tell }

A machine that is there but waits at boot with its disk locked gets its unlock ring forwarded
(`recovery: { unlock: false }` switches that off): the ring lives at home, and the control
node is what stays in the house. That is how a rented master comes back after a restart.

`tell` says it (the page shows it). `restart` is a power cycle through the machine's
provider: it destroys nothing. `reinstall` is not built yet and is read as `restart`.

A part kept ready on another machine (`keep_ready`) goes there by itself when the machine that
runs it has not answered for `recovery: { switch: { after: 5 } }` minutes, its standby answers,
and the standby cannot reach that machine either: two that agree, the control node's look and
the standby. A machine that lost only its way to the control node is still seen by its standby,
and nothing is switched. `recovery: { switch: false }` leaves it to a person.
Guards no policy switches off: the same machine is not acted on twice within an hour (unless
it answered in between: then the act worked, and a new outage is a new one) nor more than
three times a day, and nothing is done when the provider cannot be asked, since then this
computer is the one that is cut off.
"""
from . import util
from .util import KiwiError

DO = ("tell", "restart", "reinstall")
DEFAULT = {"master": {"after": 15, "do": "tell"}, "node": {"after": 60, "do": "tell"}}
AGAIN_AFTER = 3600          # seconds between two acts on one machine
A_DAY = 3                   # acts on one machine in 24 hours
OPEN_AGAIN = 300            # seconds between two tries to open one disk


def policy(fleet, kind):
    """(minutes, what to do) for a master or a node."""
    cfg = dict(DEFAULT[kind], **(((fleet.defaults.get("recovery") or {}).get(kind)) or {}))
    try:
        after = float(cfg.get("after"))
    except (TypeError, ValueError):
        raise KiwiError("recovery.%s.after is a number of minutes" % kind)
    if cfg.get("do") not in DO or after < 1:
        raise KiwiError("recovery.%s: do is one of %s, after at least 1 (minutes)" % (kind, ", ".join(DO)))
    return after, cfg["do"]


REPORT_EVERY = 60          # seconds between two reports of a machine's agent
# conclusions of step()'s look under which nothing is done to the machine or its parts
QUIET_CONCLUSIONS = ("planned", "switched off", "its whole site is away", "only the way from the control node is cut")
PLANNED_WAIT = 600         # seconds a machine that said it restarts may be away before anything is done or told
WORDS = {"reboot": "restarts", "poweroff": "switches off", "halt": "stops", "stop": "stops"}


def _ago(seconds):
    return util.count(max(1, int(seconds // 60)), "minute")


def step(fleet, kinds, said, state, now, restart, where=None, locked=None, unlock=None, point=None, last=None,
         words=None, site=None, reached=None, provider=None):
    """One look. `kinds`: {machine: "master"|"node"} for the machines that have their role;
    `said`: {machine: bool} whether it answered just now; `state`: the fleet's state, changed
    in place (`gone`: since when, `acted`: when); `restart(name)` does a power cycle and may
    raise KiwiError; `where(name)` says what the machine is at its provider, or None. `locked`: {machine: bool}
    whether it waits at boot with its disk locked; `unlock(name)` forwards its ring and may raise
    KiwiError. More kinds of events then: unlocked, locked. `point(address)` points the master's
    name (wg_host) at an address and returns the one it had, or raises KiwiError: with it, a master
    gone for its `after` sends the devices to its standby, and back when it answers (moved).
    Returns the events: [{host, kind: waiting|tell|restarted|held|moved, text}].

    Before anything is done, a machine that is gone is looked at in a fixed order, and what was
    found is kept as one record per case (`state["cases"]`, docs/design/authority.md step 3):
    did it say it goes down (`words`: {machine: its last report, {at, reason, why}})? Does no
    machine of its site answer either (`site`: {machine: its site})? Do the machines that give it
    its permissions, or its standby, still reach it (`reached(name)`: (True|False|None, what was
    found))? What does its provider say (`provider(name)`: RUNNING, SHUTOFF or None)? Only what none
    of these explains is acted on by the policy. An event of a machine
    that went down as it said carries `planned`; one that answers again closes its case (closed)."""
    gone, acted, events = state.setdefault("gone", {}), state.setdefault("acted", {}), []
    pointed, back = state.setdefault("pointed", {}), state.setdefault("back", {})
    seen_ok, off = state.setdefault("seen_ok", {}), state.setdefault("off", {})
    cases = state.setdefault("cases", {})
    for name in list(cases):            # a case ends when the machine answers again: one line for all of it
        if said.get(name) or name not in kinds:
            c = cases.pop(name)
            if said.get(name):
                found = c.get("found") or ""
                if c.get("conclusion") == "not known yet":     # back before its policy's time: nothing to look into
                    found = "It was back before anything was to be done."
                events.append({"host": name, "kind": "closed", "planned": c.get("conclusion") in ("planned", "switched off"),
                               "text": "%s answers again, after %s away. %s%s" % (
                                   name, _ago(now - c.get("opened", now)), found,
                                   (" Done: %s." % ", ".join(c["did"])) if c.get("did") else "")})
    for name in kinds:
        if said.get(name):
            seen_ok[name] = now                 # the last look that saw it answer
            off.pop(name, None)                 # switched off on purpose, and on again: as any other
    for name in list(gone):
        if said.get(name) or name not in kinds:
            del gone[name]                      # back, or no longer a machine of the network
    for name in acted:
        if said.get(name):
            back[name] = now                    # it answered after an act: the act worked
    for name, there in list(pointed.items()):   # its devices went to the standby: back once it answers
        if said.get(name) and point:
            try:
                point(there["before"])
            except KiwiError as e:
                events.append({"host": name, "kind": "held", "text": "%s answers again, but its name could not be pointed "
                               "back at it (%s): its devices stay at %s" % (name, str(e).splitlines()[0][:120], there["standby"])})
                continue
            del pointed[name]
            events.append({"host": name, "kind": "moved", "text": "%s answers again: its devices are sent back to it" % name})
    opened = state.setdefault("opened", {})
    for name, kind in kinds.items():
        if (locked or {}).get(name):            # there, but its disk waits: not gone
            gone.pop(name, None)
            what = "%s waits at boot with its disk locked" % name
            times = [t for t in opened.get(name, []) if now - t < 86400]
            opened[name] = times
            if unlock is None or (fleet.defaults.get("recovery") or {}).get("unlock", True) is False:
                events.append({"host": name, "kind": "locked", "text": what})
            elif len(times) >= 2 * A_DAY:
                events.append({"host": name, "kind": "locked",
                               "text": what + ". Its ring was forwarded %d times today and it waits again: look at it" % len(times)})
            elif times and now - times[-1] < OPEN_AGAIN:
                events.append({"host": name, "kind": "unlocked", "text": what + ": its ring was forwarded a moment ago"})
            else:
                try:
                    unlock(name)
                except KiwiError as e:
                    events.append({"host": name, "kind": "locked", "text": what + ". It could not be opened from here (%s)"
                                   % str(e).splitlines()[0][:160]})
                    continue
                times.append(now)
                events.append({"host": name, "kind": "unlocked", "text": what + ": its ring was forwarded, it starts now"})
            continue
        if said.get(name, True):
            continue
        if name in off:                         # switched off on purpose (power off): not gone, nothing done
            gone.pop(name, None)
            events.append({"host": name, "kind": "off", "text": "%s is switched off on purpose" % name})
            continue
        # gone since the report that did not come after its last one (the agent reports every minute), not
        # since this look: a look comes minutes after a machine fell silent
        heard = (last or {}).get(name)
        since = min(now, heard + REPORT_EVERY) if heard and now - heard < 3600 else now
        # not before the last look that saw it answer (an agent that stopped reporting while the machine ran on)
        since = gone.setdefault(name, min(now, max(since, seen_ok.get(name, 0))))
        minutes = int((now - since) / 60)
        after, do = policy(fleet, kind)
        what = "%s%s has not answered for %d minute%s" % (name, " (the master)" if kind == "master" else "", minutes,
                                                        "" if minutes == 1 else "s")
        case = cases.setdefault(name, {"opened": since, "did": []})
        checks = []

        def found(conclusion):
            case.update(conclusion=conclusion, found=("%s: %s." % (conclusion[0].upper() + conclusion[1:], "; ".join(checks)))
                        if checks else conclusion[0].upper() + conclusion[1:] + ".")
        # 1. did it say it goes down? (its last report, after the last look that saw it answer)
        word = (words or {}).get(name)
        if word and word.get("at", 0) >= seen_ok.get(name, 0) - REPORT_EVERY and now - word.get("at", 0) < 86400:
            checks.append("it said %s ago that it %s%s" % (_ago(now - word["at"]), WORDS.get(word.get("reason"), "stops"),
                                                          " (%s)" % word["why"] if word.get("why") else ""))
            if word.get("reason") in ("poweroff", "halt") and word.get("why"):
                # switched off by kiwi-server (power off): left alone. One without a reason (the provider's
                # maintenance, the button) is looked at like any other silence
                gone.pop(name, None)
                found("switched off")
                events.append({"host": name, "kind": "off", "planned": True, "text": "%s is switched off: %s" % (name, checks[0])})
                continue
            if word.get("reason") == "reboot":
                if now - word["at"] < max(PLANNED_WAIT, after * 60):
                    found("planned")
                    events.append({"host": name, "kind": "waiting", "planned": True, "text": "%s restarts: %s" % (name, checks[0])})
                    continue
                checks.append("it is not back %s later" % _ago(now - word["at"]))
        if minutes < after:
            found("not known yet")
            events.append({"host": name, "kind": "waiting", "text": what})
            continue
        def send_devices(what=what):
            """A master gone for its `after`: its devices go to its standby (moved), also when its whole site is away."""
            standbys = [s for s in fleet.standbys(name) if s.standby_endpoint] if point and kind == "master" else []
            if point and standbys and name not in pointed:
                sb = standbys[0]
                try:
                    before = point(sb.standby_endpoint)
                except KiwiError as e:
                    events.append({"host": name, "kind": "held", "text": what + ". Its devices could not be sent to its "
                                   "standby %s (%s)" % (sb.name, str(e).splitlines()[0][:120])})
                else:
                    pointed[name] = {"standby": sb.name, "before": before, "since": now}
                    events.append({"host": name, "kind": "moved", "text": what + ": its devices are sent to its standby %s" % sb.name})
        # 2. does any other machine of its site answer? None: the whole site is away, restarting one helps nothing
        if site and site.get(name):
            mates = sorted(n for n in kinds if n != name and site.get(n) == site[name] and n not in off)
            if mates and not any(said.get(n, True) for n in mates):
                checks.append("no other machine at %s answers either (%s)" % (site[name], ", ".join(mates)))
                found("its whole site is away")
                send_devices(what)          # the devices still go to a standby master elsewhere
                events.append({"host": name, "kind": "tell", "text": "%s. %s Nothing is restarted." % (what, case["found"])})
                continue
        # 3. do the machines that give it its permissions, or its standby, still reach it?
        if reached:
            try:
                r = reached(name)
            except KiwiError as e:
                r = (None, "its voters could not be asked (%s)" % str(e).splitlines()[0][:80])
            if r and r[1]:
                checks.append(r[1])
            if r and r[0]:
                found("only the way from the control node is cut")
                events.append({"host": name, "kind": "held", "text": "%s. %s Nothing is done." % (what, case["found"])})
                continue
        # 4. what does its provider say? Switched off there: the restart below starts it. Running and silent: it hangs
        ps = None
        if provider and (where(name) if where else (fleet.hosts[name].cfg.get("vps") or {}).get("server")):
            try:
                ps = provider(name)
            except KiwiError:
                ps = None
            if ps:
                checks.append({"SHUTOFF": "its provider says it is switched off",
                               "RUNNING": "its provider says it runs: it hangs"}.get(ps, "its provider says %s" % ps.lower()))
        found({"SHUTOFF": "switched off at its provider", "RUNNING": "it hangs"}.get(ps, "not known"))
        if checks:
            what = "%s (%s)" % (what, "; ".join(checks))
        send_devices(what)
        if do == "tell":
            events.append({"host": name, "kind": "tell", "text": what})
            continue
        ref = where(name) if where else (fleet.hosts[name].cfg.get("vps") or {}).get("server")
        if not ref:
            events.append({"host": name, "kind": "tell",
                           "text": what + ". It is not known at a provider, so it cannot be restarted from here"})
            continue
        times = [t for t in acted.get(name, []) if now - t < 86400]
        acted[name] = times
        if len(times) >= A_DAY:
            events.append({"host": name, "kind": "held",
                           "text": what + ". It was restarted %d times today and is still gone: look at it" % len(times)})
            continue
        if times and now - times[-1] < AGAIN_AFTER and back.get(name, 0) <= times[-1]:
            events.append({"host": name, "kind": "held", "text": what + ". It was restarted %s ago: waiting for it"
                           % util.count((now - times[-1]) / 60, "minute")})
            continue
        try:
            restart(name)
        except KiwiError as e:
            # the provider does not answer: this computer may be the one that is cut off
            events.append({"host": name, "kind": "held",
                           "text": what + ". The restart at its provider did not work (%s): nothing was done"
                           % str(e).splitlines()[0][:120]})
            continue
        times.append(now)
        case["did"].append("restarted at its provider")
        events.append({"host": name, "kind": "restarted", "text": what + ": restarted at its provider"})
    for e in events:        # since when it is silent: the messages count from then (health.update)
        if e.get("host") in gone:
            e.setdefault("since", gone[e["host"]])
    return events


SWITCH_AFTER = 5            # minutes a machine is gone before its parts kept ready elsewhere go there
COPY_LAG = 600              # seconds a copy kept ready may have been behind when its machine went silent
SWITCH_AGAIN = 1800         # seconds before the same part is switched by itself once more


def switch_policy(fleet):
    """(on, after minutes) for parts kept ready on another machine."""
    rec = fleet.defaults.get("recovery") or {}
    sw = rec.get("switch", {})
    if sw is False:
        return False, SWITCH_AFTER
    if not isinstance(sw, dict):
        return False, SWITCH_AFTER          # a word where a block belongs: by hand, the safe side
    try:
        after = max(1, int(sw.get("after", SWITCH_AFTER)))
    except (TypeError, ValueError):
        after = SWITCH_AFTER
    return sw.get("do", "switch") == "switch", after        # tell, or a word it does not know: by hand


def switches(fleet, links, said, state, now, sees, copy_age=None, independent=None):
    """After step(): the parts kept ready on another machine whose machine is gone. `links`:
    [(machine it runs on, part, standby)]; `said` as for step(); `state` the fleet's state, with
    step()'s `gone`; `sees(standby, machine)` whether the standby reaches that machine itself;
    `copy_age(standby, part)` how old the standby's copy is (seconds, None: none); `independent(standby,
    machine)` whether the standby's word counts beside the control node's (not when both reach the
    machine the same way, the VPN, and that way is down). A copy that was
    behind already when the machine went silent is not switched to by itself: that would lose
    what came since, and the machine, once back, would take the old copy over its own.
    Returns events: kind `switch` (with `part`: the control node starts the switch), or waiting,
    held, tell."""
    on, after = switch_policy(fleet)
    gone, done, events = state.get("gone") or {}, state.setdefault("switched", {}), []
    from . import policy
    for src, part, dst in links:
        if said.get(src, True) or src not in gone or src in (state.get("off") or {}):
            continue
        # what step()'s look found explains it: a restart it said it makes, switched off, its whole site away,
        # or only the way from here cut. Nothing is switched then (the review of 22 Nov 2026)
        if ((state.get("cases") or {}).get(src) or {}).get("conclusion") in QUIET_CONCLUSIONS:
            continue
        lost = policy.row(fleet, fleet.hosts[src], part, dst, None)["lost"] if src in fleet.hosts else ("switch" if on else "ask")
        if lost == "wait":
            continue        # its row says it waits for its machine
        h = fleet.hosts.get(src)
        if "control" in ((getattr(h, "modules", None) or []) + list(((getattr(h, "role_settings_raw", None) or {}).get("modules")) or [])):
            continue        # the control node's own machine: whoever reads this is not it (a computer's `gone`)
        minutes = int((now - gone[src]) / 60)
        what = "%s has not answered for %d minute%s; %s is kept ready on %s" % (src, minutes, "" if minutes == 1 else "s", part, dst)
        key = "%s/%s" % (src, part)
        if lost != "switch":                # a person switches it (its row, or recovery: switch off)
            events.append({"host": src, "kind": "tell", "part": part, "text": what + ": kiwi-server switch, by hand"})
            continue
        if minutes < after:
            events.append({"host": src, "kind": "waiting", "part": part, "text": what})
            continue
        if now - done.get(key, 0) < SWITCH_AGAIN:
            events.append({"host": src, "kind": "held", "part": part, "text": what + ": its switch is on its way"})
            continue
        if not said.get(dst):
            events.append({"host": src, "kind": "held", "part": part,
                           "text": what + ", which does not answer either: nothing is switched"})
            continue
        if independent is not None and not independent(dst, src):
            events.append({"host": src, "kind": "held", "part": part,
                           "text": what + ": %s is at another site and the master does not answer, so %s may only be cut off "
                                          "the same way; nothing is switched" % (dst, src)})
            continue
        try:
            seen = sees(dst, src)
        except KiwiError as e:
            events.append({"host": src, "kind": "held", "part": part,
                           "text": what + ": %s could not be asked whether it reaches %s (%s)" % (dst, src, str(e).splitlines()[-1][:100])})
            continue
        if seen:
            events.append({"host": src, "kind": "held", "part": part,
                           "text": what + ": %s still reaches it, so only the way from here is cut; nothing is switched" % dst})
            continue
        if copy_age is not None:
            try:
                age = copy_age(dst, part)
            except KiwiError:
                age = None
            behind = None if age is None else age - (now - gone[src])      # how far behind it was when the machine fell silent
            if behind is None or behind > COPY_LAG:
                events.append({"host": src, "kind": "tell", "part": part, "text": what + ": %s, so nothing is switched by itself "
                               "(kiwi-server switch, by hand, if that copy will do)" % (
                                   "%s has no copy of it" % dst if age is None else
                                   "its copy there was %d minutes behind already when %s fell silent" % (behind // 60, src))})
                continue
        done[key] = now
        events.append({"host": src, "kind": "switch", "part": part, "to": dst,
                       "text": what + ", and %s does not reach it either: %s goes to %s" % (dst, part, dst)})
    return events
