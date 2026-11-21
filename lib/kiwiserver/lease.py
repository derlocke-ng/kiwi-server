"""Permissions that run out (docs/design/authority.md, step 2): a part that has a standby runs on
its machine only while one of its voters said so within the last three minutes. The voters are
the standby and a witness (another node at the machine's site; the master where there is none).
They give the permission; the machine never asks. So a machine that is cut off, or that comes
back after its part went to the standby, has stopped the part by itself, and nobody has to tell
it.

On the machine (the holder), `kiwi-lease`:
  grant   the forced command of a voter's key: "grant PART VOTER", the permission for LENGTH s
  tick    every 20 s: starts a part that may run, stops one that may not (kiwi-stack lease)
  check   exit 0 while PART may run here
  show    "PART SECONDS-LEFT" per part (the agent's report)
On a voter, `kiwi-vote` (every minute): gives each of its machines the permission, and on the
standby takes the part when the machine is gone: its own permission and the witness's have run
out, the switch by itself is set, and the copy here is fresh. The witness answers the standby's
question ("ask PART MACHINE", the forced command of the standby's key there) from what it gave
alone, and gives that machine nothing for that part any more once it said "gone".

Times are lengths, not clock times: the machine adds them to its own clock, a voter counts from
when it gave. A voter counts a permission as run out MARGIN seconds after the machine would, so
clocks that differ, a slow login and the machine's tick between them change nothing."""
import os
import shlex
import subprocess

from . import remote, util
from .util import KiwiError

DIR = "/etc/kiwi-server/lease"
LEASE_PATH = "/usr/local/sbin/kiwi-lease"
VOTE_PATH = "/usr/local/sbin/kiwi-vote"
UNIT = "kiwi-lease"          # the holder's tick
VOTE_UNIT = "kiwi-vote"      # the voter's minute
MARK = "kiwi-lease"          # the comment of its lines in root's authorized_keys
LENGTH = 180                 # seconds a permission lasts (the owner, 21 Nov 2026: three minutes)
MARGIN = 60                  # seconds a voter waits beyond that before it counts it as run out
COPY_FRESH = 900             # seconds: a standby takes a part only with a copy fetched this recently

HOLDER = r'''#!/bin/bash
# kiwi-server: the permissions of the parts of this machine that have a standby (lease.py). A part
# runs here only while its standby or its witness said so within the last LENGTH seconds.
d=${KIWI_LEASE_DIR:-/etc/kiwi-server/lease}
run=${KIWI_LEASE_RUN:-/run/kiwi-lease}
now=$(date +%s)
length=$(cat "$d/length" 2>/dev/null || echo 180)
until_of() { cat "$d/$1.until" 2>/dev/null || echo 0; }
may() {     # PART: may it run here now? A part installed a moment ago, never granted: for LENGTH s
    (( now < $(until_of "$1") )) && return 0
    [[ ! -e $d/$1.until && -e $d/$1.fresh ]] && (( now < $(cat "$d/$1.fresh") + length ))
}
case "${1:-}" in
grant)
    set -f
    read -r verb part voter extra <<< "${SSH_ORIGINAL_COMMAND:-}"
    if [[ $verb != grant || ! $part =~ ^[a-z0-9-]+$ || ! $voter =~ ^[a-z0-9-]+$ || -n $extra ]]; then
        logger -t kiwi-lease "refused: ${SSH_ORIGINAL_COMMAND:-}" 2>/dev/null
        echo "kiwi-lease: only: grant PART VOTER" >&2; exit 1
    fi
    if ! grep -qx "$part $voter" "$d/voters" 2>/dev/null; then
        echo "kiwi-lease: $voter gives no permission for $part here" >&2; exit 1
    fi
    exec 9>"$d/.lock"; flock 9
    u=$((now + length))
    (( u > $(until_of "$part") )) && echo "$u" > "$d/$part.until"
    rm -f "$d/$part.fresh"
    echo "$voter $now" > "$d/$part.by-$voter"
    echo "granted $length" ;;
check)
    may "${2:?PART}" ;;
show)
    while read -r part _rest; do
        [[ -n $part ]] || continue
        left=$(( $(until_of "$part") - now )); (( left < 0 )) && left=0
        echo "$part $left $(cat "$run/state-$part" 2>/dev/null || echo -)"
    done < "$d/parts" ;;
tick)
    mkdir -p "$run"
    exec 9>"$run/lock"; flock -n 9 || exit 0
    while read -r part names; do
        [[ -n $names ]] || continue
        was=$(cat "$run/state-$part" 2>/dev/null)
        # shellcheck disable=SC2086
        if may "$part"; then
            if [[ $was != running ]] && kiwi-stack lease start $names >/dev/null 2>&1; then
                echo running > "$run/state-$part"
                logger -t kiwi-lease "$part may run here: started ($names)"
            fi
        elif [[ $was != stopped ]]; then
            kiwi-stack lease stop $names >/dev/null 2>&1
            echo stopped > "$run/state-$part"
            [[ -z $was ]] || logger -t kiwi-lease "$part: its permission ran out: stopped ($names)"
        fi
    done < "$d/parts" ;;
*)
    echo "usage: kiwi-lease grant|tick|check PART|show" >&2; exit 2 ;;
esac
'''

VOTER = r'''#!/bin/bash
# kiwi-server: this machine's votes (lease.py). Every minute it gives the machines it votes for the
# permission to run their parts; as a standby it takes a part whose machine is gone. vote.conf:
#   PART MACHINE ADDRESS standby SWITCH WITNESS-ADDRESS CONTAINERS...    (SWITCH: on|off)
#   PART MACHINE ADDRESS witness
d=${KIWI_LEASE_DIR:-/etc/kiwi-server/lease}
ssh_to() { ssh -i "$d/key" -o UserKnownHostsFile="$d/known_hosts" -o StrictHostKeyChecking=yes -o BatchMode=yes \
               -o ConnectTimeout=10 -o IdentitiesOnly=yes "root@$1" "${@:2}"; }
now=$(date +%s)
me=$(cat "$d/me")
length=$(cat "$d/length" 2>/dev/null || echo 180)
margin=$(cat "$d/margin" 2>/dev/null || echo 60)
fresh=$(cat "$d/copy-fresh" 2>/dev/null || echo 900)
mkdir -p "$d/state"
gone_since() {      # PART MACHINE: true when what this machine gave there has run out for sure
    local given; given=$(cat "$d/state/$1@$2.given" 2>/dev/null || echo 0)
    (( now > given + length + margin ))
}
if [[ ${1:-} == ask ]]; then          # the forced command of the standby's key, on the witness
    set -f
    read -r verb part machine extra <<< "${SSH_ORIGINAL_COMMAND:-}"
    if [[ $verb != ask || ! $part =~ ^[a-z0-9-]+$ || ! $machine =~ ^[a-z0-9-]+$ || -n $extra ]] \
            || ! grep -q "^$part $machine [^ ]* witness" "$d/vote.conf"; then
        echo "kiwi-vote: only: ask PART MACHINE, for a part this machine is the witness of" >&2; exit 1
    fi
    exec 9>"$d/.lock"; flock 9
    if [[ -e $d/state/$part@$machine.refused ]] || gone_since "$part" "$machine"; then
        date +%s > "$d/state/$part@$machine.refused"      # from now on nothing more for it from here
        logger -t kiwi-vote "$part of $machine: gone, said to its standby; no permission from here any more"
        echo gone
    else
        echo here
    fi
    exit 0
fi
exec 9>"$d/.lock"; flock -n 9 || exit 0
while read -r part machine address role switch witness containers; do
    [[ -n $role ]] || continue
    st=$d/state/$part@$machine
    [[ -e $st.refused ]] && continue
    if ssh_to "$address" "grant $part $me" > /dev/null 2> "$st.err"; then
        echo "$now" > "$st.given"
        rm -f "$st.said"
        continue
    fi
    why=$(tail -1 "$st.err")
    [[ $why == "$(cat "$st.said" 2>/dev/null)" ]] || { logger -t kiwi-vote "$part of $machine: no permission given: $why"; echo "$why" > "$st.said"; }
    [[ $role == standby && $switch == on && -n $containers ]] || continue
    gone_since "$part" "$machine" || continue
    ok=$(cat "${KIWI_STANDBY_DIR:-/etc/kiwi-server/standby}/state/$part.ok" 2>/dev/null || echo 0)
    if (( now - ok > fresh )); then
        logger -t kiwi-vote "$part of $machine: gone, but the copy here is $(( (now - ok) / 60 )) minutes old: not taken"
        continue
    fi
    answer=$(ssh_to "$witness" "ask $part $machine" 2>/dev/null)
    if [[ $answer != gone ]]; then
        logger -t kiwi-vote "$part of $machine: its witness says ${answer:-nothing}: not taken"
        continue
    fi
    date +%s > "$st.refused"
    # shellcheck disable=SC2086
    if kiwi-stack ready start $containers; then
        echo "$now $machine" > "$d/state/$part.took"
        logger -t kiwi-vote "$part of $machine: taken here (its permission ran out, the witness agrees)"
    else
        logger -t kiwi-vote "$part of $machine: taken, but its containers did not start here"
    fi
done < "$d/vote.conf"
'''

TICK_SERVICE = ("[Unit]\nDescription=kiwi-server: start or stop the parts with a standby by their permission\n"
                "After=kiwi-stack.service\n\n[Service]\nType=oneshot\nExecStart=%s tick\n" % LEASE_PATH)
TICK_TIMER = ("[Unit]\nDescription=kiwi-server: the parts' permissions every 20 seconds\n\n[Timer]\nOnBootSec=15s\n"
              "OnUnitActiveSec=20s\nAccuracySec=2s\n\n[Install]\nWantedBy=timers.target\n")
VOTE_SERVICE = ("[Unit]\nDescription=kiwi-server: give the machines this one votes for their permissions\n"
                "After=network-online.target\n\n[Service]\nType=oneshot\nExecStart=%s\n" % VOTE_PATH)
VOTE_TIMER = ("[Unit]\nDescription=kiwi-server: the votes every minute\n\n[Timer]\nOnBootSec=20s\n"
              "OnUnitActiveSec=60s\nAccuracySec=2s\n\n[Install]\nWantedBy=timers.target\n")


def witness(fleet, machine, standby, is_master=None):
    """The third voter for a part of `machine` kept ready on `standby`: another node at the
    machine's site (it sees the same network and cannot be reached from the Internet), by name;
    the master where there is none. None: no third machine at all."""
    from .cli import site_of
    site = site_of(fleet.hosts[machine])
    master = is_master or (lambda h: False)
    near = sorted(h.name for h in fleet.hosts.values()
                  if h.name not in (machine, standby) and not master(h) and site_of(h) == site)
    if near:
        return near[0]
    far = sorted(h.name for h in fleet.hosts.values() if h.name not in (machine, standby) and master(h))
    return far[0] if far else None


def holder_conf(parts):
    """`parts` for kiwi-lease: {part: [container, ...]} -> its lines."""
    return "".join("%s %s\n" % (p, " ".join(c)) for p, c in sorted(parts.items()) if c)


def voters_conf(voters):
    """`voters`: {part: [voter, ...]} -> the lines a grant is checked against."""
    return "".join("%s %s\n" % (p, v) for p, vs in sorted(voters.items()) for v in sorted(set(vs)))


def vote_conf(lines):
    """Lines of vote.conf from dicts: part, machine, address, role ("standby" with switch, witness
    address and containers; or "witness")."""
    out = []
    for x in lines:
        if x["role"] == "standby":
            out.append("%s %s %s standby %s %s %s" % (x["part"], x["machine"], x["address"], "on" if x.get("switch") else "off",
                                                      x.get("witness") or "-", " ".join(x.get("containers") or [])))
        else:
            out.append("%s %s %s witness" % (x["part"], x["machine"], x["address"]))
    return "".join(line.rstrip() + "\n" for line in out)


def authorized_line(public, command):
    """A voter's key on another machine: one forced command, nothing else."""
    return 'command="%s",restrict %s %s' % (command, " ".join(public.split()[:2]), MARK)


def parse_show(text):
    """{part: (seconds left, state)} from `kiwi-lease show`."""
    out = {}
    for line in (text or "").splitlines():
        p = line.split()
        if len(p) >= 2 and p[1].isdigit():
            out[p[0]] = (int(p[1]), p[2] if len(p) > 2 else "-")
    return out


def script_text(name):
    return {"holder": HOLDER, "voter": VOTER}[name]


def write_scripts(folder):
    """Both scripts into `folder` (tests run them there)."""
    os.makedirs(folder, exist_ok=True)
    for name, text in (("kiwi-lease", HOLDER), ("kiwi-vote", VOTER)):
        util.write_text(os.path.join(folder, name), text, 0o755)


# ---- on apply: what each machine gets ------------------------------------------------------------

INSTALL = r"""set -eu
d=%(dir)s
install -d -m 0700 "$d" "$d/state"
umask 077
echo %(length)d > "$d/length"; echo %(margin)d > "$d/margin"; echo %(fresh)d > "$d/copy-fresh"
echo %(me)s > "$d/me"
# the parts of this machine that run by a permission, and who may give it
cat > "$d/parts" <<'KIWI_EOF'
%(parts)sKIWI_EOF
cat > "$d/voters" <<'KIWI_EOF'
%(voters)sKIWI_EOF
held="%(part_names)s"
for p in $held; do [ -e "$d/$p.until" ] || [ -e "$d/$p.fresh" ] || date +%%s > "$d/$p.fresh"; done
# the machines this one votes for
cat > "$d/vote.conf" <<'KIWI_EOF'
%(vote)sKIWI_EOF
# a refusal or a part taken holds while the line it is about is still here: the fleet moved the
# part when its line is gone (the switch is written down), and so do they
for f in "$d"/state/*.refused; do
    [ -e "$f" ] || continue; b=${f##*/}; b=${b%%.refused}
    grep -q "^${b%%@*} ${b#*@} " "$d/vote.conf" || rm -f "$f"
done
for f in "$d"/state/*.took; do
    [ -e "$f" ] || continue; b=${f##*/}; b=${b%%.took}
    grep -q "^$b [^ ]* [^ ]* standby" "$d/vote.conf" || rm -f "$f"
done
cat > "$d/known_hosts" <<'KIWI_EOF'
%(known)sKIWI_EOF
key=$(cat)
if [ -n "$key" ]; then printf '%%s\n' "$key" > "$d/key.new" && mv "$d/key.new" "$d/key"; else rm -f "$d/key"; fi
umask 022
cat > %(lease_path)s.new <<'KIWI_EOF'
%(holder)sKIWI_EOF
cat > %(vote_path)s.new <<'KIWI_EOF'
%(voter)sKIWI_EOF
chmod 0755 %(lease_path)s.new %(vote_path)s.new && mv %(lease_path)s.new %(lease_path)s && mv %(vote_path)s.new %(vote_path)s
# the voters' keys here: each can do one thing (give a permission, or ask the witness)
install -d -m 0700 /root/.ssh
touch /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys
{ grep -v ' %(mark)s$' /root/.ssh/authorized_keys || true; cat <<'KIWI_EOF'
%(authorized)sKIWI_EOF
} | grep -v '^$' > /root/.ssh/authorized_keys.new || true
chmod 600 /root/.ssh/authorized_keys.new && mv /root/.ssh/authorized_keys.new /root/.ssh/authorized_keys
command -v restorecon >/dev/null 2>&1 && restorecon -R /root/.ssh >/dev/null 2>&1 || true
keys="%(authorized_any)s"
if [ -n "$keys" ] && [ -d /etc/ssh/sshd_config.d ]; then
    printf '# kiwi-server: root may sign in only with a key that has a forced command (standby, permissions)\nPermitRootLogin forced-commands-only\n' > %(sshd)s
    systemctl reload sshd 2>/dev/null || systemctl reload ssh 2>/dev/null || true
fi
cat > /etc/systemd/system/%(unit)s.service <<'KIWI_EOF'
%(tick_service)sKIWI_EOF
cat > /etc/systemd/system/%(unit)s.timer <<'KIWI_EOF'
%(tick_timer)sKIWI_EOF
cat > /etc/systemd/system/%(vote_unit)s.service <<'KIWI_EOF'
%(vote_service)sKIWI_EOF
cat > /etc/systemd/system/%(vote_unit)s.timer <<'KIWI_EOF'
%(vote_timer)sKIWI_EOF
systemctl daemon-reload
if [ -s "$d/parts" ]; then systemctl enable -q --now %(unit)s.timer; %(lease_path)s tick || true
else systemctl disable -q --now %(unit)s.timer 2>/dev/null || true; fi
if [ -s "$d/vote.conf" ]; then systemctl enable -q --now %(vote_unit)s.timer; systemctl start --no-block %(vote_unit)s.service
else systemctl disable -q --now %(vote_unit)s.timer 2>/dev/null || true; fi
cat /etc/ssh/ssh_host_ed25519_key.pub
"""

REMOVE = ("[ -e %(lease)s ] || [ -e %(vote)s ] || exit 0; "
          "systemctl disable -q --now %(unit)s.timer %(vote_unit)s.timer 2>/dev/null || true; "
          "rm -f %(lease)s %(vote)s /etc/systemd/system/%(unit)s.* /etc/systemd/system/%(vote_unit)s.* "
          "%(dir)s/parts %(dir)s/voters %(dir)s/vote.conf %(dir)s/key; systemctl daemon-reload; "
          "[ -f /root/.ssh/authorized_keys ] && { grep -v ' %(mark)s$' /root/.ssh/authorized_keys > /root/.ssh/authorized_keys.new || true; "
          "chmod 600 /root/.ssh/authorized_keys.new; mv /root/.ssh/authorized_keys.new /root/.ssh/authorized_keys; }; true"
          % {"lease": LEASE_PATH, "vote": VOTE_PATH, "unit": UNIT, "vote_unit": VOTE_UNIT, "dir": DIR, "mark": MARK})


def key_path(fleet, voter):
    """A voter's key for giving permissions and asking the witness: one per voting machine."""
    path = fleet.resolve_path("secrets/lease/%s_ed25519" % voter)
    if not os.path.isfile(path):
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "kiwi-lease-%s" % voter, "-f", path], check=True)
    return path


def hostkey_path(fleet, name):
    from . import partstandby
    return partstandby._hostkey_path(fleet, name)


def links(fleet, roles):
    """{(machine, part): (standby, witness)} of the parts that run by a permission: those with a
    standby and a third machine to be the witness. One without a witness runs as before."""
    def is_master(h):
        return h.role in roles and roles[h.role].node_type == "master" and not h.standby_of
    from . import partstandby
    out = {}
    for machine, part, standby in partstandby.links(fleet):
        if machine not in fleet.hosts or standby not in fleet.hosts:
            continue
        w = witness(fleet, machine, standby, is_master)
        if w:
            out[(machine, part)] = (standby, w)
    return out


def plan(fleet, roles, host, leased_parts, switch_on):
    """What `host` gets: {parts, voters, vote, known, authorized, waits}. `leased_parts`: {part:
    [containers]} of this machine's bundle that run by a permission; for the voter's part, the
    containers a standby starts are its own bundle's (the caller fills `containers` by part)."""
    every = links(fleet, roles)
    mine = {p: c for p, c in (leased_parts or {}).items() if (host.name, p) in every}
    voters = {p: list(every[(host.name, p)]) for p in mine}
    authorized, vote, known, waits = [], [], [], []
    for p in sorted(mine):                      # the voters' keys on this machine: they give permissions
        for v in voters[p]:
            line = authorized_line(util.read_text(key_path(fleet, v) + ".pub"), "%s grant" % LEASE_PATH)
            if line not in authorized:
                authorized.append(line)

    def known_line(name):
        try:
            hk = util.read_text(hostkey_path(fleet, name)).split()
        except OSError:
            waits.append(name)
            return False
        line = "%s %s %s" % (fleet.hosts[name].hostname, hk[0], hk[1])
        if line not in known:
            known.append(line)
        return True
    for (machine, part), (standby, wit) in sorted(every.items()):
        if host.name == standby:
            if known_line(machine) and known_line(wit):
                vote.append({"part": part, "machine": machine, "address": fleet.hosts[machine].hostname, "role": "standby",
                             "switch": switch_on, "witness": fleet.hosts[wit].hostname, "containers": None})
        elif host.name == wit:
            if known_line(machine):
                vote.append({"part": part, "machine": machine, "address": fleet.hosts[machine].hostname, "role": "witness"})
            line = authorized_line(util.read_text(key_path(fleet, standby) + ".pub"), "%s ask" % VOTE_PATH)
            if line not in authorized:          # the standby asks this witness
                authorized.append(line)
    return {"parts": mine, "voters": voters, "vote": vote, "known": known, "authorized": authorized, "waits": sorted(set(waits))}


def install_script(host, p):
    return INSTALL % {
        "dir": DIR, "length": LENGTH, "margin": MARGIN, "fresh": COPY_FRESH, "me": shlex.quote(host.name),
        "parts": holder_conf(p["parts"]), "voters": voters_conf(p["voters"]),
        "part_names": " ".join(sorted(p["parts"])),
        "vote": vote_conf(p["vote"]), "known": "".join(x + "\n" for x in p["known"]),
        "lease_path": LEASE_PATH, "vote_path": VOTE_PATH, "holder": HOLDER, "voter": VOTER,
        "mark": MARK, "authorized": "".join(x + "\n" for x in p["authorized"]), "authorized_any": "1" if p["authorized"] else "",
        "sshd": "/etc/ssh/sshd_config.d/00-kiwi-standby.conf",
        "unit": UNIT, "vote_unit": VOTE_UNIT, "tick_service": TICK_SERVICE, "tick_timer": TICK_TIMER,
        "vote_service": VOTE_SERVICE, "vote_timer": VOTE_TIMER}


def after_apply(fleet, host, tgt, roles, render, switch_on, runner=None):
    """This machine's side of the permissions, as the fleet says: it holds them for its parts that
    have a standby, it gives them (standby, witness), or neither. `render(host)` gives (script, bundle).
    Returns the parts it holds a permission for (the fleet remembers them: the watchdog leaves
    those to their voters)."""
    run = runner or remote.run
    try:
        bundle = render(host)[1]
        p = plan(fleet, roles, host, getattr(bundle, "leased_parts", None) or {}, switch_on)
        for v in p["vote"]:
            if v["role"] == "standby":          # what this machine starts when it takes the part: its ready containers
                v["containers"] = (getattr(bundle, "module_services", None) or {}).get(v["part"]) or []
        if not p["parts"] and not p["vote"] and not p["authorized"]:
            run(tgt, REMOVE)
            return []
        key = util.read_text(key_path(fleet, host.name)) if p["vote"] else ""
        said = run(tgt, install_script(host, p), key.encode()).decode(errors="replace").strip().splitlines()
        if said and said[-1].startswith("ssh-"):
            os.makedirs(os.path.dirname(hostkey_path(fleet, host.name)), mode=0o700, exist_ok=True)
            util.write_text(hostkey_path(fleet, host.name), said[-1] + "\n", 0o644)
        if p["waits"]:
            util.warn("%s: it votes once %s %s been applied (their host keys are not on record yet)"
                      % (host.name, ", ".join(p["waits"]), "has" if len(p["waits"]) == 1 else "have"))
        if p["parts"]:
            util.say("%s: %s run%s by a permission from %s" % (host.name, ", ".join(sorted(p["parts"])),
                                                              "s" if len(p["parts"]) == 1 else "",
                                                              " and ".join(sorted({v for vs in p["voters"].values() for v in vs}))))
        if p["vote"]:
            util.say("%s: gives the permission for %s" % (host.name, ", ".join("%s of %s" % (v["part"], v["machine"]) for v in p["vote"])))
        return sorted(p["parts"])
    except (KiwiError, OSError, subprocess.CalledProcessError) as e:
        util.warn("%s: its permissions were not set up: %s" % (host.name, e))
        return None


def refuse(tgt, part, machine, runner=None):
    """On a voter: give `machine` no more permission for `part` (a switch moved it away)."""
    run = runner or remote.run
    run(tgt, "[ -d %s ] && mkdir -p %s/state && date +%%s > %s/state/%s@%s.refused || true"
        % (DIR, DIR, DIR, shlex.quote(part), shlex.quote(machine)))


def drop(tgt, part, runner=None):
    """On the machine a part leaves: its permission for it goes, so no boot starts it there again."""
    run = runner or remote.run
    run(tgt, "[ -f %s/parts ] && sed -i '/^%s /d' %s/parts; rm -f %s/%s.until %s/%s.fresh; true"
        % (DIR, part, DIR, DIR, part, DIR, part))


TOOK = "for f in %s/state/*.took; do [ -e \"$f\" ] || continue; b=${f##*/}; echo \"${b%%.took} $(cat \"$f\")\"; done" % DIR


def took(text):
    """{part: (when, machine)} from what TOOK prints on a standby: the parts it took by itself."""
    out = {}
    for line in (text or "").splitlines():
        p = line.split()
        if len(p) >= 2 and p[1].isdigit():
            out[p[0]] = (int(p[1]), p[2] if len(p) > 2 else "")
    return out
