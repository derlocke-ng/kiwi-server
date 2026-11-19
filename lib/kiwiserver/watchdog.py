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
Guards no policy switches off: the same machine is not acted on twice within an hour nor
more than three times a day, and nothing is done when the provider cannot be asked, since
then this computer is the one that is cut off.
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


def step(fleet, kinds, said, state, now, restart, where=None, locked=None, unlock=None, point=None):
    """One look. `kinds`: {machine: "master"|"node"} for the machines that have their role;
    `said`: {machine: bool} whether it answered just now; `state`: the fleet's state, changed
    in place (`gone`: since when, `acted`: when); `restart(name)` does a power cycle and may
    raise KiwiError; `where(name)` says what the machine is at its provider, or None. `locked`: {machine: bool}
    whether it waits at boot with its disk locked; `unlock(name)` forwards its ring and may raise
    KiwiError. More kinds of events then: unlocked, locked. `point(address)` points the master's
    name (wg_host) at an address and returns the one it had, or raises KiwiError: with it, a master
    gone for its `after` sends the devices to its standby, and back when it answers (moved).
    Returns the events: [{host, kind: waiting|tell|restarted|held|moved, text}]."""
    gone, acted, events = state.setdefault("gone", {}), state.setdefault("acted", {}), []
    pointed = state.setdefault("pointed", {})
    for name in list(gone):
        if said.get(name) or name not in kinds:
            del gone[name]                      # back, or no longer a machine of the network
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
        since = gone.setdefault(name, now)
        minutes = int((now - since) / 60)
        after, do = policy(fleet, kind)
        what = "%s%s has not answered for %d minute%s" % (name, " (the master)" if kind == "master" else "", minutes,
                                                        "" if minutes == 1 else "s")
        if minutes < after:
            events.append({"host": name, "kind": "waiting", "text": what})
            continue
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
        if times and now - times[-1] < AGAIN_AFTER:
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
        events.append({"host": name, "kind": "restarted", "text": what + ": restarted at its provider"})
    return events
