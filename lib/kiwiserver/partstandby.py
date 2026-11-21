"""A standby for a part (docs/design/scale.md, "Standby"): another machine keeps the part's data
current, minute by minute, and can take the part over.

    hosts:
      quince:
        keep_ready:
          vault: medlar        # medlar keeps quince's vault ready

- The standby machine fetches the part's folders from the machine that runs it, every minute,
  with rsync over SSH: a key of its own that may do nothing on that machine but read those
  folders (a forced command; a folder a database lives in is read with its containers paused,
  for as long as the read takes).
- The folders land where the part would look for them on the standby machine. The part does
  not run there: it is not in that machine's list.
- `kiwi-server switch FLEET PART` gives the part to its standby: the fleet file moves it (as a
  move does: its secrets pinned, its name kept), the standby is applied and starts it with the
  data it has, the machines that answer for names learn the new place. The machine it left
  gets the change the moment it answers again (an apply that waits), and then no longer runs it.

Folders and named volumes are both followed. A folder lies at the same place on both machines;
a volume lies where each machine's container runtime keeps it, so each machine looks its own up
(the standby makes the volume if it is not there yet).
"""
import json
import os
import re
import subprocess

from . import remote, util
from .util import KiwiError

DIR = "/etc/kiwi-server/standby"
SEND_PATH = "/usr/local/sbin/kiwi-standby-send"
PULL_PATH = "/usr/local/sbin/kiwi-standby-pull"
UNIT = "kiwi-standby"
MARK = "kiwi-standby"       # the comment of its lines in root's authorized_keys
SSHD = "/etc/ssh/sshd_config.d/00-kiwi-standby.conf"

SEND = r'''#!/bin/bash
# kiwi-server: gives a part's data to the machine that keeps it ready (partstandby.py). This is the
# forced command of that machine's key: with it the key can read the folders in send.conf with
# rsync, one at a time, and do nothing else here. A folder marked "frozen" (a database lives in
# it) is read with its containers paused.
set -f
conf=/etc/kiwi-server/standby/send.conf
cmd=${SSH_ORIGINAL_COMMAND:-}
# what rsync asks its other side for when it only reads: its flags, and of the long options none but
# these two (--remove-source-files, --files-from and the like are refused with the rest)
if ! [[ $cmd =~ ^rsync\ --server\ --sender\ -[A-Za-z0-9.]+((\ --numeric-ids|\ --timeout=[0-9]+)*)\ \.\ (/[A-Za-z0-9._/-]+/)$ ]] \
        || [[ $cmd == *..* ]]; then
    logger -t kiwi-standby "refused: $cmd" 2>/dev/null
    echo "kiwi-standby: only rsync, reading one of the folders kept ready" >&2
    exit 1
fi
path=${BASH_REMATCH[3]}
rt='' kind='' containers=''
while read -r a b c; do
    if [[ $a == runtime ]]; then rt=$b; continue; fi
    if [[ $a/ == "$path" ]]; then kind=$b; containers=$c; fi
done < "$conf"
[[ -n $kind ]] || { echo "kiwi-standby: $path is not a folder kept ready" >&2; exit 1; }
paused=''
thaw() { local x; for x in $paused; do "$rt" unpause "$x" >/dev/null 2>&1; done; }
trap thaw EXIT
if [[ $kind == frozen && -n $rt ]]; then
    for x in $containers; do "$rt" pause "$x" >/dev/null 2>&1 && paused="$paused $x"; done
fi
$cmd
'''

PULL = r'''#!/bin/bash
# kiwi-server: keeps parts of other machines ready here (partstandby.py): every minute their
# folders are fetched from the machine that runs them, to where the part would look for them
# here. The part does not run here. pull.conf: PART MACHINE FOLDER [FOLDER-HERE], one line per folder.
d=/etc/kiwi-server/standby
exec 9>/run/kiwi-standby.lock; flock -n 9 || exit 0
mkdir -p "$d/state"
declare -A bad=() seen=()
while read -r part from dir here; do
    [[ -n $dir ]] || continue
    # taken over here by its permission (lease.py): the folder is the part now, nothing is fetched over it
    [[ -e $d/state/$part.taken ]] && continue
    here=${here:-$dir}                  # a volume lies elsewhere on each machine; a folder at the same place
    seen[$part]=1
    # The first fetch of a part over a folder that holds data already (this machine ran the part
    # before a switch, and may have written after the copy it now fetches was made): what it holds
    # is kept beside it first, linked, not copied, for 14 days. --delete would take it for good.
    # Once per first fetch (state/PART.keeping, gone with the first whole fetch); a folder that
    # could not be kept is not fetched over.
    find "$(dirname "$here")" -maxdepth 1 -name "$(basename "$here").kept-*" -mtime +14 -exec rm -rf {} + 2>/dev/null
    if [[ ! -e $d/state/$part.ok && -d $here && -n $(ls -A "$here" 2>/dev/null) ]] && ! grep -qxF "$here" "$d/state/$part.keeping" 2>/dev/null; then
        k="$here.kept-$(date +%Y%m%d-%H%M%S)"
        # (touched: cp -a keeps the folder's own old time, which the clean-up above goes by)
        if cp -al "$here" "$k" && touch "$k"; then
            echo "$here" >> "$d/state/$part.keeping"
            logger -t kiwi-standby "$part: what $here held is kept in $k"
        else
            rm -rf "$k"; bad[$part]=1
            logger -t kiwi-standby "$part: what $here holds could not be kept beside it: it is not fetched over"
            continue
        fi
    fi
    mkdir -p "$here"
    if ! rsync -a --delete --numeric-ids --timeout=120 \
        -e "ssh -i $d/key -o UserKnownHostsFile=$d/known_hosts -o StrictHostKeyChecking=yes -o BatchMode=yes -o ConnectTimeout=10 -o IdentitiesOnly=yes" \
        "root@$from:$dir/" "$here/" 2>"$d/state/$part.err"; then
        bad[$part]=1
        why=$(tail -1 "$d/state/$part.err")
        [[ $why == "$(cat "$d/state/$part.said" 2>/dev/null)" ]] || { logger -t kiwi-standby "$part from $from: $why"; echo "$why" > "$d/state/$part.said"; }
    fi
done < "$d/pull.conf"
for part in "${!seen[@]}"; do
    [[ -n ${bad[$part]:-} ]] || { date +%s > "$d/state/$part.ok"; rm -f "$d/state/$part.said" "$d/state/$part.keeping"; }
done
'''

SERVICE = ("[Unit]\nDescription=kiwi-server: fetch the data of the parts this machine keeps ready\nAfter=network-online.target\n\n"
           "[Service]\nType=oneshot\nExecStart=%s\n" % PULL_PATH)
TIMER = ("[Unit]\nDescription=kiwi-server: every minute, the data of the parts this machine keeps ready\n\n[Timer]\n"
         "OnBootSec=1min\nOnUnitActiveSec=1min\nAccuracySec=5s\n\n[Install]\nWantedBy=timers.target\n")

# rsync on both machines: Debian does not bring it
NEED_RSYNC = ('command -v rsync >/dev/null 2>&1 || { command -v apt-get >/dev/null 2>&1 && '
              'DEBIAN_FRONTEND=noninteractive apt-get install -y -q rsync >/dev/null 2>&1; }\n'
              'command -v rsync >/dev/null 2>&1 || { echo "rsync is not installed here, and could not be" >&2; exit 1; }\n')

# the machine that runs the part: the sender, its folders, the standby's key (stdin: the public key lines)
INSTALL_SEND = r'''set -eu
''' + NEED_RSYNC + r'''install -d -m 0700 %(dir)s /root/.ssh
cat > %(dir)s/send.conf.in <<'KIWI_EOF'
%(conf)sKIWI_EOF
# a named volume lies where this machine's container runtime keeps it: looked up here, written
# into send.conf as a folder, and told back (the standby must ask for that folder)
rt=$(sed -n 's/^runtime //p' %(dir)s/send.conf.in)
: > %(dir)s/send.conf.new
while read -r what rest; do
    case "$what" in
        vol:*) at=$("$rt" volume inspect -f '{{.Mountpoint}}' "${what#vol:}" 2>/dev/null) || at=''
               [ -n "$at" ] || { echo "kiwi-standby: no volume ${what#vol:} here" >&2; continue; }
               echo "$at $rest" >> %(dir)s/send.conf.new
               echo "kiwi-volume ${what#vol:} $at" ;;
        *)     echo "$what $rest" >> %(dir)s/send.conf.new ;;
    esac
done < %(dir)s/send.conf.in
mv %(dir)s/send.conf.new %(dir)s/send.conf; rm -f %(dir)s/send.conf.in
cat > %(send)s.new <<'KIWI_EOF'
%(script)sKIWI_EOF
chmod 0755 %(send)s.new && mv %(send)s.new %(send)s
touch /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys
{ grep -v ' %(mark)s$' /root/.ssh/authorized_keys || true; cat; } > /root/.ssh/authorized_keys.new
chmod 600 /root/.ssh/authorized_keys.new && mv /root/.ssh/authorized_keys.new /root/.ssh/authorized_keys
command -v restorecon >/dev/null 2>&1 && restorecon -R /root/.ssh >/dev/null 2>&1 || true
if [ -d /etc/ssh/sshd_config.d ]; then
    printf '# kiwi-server: root may sign in only with a key that has a forced command (the standby link)\nPermitRootLogin forced-commands-only\n' > %(sshd)s
    systemctl reload sshd 2>/dev/null || systemctl reload ssh 2>/dev/null || true
fi
cat /etc/ssh/ssh_host_ed25519_key.pub
'''
REMOVE_SEND = ("[ -e %s ] || exit 0; rm -f %s %s/send.conf %s; "
               "[ -f /root/.ssh/authorized_keys ] && { grep -v ' %s$' /root/.ssh/authorized_keys > /root/.ssh/authorized_keys.new || true; "
               "chmod 600 /root/.ssh/authorized_keys.new; mv /root/.ssh/authorized_keys.new /root/.ssh/authorized_keys; }; "
               "systemctl reload sshd 2>/dev/null || systemctl reload ssh 2>/dev/null || true"
               % (SEND_PATH, SEND_PATH, DIR, SSHD, MARK))

# the standby machine: its key (stdin), whose host keys to trust, what to fetch, the timer
INSTALL_PULL = r'''set -eu
''' + NEED_RSYNC + r'''install -d -m 0700 %(dir)s %(dir)s/state
# a part this machine took by itself is written down by now (an apply waits for that: lease.unrecorded_takes)
rm -f %(dir)s/state/*.taken
key=$(cat)
umask 077
printf '%%s\n' "$key" > %(dir)s/key.new && mv %(dir)s/key.new %(dir)s/key
cat > %(dir)s/known_hosts <<'KIWI_EOF'
%(known)sKIWI_EOF
cat > %(dir)s/pull.conf.in <<'KIWI_EOF'
%(conf)sKIWI_EOF
# a named volume: made here if it is not there yet, and fetched into where this machine keeps it
rt=$(command -v %(runtime)s >/dev/null 2>&1 && echo %(runtime)s || { command -v podman >/dev/null 2>&1 && echo podman || echo docker; })
: > %(dir)s/pull.conf.new
while read -r part from src here; do
    case "$here" in
        vol:*) "$rt" volume inspect "${here#vol:}" >/dev/null 2>&1 || "$rt" volume create "${here#vol:}" >/dev/null
               here=$("$rt" volume inspect -f '{{.Mountpoint}}' "${here#vol:}") ;;
    esac
    echo "$part $from $src${here:+ $here}" >> %(dir)s/pull.conf.new
done < %(dir)s/pull.conf.in
mv %(dir)s/pull.conf.new %(dir)s/pull.conf; rm -f %(dir)s/pull.conf.in
umask 022
cat > %(pull)s.new <<'KIWI_EOF'
%(script)sKIWI_EOF
chmod 0755 %(pull)s.new && mv %(pull)s.new %(pull)s
cat > /etc/systemd/system/%(unit)s.service <<'KIWI_EOF'
%(service)sKIWI_EOF
cat > /etc/systemd/system/%(unit)s.timer <<'KIWI_EOF'
%(timer)sKIWI_EOF
systemctl daemon-reload
systemctl enable -q %(unit)s.timer
systemctl restart %(unit)s.timer
systemctl start --no-block %(unit)s.service
'''
REMOVE_PULL = ("[ -e %s ] || exit 0; systemctl disable --now -q %s.timer 2>/dev/null || true; rm -rf %s %s/pull.conf %s/key %s/known_hosts "
               "/etc/systemd/system/%s.service /etc/systemd/system/%s.timer; systemctl daemon-reload"
               % (PULL_PATH, UNIT, PULL_PATH, DIR, DIR, DIR, UNIT, UNIT))


def kept(host):
    """{part: the machine that keeps it ready} for the parts of this machine that have a standby."""
    k = host.cfg.get("keep_ready") or {}
    return {str(p): str(m) for p, m in k.items()} if isinstance(k, dict) else {}


def links(fleet):
    """[(machine that runs it, part, standby machine)] of the whole fleet."""
    return [(h.name, p, m) for h in fleet.hosts.values() for p, m in sorted(kept(h).items())]


def _listed(host):
    return [str(x) for x in (host.role_settings_raw or {}).get("modules") or []]


def errors(fleet, roles):
    """What is wrong with the fleet's keep_ready blocks."""
    errs = []
    for h in fleet.hosts.values():
        k = h.cfg.get("keep_ready")
        if k in (None, {}):
            continue
        if not isinstance(k, dict):
            errs.append("%s: keep_ready: a mapping of part to machine, like { vault: medlar }" % h.name)
            continue
        for part, m in kept(h).items():
            where = "%s: keep_ready.%s" % (h.name, part)
            other = fleet.hosts.get(m)
            if part not in _listed(h):
                errs.append("%s: %s is not among %s's parts" % (where, part, h.name))
            if other is None:
                errs.append("%s: there is no machine %s" % (where, m))
                continue
            if other is h:
                errs.append("%s: a machine cannot stand by for itself" % where)
            if part in _listed(other):
                errs.append("%s: %s runs %s itself" % (where, m, part))
            if h.role in roles and other.role in roles and roles[h.role].node_type != roles[other.role].node_type:
                errs.append("%s: %s is a %s and %s a %s: their parts keep their data under different names"
                            % (where, h.name, roles[h.role].node_type, m, roles[other.role].node_type))
            if str((h.role_settings_raw or {}).get("docker_dir") or "") != str((other.role_settings_raw or {}).get("docker_dir") or ""):
                errs.append("%s: the two machines keep their stacks in different folders (docker_dir)" % where)
    return errs


def folders(bundle, part):
    """What the part keeps where, read from the machine's rendered backup script (the backup part
    knows every part's data): {"runtime", "frozen": [folders], "bulk": [folders], "containers": [names],
    "volumes": [names]}. None when the machine has no backup part or the part keeps nothing."""
    files = getattr(bundle, "files", None) or {}
    entry = files.get("kiwi/backup.sh")
    if not entry:
        return None
    text = entry[0] if isinstance(entry, (tuple, list)) else entry
    text = text.decode(errors="replace") if isinstance(text, bytes) else text

    def value(name):
        m = re.search(r'^%s\[%s\]="([^"]*)"$' % (name, re.escape(part)), text, re.M)
        return m.group(1).split() if m else None
    frozen, bulk = value("FROZEN"), value("BULK")
    if frozen is None and bulk is None:
        return None
    out = {"frozen": [], "bulk": [], "volumes": [], "frozen_volumes": [], "bulk_volumes": [], "containers": value("FREEZE") or []}
    for kind, mounts in (("frozen", frozen or []), ("bulk", bulk or [])):
        for m in mounts:
            src = m.split(":", 1)[0]
            if src.startswith("/"):
                out[kind].append(src.rstrip("/"))
            else:                           # a named volume: where it lies is the machine's own business
                out["volumes"].append(src)
                out[kind + "_volumes"].append(src)
    rt = re.search(r"\b(podman|docker) run --rm --network host", text)
    out["runtime"] = rt.group(1) if rt else ""
    return out


def key_path(fleet, standby):
    """The standby machine's key for fetching: one per standby machine, made on first use."""
    path = fleet.resolve_path("secrets/standby/%s_ed25519" % standby)
    if not os.path.isfile(path):
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "kiwi-standby-%s" % standby, "-f", path], check=True)
    return path


def _hostkey_path(fleet, name):
    return fleet.resolve_path("secrets/standby/%s.hostkey" % name)


def _volumes_path(fleet, name):
    return fleet.resolve_path("secrets/standby/%s.volumes" % name)


def _runtime_of(bundle):
    """podman or docker, as the machine's rendered backup script runs its containers; "" unknown."""
    entry = (getattr(bundle, "files", None) or {}).get("kiwi/backup.sh")
    if not entry:
        return ""
    text = entry[0] if isinstance(entry, (tuple, list)) else entry
    text = text.decode(errors="replace") if isinstance(text, bytes) else text
    rt = re.search(r"\b(podman|docker) run --rm --network host", text)
    return rt.group(1) if rt else ""


def authorized_line(public):
    """The standby's key on the machine that runs the part: the sender and nothing else."""
    kind, blob = public.split()[:2]
    return 'command="%s",restrict %s %s %s' % (SEND_PATH, kind, blob, MARK)


def send_conf(found_by_part):
    """send.conf: `runtime R`, then FOLDER KIND CONTAINERS... per folder."""
    lines, runtime = [], ""
    for part in sorted(found_by_part):
        f = found_by_part[part]
        runtime = runtime or f["runtime"]
        lines += ["%s frozen %s" % (d, " ".join(f["containers"])) for d in f["frozen"]]
        lines += ["%s bulk" % d for d in f["bulk"]]
        lines += ["vol:%s frozen %s" % (v, " ".join(f["containers"])) for v in f.get("frozen_volumes") or []]
        lines += ["vol:%s bulk" % v for v in f.get("bulk_volumes") or []]
    return "".join(x + "\n" for x in (["runtime %s" % runtime] if runtime else []) + lines)


def after_apply(fleet, host, tgt, roles, ms, render, runner=None):
    """A machine's part in the standby links as the fleet says: it gives the folders of its parts
    that another machine keeps ready, it fetches those of the parts it keeps ready, or neither.
    `render(host)` gives (script, bundle) as an apply renders that machine."""
    run = runner or remote.run
    try:
        # the parts of this machine another one keeps ready
        mine = kept(host)
        if mine:
            found, publics = {}, []
            for part, m in mine.items():
                f = folders(render(host)[1], part)
                if f is None:
                    util.warn("%s: %s is not kept ready on %s: its data comes over through what the backup part knows of it, "
                              "and %s has no backup part or the part keeps no data" % (host.name, part, m, host.name))
                    continue
                found[part] = f
                pub = util.read_text(key_path(fleet, m) + ".pub").strip()
                if authorized_line(pub) not in publics:
                    publics.append(authorized_line(pub))
            if found:
                script = INSTALL_SEND % {"dir": DIR, "conf": send_conf(found), "send": SEND_PATH, "script": SEND, "mark": MARK, "sshd": SSHD}
                said = run(tgt, script, ("".join(p + "\n" for p in publics)).encode()).decode(errors="replace").strip().splitlines()
                hostkey = said[-1]
                os.makedirs(os.path.dirname(_hostkey_path(fleet, host.name)), mode=0o700, exist_ok=True)
                util.write_text(_hostkey_path(fleet, host.name), hostkey + "\n", 0o644)
                # where this machine keeps its volumes: the standby asks for those folders
                where = {x.split()[1]: x.split()[2] for x in said if x.startswith("kiwi-volume ") and len(x.split()) == 3}
                util.write_text(_volumes_path(fleet, host.name), json.dumps(where, sort_keys=True) + "\n", 0o644)
                util.say("%s: %s kept ready on %s" % (host.name, ", ".join(sorted(found)),
                                                     ", ".join(sorted({mine[p] for p in found}))))
            else:
                run(tgt, REMOVE_SEND)
        else:
            run(tgt, REMOVE_SEND)
        # the parts of other machines this one keeps ready
        theirs = [(a, p) for a, p, m in links(fleet) if m == host.name]
        if not theirs:
            run(tgt, REMOVE_PULL)
            return
        conf, known, waits = [], [], []
        for a, part in theirs:
            primary = fleet.hosts[a]
            f = folders(render(primary)[1], part)
            if f is None:
                continue
            try:
                hk = util.read_text(_hostkey_path(fleet, a)).split()
                there = json.loads(util.read_text(_volumes_path(fleet, a))) if f["volumes"] else {}
            except (OSError, ValueError):
                waits.append(a)
                continue
            if any(v not in there for v in f["volumes"]):       # that machine has not said where they lie yet
                waits.append(a)
                continue
            line = "%s %s %s" % (primary.hostname, hk[0], hk[1])
            if line not in known:
                known.append(line)
            conf += ["%s %s %s" % (part, primary.hostname, d) for d in f["frozen"] + f["bulk"]]
            conf += ["%s %s %s vol:%s" % (part, primary.hostname, there[v], v) for v in f["volumes"]]
        if waits:
            util.warn("%s: it fetches from %s after that machine's next apply (its host key is not on record yet)"
                      % (host.name, ", ".join(sorted(set(waits)))))
        if not conf:
            run(tgt, REMOVE_PULL)
            return
        mine_rt = _runtime_of(render(host)[1]) or "podman"
        script = INSTALL_PULL % {"dir": DIR, "known": "".join(x + "\n" for x in known), "conf": "".join(x + "\n" for x in conf),
                                 "pull": PULL_PATH, "script": PULL, "unit": UNIT, "service": SERVICE, "timer": TIMER,
                                 "runtime": mine_rt}
        run(tgt, script, util.read_text(key_path(fleet, host.name)).encode())
        util.say("%s: keeps ready: %s" % (host.name, ", ".join("%s of %s" % (p, a) for a, p in sorted(theirs))))
    except (KiwiError, OSError, subprocess.CalledProcessError) as e:
        util.warn("%s: its standby link was not set up: %s" % (host.name, e))


AGE = "for f in %s/state/*.ok; do [ -e \"$f\" ] || continue; b=${f##*/}; echo \"${b%%.ok} $(( $(date +%%s) - $(cat \"$f\") ))\"; done" % DIR


def ages(text):
    """{part: seconds since its data was last fetched whole} from what AGE prints on a standby machine."""
    out = {}
    for line in text.splitlines():
        p = line.split()
        if len(p) == 2 and p[1].lstrip("-").isdigit():
            out[p[0]] = int(p[1])
    return out
