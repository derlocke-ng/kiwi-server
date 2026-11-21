"""What the network remembers about its machines, nothing secret (`.kiwi-state.json`): when each
was applied and with what, which backups a server was told to take, what a machine holds back,
which is switched off on purpose, which copies of the network were made.

It has one keeper (docs/design/authority.md, step 1, "one truth"). Where the network has a control
node, that is the control node: a computer keeps a copy it reads from, sends each change it makes
(not the file), and takes the control node's copy with every sync. A computer remembered its own
before, and the control node's applies never reached it: the app showed machines as "changes not
applied yet" that had them (the owner, 21 Nov 2026: "that's the stuff i don't wanna see").
Without a control node the computer is the keeper, as before.

A change is a list of steps, ["set", [key, ...], value] or ["del", [key, ...]], worked out from the
state before and after: two computers that change different machines' lines never meet."""
import copy
import json
import os
import time

from . import util

FILE = ".kiwi-state.json"
OUTBOX = os.path.join("control", "state-out.json")     # changes made here, not taken by the control node yet
HANDED = os.path.join("control", "state-handed")       # this copy's own notes went to the control node once
TIMES = ".kiwi-state-times.json"    # on the keeper: when each line was last changed, so an older change gives way
KEEP_TIMES = 30 * 86400
REMOTE = os.path.join("control", "remote")             # controlsync.REMOTE: this copy has a control node


def path(fleet_dir):
    return os.path.join(fleet_dir, FILE)


def lock(fleet_dir):
    """One at a time, across processes, for reading, changing and writing it: two applies at once
    (two jobs on the control node) must not write over each other's lines."""
    return util.dir_lock(os.path.join(fleet_dir, "output"))


def read(fleet_dir):
    try:
        state = json.loads(util.read_text(path(fleet_dir)))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _write(fleet_dir, state):
    util.write_text(path(fleet_dir), json.dumps(state, indent=1, sort_keys=True) + "\n", 0o644)


def kept_elsewhere(fleet_dir):
    """The control node keeps it, not this copy: a computer joined to one. Never on the control
    node itself (KIWI_CONTROL_HOST), whose folder has no `control/remote`."""
    return not os.environ.get("KIWI_CONTROL_HOST") and os.path.isfile(os.path.join(fleet_dir, REMOTE))


def diff(before, after, at=()):
    """The steps that make `before` into `after`, down to single lines: a map that is new or gone
    here is new or gone line by line, so the keeper's other lines in it stay (a computer that
    knew no `applied` yet must not empty the keeper's)."""
    steps = []
    for k in sorted(set(before) | set(after), key=str):
        where = list(at) + [k]
        b, a = before.get(k), after.get(k)
        if isinstance(b, dict) or isinstance(a, dict):
            if isinstance(b, dict) and isinstance(a, dict):
                steps += diff(b, a, where)
                continue
            if isinstance(b, dict) and k not in after:
                steps += diff(b, {}, where)
                continue
            if isinstance(a, dict) and k not in before:
                steps += diff({}, a, where)
                continue
        if isinstance(b, list) and isinstance(a, list) and all(isinstance(x, (str, int)) for x in a + b):
            # a list of names (standby_links, lease_hosts): item by item, so two sides that change one list at
            # once keep both their changes (the review of 22 Nov 2026)
            steps += [["remove", where, x] for x in b if x not in a] + [["add", where, x] for x in a if x not in b]
            continue
        if k not in after:
            steps.append(["del", where])
        elif k not in before or b != a:
            steps.append(["set", where, a])
    return steps


def apply(state, steps):
    """`state` with the steps done (in place, and returned). A step that does not fit (a path
    through something that is no map) is left out: the keeper's state stays whole."""
    for step in steps or []:
        try:
            op, where = step[0], [str(k) for k in step[1]]
        except (TypeError, IndexError, KeyError):
            continue
        if not where or op not in ("set", "del", "add", "remove"):
            continue
        node, chain = state, []
        for k in where[:-1]:
            if not isinstance(node, dict):
                break
            if op == "del" and k not in node:
                node = None
                break
            chain.append((node, k))
            node = node.setdefault(k, {})
        if not isinstance(node, dict):
            continue
        if op in ("add", "remove") and len(step) > 2:
            items = node.get(where[-1])
            items = list(items) if isinstance(items, list) else []
            if op == "add" and step[2] not in items:
                items.append(step[2])
            elif op == "remove" and step[2] in items:
                items.remove(step[2])
            node[where[-1]] = sorted(items, key=str)
        elif op == "set" and len(step) > 2:
            node[where[-1]] = step[2]
        elif op == "del":
            node.pop(where[-1], None)
            for parent, k in reversed(chain):       # a map left empty goes too
                if parent.get(k) == {}:
                    parent.pop(k)
    return state


def outbox(fleet_dir):
    try:
        steps = json.loads(util.read_text(os.path.join(fleet_dir, OUTBOX)))
    except (OSError, ValueError):
        return []
    return steps if isinstance(steps, list) else []


def _set_outbox(fleet_dir, steps):
    p = os.path.join(fleet_dir, OUTBOX)
    if steps:
        os.makedirs(os.path.dirname(p), mode=0o700, exist_ok=True)
        util.write_text(p, json.dumps(steps) + "\n", 0o600)
    elif os.path.exists(p):
        os.remove(p)


def _line(step):
    """The line a step is about: its path, and for a list the item."""
    return json.dumps(list(step[1]) + ([step[2]] if step[0] in ("add", "remove") else []))


def _timed(steps, when):
    """Steps with the time they were made, as their fourth element (an older one gives way, receive)."""
    return [[s[0], s[1], s[2] if len(s) > 2 else None, when] for s in steps]


def _times(fleet_dir):
    try:
        t = json.loads(util.read_text(os.path.join(fleet_dir, TIMES)))
    except (OSError, ValueError):
        return {}
    return t if isinstance(t, dict) else {}


def _set_times(fleet_dir, stamps, now):
    stamps = {k: v for k, v in stamps.items() if isinstance(v, (int, float)) and now - v < KEEP_TIMES}
    util.write_text(os.path.join(fleet_dir, TIMES), json.dumps(stamps, sort_keys=True) + "\n", 0o644)


def change(fleet_dir, mutate):
    """Change what is remembered: `mutate(state)` changes the map it is given (and may return
    something, which is returned). Written here at once; on a computer with a control node the
    steps also wait in the outbox for the next sync, which sends them, each with its time."""
    with lock(fleet_dir):
        before = read(fleet_dir)
        after = copy.deepcopy(before)
        result = mutate(after)
        steps = diff(before, after)
        if steps:
            _write(fleet_dir, after)
            now = int(time.time())
            if kept_elsewhere(fleet_dir):
                _set_outbox(fleet_dir, outbox(fleet_dir) + _timed(steps, now))
            else:                           # the keeper's own change: its lines are this new
                stamps = _times(fleet_dir)
                stamps.update({_line(s): now for s in steps})
                _set_times(fleet_dir, stamps, now)
    return result


def take(fleet_dir, theirs, sent=0):
    """The keeper's state came (a sync): it becomes this copy's, with the changes made here since
    that it has not had yet. `sent`: how many of the outbox's steps it took with this answer."""
    with lock(fleet_dir):
        left = outbox(fleet_dir)[sent:]
        _write(fleet_dir, apply(copy.deepcopy(theirs if isinstance(theirs, dict) else {}), left))
        _set_outbox(fleet_dir, left)


def receive(fleet_dir, steps):
    """On the keeper: a computer's changes. Returns the state after them. A step older than the last
    change of its line here gives way (the review of 22 Nov 2026: an outbox sent hours later wrote
    over newer values); one without a time is taken, as before."""
    with lock(fleet_dir):
        stamps, now, taken = _times(fleet_dir), int(time.time()), []
        for s in steps or []:
            try:
                when, line = (s[3] if len(s) > 3 else None), _line(s)
            except (TypeError, IndexError, KeyError):
                continue
            if isinstance(when, (int, float)) and stamps.get(line, 0) > when:
                continue
            taken.append(s)
            stamps[line] = when if isinstance(when, (int, float)) else now
        state = apply(read(fleet_dir), taken)
        if taken:
            _write(fleet_dir, state)
            _set_times(fleet_dir, stamps, now)
        return state


def handed(fleet_dir):
    return os.path.isfile(os.path.join(fleet_dir, HANDED))


def hand_over(fleet_dir, theirs):
    """The first exchange of a copy that kept its own notes until now: the steps that give the
    keeper what is newer here (a machine this computer applied after the control node did, a
    copy of the network made here, install media used from here). The rest is the keeper's."""
    mine, theirs, steps = read(fleet_dir), theirs or {}, []
    for name, t in (mine.get("applied") or {}).items():
        if isinstance(t, (int, float)) and t > ((theirs.get("applied") or {}).get(name) or 0):
            steps.append(["set", ["applied", name], t])
            for key in ("rendered", "backup_clients"):
                if name in (mine.get(key) or {}):
                    steps.append(["set", [key, name], mine[key][name]])
    for name, mid in (mine.get("installed_media") or {}).items():
        if name not in (theirs.get("installed_media") or {}):
            steps.append(["set", ["installed_media", name], mid])
    # a machine switched off from here, a part held back from here: the keeper had no word of them
    for key in ("off", "hold", "hold_parts"):
        for name, v in (mine.get(key) or {}).items():
            if name not in (theirs.get(key) or {}):
                steps.append(["set", [key, name], v])
    copy_here, copy_there = mine.get("copy") or {}, theirs.get("copy") or {}
    for name, t in (copy_here.get("hosts") or {}).items():
        if t > ((copy_there.get("hosts") or {}).get(name) or 0):
            steps.append(["set", ["copy", "hosts", name], t])
    for key in ("local", "fetched"):
        here, there = copy_here.get(key), copy_there.get(key)
        when = (lambda x: (x or {}).get("time", 0) if isinstance(x, dict) else (x or 0))
        if here is not None and when(here) > when(there):
            steps.append(["set", ["copy", key], here])
    return steps


def mark_handed(fleet_dir):
    p = os.path.join(fleet_dir, HANDED)
    os.makedirs(os.path.dirname(p), mode=0o700, exist_ok=True)
    util.write_text(p, "", 0o600)
