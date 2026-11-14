"""The control node looks after machines that are gone (design/recovery.md, step 2).

`recovery:` in the fleet file's defaults says, per kind of machine, how long it must be
unreachable and what may then be done without a person:

    recovery:
      master: { after: 15, do: restart }     # minutes; tell | restart | reinstall
      node:   { after: 60, do: tell }

`tell` says it (the page shows it). `restart` is a power cycle through the machine's
provider: it destroys nothing. `reinstall` is not built yet and is read as `restart`.
Guards no policy switches off: the same machine is not acted on twice within an hour nor
more than three times a day, and nothing is done when the provider cannot be asked, since
then this computer is the one that is cut off.
"""
from .util import KiwiError

DO = ("tell", "restart", "reinstall")
DEFAULT = {"master": {"after": 15, "do": "tell"}, "node": {"after": 60, "do": "tell"}}
AGAIN_AFTER = 3600          # seconds between two acts on one machine
A_DAY = 3                   # acts on one machine in 24 hours


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


def step(fleet, kinds, said, state, now, restart, where=None):
    """One look. `kinds`: {machine: "master"|"node"} for the machines that have their role;
    `said`: {machine: bool} whether it answered just now; `state`: the fleet's state, changed
    in place (`gone`: since when, `acted`: when); `restart(name)` does a power cycle and may
    raise KiwiError; `where(name)` says what the machine is at its provider, or None. Returns the events: [{host, kind: waiting|tell|restarted|held, text}]."""
    gone, acted, events = state.setdefault("gone", {}), state.setdefault("acted", {}), []
    for name in list(gone):
        if said.get(name) or name not in kinds:
            del gone[name]                      # back, or no longer a machine of the network
    for name, kind in kinds.items():
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
            events.append({"host": name, "kind": "held", "text": what + ". It was restarted %d minutes ago: waiting for it"
                           % int((now - times[-1]) / 60)})
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
