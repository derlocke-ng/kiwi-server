"""What a removed controller knew, made new (docs/design/controllers.md, Rotation).

Built so far: the SSH keys. The fleet's own admin key and its SSH CA are made anew, and
each machine takes the new ones while the old still open it; the old ones go when every
machine has changed. A run that could not reach a machine is finished by the next run.
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
            "A machine that is gone or will be installed anew: --waive NAME" % ", ".join(left))
    else:
        keys = state["ssh"]["keys"]
        finish(fleet, state)
        say("the old SSH keys are gone")
        if keys:                # (found: a desktop VM that had the fleet's key by hand was locked out)
            say("anything outside the fleet that you gave the fleet's key by hand (a desktop, a NAS) needs the new "
                "one in its authorized_keys: %s" % ", ".join(k + ".pub" for k in keys))
    return left


def plan(fleet, control_ways=()):
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
    rows.append(("Generated passwords (databases, admin logins)", "not built yet", "a new seed: a later release"))
    rows.append(("WireGuard keys of the machines", "not built yet", "a later release"))
    rows.append(("The key that unlocks disks at boot", "not built yet", "a later release"))
    rows.append(("The certificate authority", "not built yet", "a later release; every device imports the new one"))
    outside = [(n, w) for n, w in (("secrets/netcup.token", "the hoster's API token"),)
               if os.path.isfile(fleet.resolve_path(n))]
    for _n, what in outside:
        rows.append((what[0].upper() + what[1:], "kept outside", "by hand, at the provider"))
    return rows
