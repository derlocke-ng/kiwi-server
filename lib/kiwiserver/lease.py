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

from . import util

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
        echo "$now" > "$d/state/$part.took"
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
    return 'command="%s",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty %s %s' % (
        command, " ".join(public.split()[:2]), MARK)


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
