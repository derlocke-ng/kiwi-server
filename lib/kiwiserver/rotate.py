"""What a removed controller knew, made new (docs/design/controllers.md, Rotation).

The SSH keys: the fleet's own admin key and its SSH CA are made anew, and each machine
takes the new ones while the old still open it; the old ones go when every machine has
changed. A run that could not reach a machine is finished by the next run.

The seed: a new one, so every generated secret that its containers read at every start
(`rotate: restart` in the module) changes at the machine's next apply. The others live in
a database or an account: they are pinned to the value they have (secrets/pins.json) until
their module has a step that changes them there.
"""
import json
import os
import shlex
import subprocess
import time

from . import config, remote, sshca, util
from .util import KiwiError

STATE = "secrets/rotation.json"
OLD = ".old"


def _state_path(fleet):
    return fleet.resolve_path(STATE)


def load(fleet):
    p = _state_path(fleet)
    return json.loads(util.read_text(p)) if os.path.isfile(p) else {}


def _save(fleet, state):
    util.write_text(_state_path(fleet), json.dumps(state, indent=1) + "\n", 0o600)


def fleet_keys(fleet):
    """The private admin keys the fleet itself holds, as admin.ssh_keys names them without
    the .pub (entries that are a .pub with the private key next to it)."""
    out = []
    for a in [fleet.defaults.get("admin") or {}] + [h.admin for h in fleet.hosts.values()]:
        for k in a.get("ssh_keys") or []:
            k = str(k)
            if config.is_ssh_key(k) or not k.endswith(".pub"):
                continue
            if os.path.isfile(fleet.resolve_path(k[:-4])) and k[:-4] not in out:
                out.append(k[:-4])
    return out


def other_keys(fleet):
    """admin.ssh_keys that are somebody's own key: not the fleet's to replace."""
    out = []
    for a in [fleet.defaults.get("admin") or {}] + [h.admin for h in fleet.hosts.values()]:
        for k in a.get("ssh_keys") or []:
            k = str(k)
            mine = not config.is_ssh_key(k) and k.endswith(".pub") and os.path.isfile(fleet.resolve_path(k[:-4]))
            if not mine and k not in out:
                out.append(k)
    return out


def _keygen(*argv):
    r = subprocess.run(["ssh-keygen"] + list(argv), capture_output=True, text=True)
    if r.returncode != 0:
        raise KiwiError("ssh-keygen: %s" % (r.stderr.strip() or r.stdout.strip())[:300])


def _blob(pub_path):
    return util.read_text(pub_path).split()[1]


def pending(fleet, state=None):
    """The machines that still take the old keys, or [] when no rotation is under way."""
    ssh = (state if state is not None else load(fleet)).get("ssh")
    if not ssh:
        return []
    return [n for n in fleet.hosts if n not in ssh["done"]]


def begin(fleet, why=""):
    """New keys beside the old ones (.old). Nothing when a rotation is under way already."""
    state = load(fleet)
    if state.get("ssh"):
        return state
    keys = fleet_keys(fleet)
    if not keys and not sshca.exists(fleet):
        raise KiwiError("this fleet holds no SSH key of its own (admin.ssh_keys are keys of yours): "
                        "replace those by hand, then apply")
    for priv in map(fleet.resolve_path, keys):
        comment = (util.read_text(priv + ".pub").split(None, 2) + [""] * 3)[2].strip() or "kiwi admin"
        os.replace(priv, priv + OLD)
        os.replace(priv + ".pub", priv + OLD + ".pub")
        _keygen("-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", priv)
        os.chmod(priv, 0o600)
    had_ca = sshca.exists(fleet)
    if had_ca:
        ca = sshca.path(fleet)
        os.replace(ca, ca + OLD)
        os.replace(ca + ".pub", ca + OLD + ".pub")
        sshca.init(fleet)               # the new one is trusted beside the old from here on
    state["ssh"] = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "why": why, "done": [],
                    "keys": keys, "ca": had_ca}
    _save(fleet, state)
    return state


# as root on the machine; $1 the admin user, $2 the old key's blob, $3 the new key's line
_ADD = r'''
set -eu
U=$1; old=$2; new=$3
H=$(getent passwd "$U" | cut -d: -f6)
[ -n "$H" ] || { echo "no user $U here" >&2; exit 1; }
blob=$(printf '%s' "$new" | cut -d' ' -f2)
add() {
    [ ! -s "$1" ] || [ -z "$(tail -c1 "$1")" ] || echo >> "$1"
    printf '%s\n' "$new" >> "$1"
}
found=
for f in "$H/.ssh/authorized_keys" "$H"/.ssh/authorized_keys.d/*; do
    [ -f "$f" ] || continue
    grep -qF -- "$old" "$f" || continue
    found=1
    grep -qF -- "$blob" "$f" || add "$f"
done
if [ -z "$found" ] && ! cat "$H/.ssh/authorized_keys" "$H"/.ssh/authorized_keys.d/* 2>/dev/null | grep -qF -- "$blob"; then
    f="$H/.ssh/authorized_keys"
    [ -d "$H/.ssh" ] || { mkdir -m 0700 "$H/.ssh"; chown "$U:" "$H/.ssh"; }
    fresh=; [ -e "$f" ] || fresh=1
    add "$f"
    [ -z "$fresh" ] || { chown "$U:" "$f"; chmod 0600 "$f"; }
fi
'''

_DROP = r'''
set -eu
U=$1; old=$2
H=$(getent passwd "$U" | cut -d: -f6)
for f in "$H/.ssh/authorized_keys" "$H"/.ssh/authorized_keys.d/*; do
    [ -f "$f" ] || continue
    grep -qF -- "$old" "$f" || continue
    grep -vF -- "$old" "$f" > "$f.kiwi" || true
    cat "$f.kiwi" > "$f"
    rm -f "$f.kiwi"
done
'''


def _script(body, *args):
    return "set -- %s\n%s" % (" ".join(shlex.quote(a) for a in args), body)


def _opens(tgt, priv, runner=subprocess.run):
    """Does this key alone get in?"""
    argv = ["ssh"] + remote.ssh_opts(identities=False) + ["-o", "IdentitiesOnly=yes", "-i", priv, tgt, "true"]
    return runner(argv, capture_output=True, stdin=subprocess.DEVNULL).returncode == 0


def host(fleet, h, tgt, state, runner=subprocess.run):
    """One machine: the new key in, proven, the old key out, the new CA on."""
    user = str((h.admin or {}).get("user") or "core")
    mine = [str(k)[:-4] for k in (h.admin or {}).get("ssh_keys") or []]
    for name in state["ssh"]["keys"]:
        priv = fleet.resolve_path(name)
        if name not in mine or not os.path.isfile(priv + OLD + ".pub"):
            continue
        old, new = _blob(priv + OLD + ".pub"), " ".join(util.read_text(priv + ".pub").split())
        remote.run(tgt, _script(_ADD, user, old, new), runner=runner)
        if not _opens(tgt, priv, runner):
            raise KiwiError("%s: the new key does not open it: the old one stays" % h.name)
        remote.run(tgt, _script(_DROP, user, old), runner=runner)
    if state["ssh"].get("ca") and sshca.exists(fleet):
        sshca.install(fleet, h, tgt)


def finish(fleet, state):
    """Every machine changed: the old keys go."""
    for name in state["ssh"]["keys"]:
        for p in (fleet.resolve_path(name) + OLD, fleet.resolve_path(name) + OLD + ".pub"):
            if os.path.isfile(p):
                os.remove(p)
    ca = sshca.path(fleet)
    if os.path.isfile(ca + OLD + ".pub"):
        gone = _blob(ca + OLD + ".pub")
        known = fleet.resolve_path("secrets/known_hosts")
        if os.path.isfile(known):
            lines = util.read_text(known).splitlines()
            keep = [x for x in lines if not (x.startswith("@cert-authority ") and gone in x)]
            if keep != lines:
                util.write_text(known, "\n".join(keep) + "\n", 0o600)
        for p in (ca + OLD, ca + OLD + ".pub"):
            if os.path.isfile(p):
                os.remove(p)
    state.setdefault("last", {})["ssh"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    state["ssh"] = None
    _save(fleet, state)


def ssh(fleet, names=(), waive=(), why="", reach=None, say=util.say, runner=subprocess.run):
    """New SSH keys on the named machines (all that still have the old ones). Returns the
    machines that still wait."""
    state = begin(fleet, why)
    for n in waive:
        if n not in fleet.hosts:
            raise KiwiError("no such machine: %s" % n)
        if n not in state["ssh"]["done"]:
            state["ssh"]["done"].append(n)
            say("%s: left out (it keeps the old key until it is installed anew)" % n)
    _save(fleet, state)
    for n in (names or pending(fleet, state)):
        if n not in fleet.hosts:
            raise KiwiError("no such machine: %s" % n)
        if n in state["ssh"]["done"]:
            continue
        h = fleet.hosts[n]
        try:
            tgt = reach(h) if reach else remote.reach(h)
            host(fleet, h, tgt, state, runner)
        except KiwiError as e:
            say("%s: not changed: %s" % (n, e))
            continue
        state["ssh"]["done"].append(n)
        _save(fleet, state)
        say("%s: takes the new key%s" % (n, " and the new SSH CA" if state["ssh"].get("ca") else ""))
    left = pending(fleet, state)
    if left:
        say("still on the old keys: %s. Run this again when they are on; the old keys work until then. "
            "A machine that is gone or will be installed anew: Leave out on the page, or --waive NAME" % ", ".join(left))
    else:
        keys = state["ssh"]["keys"]
        finish(fleet, state)
        say("the old SSH keys are gone")
        if keys:                # (found: a desktop VM that had the fleet's key by hand was locked out)
            say("anything outside the fleet that you gave the fleet's key by hand (a desktop, a NAS) needs the new "
                "one in its authorized_keys: %s" % ", ".join(k + ".pub" for k in keys))
    return left


PINS = "secrets/pins.json"


def generated(fleet, ms):
    """[(hostname, module, setting)]: every generated secret of the fleet's machines."""
    out = []
    for h in fleet.hosts.values():
        for m in h.modules:
            out += [(h.hostname, m, s) for s in ms.get(m).settings if s.generate]
    return out


def seed_pending(fleet, state=None):
    seed = (state if state is not None else load(fleet)).get("seed")
    return [n for n in fleet.hosts if n not in seed["done"]] if seed else []


def seed_begin(fleet, ms, why=""):
    """A new seed. Before: what does not follow it is pinned to the value it has. The old
    seed stays beside it (.old) until every machine has changed, as a way back."""
    from kiwimodules.machine import seed_id
    state = load(fleet)
    if state.get("seed"):
        return state
    if not fleet.has_seed():
        raise KiwiError("this fleet has no seed: no secret of it is generated")
    pins = fleet.pins()
    for hostname, module, s in generated(fleet, ms):
        key = "%s/%s/%s" % (hostname, module, s.key)
        if getattr(s, "rotate", "") == "restart":
            pins.pop(key, None)         # pinned by an older release that could not change it yet
        # all but the ones a restart alone changes: one that is changed where it lives keeps
        # its value until that has worked on its machine (in_place)
        if getattr(s, "rotate", "") != "restart" and key not in pins:
            pins[key] = fleet.derive_secret(hostname, module, s.key)
    util.write_text(fleet.resolve_path(PINS), json.dumps(pins, indent=1, sort_keys=True) + "\n", 0o600)
    path = fleet.resolve_path("secrets/seed")
    was = seed_id(util.read_bytes(path))
    os.replace(path, path + OLD)
    util.write_bytes(path, os.urandom(32), 0o600)
    state["seed"] = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "why": why, "from": was, "done": []}
    _save(fleet, state)
    return state


def in_place(fleet, h, tgt, roles, ms, say=util.say, run=None):
    """This machine's pinned secrets whose module says how they are changed where they live
    (`rotate: {in, run}`): the new value is set there, then the pin goes and the next apply
    brings the files. One that does not work keeps its pin, so nothing breaks; the next run
    tries again (the command sets the same value)."""
    from kiwimodules import modules as modmod
    from kiwimodules.machine import derive_secret
    run = run or remote.run
    pins = fleet.pins()
    prefix = modmod.PREFIX[roles[h.role].node_type]
    runtime = str((h.role_settings or {}).get("runtime") or "podman")
    changed = []
    for hostname, module, s in generated(fleet, ms):
        key = "%s/%s/%s" % (hostname, module, s.key)
        if hostname != h.hostname or getattr(s, "rotate", "") != "exec" or key not in pins:
            continue
        if (h.role_settings_raw.get(module) or {}).get(s.key):
            continue                    # written in the fleet file: not ours to change
        new = derive_secret(fleet.seed(), hostname, module, s.key)
        script = "IFS= read -r NEW; export NEW; exec %s exec -e NEW %s sh -c %s" % (
            shlex.quote(runtime), shlex.quote("%s-%s" % (prefix, s.rotate_in)), shlex.quote(s.rotate_run))
        try:
            run(tgt, script, (new + "\n").encode())        # on stdin: never on a command line or in sudo's log
        except KiwiError as e:
            say("%s: %s.%s keeps its value (changing it did not work: %s)" % (h.name, module, s.key, str(e)[-160:]))
            continue
        del pins[key]
        util.write_text(fleet.resolve_path(PINS), json.dumps(pins, indent=1, sort_keys=True) + "\n", 0o600)
        changed.append("%s.%s" % (module, s.key))
        say("%s: %s.%s is changed where it lives" % (h.name, module, s.key))
    return changed


def seed(fleet, ms, apply, names=(), waive=(), why="", say=util.say, before=None):
    """The machines take the new seed: `apply(name)` is an apply of that machine, 0 when it
    worked; `before(name)` runs ahead of it. Returns the machines that still wait."""
    state = seed_begin(fleet, ms, why)
    for n in list(names) + list(waive):
        if n not in fleet.hosts:
            raise KiwiError("no such machine: %s" % n)
    for n in waive:
        if n not in state["seed"]["done"]:
            state["seed"]["done"].append(n)
            say("%s: left out (its next apply brings the new secrets)" % n)
    _save(fleet, state)
    todo = [n for n in (names or seed_pending(fleet, state)) if n not in state["seed"]["done"]]
    me = os.environ.get("KIWI_CONTROL_HOST")        # this node last: its apply may restart what runs this
    for n in [x for x in todo if x != me] + [x for x in todo if x == me]:
        if before:
            before(n)                   # what is changed where it lives, before the files follow
        if apply(n) != 0:
            say("%s: not changed" % n)
            continue
        state["seed"]["done"].append(n)
        _save(fleet, state)
        say("%s: runs with the new secrets" % n)
    left = seed_pending(fleet, state)
    if left:
        say("still on the old secrets: %s. Run this again when they are on. "
            "A machine that is gone or will be installed anew: Leave out on the page, or --waive NAME" % ", ".join(left))
    else:
        old = fleet.resolve_path("secrets/seed") + OLD
        if os.path.isfile(old):
            os.remove(old)
        state.setdefault("last", {}).update(seed=time.strftime("%Y-%m-%dT%H:%M:%S"), seed_from=state["seed"]["from"])
        state["seed"] = None
        _save(fleet, state)
        say("every machine runs with the new secrets; the old seed is gone")
    return left


def plan(fleet, control_ways=(), ms=None):
    """[(what, state, how)]: what a computer that managed this network could read, and how
    each is replaced."""
    state = load(fleet)
    last = state.get("last") or {}
    rows = []
    left = pending(fleet, state)
    if left:
        rows.append(("SSH keys of the machines", "under way: %s still on the old ones" % ", ".join(left), "rotate ssh"))
    elif fleet_keys(fleet) or sshca.exists(fleet):
        rows.append(("SSH keys of the machines", "replaced %s" % last["ssh"][:10] if last.get("ssh") else "never replaced", "rotate ssh"))
    if fleet_keys(fleet):
        rows.append(("The fleet's key on machines outside the fleet", "not reached by rotate ssh",
                     "by hand: the new %s into their authorized_keys" % ", ".join(k + ".pub" for k in fleet_keys(fleet))))
    for k in other_keys(fleet):
        rows.append(("SSH key %s" % (k if not config.is_ssh_key(k) else " ".join(k.split()[2:]) or k.split()[1][-12:]),
                     "a key of yours", "by hand: change admin.ssh_keys, then apply"))
    if "control" in control_ways:
        rows.append(("The control page's password", "", "on the page: Computers, Change the password"))
    if ms is not None and fleet.has_seed():
        gen = generated(fleet, ms)
        waits = seed_pending(fleet, state)
        follow = sorted({"%s.%s" % (m, x.key) for _h, m, x in gen if getattr(x, "rotate", "")})
        stay = sorted({"%s.%s" % (m, x.key) for _h, m, x in gen if not getattr(x, "rotate", "")})
        when = ("under way: %s still on the old ones" % ", ".join(waits) if waits
                else "replaced %s" % last["seed"][:10] if last.get("seed") else "never replaced")
        if follow:
            rows.append(("Secrets inside the services (%d kinds)" % len(follow), when, "rotate seed"))
        if stay:
            rows.append(("Database and admin passwords (%d kinds)" % len(stay), "kept as they are",
                         "not built yet: %s" % ", ".join(stay)))
    else:
        rows.append(("Generated passwords (databases, admin logins)", "", "rotate seed"))
    rows.append(("WireGuard keys of the machines", "not built yet", "a later release"))
    rows.append(("The key that unlocks disks at boot", "not built yet", "a later release"))
    rows.append(("The certificate authority", "not built yet", "a later release; every device imports the new one"))
    outside = [(n, w) for n, w in (("secrets/netcup.token", "the hoster's API token"),)
               if os.path.isfile(fleet.resolve_path(n))]
    for _n, what in outside:
        rows.append((what[0].upper() + what[1:], "kept outside", "by hand, at the provider"))
    return rows
