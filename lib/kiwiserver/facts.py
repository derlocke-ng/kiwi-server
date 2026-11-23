"""What a running machine says about itself, as data.

One read-only shell script, plain sh, so it runs on Debian and on CoreOS alike.
It prints key=value lines and nothing that is a secret: the WireGuard peers come
from `wg show latest-handshakes`, public keys only, never `wg show dump`, which
carries the private one. `kiwi-server status --porcelain` parses this and says
what is wrong; the app shows that and decides nothing of its own.
"""
import re
import time
from . import util

MARK = "--- kiwi-facts ---"

# what `kiwi-server status` has always printed for a person
HUMAN = ("v=$(head -n1 /etc/kiwi-server/stack.env 2>/dev/null | sed -n 's/^# written by kiwi-server \\([^ ]*\\) .*/\\1/p'); "
         "echo \"applied: $(cat /var/lib/kiwi-server/role.done 2>/dev/null || echo never)${v:+ with kiwi-server $v}\"; "
         "if [ -f /var/lib/kiwi-server/role.failed ]; then echo 'ROLE FAILED:'; "
         "sed 's/^/  /' /var/lib/kiwi-server/role.failed; fi; "
         "echo \"uptime: $(uptime -p 2>/dev/null || uptime)\"; "
         "if [ -x /usr/local/bin/kiwi-stack ]; then kiwi-stack status 2>&1 || true; else echo 'no stack'; fi")

PROBE = r"""
kv() { printf '%s=%s\n' "$1" "$2"; }
kv now "$(date +%s)"
kv applied "$(cat /var/lib/kiwi-server/role.done 2>/dev/null)"
if [ -f /var/lib/kiwi-server/role.failed ]; then
    kv role_failed "$(head -n 3 /var/lib/kiwi-server/role.failed | tr '\n' ' ' | cut -c1-300)"
fi
(. /etc/os-release 2>/dev/null; kv os "${PRETTY_NAME:-}")
kv kernel "$(uname -r)"
kv uptime "$(cut -d. -f1 /proc/uptime)"
kv load "$(cut -d' ' -f1-3 /proc/loadavg)"
kv cpus "$(nproc 2>/dev/null)"
kv mem "$(awk '/^MemTotal:/{t=$2} /^MemAvailable:/{a=$2} END{print t, a}' /proc/meminfo)"
# /var, not /: a CoreOS root is a read-only image that is always 100% full
df -kP /var /var/mnt/data /mnt/data 2>/dev/null | awk 'NR>1{print "disk=" $6, $2, $3}'
kv clock "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)"
# A clock set back: WireGuard takes no handshake dated before the last one it took, so a tunnel
# stays down until the time has caught up. The moment the kernel says it started moves when the
# clock is set: compared with the last look's, kept in /run (gone at a restart).
bt=$(awk '/^btime /{print $2}' /proc/stat 2>/dev/null)
if [ -n "$bt" ] && mkdir -p /run/kiwi-server 2>/dev/null && [ -w /run/kiwi-server ]; then
    was=$(cat /run/kiwi-server/btime 2>/dev/null)
    # when: by the time the machine has been up, not by the clock that is in doubt
    if [ -n "$was" ] && [ $((was - bt)) -ge 5 ]; then echo "$(cut -d. -f1 /proc/uptime) $((was - bt))" > /run/kiwi-server/clock-back; fi
    echo "$bt" > /run/kiwi-server/btime
    if read -r at by < /run/kiwi-server/clock-back 2>/dev/null; then kv clock_back "$(( $(cut -d. -f1 /proc/uptime) - at )) $by"; fi
fi
if [ -e /run/reboot-required ] || [ -e /run/ostree/staged-deployment ]; then kv reboot yes; fi
kv failed "$(systemctl --failed --no-legend --plain 2>/dev/null | awk '{print $1}' | tr '\n' ' ')"
# bootupd holds the bootloader back when Secure Boot's db lacks Microsoft's 2023 UEFI CA (23 Nov 2026)
kv secureboot_ca "$(systemctl is-failed -q bootloader-update.service 2>/dev/null && journalctl -b -u bootloader-update.service -o cat 2>/dev/null | grep -q 'UEFI CA 2023' && echo missing)"
kv kiwi_units "$(systemctl list-units --all --no-legend --plain --type=service 'kiwi-*' 2>/dev/null | awk '{print $1":"$3":"$4}' | tr '\n' ' ')"
luks=$(blkid -c /dev/null -t TYPE=crypto_LUKS -o device 2>/dev/null | head -n 1)
if [ -n "$luks" ]; then kv luks "$(clevis luks list -d "$luks" 2>/dev/null | wc -l)"
elif grep -q 'coreos.liveiso=' /proc/cmdline 2>/dev/null || [ "$(findmnt -n -o FSTYPE / 2>/dev/null)" = tmpfs ]; then
  kv luks ram       # in RAM only (a live system): no disk holds anything
else kv luks none; fi
# the data disks kiwi-server mounts (fleet.yaml mounts) that are not in LUKS
kv plaindata "$(sed -n '/^# kiwi-server mounts: begin/,/^# kiwi-server mounts: end/p' /etc/fstab 2>/dev/null | grep -v '^#' | grep -vc '^/dev/mapper/kiwi-data')"
if [ -f /etc/kiwi-server/stack.env ]; then
    # shellcheck disable=SC1091
    . /etc/kiwi-server/stack.env
    rt=${STACK_RUNTIME:-podman}
    kv runtime "$rt"
    if [ -x "${STACK_DIR:-}/kiwi/backup.sh" ]; then
        kv backup "$(stat -c %Y "$STACK_DIR/kiwi/backup/last-success" 2>/dev/null || echo 0)"
        if [ -f "$STACK_DIR/kiwi/backup/held" ]; then kv backup_held "$(head -c 300 "$STACK_DIR/kiwi/backup/held" | tr '\n' ' ')"; fi
        for f in "$STACK_DIR"/kiwi/backup/last-success.*; do
            [ -f "$f" ] && kv backup_copy "${f##*/last-success.} $(stat -c %Y "$f")"
        done
    fi
    df -kP "${STACK_DIR:-/var}" 2>/dev/null | awk 'NR>1{print "disk=" $6, $2, $3}'
    # a backup node: what each machine keeps here, and how fast it grew in the last week (new
    # packs; a prune rewrites some, so this errs on the side of too fast)
    store=$("$rt" inspect "${STACK_PREFIX:-km}-backup-server" --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' 2>/dev/null)
    if [ -n "$store" ] && [ -d "$store" ]; then
        df -kP "$store" | awk 'NR>1{print "disk=" $6, $2, $3; print "backup_store=" $6, $4}'
        for repo in "$store"/*/; do
            [ -d "$repo/snapshots" ] || continue
            kv backup_repo "$(basename "$repo") $(du -sk "$repo" | cut -f1) $(ls "$repo/snapshots" | wc -l) $(find "$repo/data" -type f -mtime -7 -printf '%s\n' 2>/dev/null | awk '{s+=$1} END {print int(s/1024)}')"
        done
    fi
    # the parts this machine keeps ready for another one: how old each one's copy is (-1: none yet),
    # and what the last fetch that failed said
    if [ -f /etc/kiwi-server/standby/pull.conf ]; then
        for p in $(awk '{print $1}' /etc/kiwi-server/standby/pull.conf | sort -u); do
            ok=$(cat "/etc/kiwi-server/standby/state/$p.ok" 2>/dev/null); age=-1
            [ -n "$ok" ] && age=$(( $(date +%s) - ok ))
            kv standby "$p $age $(head -c 160 "/etc/kiwi-server/standby/state/$p.said" 2>/dev/null | tr '\n' ' ')"
        done
    fi
    for c in "${STACK_DIR:-}/${STACK_PREFIX:-km}-nginx/acme"/*/fullchain.pem; do
        [ -f "$c" ] || continue
        kv certificate "$(basename "$(dirname "$c")") $(date -d "$(openssl x509 -enddate -noout -in "$c" 2>/dev/null | cut -d= -f2)" +%s 2>/dev/null)"
    done
    # the hidden services, as the onion part's self-check last asked them through Tor
    if [ -f "${STACK_DIR:-}/${STACK_PREFIX:-km}-onion/var/kiwi-check" ]; then
        while read -r line; do kv onion_check "$line"; done < "${STACK_DIR}/${STACK_PREFIX:-km}-onion/var/kiwi-check"
    fi
    # a master: what each linked network opens to this one, as that network's DNS says over the
    # link (a TXT record it keeps for this network's mesh; empty: it does not say, "-": nothing)
    if [ -f "${STACK_DIR:-}/kiwi/federation-ask" ]; then
        while read -r pn pd lb; do
            [ -n "$lb" ] || continue
            kv opened "$pn $(timeout 8 "$rt" exec "${STACK_PREFIX:-km}-pihole" dig +short +time=2 +tries=1 @127.0.0.1 TXT \
                "$lb._kiwi-share.$pd" </dev/null 2>/dev/null | tr -d '"' | tr '\n' ' ')"
            kv partner_master "$pn $(timeout 8 "$rt" exec "${STACK_PREFIX:-km}-pihole" dig +short +time=2 +tries=1 @127.0.0.1 TXT \
                "_kiwi-master.$pd" </dev/null 2>/dev/null | tr -d '"' | head -n 1)"
        done < "${STACK_DIR}/kiwi/federation-ask"
    fi
    "$rt" ps -a --format 'container={{.Names}}|{{.State}}|{{.Status}}|{{.Image}}' 2>/dev/null
    for s in ${STACK_SERVICES:-}; do
        if [ -e "/run/kiwi-stack/sleeping/$s" ]; then
            kv service "$s sleeping"        # a game server nobody plays on: the first player wakes it
        elif [ "$rt" = podman ]; then
            kv service "$s $(systemctl is-active "$s.service" 2>/dev/null)"
        else
            kv service "$s $(docker inspect -f '{{.State.Status}}' "$s" 2>/dev/null || echo missing)"
        fi
    done
    # a container that restarts again and again (it crashes at its start): systemd and docker go on
    # restarting it for ever, so the count of its restarts is kept here, and how many came in the
    # last ten minutes is said (a crash loop is told, docs/design/runbook.md)
    rs=/run/kiwi-server/restarts
    if mkdir -p "$rs" 2>/dev/null; then
        now_s=$(date +%s)
        for s in ${STACK_SERVICES:-}; do
            case $s in *[!A-Za-z0-9_.-]*|"") continue ;; esac
            if [ "$rt" = podman ]; then n=$(systemctl show -p NRestarts --value "$s.service" 2>/dev/null)
            else n=$(docker inspect -f '{{.RestartCount}}' "$s" 2>/dev/null); fi
            case $n in ''|*[!0-9]*) continue ;; esac
            { awk -v t=$((now_s - 900)) '$1 >= t' "$rs/$s" 2>/dev/null; echo "$now_s $n"; } > "$rs/$s.new" && mv "$rs/$s.new" "$rs/$s"
            first=$(awk -v t=$((now_s - 600)) '$1 >= t {print $2; exit}' "$rs/$s")
            [ -n "$first" ] && [ "$n" -gt "$first" ] && kv restarts "$s $((n - first))"
        done
    fi
    # the site's moving address (the vip module): this machine holds it or waits for it
    if "$rt" inspect "${STACK_PREFIX:-km}-vip" >/dev/null 2>&1; then
        va=$("$rt" inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "${STACK_PREFIX:-km}-vip" 2>/dev/null \
             | sed -n 's/^VIP_ADDRESS=//p' | cut -d/ -f1 | head -n 1)
        if [ -n "$va" ]; then
            if ip -4 -o addr show 2>/dev/null | grep -q " $va/"; then kv vip "$va holds"; else kv vip "$va waits"; fi
        fi
    fi
    # what this machine's runbook did in the last hour (runbook.py): when, and what
    if [ -f /var/lib/kiwi-server/runbook/log ]; then
        t0=$(( $(date +%s) - 3600 ))
        tail -n 8 /var/lib/kiwi-server/runbook/log | while read -r t rest; do
            case $t in *[!0-9]*|"") continue ;; esac
            [ "$t" -ge "$t0" ] && kv runbook "$t $(printf '%s' "$rest" | head -c 160)"
        done
    fi
    if "$rt" inspect "${STACK_PREFIX:-km}-fail2ban" >/dev/null 2>&1; then
        # how many addresses are shut out right now, over all its jails
        kv banned "$("$rt" exec "${STACK_PREFIX:-km}-fail2ban" fail2ban-client banned 2>/dev/null | grep -oE "'[0-9a-fA-F.:]+'" | sort -u | wc -l)"
    fi
    if [ -n "${STACK_VPN_CONTAINER:-}" ]; then
        kv vpn "$STACK_VPN_CONTAINER"
        kv tunnel "$("$rt" exec "$STACK_VPN_CONTAINER" ip -o link show tun0 2>/dev/null | sed -n 's/.* mtu \([0-9]*\) .*/\1/p')"
        kv forward "$("$rt" exec "$STACK_VPN_CONTAINER" cat /proc/sys/net/ipv4/ip_forward 2>/dev/null)"
    fi
    if "$rt" inspect "${STACK_PREFIX:-km}-vpn-server" >/dev/null 2>&1; then
        "$rt" exec "${STACK_PREFIX:-km}-vpn-server" wg show wg0 latest-handshakes 2>/dev/null |
            awk -v now="$(date +%s)" 'NF==2{t++; if ($2>0 && now-$2<=180) r++} END{print "peers=" t+0, r+0}'
    fi
fi
# Packets cut into pieces: a tunnel's MTU is larger than its path carries. The kernel counts
# the pieces it made and the packets it put together again (IPv4), here and inside the VPN's
# containers; what the count grew by since the last look is kept in /run with its time.
frag_of() { awk '/^Ip:/{ if (!h) { for (i = 1; i <= NF; i++) n[i] = $i; h = 1 } else for (i = 1; i <= NF; i++) if (n[i] == "FragCreates" || n[i] == "ReasmOKs") s += $i } END { print s + 0 }'; }
if [ -w /run/kiwi-server ]; then
    fr=$(frag_of < /proc/net/snmp)
    seen=""
    for c in "${STACK_VPN_CONTAINER:-}" "${STACK_PREFIX:-km}-vpn-server"; do
        case " $seen " in *" $c "*) continue ;; esac
        seen="$seen $c"
        if [ -n "$c" ] && [ -n "${rt:-}" ]; then fr=$((fr + $("$rt" exec "$c" cat /proc/net/snmp 2>/dev/null | frag_of))); fi
    done
    was=$(cat /run/kiwi-server/frag 2>/dev/null)
    if [ -n "$was" ] && [ "$fr" -gt "$was" ]; then echo "$(cut -d. -f1 /proc/uptime) $((fr - was))" > /run/kiwi-server/frag-seen; fi
    echo "$fr" > /run/kiwi-server/frag
    if read -r at by < /run/kiwi-server/frag-seen 2>/dev/null; then kv fragmented "$(( $(cut -d. -f1 /proc/uptime) - at )) $by"; fi
fi
"""

SCRIPT = HUMAN + "\necho '" + MARK + "'\n" + PROBE

_HEALTH = re.compile(r"\((?:health: )?(healthy|unhealthy|starting)\)")
# podman's healthchecks are transient units named <64 hex>-<16 hex>; one that fails
# while its container starts is not a broken machine, and the container says so itself
_HEALTHCHECK_UNIT = re.compile(r"^[0-9a-f]{64}-[0-9a-f]+\.(service|timer)$")
RUNNING = ("active", "running")
BOOTING = 300     # seconds after boot in which a stack that is not up yet is only late
BACKUP_LATE = 36 * 3600      # a nightly backup that missed a night
BACKUP_BAD = 7 * 86400
CLOCK_MARGIN = 600                 # seconds a set-back clock is still named after the time has caught up
FRAGMENT_SHOWN = 3600              # seconds for which packets cut into pieces are named
BACKUP_FULL_SOON = 30              # days of free space left on a backup node before it is a problem
CERTIFICATE_LATE = 21 * 86400    # renewal starts at 30 days: this many left and it has been failing
CERTIFICATE_BAD = 7 * 86400
STANDBY_LATE = 600               # seconds: a copy kept ready is fetched every minute


def human(kib):
    """1.5 GB, 120 MB: sizes for a person."""
    for unit, size in (("TB", 1 << 30), ("GB", 1 << 20), ("MB", 1 << 10)):
        if kib >= size:
            return ("%.1f %s" % (kib / size, unit)).replace(".0 ", " ")
    return "%d KB" % kib


def lasts(days):
    return util.count(days, "day") if days < 60 else util.count(days / 7, "week") if days < 730 else "years"


def split(text):
    """(what `status` shows a person, the probe's lines)."""
    human, found, data = text.partition(MARK + "\n")
    return (human, data) if found else (text, "")


def _int(value, default=0):
    try:
        return int(str(value).strip())
    except ValueError:
        return default


def _pair(value, word, now):
    """"AGO N" (seconds ago, a number) as {"at": when by this computer's clock, word: N}, or None."""
    parts = str(value or "").split()
    if len(parts) != 2 or not all(x.isdigit() for x in parts):
        return None
    return {"at": int(now) - int(parts[0]), word: int(parts[1])}


def lasts_s(seconds):
    """90 s, 12 minutes, 3 hours: a length of time in the unit one would say it in."""
    s = int(seconds)
    return (util.count(s, "second") if s < 120 else util.count(s // 60, "minute") if s < 7200
            else util.count(s // 3600, "hour") if s < 172800 else util.count(s // 86400, "day"))


def parse(data, local_now=None):
    """The probe's lines as a dict."""
    raw, disks, containers, units, certificates, repos, copies, onion, opened, partner_masters = {}, {}, {}, [], [], [], [], [], {}, {}
    kept, acted, restarts, out_extra = [], [], {}, {}
    for line in data.splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        if key == "disk":
            parts = value.split()
            if len(parts) == 3:       # one line per mount, however many paths named it
                disks[parts[0]] = {"mount": parts[0], "size": _int(parts[1]), "used": _int(parts[2])}
        elif key == "container":
            parts = value.split("|", 3)
            if len(parts) == 4:
                containers[parts[0]] = parts
        elif key == "service":
            name, _, state = value.partition(" ")
            units.append((name, state.strip() or "unknown"))
        elif key == "backup_copy":
            name, _, at = value.partition(" ")
            copies.append({"server": name, "last": _int(at)})
        elif key == "backup_repo":
            parts = value.split()
            if len(parts) == 4:
                repos.append({"client": parts[0], "kib": _int(parts[1]), "snapshots": _int(parts[2]), "week_kib": _int(parts[3])})
        elif key == "onion_check":
            parts = value.split()
            if len(parts) == 4 and parts[1].endswith(".onion"):
                onion.append({"service": parts[0], "address": parts[1], "code": parts[2], "ok": parts[2] != "000",
                              "at": _int(parts[3])})
        elif key == "opened":
            name, _, what = value.partition(" ")
            words = what.split()
            opened[name] = None if not words else [w for w in words if w != "-"]
        elif key == "partner_master":
            name, _, master = value.partition(" ")
            if master.strip():
                partner_masters[name] = master.strip()
        elif key == "certificate":
            name, _, expires = value.partition(" ")
            certificates.append({"name": name, "expires": _int(expires)})
        elif key == "vip":
            a, _sp, st = value.partition(" ")
            if a and st.strip() in ("holds", "waits"):
                raw_vip = {"address": a, "holds": st.strip() == "holds"}
                out_extra["vip"] = raw_vip
        elif key == "restarts":
            name, _sp, n = value.partition(" ")
            if n.strip().isdigit() and int(n) > 0:
                restarts[name] = int(n)
        elif key == "runbook":
            t, _sp, what = value.partition(" ")
            if t.isdigit() and what.strip():
                acted.append({"at": int(t), "what": what.strip()})
        elif key == "standby":
            p = value.split(" ", 2)
            if len(p) >= 2 and p[1].lstrip("-").isdigit():
                kept.append({"part": p[0], "age": int(p[1]) if int(p[1]) >= 0 else None, "said": (p[2] if len(p) > 2 else "").strip()})
        else:
            raw[key] = value.strip()
    services = []

    def entry(name, state, c):
        status = c[2] if c else ""
        health = _HEALTH.search(status)
        return {"name": name, "state": state, "running": state in RUNNING, "status": status,
                "image": c[3] if c else "", "health": health.group(1) if health else "", "asleep": state == "sleeping"}
    for name, state in units:
        services.append(dict(entry(name, state, containers.pop(name, None)), stack=True))
    for name, c in sorted(containers.items()):     # started by hand: shown, never counted as missing
        services.append(dict(entry(name, c[1], c), stack=False))
    mem = [_int(x) for x in raw.get("mem", "").split()]
    load = raw.get("load", "").split()
    peers = [_int(x) for x in raw.get("peers", "").split()]
    remote_now = _int(raw.get("now"))
    out = {
        "applied": raw.get("applied", ""),
        "role_failed": raw.get("role_failed", ""),
        "os": raw.get("os", ""),
        "kernel": raw.get("kernel", ""),
        "uptime": _int(raw.get("uptime")),
        "load": [float(x) for x in load] if len(load) == 3 else [],
        "cpus": _int(raw.get("cpus")),
        "memory": {"total": mem[0], "available": mem[1]} if len(mem) == 2 else {},
        "disks": list(disks.values()),
        "clock_synced": raw.get("clock") == "yes",
        # the clock was set back (when, by how many seconds); packets were cut into pieces (when, how many)
        "clock_back": _pair(raw.get("clock_back"), "by", local_now if local_now is not None else time.time()),
        "fragmented": _pair(raw.get("fragmented"), "count", local_now if local_now is not None else time.time()),
        "skew": remote_now - int(local_now if local_now is not None else time.time()) if remote_now else 0,
        "reboot": raw.get("reboot") == "yes",
        # a game server asleep whose stop ended with an error code of its own is no failure (Zandronum)
        "failed_units": [u for u in raw.get("failed", "").split() if not _HEALTHCHECK_UNIT.match(u)
                         and u[:-len(".service")] not in {s["name"] for s in services if s["asleep"]}
                         and not (u == "bootloader-update.service" and raw.get("secureboot_ca") == "missing")],
        # its own problem below, with the way out, not a bare failed unit
        "secureboot_ca_missing": raw.get("secureboot_ca") == "missing",
        # kiwi's own units on the machine (the update, the runbook, the role): name, active, sub
        "kiwi_units": [{"name": u.split(":")[0][:-len(".service")], "active": u.split(":")[1], "sub": u.split(":")[2]}
                       for u in raw.get("kiwi_units", "").split() if u.count(":") == 2 and u.split(":")[0].endswith(".service")],
        "runtime": raw.get("runtime", ""),
        # a master: what each linked network opens to this one ({name: [names]}; None: it does not say)
        "opened": opened,
        "partner_masters": partner_masters,     # and the name of each one's master
        "services": services,
        "running": sum(1 for s in services if s["stack"] and s["running"]),
        "expected": sum(1 for s in services if s["stack"] and not s["asleep"]),     # asleep: not missing
        "asleep": [s["name"] for s in services if s["asleep"]],
    }
    if "backup" in raw:
        last = _int(raw["backup"])
        out["backup"] = {"last": last, "age": remote_now - last if last and remote_now else None,
                         "held": raw.get("backup_held", "").replace("backup: ", "", 1),
                         "copies": [dict(c, age=remote_now - c["last"] if c["last"] and remote_now else None) for c in copies]}
    store = raw.get("backup_store", "").split()
    if len(store) == 2:
        out["backup_store"] = {"mount": store[0], "free_kib": _int(store[1]), "repos": repos}
    if onion:
        out["onion"] = onion
    if certificates:
        out["certificates"] = [dict(c, left=c["expires"] - remote_now) for c in certificates if c["expires"] and remote_now]
    if kept:
        out["standby"] = kept
    if acted:
        out["runbook"] = acted
    if restarts:
        out["restarts"] = restarts
    out.update(out_extra)
    if raw.get("banned", "").isdigit():
        out["banned"] = int(raw["banned"])
    if _int(raw.get("plaindata")):
        out["plain_data_disks"] = _int(raw["plaindata"])
    if raw.get("luks"):
        out["encryption"] = (raw["luks"] if raw["luks"] in ("none", "ram")
                             else ("bound" if _int(raw["luks"]) else "key"))
    if raw.get("vpn"):
        container = next((s for s in services if s["name"] == raw["vpn"]), None)
        out["tunnel"] = {"container": raw["vpn"], "running": bool(container and container["running"]),
                         "mtu": _int(raw.get("tunnel")), "forward": raw.get("forward") == "1"}
    if len(peers) == 2:
        out["peers"] = {"total": peers[0], "recent": peers[1]}
    return out


# what a person can do about each kind of problem: with it in the app, on the page and in messages
HINTS = {
    "unreachable": "Is it on and on the network? Look at its screen or restart it.",
    "locked": "Its disk waits for its key: unlock it, or the rings it needs must be up.",
    "rebuilt": "When it is installed and on, unlock it if its disk is encrypted, then apply it.",
    "role-failed": "Open the machine: the output of its last apply says where it stopped. Apply it again.",
    "not-applied": "Apply it.",
    "service": "Restart it under the machine's services, and read its log there.",
    "unhealthy": "Read its log under the machine's services; a restart often helps.",
    "tunnel": "Apply the machine. If it stays, read the VPN client's log.",
    "secureboot-ca": "fwupd brings the certificate: on the machine, sudo fwupdmgr get-updates lists the Secure Boot "
                     "Signature Database update; sudo fwupdmgr update installs it, then a restart. A firmware (BIOS) "
                     "update does too. It adds the 2023 certificate and keeps the 2011 one. When fwupd's write "
                     "is refused (Permission denied), the firmware takes changes signed by its maker only (its KEK has "
                     "no Microsoft key, an ASUS board, 23 Nov 2026): in its setup, Secure Boot, Key Management, "
                     "Restore Factory Keys after a BIOS update, or append the certificate to db from a USB stick.",
    "failed-units": "Their log and a restart are with the machine's services (the app, the control page, or kiwi-server service FLEET MACHINE NAME logs).",
    "crash-loop": "Its log says why it stops: kiwi-server service FLEET MACHINE NAME logs. A setting, a full disk or its data.",
    "disk-full": "Remove what is not needed there, or give the machine a bigger disk.",
    "backup-store-full": "Remove old snapshots (Backups, Prune), or give the backup machine more space.",
    "memory": "Fewer services on it, or more memory.",
    "clock": "Apply the machine: its time service is set up again.",
    "clock-back": "It passes by itself at the time named. Sooner: restart the VPN service at the other end (the master's wg, or this machine's VPN client).",
    "backups-held": "Backups: what holds them is said there.",
    "backup-none": "Backups: Save it now, and look whether the backup machine answers.",
    "backup-old": "Backups: Save it now, and look whether the backup machine answers.",
    "copy-old": "Look whether that backup machine answers; the next backup copies again.",
    "certificate": "Apply the machine to renew it. A public name: its DNS record must point at the master.",
    "standby-old": "Look whether the machine that runs the part answers; its next apply sets the fetch up again. "
                   "On this machine: journalctl -t kiwi-standby.",
    "disk-plain": "Install it again with encryption when it suits you, or leave this warning out in its settings.",
    "data-disk-plain": "Its data disk is encrypted when it is set up again empty, or leave this warning out in its settings.",
}
# What a person can do about a problem, beside the hint: [{label, page, app, run}]. `page`: a
# page of the control page; `app`: the app's own action for it; `run`: a job for this machine
# (the page's /run, the app's command). One table for the page, the app and the phone.
_BREAKS = {"label": "Recovery", "page": "/recovery", "app": "recovery"}
_UNLOCK = {"label": "Unlock it", "run": "unlock"}
_RINGS = {"label": "Disk unlock", "page": "/unlock", "app": "unlock-picture"}
_BACKUPS = {"label": "Backups", "page": "/backups"}
ACTIONS = {
    "unreachable": [_BREAKS],
    "locked": [_UNLOCK, _RINGS, _BREAKS],
    "rebuilt": [_UNLOCK, _RINGS, _BREAKS],
    "role-failed": [{"label": "Apply", "run": "apply"}],
    "not-applied": [{"label": "Apply", "run": "apply"}],
    "tunnel": [{"label": "Apply", "run": "apply"}],
    "clock": [{"label": "Apply", "run": "apply"}],
    "certificate": [{"label": "Apply", "run": "apply"}],
    "backups-held": [_BACKUPS], "backup-none": [_BACKUPS], "backup-old": [_BACKUPS], "backup-store-full": [_BACKUPS],
}


def actions(key):
    return [dict(a) for a in ACTIONS.get(key or "", [])]


# the warnings a machine may leave out (its setting `quiet`): never one that says something is broken
QUIETABLE = {"disk-plain": "The disk is not encrypted", "data-disk-plain": "A data disk is not encrypted",
             "clock": "The clock", "memory": "Little free memory", "failed-units": "Failed units", "crash-loop": "A service that does not stay up",
             "unhealthy": "A service reports unhealthy", "not-applied": "No role applied yet"}


CRASH_LOOP = 3       # restarts in ten minutes that make a crash loop


def assess(f, quiet=(), now=None):
    """(state, problems, notes). state: ok, warn or bad; a problem is {level, text, key, hint}.
    `quiet`: the keys of warnings this machine leaves out (QUIETABLE): they become notes."""
    bad = lambda text, key: {"level": "bad", "text": text, "key": key, "hint": HINTS.get(key, ""),     # noqa: E731
                             "actions": actions(key)}
    warn = lambda text, key: {"level": "warn", "text": text, "key": key, "hint": HINTS.get(key, ""),   # noqa: E731
                              "actions": actions(key)}
    problems, notes = [], []
    booting = 0 < f.get("uptime", 0) < BOOTING
    if f.get("role_failed"):
        problems.append(bad("the role failed: %s" % f["role_failed"], "role-failed"))
    elif not f.get("applied"):
        problems.append(warn("no role has been applied here yet", "not-applied"))
    for s in f.get("services", []):
        if s.get("asleep"):
            notes.append("%s sleeps until a player comes" % s["name"])
        elif s["stack"] and not s["running"]:
            if booting and s["state"] in ("inactive", "activating", "created", "missing"):
                notes.append("%s has not started yet: the machine just booted" % s["name"])
            else:
                problems.append(bad("%s is not running (%s)" % (s["name"], s["state"]), "service"))
        elif s["health"] == "unhealthy":
            problems.append(warn("%s reports unhealthy" % s["name"], "unhealthy"))
        elif s["health"] == "starting":
            notes.append("%s is still starting" % s["name"])
    t = f.get("tunnel")
    if t and t["running"]:
        if not t["mtu"]:
            problems.append(bad("the VPN client %s has no tunnel" % t["container"], "tunnel"))
        if not t["forward"]:
            problems.append(bad("the VPN client %s does not forward: the mesh behind it has no internet" % t["container"], "tunnel"))
    if f.get("failed_units"):
        problems.append(warn("failed units: %s" % ", ".join(f["failed_units"]), "failed-units"))
    if f.get("secureboot_ca_missing"):
        problems.append(warn("Secure Boot: the firmware lacks Microsoft's 2023 UEFI certificate, so the bootloader is "
                             "not updated (it still starts)", "secureboot-ca"))
    loops = sorted(((n, s) for s, n in (f.get("restarts") or {}).items() if n >= CRASH_LOOP), reverse=True)
    if loops:
        # restarted again and again by systemd or docker: it does not stay up (a setting, a disk, its data)
        problems.append(warn("%s: restarted again and again in the last ten minutes, it does not stay up" % (
            ", ".join("%s (%d times)" % (s, n) for n, s in loops)), "crash-loop"))
    for d in f.get("disks", []):
        used = d["used"] / d["size"] if d["size"] else 0
        if used >= .85:
            problems.append((bad if used >= .95 else warn)("%s is %d%% full" % (d["mount"], used * 100), "disk-full"))
    if f.get("disks"):
        notes.append("disk " + ", ".join("%s %d%% of %s" % (d["mount"], 100 * d["used"] / d["size"], human(d["size"]))
                                         for d in f["disks"] if d["size"]))
    st = f.get("backup_store")
    if st:
        repos = st["repos"]
        week = sum(r["week_kib"] for r in repos)
        days = st["free_kib"] / (week / 7) if week else None
        notes.append("backups here: %s; %s free%s" % (
            ", ".join("%s %s in %s" % (r["client"], human(r["kib"]), util.count(r["snapshots"], "snapshot")) for r in repos) or "none yet",
            human(st["free_kib"]), ", about %s at last week's rate" % lasts(days) if days is not None else ""))
        if days is not None and days < BACKUP_FULL_SOON:
            problems.append((bad if days < 7 else warn)(
                "the backups fill %s in about %s at last week's rate: kiwi-server prune" % (st["mount"], lasts(days)), "backup-store-full"))
    m = f.get("memory") or {}
    if m.get("total") and m["available"] / m["total"] < .05:
        problems.append(warn("only %d MiB of memory free" % (m["available"] / 1024), "memory"))
    if f.get("os") and not f.get("clock_synced"):
        problems.append(warn("the clock is not synchronised", "clock"))
    if abs(f.get("skew", 0)) > 30:
        problems.append(warn("the clock is %+d s off from this computer's" % f["skew"], "clock"))
    now = int(now if now is not None else time.time())
    back = f.get("clock_back")         # its times: this computer's (the machine says how long ago, by its uptime)
    if back and now < back["at"] + back["by"] + CLOCK_MARGIN:
        # WireGuard refuses handshakes dated before the last one it took: until the time has caught up
        problems.append(warn("its clock was set back %s at %s: until %s the VPN's other ends may refuse it"
                             % (lasts_s(back["by"]), time.strftime("%H:%M", time.localtime(back["at"])),
                                time.strftime("%H:%M", time.localtime(back["at"] + back["by"]))), "clock-back"))
    frag = f.get("fragmented")
    if frag and now - frag["at"] < FRAGMENT_SHOWN:
        notes.append("packets were cut into pieces here (%d, last at %s): a tunnel's MTU is larger than its path carries; "
                     "kiwi-server mtu measures it" % (frag["count"], time.strftime("%H:%M", time.localtime(frag["at"]))))
    if f.get("reboot"):
        notes.append("an update is waiting for a reboot")
    if f.get("banned"):
        notes.append("fail2ban keeps %d %s out right now" % (f["banned"], "address" if f["banned"] == 1 else "addresses"))
    if f.get("vip"):
        notes.append("%s the moving address %s" % ("holds" if f["vip"]["holds"] else "waits for", f["vip"]["address"]))
    for a in f.get("runbook") or []:     # what its runbook did by itself, as the admins wrote it (the last hour)
        notes.append("the runbook, %s: %s" % (time.strftime("%H:%M", time.localtime(a["at"])), a["what"]))
    b = f.get("backup")
    if b:
        if b.get("held"):
            problems.append(warn("backups are held: %s" % b["held"], "backups-held"))
        elif not b["last"]:
            # the first run follows the first apply: late only once the machine has been up a while
            (notes.append("no backup yet") if booting else problems.append(warn("no backup has succeeded yet", "backup-none")))
        elif b["age"] is not None and b["age"] > BACKUP_BAD:
            problems.append(bad("the last backup is %d days old" % (b["age"] / 86400), "backup-old"))
        elif b["age"] is not None and b["age"] > BACKUP_LATE:
            problems.append(warn("the last backup is %d hours old" % (b["age"] / 3600), "backup-old"))
        elif b["age"] is not None:
            notes.append("last backup %d h ago" % (b["age"] / 3600))
        copies = b.get("copies") or []
        if len(copies) > 1:
            notes.append("copies: " + ", ".join("%s %s" % (c["server"], "%d h ago" % (c["age"] / 3600) if c["age"] is not None else "never")
                                              for c in copies))
        for c in copies:
            if c["age"] is not None and c["age"] > BACKUP_LATE and b["age"] is not None and b["age"] <= BACKUP_LATE:
                problems.append(warn("the last copy at %s is %d hours old" % (c["server"], c["age"] / 3600), "copy-old"))
    for c in f.get("certificates", []):
        days = int(c["left"] / 86400)
        if c["left"] < CERTIFICATE_BAD:
            problems.append(bad("the certificate of %s %s" % (c["name"], "has expired" if c["left"] < 0 else "expires in %s" % util.count(days, "day")), "certificate"))
        elif c["left"] < CERTIFICATE_LATE:
            problems.append(warn("the certificate of %s expires in %s: it should have been renewed" % (c["name"], util.count(days, "day")), "certificate"))
    for k in f.get("standby") or []:
        # a copy that is not fresh is what a switch by itself would start with
        if k["age"] is None:
            problems.append(warn("no copy yet of %s, which it keeps ready%s" % (k["part"], ": " + k["said"] if k["said"] else ""), "standby-old"))
        elif k["age"] > STANDBY_LATE:
            problems.append(warn("its copy of %s, which it keeps ready, is %s old%s" % (
                k["part"], lasts_s(k["age"]), ": " + k["said"] if k["said"] else ""), "standby-old"))
    for o in f.get("onion") or []:
        if not o["ok"]:
            notes.append("the hidden service %s did not answer through Tor when last asked" % o["service"])
    if f.get("certificates"):
        notes.append("certificates good for %d days" % int(min(c["left"] for c in f["certificates"]) / 86400))
    encryption = f.get("encryption")
    if encryption == "none":    # encrypted is the default: a machine without it says so, every time
        problems.append(warn("the disk is not encrypted: whoever takes it can read everything on it", "disk-plain"))
    elif encryption == "key":
        notes.append("the disk opens only with a typed key")
    elif encryption == "ram":
        notes.append("in RAM only: nothing stays on a disk")
    if f.get("plain_data_disks"):
        n = f["plain_data_disks"]
        problems.append(warn("%s not encrypted: whoever takes %s can read everything on %s"
                             % ("a data disk is" if n == 1 else "%d data disks are" % n, "it" if n == 1 else "them",
                                "it" if n == 1 else "them"), "data-disk-plain"))
    kept = []
    for p in problems:      # what this machine leaves out on purpose stays visible, as a note
        if p["level"] == "warn" and p["key"] in quiet:
            notes.append("left out on purpose: " + p["text"])
        else:
            kept.append(p)
    problems = kept
    levels = {p["level"] for p in problems}
    return ("bad" if "bad" in levels else "warn" if "warn" in levels else "ok"), problems, notes
