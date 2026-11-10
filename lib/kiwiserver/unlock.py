"""A machine's disk, opened at boot by its Tang rings.

The host's `unlock:` block names the rings, the `tang` module on another host serves
them, and this binds the disk with Clevis after the role has run. The key a person can
type stays in its own keyslot: a ring that is gone costs a console, not the disk.

The thumbprint comes from the ring's host over SSH, so the machine being bound does not
have to trust whatever answered at that address. Design: docs/design/control-plane.md,
Unlock.
"""
import atexit
import ipaddress
import json
import os
import re
import shlex
import subprocess
import tempfile
import time
import urllib.parse

from . import luks, remote, util
from .util import KiwiError

DEBIAN_PACKAGES = "clevis clevis-luks clevis-initramfs"

# Debian's initramfs has no network unless told. stdin: the IP= value, with IFACE for the
# interface that holds the default route.
PREPARE_DEBIAN = r'''
set -eu
IFS= read -r ip
if ! dpkg -s clevis-initramfs >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q %s >/dev/null
fi
dev=$(ip -4 route show default | sed -n 's/.* dev \([^ ]*\).*/\1/p' | head -1)
[ -n "$dev" ] || { echo "no default route to take the interface from" >&2; exit 1; }
want="IP=$(printf %%s "$ip" | sed "s/IFACE/$dev/")"
f=/etc/initramfs-tools/conf.d/kiwi-unlock
if [ "$(cat "$f" 2>/dev/null)" != "$want" ]; then
    printf '%%s\n' "$want" > "$f"
    update-initramfs -u >/dev/null 2>&1
    echo updated
fi
''' % DEBIAN_PACKAGES

# stdin: the disk's key. stdout: `device DEV` and what is bound to it, or none.
LIST_SCRIPT = luks._PRELUDE + r'''
IFS= read -r key
printf %s "$key" > "$d/key"
for dev in $(blkid -c /dev/null -t TYPE=crypto_LUKS -o device); do
    cryptsetup open --test-passphrase --key-file="$d/key" "$dev" >/dev/null 2>&1 || continue
    echo "device $dev"
    clevis luks list -d "$dev" 2>/dev/null
    exit 0
done
echo none
'''

# stdin: the disk's key, the device, the Clevis tree, the slots to free. The new binding
# is in place before an old one goes, so a run that is cut off still leaves a way in.
BIND_SCRIPT = luks._PRELUDE + r'''
IFS= read -r key; IFS= read -r dev; IFS= read -r tree; IFS= read -r old
printf %s "$key" > "$d/key"
clevis luks bind -y -k "$d/key" -d "$dev" sss "$tree" || exit 1
for slot in $old; do clevis luks unbind -f -d "$dev" -s "$slot" || exit 1; done
'''


# On CoreOS Ignition binds the disk at the first boot and nobody ever had a key for it.
# This adds one a person can type, opened with the ring's own share. stdin: the new key.
ADD_KEY_SCRIPT = luks._PRELUDE + r'''
IFS= read -r key
printf %s "$key" > "$d/new"
for dev in $(blkid -c /dev/null -t TYPE=crypto_LUKS -o device); do
    if cryptsetup open --test-passphrase --key-file="$d/new" "$dev" >/dev/null 2>&1; then echo "already $dev"; exit 0; fi
    slot=$(clevis luks list -d "$dev" 2>/dev/null | sed -n 's/^\([0-9]*\):.*/\1/p' | head -1)
    [ -n "$slot" ] || continue
    clevis luks pass -d "$dev" -s "$slot" > "$d/old" || exit 1
    cryptsetup luksAddKey --batch-mode --key-file="$d/old" "$dev" "$d/new" || exit 1
    cryptsetup open --test-passphrase --key-file="$d/new" "$dev" >/dev/null 2>&1 || exit 1
    echo "added $dev"; exit 0
done
echo none
'''


# a forwarded ring listens on 127.0.0.1, and Debian's initramfs leaves the loopback down
LOOPBACK_HOOK = '#!/bin/sh\n[ "$1" = prereqs ] && exit 0\nip link set lo up'
LOOPBACK_PATH = "/etc/initramfs-tools/scripts/init-premount/kiwi-loopback"

# An SSH server in Debian's initramfs, for a machine nothing can reach at boot but a person.
# stdin: the keys that may log in.
PREPARE_DROPBEAR = r'''
set -eu
keys=$(cat)
if ! dpkg -s dropbear-initramfs >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q dropbear-initramfs >/dev/null
fi
d=/etc/dropbear/initramfs
changed=
if [ "$(cat $d/authorized_keys 2>/dev/null)" != "$keys" ]; then
    printf '%%s\n' "$keys" > $d/authorized_keys; chmod 0600 $d/authorized_keys; changed=1
fi
want='%s'
if [ "$(cat $d/dropbear.conf 2>/dev/null)" != "$want" ]; then
    printf '%%s\n' "$want" > $d/dropbear.conf; changed=1
fi
h=%s
hook='%s'
if [ "$(cat $h 2>/dev/null)" != "$hook" ]; then
    printf '%%s\n' "$hook" > $h; chmod 0755 $h; changed=1
fi
[ -z "$changed" ] || { update-initramfs -u >/dev/null 2>&1; echo updated >&2; }
dropbearkey -y -f $d/dropbear_ed25519_host_key | grep '^ssh-ed25519' | cut -d' ' -f1,2
'''

# The same on CoreOS: dracut-sshd, layered, with a host key of its own since the boot
# partition is not encrypted and a config that allows nothing but forwarding in. stdin: the
# keys that may log in. Takes effect at the next boot.
# sshd refuses a key for an account it takes as locked, and the initramfs's /etc/shadow has
# no root in it. dracut will not overwrite that file, so the line goes in under another
# name and sshd's unit adds it at the start. `*` is no password at all, without being locked.
PREPARE_DRACUT_SSHD = r'''
set -eu
keys=$(cat)
d=/etc/dracut-sshd
mkdir -p $d; chmod 0700 $d
k=/etc/ssh/dracut_ssh_host_ed25519_key
[ -f $k ] || ssh-keygen -q -t ed25519 -N '' -f $k
changed=
if [ "$(cat $d/authorized_keys 2>/dev/null)" != "$keys" ]; then
    printf '%s\n' "$keys" > $d/authorized_keys; chmod 0600 $d/authorized_keys
    changed=1
fi
cat > $d/sshd_config.new <<'CONFIG'
PermitRootLogin forced-commands-only
AuthorizedKeysFile .ssh/authorized_keys
AuthenticationMethods publickey
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM no
X11Forwarding no
AllowAgentForwarding no
AllowStreamLocalForwarding no
AllowTcpForwarding remote
GatewayPorts no
PermitTunnel no
PermitUserRC no
MaxAuthTries 3
MaxSessions 2
LoginGraceTime 20
ClientAliveInterval 15
ClientAliveCountMax 4
SyslogFacility AUTHPRIV
CONFIG
if ! cmp -s $d/sshd_config.new $d/sshd_config; then mv $d/sshd_config.new $d/sshd_config; changed=1; else rm $d/sshd_config.new; fi
printf 'root:*:19000:0:99999:7:::\n' > $d/kiwi-shadow; chmod 0600 $d/kiwi-shadow
cat > $d/kiwi-sshd.conf <<'UNIT'
[Service]
ExecStartPre=/bin/sh -c 'grep -q ^root: /etc/shadow || cat /etc/kiwi-shadow >> /etc/shadow'
UNIT
rpm-ostree install --idempotent --allow-inactive dracut-sshd >/dev/null
if rpm-ostree initramfs | grep -q 'Initramfs regeneration: enabled'; then
    [ -z "$changed" ] || rpm-ostree initramfs --disable >/dev/null
fi
rpm-ostree initramfs | grep -q 'Initramfs regeneration: enabled' ||
    rpm-ostree initramfs --enable --arg=--include --arg=$d/kiwi-shadow --arg=/etc/kiwi-shadow \
        --arg=--include --arg=$d/kiwi-sshd.conf --arg=/etc/systemd/system/sshd.service.d/kiwi.conf >/dev/null
cut -d' ' -f1,2 $k.pub
'''

_CONNECTED = "kiwi-unlock-connected"
SSH_PORT = {"debian": 2222}      # else 22: CoreOS's initramfs sshd takes the normal port


def ssh_port(host):
    return SSH_PORT.get(host.target, 22)


OPERATOR = "operator"
OPERATOR_IMAGE = "ghcr.io/derlocke-ng/tang:15-kiwi2"


class OperatorRing:
    """The ring on the operator's own machine, for what nothing else can open: a master that
    has no other machine it could rely on, the machine that serves the other rings. Its keys
    are in the secret store. It runs only while kiwi-server needs it, on a port of its own,
    so it answers nobody else, and a store that is locked means it cannot run at all."""
    name = "this machine"

    def __init__(self, fleet, run=subprocess.run):
        self.fleet, self.run, self.port, self.container = fleet, run, None, None

    def _db(self):
        return self.fleet.resolve_path("secrets/tang/operator")

    def start(self):
        if self.port:
            return self.port
        db = self._db()
        os.makedirs(db, mode=0o700, exist_ok=True)
        # root in a rootless container is this user: the keys stay readable for the store
        base = ["podman", "run", "--rm", "--security-opt", "label=disable", "-v", "%s:/db" % db]
        if not [f for f in os.listdir(db) if not f.startswith(".")]:
            # tangd-keygen gives the keys to the image's tang user, whom this user cannot read as
            r = self.run(base + ["--entrypoint", "/bin/sh", OPERATOR_IMAGE, "-c", "/usr/libexec/tangd-keygen /db && chown -R 0:0 /db"],
                         capture_output=True)
            if r.returncode != 0:
                raise KiwiError("the operator ring's keys could not be made: %s" % r.stderr.decode(errors="replace").strip()[-200:])
        port = remote.free_port()
        name = "kiwi-tang-operator-%d" % port
        r = self.run(base[:2] + ["-d", "--name", name, "-p", "127.0.0.1:%d:7500" % port] + base[2:]
                     + ["--entrypoint", "/usr/libexec/tangd", OPERATOR_IMAGE, "-l", "-p", "7500", "/db"], capture_output=True)
        if r.returncode != 0:
            raise KiwiError("the operator ring did not start: %s" % r.stderr.decode(errors="replace").strip()[-200:])
        self.container, self.port = name, port
        atexit.register(self.stop)
        for _ in range(40):
            if remote._answers("127.0.0.1", port) and self.advertisement(quiet=True):
                return port
            time.sleep(0.25)
        raise KiwiError("the operator ring started but does not answer")

    def stop(self):
        if self.container:
            self.run(["podman", "rm", "-f", self.container], capture_output=True)
            self.container, self.port = None, None

    def _exec(self, *command):
        return self.run(["podman", "exec", self.container, *command], capture_output=True)

    def thumbprint(self):
        out = self._exec("tang-show-keys", "7500").stdout.decode(errors="replace").split()
        if not out:
            raise KiwiError("the operator ring reports no key")
        return out[0]

    def advertisement(self, quiet=False):
        text = self._exec("curl", "-sf", "http://localhost:7500/adv").stdout.decode(errors="replace").strip()
        try:
            json.loads(text)
        except ValueError:
            if quiet:
                return ""
            raise KiwiError("the operator ring gave no advertisement")
        return text


def wanted(host):
    return bool(host.cfg["unlock"].get("rings"))


def ring_servers(fleet, resolve):
    """ring -> (host, port), for every ring a host of this fleet serves, and the operator's.
    `resolve(host)` fills in its settings; a host that cannot be resolved serves nothing."""
    out = {OPERATOR: (OperatorRing(fleet), 0)}
    for h in fleet.hosts.values():
        try:
            resolve(h)
        except KiwiError:
            continue
        if "tang" not in h.modules:
            continue
        for ring, port in h.module_settings["tang"]["rings"].items():
            if ring in out:
                raise KiwiError("the ring %s is served by both %s and %s" % (ring, out[ring][0].name, h.name))
            out[ring] = (h, int(port))
    return out


def _lan(host):
    net = host.cfg["network"]
    if net.get("dhcp", True) or not net.get("address"):
        return None
    try:
        return ipaddress.ip_interface(str(net["address"]))
    except ValueError:
        return None


def url(client, ring, server, port):
    """Where this machine's initramfs finds the ring: it has plain network and nothing else.
    `unlock.via` names the way when it is not the LAN: a relay, a forwarded port."""
    via = client.cfg["unlock"].get("via") or {}
    if via.get(ring):
        return str(via[ring])
    mine, theirs = _lan(client), _lan(server)
    if mine and theirs and theirs.ip in mine.network:
        return "http://%s:%d" % (theirs.ip, port)
    raise KiwiError("%s cannot reach the ring on %s at boot: they are not on one LAN with static addresses "
                    "(unlock.via says where else to find it)" % (client.name, server.name))


def thumbprint(server, ring, port):
    """The ring's signing key, as its own host reports it."""
    out = _in_ring(server, ring, "tang-show-keys 7500")
    for line in out.split():
        return line
    raise KiwiError("%s: the ring %s reports no key — is its container running?" % (server.name, ring))


def advertisement(server, ring):
    """What the ring offers, from its own host: a binding must not need to reach it at
    bind time, as `via` may name a port that exists only while it is being forwarded."""
    out = _in_ring(server, ring, "curl -sf http://localhost:7500/adv").strip()
    try:
        json.loads(out)
    except ValueError:
        raise KiwiError("%s: the ring %s gave no advertisement — is its container running?" % (server.name, ring))
    return out


def _in_ring(server, ring, command):
    runtime = shlex.quote(server.role_settings.get("runtime") or "podman")
    return remote.run(remote.reach(server), "%s exec kn-tang-%s %s" % (runtime, ring, command)).decode(errors="replace")


def pins(host, servers):
    """Where this host finds each of its rings, and the key each must answer with."""
    out = []
    for ring in host.cfg["unlock"]["rings"]:
        if ring not in servers:
            raise KiwiError("%s: no host of this fleet serves the ring %s (the tang module's rings)" % (host.name, ring))
        server, port = servers[ring]
        if isinstance(server, OperatorRing):
            if not (host.cfg["unlock"].get("via") or {}).get(ring):
                raise KiwiError("%s: the operator ring is reached through a forwarded port: unlock.via needs it" % host.name)
            server.start()
            out.append({"url": url(host, ring, server, port), "thp": server.thumbprint(), "adv": server.advertisement()})
            continue
        if server is host:
            raise KiwiError("%s cannot be opened by the ring %s: it serves that ring itself" % (host.name, ring))
        out.append({"url": url(host, ring, server, port), "thp": thumbprint(server, ring, port), "adv": advertisement(server, ring)})
    return out


def tree(host, servers):
    """The Clevis configuration for this host's rings."""
    return {"t": int(host.cfg["unlock"]["threshold"]),
            "pins": {"tang": [{"url": p["url"], "adv": json.loads(p["adv"])} for p in pins(host, servers)]}}


def _ip_setting(host):
    me = _lan(host)
    if me is None:
        return "dhcp"
    return "%s::%s:%s::IFACE:off" % (me.ip, host.cfg["network"].get("gateway") or "", me.netmask)


def _bound(listing):
    """[(slot, t, urls)] of what `clevis luks list` shows as an sss tree of tang pins."""
    out = []
    for m in re.finditer(r"^(\d+): sss '(\{.*\})'\s*$", listing, re.M):
        try:
            cfg = json.loads(m.group(2))
            out.append((m.group(1), int(cfg["t"]), sorted(p["url"] for p in cfg["pins"]["tang"])))
        except (ValueError, KeyError, TypeError):
            continue
    return out


def _unlock_key(fleet):
    """A key for the initramfs and for nothing else: what a person does with it there is
    limited by its line in authorized_keys, and it opens no running machine."""
    path = fleet.resolve_path("secrets/unlock_ed25519")
    if not os.path.isfile(path):
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "kiwi-unlock", "-f", path], check=True)
    return path


def authorized_line(host):
    """That key's line in the initramfs: as tight as the server there can make it.
    Dropbear ignores a key with an option it does not know, so it gets only what it knows."""
    public = " ".join(util.read_bytes(_unlock_key(host.fleet) + ".pub").decode().split()[:2])
    forwards = _forwarded_rings(host)
    if host.target == "debian":
        options = ["no-agent-forwarding", "no-X11-forwarding", "no-pty"] + ([] if forwards else ["no-port-forwarding"])
        options.append('command="case $SSH_ORIGINAL_COMMAND in unlock) cryptroot-unlock;; *) echo %s; sleep 900;; esac"' % _CONNECTED)
    else:
        options = ["restrict"]
        if forwards:
            options.append("port-forwarding")
            options += ['permitlisten="127.0.0.1:%d"' % forwarded_ring(host, r)[1] for r in forwards]
        options.append('command="echo %s; sleep 900"' % _CONNECTED)
        allowed = host.cfg["unlock"].get("ssh_from") or []
        if allowed:
            options.append('from="%s"' % ",".join(str(a) for a in allowed))
    return "%s %s" % (",".join(options), public)


def dropbear_conf(host):
    # no forwarded ring: nothing here needs forwarding at all
    return 'DROPBEAR_OPTIONS="-p %d -s -j%s -T 3 -I 600"' % (ssh_port(host), "" if _forwarded_rings(host) else " -k")


def initramfs_ip(host):
    """The IP= for Debian's initramfs, with IFACE for the interface of the default route."""
    return _ip_setting(host)


def _known_hosts(host):
    return host.fleet.resolve_path("secrets/known_hosts.initramfs")


def _initramfs_ssh(host, tgt):
    """SSH in the initramfs, and its host key on record while the machine can vouch for it:
    the boot partition is not encrypted, so that key is the only proof of who answers."""
    script = (PREPARE_DROPBEAR % (dropbear_conf(host), LOOPBACK_PATH, LOOPBACK_HOOK)) if host.target == "debian" else PREPARE_DRACUT_SSHD
    out = remote.run(tgt, script, (authorized_line(host) + "\n").encode())
    key = out.decode(errors="replace").strip()
    if not key.startswith("ssh-ed25519 "):
        raise KiwiError("%s: the initramfs has no SSH host key" % host.name)
    path = _known_hosts(host)
    lines = util.read_bytes(path).decode().splitlines() if os.path.isfile(path) else []
    for address in remote.addresses(host):
        name = "[%s]:%d" % (address, ssh_port(host))
        lines = [ln for ln in lines if not ln.startswith(name + " ")] + ["%s %s" % (name, key)]
    util.write_bytes(path, ("\n".join(lines) + "\n").encode(), 0o600)


def _coreos_key(host, tgt, servers):
    """A CoreOS disk was bound at install; what it lacks is a key for the console. It is
    written here before the machine changes, so a run that is cut off can be repeated.
    Reading the binding takes the ring, so a ring only we can reach is forwarded in."""
    key = luks.current_key(host) or luks.make_key(host)
    options = ["-R", _forward(host, servers)[1]] if _forwarded_rings(host) else []
    out = remote.run(tgt, ADD_KEY_SCRIPT, (key + "\n").encode(), options=options).decode(errors="replace").split()
    state = out[0] if out else "none"
    if state == "added":
        util.say("%s: the disk has a key for the console now, in %s" % (host.name, luks.key_file(host)))
    elif state != "already":
        raise KiwiError("%s: no disk bound to Tang here — installed without unlock:? It cannot be added to a running CoreOS" % host.name)
    return state == "added"


def bind(host, tgt, servers):
    """Bind the disk to the host's rings; what is already right stays as it is."""
    coreos = host.target != "debian"
    if coreos:
        _coreos_key(host, tgt, servers)
    key = luks.current_key(host)
    if not key:
        raise KiwiError("%s: unlock: is set but the disk's key is not on record (secrets/luks/%s.key)" % (host.name, host.name))
    wanted_tree = tree(host, servers)
    if not coreos and b"updated" in remote.run(tgt, PREPARE_DEBIAN, (_ip_setting(host) + "\n").encode()):
        util.say("%s: the initramfs now brings up the network at boot" % host.name)
    changed = _rebind(host, tgt, key, wanted_tree)
    if forwarded(host):
        # it waits at boot for someone to forward the ring: it must not reboot unasked
        remote.run(tgt, "systemctl disable --now kiwi-reboot-if-required.timer zincati.service 2>/dev/null || true")
    if host.cfg["unlock"].get("ssh"):
        _initramfs_ssh(host, tgt)
        if coreos:
            util.say("%s: SSH in the initramfs comes with the next boot" % host.name)
    return changed


def _rebind(host, tgt, key, wanted_tree):
    listing = remote.run(tgt, LIST_SCRIPT, (key + "\n").encode()).decode(errors="replace")
    if not listing.startswith("device "):
        raise KiwiError("%s: no disk on the machine opens with the key on record" % host.name)
    dev = listing.split()[1]
    urls = sorted(p["url"] for p in wanted_tree["pins"]["tang"])
    now = _bound(listing)
    if [(t, u) for _s, t, u in now] == [(wanted_tree["t"], urls)]:
        return False
    old = " ".join(s for s, _t, _u in now)
    data = "%s\n%s\n%s\n%s\n" % (key, dev, json.dumps(wanted_tree, separators=(",", ":")), old)
    remote.run(tgt, BIND_SCRIPT, data.encode())
    util.say("%s: the disk opens by itself with %d of %s; its key stays for the console"
             % (host.name, wanted_tree["t"], ", ".join(host.cfg["unlock"]["rings"])))
    return True



def forwarded(host):
    """Does opening this disk take a ring that someone has to forward in? Rings it reaches
    by itself may be enough for the threshold, and then nobody has to be there."""
    cfg = host.cfg["unlock"]
    return len(cfg["rings"]) - len(_forwarded_rings(host)) < int(cfg["threshold"])


def _forwarded_rings(host):
    via = host.cfg["unlock"].get("via") or {}
    return [r for r in host.cfg["unlock"]["rings"] if str(via.get(r) or "").startswith("http://127.0.0.1:")]


def _forward(host, servers, ring=None):
    """(ring, what ssh -R needs) for a ring this operator's machine can reach."""
    name, inside = forwarded_ring(host, ring)
    if name not in servers:
        raise KiwiError("no host of this fleet serves the ring %s" % name)
    server, port = servers[name]
    if isinstance(server, OperatorRing):
        return name, "127.0.0.1:%d:127.0.0.1:%d" % (inside, server.start())
    ring_at = next((a for a in remote.addresses(server) if remote._answers(a, port)), None)
    if not ring_at:
        raise KiwiError("the ring %s on %s does not answer from this machine" % (name, server.name))
    return name, "127.0.0.1:%d:%s:%d" % (inside, ring_at, port)


def forwarded_ring(host, ring=None):
    """(ring, port inside the initramfs) of a ring reached through a forwarded port."""
    via = host.cfg["unlock"].get("via") or {}
    choices = _forwarded_rings(host)
    if ring and ring not in choices:
        raise KiwiError("%s: unlock.via does not forward the ring %s to 127.0.0.1" % (host.name, ring))
    if not choices:
        raise KiwiError("%s: no ring in unlock.via is reached through 127.0.0.1, so there is nothing to forward" % host.name)
    name = ring or choices[0]
    return name, urllib.parse.urlsplit(str(via[name])).port


def _initramfs_ssh_argv(host, address, options=(), command=()):
    """ssh into the initramfs: its own known_hosts, and the first option given is the one that
    counts. Before the first apply nothing can have vouched for the host key, and what goes
    over that session is a key that dies at that apply."""
    strict = "accept-new" if luks.at_first_boot(host) else "yes"
    return (["ssh", "-o", "UserKnownHostsFile=%s" % _known_hosts(host), "-o", "StrictHostKeyChecking=%s" % strict,
             "-o", "IdentitiesOnly=yes", "-i", _unlock_key(host.fleet)] + remote.ssh_opts(identities=False)
            + list(options or ["-p", str(ssh_port(host))]) + ["root@%s" % address] + list(command))


def waiting_at(host, runner=subprocess.run):
    """The address where this machine's initramfs is waiting for its key, or None. It is known
    by the key it shows, without logging in: a port that merely answers could be the system."""
    if not host.cfg["unlock"].get("ssh"):
        return None
    port = ssh_port(host)
    for a in remote.addresses(host):
        if remote._answers(a, port) and remote.shows_key_from(a, port, _known_hosts(host), runner):
            return a
    return None


def _key_not_on_record(r):
    """ssh refused the session because the host key differs from the one pinned."""
    text = (r.stderr or b"").decode(errors="replace")
    return r.returncode == 255 and ("REMOTE HOST IDENTIFICATION HAS CHANGED" in text or "Host key verification failed" in text)


def _last_words(r):
    lines = (r.stderr or b"").decode(errors="replace").strip().splitlines()
    return lines[-1] if lines else "no message"


def _as_new_installation(host, address, port, runner):
    """An initramfs whose host key is not the one on record is a new installation, or someone
    else's machine. Either way it gets the one-time key and nothing more: that key opens only a
    disk installed from this fleet's ISO, and the first apply replaces it. The key on record,
    which opens the old installation's disk, never goes to a host that cannot prove which it is."""
    once = luks.one_time_key(host)
    if not once:
        raise KiwiError("%s: its initramfs at %s shows a host key that is not on record and there is no one-time key "
                        "(%s) for a new installation: if it was reinstalled, build its ISO again" % (host.name, address, luks.once_file(host)))
    util.say("%s: its initramfs shows a host key that is not on record: a new installation, it gets the one-time key"
             % host.name)
    with tempfile.TemporaryDirectory() as d:
        argv = (["ssh", "-o", "UserKnownHostsFile=%s" % os.path.join(d, "known_hosts"), "-o", "StrictHostKeyChecking=accept-new",
                 "-o", "IdentitiesOnly=yes", "-i", _unlock_key(host.fleet)] + remote.ssh_opts(identities=False)
                + ["-p", str(port), "root@%s" % address, "unlock"])
        return runner(argv, input=once.encode(), capture_output=True)


def type_key(host, address=None, port=None, timeout=300, runner=subprocess.run, clock=time.monotonic, sleep=time.sleep):
    """The disk's key, handed to the initramfs over SSH, for when no ring can help. It goes
    on stdin without a line end: Debian passes stdin to cryptsetup as it is."""
    if host.target != "debian":
        raise KiwiError("%s: typing the key over SSH is built for Debian; on %s use the console" % (host.name, host.target))
    key = luks.current_key(host) or luks.one_time_key(host)
    if not key:
        raise KiwiError("%s: the disk's key is not on record (secrets/luks/%s.key)" % (host.name, host.name))
    addresses = [address] if address else remote.addresses(host)
    sshport = port or ssh_port(host)
    deadline, last = clock() + timeout, ""
    while clock() < deadline:
        for a in addresses:
            if not remote._answers(a, sshport):
                continue
            r = runner(_initramfs_ssh_argv(host, a, ["-p", str(sshport)], ["unlock"]), input=key.encode(), capture_output=True)
            if _key_not_on_record(r):
                r = _as_new_installation(host, a, sshport, runner)
            if r.returncode == 0:
                util.say("%s: the disk is open; the machine goes on booting" % host.name)
                return True
            if r.returncode == 255:         # ssh's own failure: no session, so no try was counted
                last = _last_words(r)
                continue
            said = r.stderr.decode(errors="replace").strip().splitlines()[-1:] or ["no message"]
            raise KiwiError("%s: the initramfs did not take the key (%s) — it counts tries; see the console before trying again" % (host.name, said[0]))
        sleep(3)
    raise KiwiError("%s: no initramfs answered within %d s%s" % (host.name, timeout, " (ssh: %s)" % last if last else ""))


def open_disk(host, servers, ring=None, address=None, port=None, timeout=300, runner=subprocess.run, clock=time.monotonic, sleep=time.sleep):
    """Forward a ring into a machine's initramfs and stay until the disk has opened, which
    is when the initramfs and its SSH server go away. The machine learns nothing it can
    use, and needs no key or tunnel of its own on the unencrypted boot partition."""
    name, forward = _forward(host, servers, ring)
    util.say("%s: waiting for its initramfs; forwarding the ring %s" % (host.name, name))
    addresses = [address] if address else remote.addresses(host)
    sshport = port or ssh_port(host)
    options = ["-p", str(sshport), "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3",
               "-R", forward]
    # The key's forced command prints a marker once it is in and the port is forwarded. It
    # ends when the initramfs does, which can be seconds later: how long it lasted says nothing.
    command = ["wait"]
    deadline, last = clock() + timeout, ""
    while clock() < deadline:
        for a in addresses:
            if not remote._answers(a, sshport):
                continue
            r = runner(_initramfs_ssh_argv(host, a, options, command), stdin=subprocess.DEVNULL, capture_output=True)
            if _key_not_on_record(r):
                # with a ring, a machine that cannot prove which it is could open the real one's disk
                raise KiwiError("%s: its initramfs at %s shows a host key that is not on record, so no ring goes to it. "
                                "A reinstalled machine opens with kiwi-server unlock %s %s --manual until its first apply"
                                % (host.name, a, os.path.basename(host.fleet.path), host.name))
            if r.returncode == 255:
                last = _last_words(r)
            if _CONNECTED.encode() in r.stdout:
                util.say("%s: the disk is open; the machine goes on booting" % host.name)
                return True
        sleep(3)
    raise KiwiError("%s: no initramfs answered within %d s%s" % (host.name, timeout, " (ssh: %s)" % last if last else ""))
