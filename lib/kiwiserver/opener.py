"""An opener (docs/design/unlock.md): a machine at home that opens the way for a machine away from
home which waits at boot for a ring of this one. Once a minute it looks whether that machine's
boot system answers (Debian: port 2222, closed while the system runs) and, if so, offers it its
ring through an SSH tunnel with a key that can do nothing else there. Without it only the control
node opens a master at a hoster: off after a power cut, the master stayed locked.
"""
from . import remote, unlock, util
from .util import KiwiError

DIR = "/etc/kiwi-server/opener"
SCRIPT_PATH = "/usr/local/sbin/kiwi-opener"
UNIT = "kiwi-opener"
SCRIPT = r'''#!/bin/bash
# kiwi-server: opens the way for a machine that waits at boot for a ring of this machine (openers,
# docs/design/unlock.md). Run every minute. Its key can only offer the ring's port in that boot system.
d=/etc/kiwi-server/opener
exec 9>/run/kiwi-opener.lock; flock -n 9 || exit 0
while read -r name addr port via ring; do
    [ -n "$ring" ] || continue
    timeout 3 bash -c "</dev/tcp/$addr/$port" 2>/dev/null || continue      # it waits only then
    logger -t kiwi-opener "$name waits at $addr: offering it the ring at $ring"
    timeout 600 ssh -i "$d/key" -o UserKnownHostsFile="$d/known_hosts" -o StrictHostKeyChecking=yes \
        -o BatchMode=yes -o ConnectTimeout=10 -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 \
        -p "$port" -N -R "127.0.0.1:$via:$ring" "root@$addr" </dev/null
    rc=$?; [ $rc = 255 ] && logger -t kiwi-opener "$name: its boot system is done (it went on booting)" \
        || logger -t kiwi-opener "$name: the way is closed again (exit $rc)"
done < "$d/targets"
'''
SERVICE = "[Unit]\nDescription=kiwi-server: open the way for a machine that waits for a ring of this one\n\n[Service]\nType=oneshot\nExecStart=%s\n" % SCRIPT_PATH
TIMER = "[Unit]\nDescription=kiwi-server: look every minute for a machine that waits for a ring of this one\n\n[Timer]\nOnBootSec=1min\nOnUnitActiveSec=1min\nAccuracySec=10s\n\n[Install]\nWantedBy=timers.target\n"
INSTALL = r'''set -eu
install -d -m 0700 %(dir)s
key=$(cat)
umask 077
printf '%%s\n' "$key" > %(dir)s/key.new && mv %(dir)s/key.new %(dir)s/key
cat > %(dir)s/known_hosts <<'KIWI_EOF'
%(known)sKIWI_EOF
cat > %(dir)s/targets <<'KIWI_EOF'
%(targets)sKIWI_EOF
umask 022
cat > %(path)s.new <<'KIWI_EOF'
%(script)sKIWI_EOF
chmod 0755 %(path)s.new && mv %(path)s.new %(path)s
cat > /etc/systemd/system/%(unit)s.service <<'KIWI_EOF'
%(service)sKIWI_EOF
cat > /etc/systemd/system/%(unit)s.timer <<'KIWI_EOF'
%(timer)sKIWI_EOF
systemctl daemon-reload
systemctl enable --now -q %(unit)s.timer
'''
REMOVE = ("systemctl disable --now -q %s.timer 2>/dev/null || true; rm -rf %s %s /etc/systemd/system/%s.service "
          "/etc/systemd/system/%s.timer; systemctl daemon-reload" % (UNIT, DIR, SCRIPT_PATH, UNIT, UNIT))


def jobs(host, servers):
    """[(machine, ring, via, ring_port)] this machine opens the way for."""
    out = []
    for c in sorted(host.fleet.hosts.values(), key=lambda x: x.name):
        if c is host or not (c.cfg.get("unlock") or {}).get("rings"):
            continue
        for opener, ring, via, port in unlock.openers(c, servers):
            if opener is host:
                out.append((c, ring, via, port))
    return out


def plan(host, servers):
    """(targets text, known_hosts text) for this opener, or None when it opens nothing."""
    js = jobs(host, servers)
    if not js:
        return None
    try:
        known = util.read_text(unlock._known_hosts(host))
    except OSError:
        known = ""
    targets, keep = [], []
    for c, ring, via, port in js:
        addresses = remote.addresses(c)
        for a in addresses:
            targets.append("%s %s %d %d 127.0.0.1:%d" % (c.name, a, unlock.ssh_port(c), via, port))
            name = "[%s]:%d " % (a, unlock.ssh_port(c))
            keep += [ln for ln in known.splitlines() if ln.startswith(name)]
    return "".join(t + "\n" for t in targets), "".join(k + "\n" for k in dict.fromkeys(keep))


def after_apply(host, tgt, servers, runner=None):
    """Its opener as the fleet says: installed with its key and targets, or removed."""
    run = runner or remote.run
    if not servers:
        return
    try:
        p = plan(host, servers)
        if not p:
            run(tgt, REMOVE)
            return
        targets, known = p
        if not known:
            util.warn("%s: no boot-system host key on record yet for the machines it opens: it opens them after their "
                      "next apply" % host.name)
        key = util.read_text(unlock.opener_key(host.fleet, host.name))
        script = INSTALL % {"dir": DIR, "known": known, "targets": targets, "path": SCRIPT_PATH, "script": SCRIPT,
                            "unit": UNIT, "service": SERVICE, "timer": TIMER}
        run(tgt, script, key.encode())
        util.say("%s: opens the way for %s when it waits at boot" % (
            host.name, ", ".join(sorted({c.name for c, _r, _v, _p in jobs(host, servers)}))))
    except KiwiError as e:
        util.warn("%s: its opener was not set up: %s" % (host.name, e))

