"""What a running machine says about itself, as data.

One read-only shell script, plain sh, so it runs on Debian and on CoreOS alike.
It prints key=value lines and nothing that is a secret: the WireGuard peers come
from `wg show latest-handshakes`, public keys only, never `wg show dump`, which
carries the private one. `kiwi-server status --porcelain` parses this and says
what is wrong; the app shows that and decides nothing of its own.
"""
import re
import time

MARK = "--- kiwi-facts ---"

# what `kiwi-server status` has always printed for a person
HUMAN = ("echo \"applied: $(cat /var/lib/kiwi-server/role.done 2>/dev/null || echo never)\"; "
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
if [ -e /run/reboot-required ] || [ -e /run/ostree/staged-deployment ]; then kv reboot yes; fi
kv failed "$(systemctl --failed --no-legend --plain 2>/dev/null | awk '{print $1}' | tr '\n' ' ')"
luks=$(blkid -c /dev/null -t TYPE=crypto_LUKS -o device 2>/dev/null | head -n 1)
if [ -n "$luks" ]; then kv luks "$(clevis luks list -d "$luks" 2>/dev/null | wc -l)"; else kv luks none; fi
if [ -f /etc/kiwi-server/stack.env ]; then
    # shellcheck disable=SC1091
    . /etc/kiwi-server/stack.env
    rt=${STACK_RUNTIME:-podman}
    kv runtime "$rt"
    if [ -x "${STACK_DIR:-}/kiwi/backup.sh" ]; then
        kv backup "$(stat -c %Y "$STACK_DIR/kiwi/backup/last-success" 2>/dev/null || echo 0)"
        if [ -f "$STACK_DIR/kiwi/backup/held" ]; then kv backup_held "$(head -c 300 "$STACK_DIR/kiwi/backup/held" | tr '\n' ' ')"; fi
    fi
    for c in "${STACK_DIR:-}/${STACK_PREFIX:-km}-nginx/acme"/*/fullchain.pem; do
        [ -f "$c" ] || continue
        kv certificate "$(basename "$(dirname "$c")") $(date -d "$(openssl x509 -enddate -noout -in "$c" 2>/dev/null | cut -d= -f2)" +%s 2>/dev/null)"
    done
    "$rt" ps -a --format 'container={{.Names}}|{{.State}}|{{.Status}}|{{.Image}}' 2>/dev/null
    for s in ${STACK_SERVICES:-}; do
        if [ "$rt" = podman ]; then
            kv service "$s $(systemctl is-active "$s.service" 2>/dev/null)"
        else
            kv service "$s $(docker inspect -f '{{.State.Status}}' "$s" 2>/dev/null || echo missing)"
        fi
    done
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
CERTIFICATE_LATE = 21 * 86400    # renewal starts at 30 days: this many left and it has been failing
CERTIFICATE_BAD = 7 * 86400


def split(text):
    """(what `status` shows a person, the probe's lines)."""
    human, found, data = text.partition(MARK + "\n")
    return (human, data) if found else (text, "")


def _int(value, default=0):
    try:
        return int(str(value).strip())
    except ValueError:
        return default


def parse(data, local_now=None):
    """The probe's lines as a dict."""
    raw, disks, containers, units, certificates = {}, {}, {}, [], []
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
        elif key == "certificate":
            name, _, expires = value.partition(" ")
            certificates.append({"name": name, "expires": _int(expires)})
        else:
            raw[key] = value.strip()
    services = []

    def entry(name, state, c):
        status = c[2] if c else ""
        health = _HEALTH.search(status)
        return {"name": name, "state": state, "running": state in RUNNING, "status": status,
                "image": c[3] if c else "", "health": health.group(1) if health else ""}
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
        "skew": remote_now - int(local_now if local_now is not None else time.time()) if remote_now else 0,
        "reboot": raw.get("reboot") == "yes",
        "failed_units": [u for u in raw.get("failed", "").split() if not _HEALTHCHECK_UNIT.match(u)],
        "runtime": raw.get("runtime", ""),
        "services": services,
        "running": sum(1 for s in services if s["stack"] and s["running"]),
        "expected": sum(1 for s in services if s["stack"]),
    }
    if "backup" in raw:
        last = _int(raw["backup"])
        out["backup"] = {"last": last, "age": remote_now - last if last and remote_now else None,
                         "held": raw.get("backup_held", "").replace("backup: ", "", 1)}
    if certificates:
        out["certificates"] = [dict(c, left=c["expires"] - remote_now) for c in certificates if c["expires"] and remote_now]
    if raw.get("luks"):
        out["encryption"] = "none" if raw["luks"] == "none" else ("bound" if _int(raw["luks"]) else "key")
    if raw.get("vpn"):
        container = next((s for s in services if s["name"] == raw["vpn"]), None)
        out["tunnel"] = {"container": raw["vpn"], "running": bool(container and container["running"]),
                         "mtu": _int(raw.get("tunnel")), "forward": raw.get("forward") == "1"}
    if len(peers) == 2:
        out["peers"] = {"total": peers[0], "recent": peers[1]}
    return out


def assess(f):
    """(state, problems, notes). state: ok, warn or bad; a problem is {level, text}."""
    bad = lambda text: {"level": "bad", "text": text}    # noqa: E731
    warn = lambda text: {"level": "warn", "text": text}  # noqa: E731
    problems, notes = [], []
    booting = 0 < f.get("uptime", 0) < BOOTING
    if f.get("role_failed"):
        problems.append(bad("the role failed: %s" % f["role_failed"]))
    elif not f.get("applied"):
        problems.append(warn("no role has been applied here yet"))
    for s in f.get("services", []):
        if s["stack"] and not s["running"]:
            if booting and s["state"] in ("inactive", "activating", "created", "missing"):
                notes.append("%s has not started yet: the machine just booted" % s["name"])
            else:
                problems.append(bad("%s is not running (%s)" % (s["name"], s["state"])))
        elif s["health"] == "unhealthy":
            problems.append(warn("%s reports unhealthy" % s["name"]))
        elif s["health"] == "starting":
            notes.append("%s is still starting" % s["name"])
    t = f.get("tunnel")
    if t and t["running"]:
        if not t["mtu"]:
            problems.append(bad("the VPN client %s has no tunnel" % t["container"]))
        if not t["forward"]:
            problems.append(bad("the VPN client %s does not forward: the mesh behind it has no internet" % t["container"]))
    if f.get("failed_units"):
        problems.append(warn("failed units: %s" % ", ".join(f["failed_units"])))
    for d in f.get("disks", []):
        used = d["used"] / d["size"] if d["size"] else 0
        if used >= .85:
            problems.append((bad if used >= .95 else warn)("%s is %d%% full" % (d["mount"], used * 100)))
    m = f.get("memory") or {}
    if m.get("total") and m["available"] / m["total"] < .05:
        problems.append(warn("only %d MiB of memory free" % (m["available"] / 1024)))
    if f.get("os") and not f.get("clock_synced"):
        problems.append(warn("the clock is not synchronised"))
    if abs(f.get("skew", 0)) > 30:
        problems.append(warn("the clock is %+d s off from this computer's" % f["skew"]))
    if f.get("reboot"):
        notes.append("an update is waiting for a reboot")
    b = f.get("backup")
    if b:
        if b.get("held"):
            problems.append(warn("backups are held: %s" % b["held"]))
        elif not b["last"]:
            # the first run follows the first apply: late only once the machine has been up a while
            (notes.append("no backup yet") if booting else problems.append(warn("no backup has succeeded yet")))
        elif b["age"] is not None and b["age"] > BACKUP_BAD:
            problems.append(bad("the last backup is %d days old" % (b["age"] / 86400)))
        elif b["age"] is not None and b["age"] > BACKUP_LATE:
            problems.append(warn("the last backup is %d hours old" % (b["age"] / 3600)))
        elif b["age"] is not None:
            notes.append("last backup %d h ago" % (b["age"] / 3600))
    for c in f.get("certificates", []):
        days = int(c["left"] / 86400)
        if c["left"] < CERTIFICATE_BAD:
            problems.append(bad("the certificate of %s %s" % (c["name"], "has expired" if c["left"] < 0 else "expires in %d days" % days)))
        elif c["left"] < CERTIFICATE_LATE:
            problems.append(warn("the certificate of %s expires in %d days: it should have been renewed" % (c["name"], days)))
    if f.get("certificates"):
        notes.append("certificates good for %d days" % int(min(c["left"] for c in f["certificates"]) / 86400))
    encryption = f.get("encryption")
    if encryption == "none":
        notes.append("the disk is not encrypted")
    elif encryption == "key":
        notes.append("the disk opens only with a typed key")
    levels = {p["level"] for p in problems}
    return ("bad" if "bad" in levels else "warn" if "warn" in levels else "ok"), problems, notes
