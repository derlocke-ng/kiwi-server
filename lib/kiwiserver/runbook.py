"""The runbook (docs/design/runbook.md): what happens in which case, as the admins wrote it
beforehand in the fleet file. Fixed lists of cases and actions, no rule language; the first line
that fits a machine (and a part) wins; a case no line covers is told, and nothing is done.

    defaults:
      runbook:
        - { case: part-down, for: "role:node", do: restart, after: 2, tries: 3 }
        - { case: machine-gone, for: sorbet, do: restart, after: 15 }

Step 1 of the design: the table, checked, and its sentences per machine. Nothing acts by it yet."""
from .util import KiwiError

# case: (who sees it, its actions, what it is in words)
CASES = {
    "part-down": ("machine", ("restart", "tell"), "a part is down"),
    # old snapshots are pruned from where the keys are (kiwi-server prune): a machine cannot delete its backups
    "disk-full": ("machine", ("prune-images", "tell"), "its disk fills up"),
    "update-failed": ("machine", ("roll-back", "tell"), "an update fails"),
    "cut-off": ("machine", ("keep-running", "stop", "tell"), "it is cut off from the network"),
    "machine-gone": ("control", ("restart", "tell"), "it does not answer"),
    "backup-overdue": ("control", ("backup-now", "tell"), "its backup is overdue"),
    "certificate-due": ("control", ("apply", "tell"), "a certificate runs out"),
}
KEYS = ("case", "for", "do", "after", "tries", "at")
DOING = {"restart": "restarted", "tell": "you are told", "prune-images": "images no container uses are removed",
         "roll-back": "it goes back to the image before",
         "keep-running": "it keeps running", "stop": "its parts with a permission stop",
         "backup-now": "a backup starts", "apply": "it is applied"}


def lines(cfg, where="runbook"):
    """The lines of a machine's (or the defaults') `runbook`, checked: [dict]."""
    raw = (cfg or {}).get("runbook") or []
    if not isinstance(raw, list):
        raise KiwiError("%s: a list of lines, each with case, for and do" % where)
    out = []
    for i, line in enumerate(raw):
        at = "%s[%d]" % (where, i)
        if not isinstance(line, dict):
            raise KiwiError("%s: a line is a map with case, for and do" % at)
        bad = [k for k in line if k not in KEYS]
        if bad:
            raise KiwiError("%s: unknown key %s (%s)" % (at, ", ".join(map(str, bad)), ", ".join(KEYS)))
        case = line.get("case")
        if case not in CASES:
            raise KiwiError("%s: case is one of %s" % (at, ", ".join(CASES)))
        do = line.get("do")
        if do not in CASES[case][1]:
            raise KiwiError("%s: for %s, do is one of %s" % (at, case, ", ".join(CASES[case][1])))
        who = line.get("for", "all")
        if not isinstance(who, str) or not who.strip():
            raise KiwiError("%s: for is all, a machine's name, role:NAME, site:NAME or part:NAME" % at)
        if who.partition(":")[0] not in ("role", "site", "part") and ":" in who:
            raise KiwiError("%s: for is all, a machine's name, role:NAME, site:NAME or part:NAME" % at)
        for k in ("after", "tries", "at"):
            if k in line and (not isinstance(line[k], int) or isinstance(line[k], bool) or line[k] < 0):
                raise KiwiError("%s: %s is a whole number" % (at, k))
        if "at" in line and (case != "disk-full" or line["at"] > 100):
            raise KiwiError("%s: at is for disk-full, a percentage of the disk" % at)
        out.append(dict(line, **{"for": who.strip()}))
    return out


def fits(line, host, part=None):
    """Does `line` cover `host` (and `part`, for a case about a part)?"""
    who = line["for"]
    kind, _sep, name = who.partition(":")
    if who == "all":
        return True
    if kind == "role":
        return host.role == name
    if kind == "site":
        return str(((host.cfg or {}).get("network") or {}).get("site") or "home") == name
    if kind == "part":
        return part == name if part is not None else name in (host.modules or [])
    return host.name == who


def for_machine(host):
    """{case: line or None} for `host`: the first line that fits it, for each case; its own lines
    (hosts.NAME.runbook) before the defaults'. A line `for: part:NAME` fits a machine that runs that part."""
    mine = (lines(host.overrides or {}, "hosts.%s.runbook" % host.name)
            + lines(host.fleet.defaults or {}, "defaults.runbook"))
    return {case: next((l for l in mine if l["case"] == case and fits(l, host)), None) for case in CASES}


def sentence(case, line):
    """What happens in this case, in words."""
    what = CASES[case][2]
    if line is None:
        return "When %s: you are told; nothing is done." % what
    do = DOING[line["do"]]
    parts = [do]
    if line.get("after"):
        parts[0] = "%s after %d minute%s" % (do, line["after"], "" if line["after"] == 1 else "s")
    if line.get("at") is not None:
        parts[0] += " at %d %%" % line["at"]
    if line.get("tries") and line["do"] != "tell":
        parts.append("at most %d time%s, then you are told" % (line["tries"], "" if line["tries"] == 1 else "s"))
    return "When %s: %s." % (what, ", ".join(parts))


def errors(fleet):
    """What is wrong in the runbook lines of the defaults and of every machine."""
    errs = []
    try:
        lines(fleet.defaults or {}, "defaults.runbook")
    except KiwiError as e:
        errs.append(str(e))
    for h in fleet.hosts.values():
        own = (h.overrides or {}).get("runbook")
        if own is None:
            continue
        try:
            lines({"runbook": own}, "hosts.%s.runbook" % h.name)
        except KiwiError as e:
            errs.append(str(e))
    return errs


# ---- step 2: the machine acts on its own lines (part-down) -----------------------------------

DIR = "/etc/kiwi-server/runbook"
SCRIPT_PATH = "/usr/local/sbin/kiwi-runbook"
UNIT = "kiwi-runbook"
ON_MACHINE = ("part-down", "disk-full")     # the cases the machine acts on itself, so far
PER_PART = ("part-down",)                    # of these, the ones about a part (a line per container)

SCRIPT = r'''#!/bin/bash
# kiwi-server: this machine's runbook (docs/design/runbook.md). Every minute it does what the admins
# wrote beforehand for the cases this machine sees on itself, and nothing else. A container that a
# person stopped (kiwi-server service … stop) or that stopped cleanly is not down: only one that
# failed, exited with an error or is unhealthy. Each line of the conf: CONTAINER CASE DO AFTER TRIES
# (disk-full: - disk-full DO AT 0).
d=${KIWI_RUNBOOK_DIR:-/etc/kiwi-server/runbook}; run=${KIWI_RUNBOOK_RUN:-/run/kiwi-server/runbook}
keep=${KIWI_RUNBOOK_KEEP:-/var/lib/kiwi-server/runbook}; held=${KIWI_STOPPED_DIR:-/run/kiwi-server/stopped}
[ -s "$d/conf" ] || exit 0
mkdir -p "$run" "$keep" || exit 0
exec 9>"$run/.lock"; flock -n 9 || exit 0
now=$(date +%s)
running() {
    if systemctl cat "$1.service" >/dev/null 2>&1; then systemctl is-active -q "$1.service"
    else [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = true ]; fi
}
down() {    # CONTAINER: is it down? (0: yes)
    local c=$1 s code h
    if [ -e "$held/$c" ]; then                          # a person stopped it: it stays stopped,
        running "$c" || return 1                        # until it runs again (started, or an apply)
        rm -f "$held/$c"
    fi
    if systemctl cat "$c.service" >/dev/null 2>&1; then
        systemctl is-failed -q "$c.service" && return 0
        systemctl is-active -q "$c.service" || return 1  # stopped, asleep: not down
        h=$(podman inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$c" 2>/dev/null)
    elif command -v docker >/dev/null 2>&1 && docker inspect "$c" >/dev/null 2>&1; then
        read -r s code h < <(docker inspect -f '{{.State.Status}} {{.State.ExitCode}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$c" 2>/dev/null)
        case $s in
            restarting) return 0 ;;
            exited|dead) case ${code:-0} in 0|137|143) return 1 ;; *) return 0 ;; esac ;;
        esac
    else
        return 1                                        # not on this machine (any more)
    fi
    [ "$h" = unhealthy ]
}
say() { logger -t kiwi-runbook "$*" 2>/dev/null; echo "$now $*" >> "$keep/log"; }
disk_full() {   # AT: the disk of the images at AT % or more: images no container uses, a day old, go
    local at=$1 where pct now_pct rt
    if command -v podman >/dev/null 2>&1; then rt=podman; where=/var/lib/containers/storage
    else rt=docker; where=/var/lib/docker; fi
    [ -d "$where" ] || where=/
    pct=$(df --output=pcent "$where" 2>/dev/null | tail -1 | tr -dc 0-9)
    [ -n "$pct" ] && (( pct >= at )) || { rm -f "$run/disk.told"; return 0; }
    if [ "$act" != prune-images ]; then
        [ -e "$run/disk.told" ] || { say "the disk is at $pct %: left to a person"; touch "$run/disk.told"; }
        return 0
    fi
    last=$(cat "$keep/disk.pruned" 2>/dev/null || echo 0)
    (( now - last >= 3600 )) || return 0            # once an hour at most
    "$rt" image prune -af --filter until=24h >/dev/null 2>&1
    echo "$now" > "$keep/disk.pruned"
    now_pct=$(df --output=pcent "$where" 2>/dev/null | tail -1 | tr -dc 0-9)
    say "the disk was at $pct %: images no container uses removed, now at ${now_pct:-?} %"
    if [ -n "$now_pct" ] && (( now_pct >= at )) && [ ! -e "$run/disk.told" ]; then
        say "the disk is still at $now_pct %: left to a person"; touch "$run/disk.told"
    fi
}
while read -r c case act after tries; do
    if [ "$case" = disk-full ]; then disk_full "${after:-90}"; continue; fi
    [ "$case" = part-down ] || continue
    case $c in *[!a-z0-9_.-]*|"") continue ;; esac
    if ! down "$c"; then
        [ -e "$run/$c.since" ] && [ -e "$run/$c.acted" ] && say "$c runs again"
        rm -f "$run/$c.since" "$run/$c.acted" "$run/$c.told"
        continue
    fi
    [ -e "$run/$c.since" ] || echo "$now" > "$run/$c.since"
    since=$(cat "$run/$c.since")
    (( now - since >= ${after:-0} * 60 )) || continue
    n=$(awk -v t=$((now - 86400)) '$1 >= t' "$keep/$c.tries" 2>/dev/null | wc -l)
    if [ "$act" != restart ] || (( n >= ${tries:-3} )); then
        if [ ! -e "$run/$c.told" ]; then
            if [ "$act" = restart ]; then say "$c is down: restarted $n times today, left to a person"
            else say "$c is down: left to a person"; fi
            touch "$run/$c.told"
        fi
        continue
    fi
    if systemctl cat "$c.service" >/dev/null 2>&1; then systemctl restart "$c.service"; else docker restart "$c" >/dev/null; fi
    rc=$?
    echo "$now" >> "$keep/$c.tries"
    say "$c was down for $((now - since)) s: restarted (try $((n + 1)) of ${tries:-3} today$([ $rc = 0 ] || echo ", it failed: $rc"))"
    echo "$now" > "$run/$c.since"; touch "$run/$c.acted"
done < "$d/conf"
if [ -f "$keep/log" ] && [ "$(wc -l < "$keep/log")" -gt 400 ]; then
    tail -n 200 "$keep/log" > "$keep/log.new" && mv "$keep/log.new" "$keep/log"
fi
exit 0
'''

SERVICE = "[Unit]\nDescription=kiwi-server: this machine's runbook\n\n[Service]\nType=oneshot\nExecStart=%s\n" % SCRIPT_PATH
TIMER = ("[Unit]\nDescription=kiwi-server: this machine's runbook, every minute\n\n[Timer]\nOnBootSec=3min\n"
         "OnUnitActiveSec=1min\n\n[Install]\nWantedBy=timers.target\n")
INSTALL = r'''set -e
install -d -m 0755 %(dir)s
cat > %(dir)s/conf.new <<'KIWI_EOF'
%(conf)sKIWI_EOF
mv %(dir)s/conf.new %(dir)s/conf
cat > %(path)s.new <<'KIWI_EOF'
%(script)sKIWI_EOF
chmod 0755 %(path)s.new && mv %(path)s.new %(path)s
cat > /etc/systemd/system/%(unit)s.service <<'KIWI_EOF'
%(service)sKIWI_EOF
cat > /etc/systemd/system/%(unit)s.timer <<'KIWI_EOF'
%(timer)sKIWI_EOF
systemctl daemon-reload
systemctl enable -q --now %(unit)s.timer
'''
REMOVE = ("[ -e %s ] || exit 0; systemctl disable -q --now %s.timer 2>/dev/null || true; "
          "rm -rf %s %s /etc/systemd/system/%s.service /etc/systemd/system/%s.timer; systemctl daemon-reload"
          % (SCRIPT_PATH, UNIT, DIR, SCRIPT_PATH, UNIT, UNIT))


def machine_lines(host, service_modules):
    """[(container, case, do, after, tries)] this machine acts on itself: for each container of its
    stack, the first line that fits its part, per case the machine sees on itself."""
    mine = (lines(host.overrides or {}, "hosts.%s.runbook" % host.name)
            + lines(host.fleet.defaults or {}, "defaults.runbook"))
    out = []
    for c, part in sorted((service_modules or {}).items()):
        for case in PER_PART:
            line = next((l for l in mine if l["case"] == case and fits(l, host, part)), None)
            if line and line["do"] != "tell":
                out.append((c, case, line["do"], int(line.get("after") or 0), int(line.get("tries", 3))))
    for case in ON_MACHINE:
        if case in PER_PART:
            continue
        line = next((l for l in mine if l["case"] == case and fits(l, host)), None)
        if line and line["do"] != "tell":
            out.append(("-", case, line["do"], int(line.get("at", 90)), 0))
    return out


def conf(rows):
    return "".join("%s %s %s %d %d\n" % r for r in rows)


def after_apply(fleet, host, tgt, render, had=False, runner=None):
    """The machine's runbook: installed with its lines when it has any it acts on, taken away when it
    `had` some and has none now. `render(host)` gives (script, bundle). Returns its lines ([] for
    none), or None when it could not be set up."""
    from . import remote
    from . import util
    run = runner or remote.run
    try:
        if not had and not any(l["case"] in ON_MACHINE and l["do"] != "tell" for l in
                               lines(host.overrides or {}) + lines(host.fleet.defaults or {})):
            return []                   # nothing to act on, nothing set up before: not even a render
        rows = machine_lines(host, getattr(render(host)[1], "service_modules", None) or {})
        if not rows:
            if had:
                run(tgt, REMOVE)
            return []
        run(tgt, INSTALL % {"dir": DIR, "conf": conf(rows), "path": SCRIPT_PATH, "script": SCRIPT, "unit": UNIT,
                            "service": SERVICE, "timer": TIMER})
        util.say("%s: its runbook acts on %s" % (host.name, ", ".join(sorted({"%s (%s)" % ("its disk" if r[0] == "-" else r[0], r[2])
                                                                          for r in rows}))))
        return rows
    except (KiwiError, OSError) as e:
        util.warn("%s: its runbook was not set up: %s" % (host.name, e))
        return None
