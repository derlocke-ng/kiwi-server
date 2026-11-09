"""A machine's disk, opened at boot by its Tang rings.

The host's `unlock:` block names the rings, the `tang` module on another host serves
them, and this binds the disk with Clevis after the role has run. The key a person can
type stays in its own keyslot: a ring that is gone costs a console, not the disk.

The thumbprint comes from the ring's host over SSH, so the machine being bound does not
have to trust whatever answered at that address. Design: docs/design/control-plane.md,
Unlock.
"""
import ipaddress
import json
import os
import re
import shlex
import subprocess
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
want='DROPBEAR_OPTIONS="-p %d -s -I 600"'
if [ "$(cat $d/dropbear.conf 2>/dev/null)" != "$want" ]; then
    printf '%%s\n' "$want" > $d/dropbear.conf; changed=1
fi
# a forwarded ring listens on 127.0.0.1, and Debian's initramfs leaves the loopback down
h=/etc/initramfs-tools/scripts/init-premount/kiwi-loopback
hook='#!/bin/sh
[ "$1" = prereqs ] && exit 0
ip link set lo up'
if [ "$(cat $h 2>/dev/null)" != "$hook" ]; then
    printf '%%s\n' "$hook" > $h; chmod 0755 $h; changed=1
fi
[ -z "$changed" ] || { update-initramfs -u >/dev/null 2>&1; echo updated >&2; }
dropbearkey -y -f $d/dropbear_ed25519_host_key | grep '^ssh-ed25519' | cut -d' ' -f1,2
'''

# The same on CoreOS: dracut-sshd, layered, with a host key of its own since the boot
# partition is not encrypted. stdin: the keys that may log in. Takes effect at the next boot.
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
if [ "$(cat $d/authorized_keys 2>/dev/null)" != "$keys" ]; then
    printf '%s\n' "$keys" > $d/authorized_keys; chmod 0600 $d/authorized_keys
    echo changed >&2
fi
printf 'root:*:19000:0:99999:7:::\n' > $d/kiwi-shadow; chmod 0600 $d/kiwi-shadow
cat > $d/kiwi-sshd.conf <<'UNIT'
[Service]
ExecStartPre=/bin/sh -c 'grep -q ^root: /etc/shadow || cat /etc/kiwi-shadow >> /etc/shadow'
UNIT
rpm-ostree install --idempotent --allow-inactive dracut-sshd >/dev/null
rpm-ostree initramfs | grep -q 'Initramfs regeneration: enabled' ||
    rpm-ostree initramfs --enable --arg=--include --arg=$d/kiwi-shadow --arg=/etc/kiwi-shadow \
        --arg=--include --arg=$d/kiwi-sshd.conf --arg=/etc/systemd/system/sshd.service.d/kiwi.conf >/dev/null
cut -d' ' -f1,2 $k.pub
'''

_CONNECTED = "kiwi-unlock-connected"
SSH_PORT = {"debian": 2222}      # else 22: CoreOS's initramfs sshd takes the normal port


def ssh_port(host):
    return SSH_PORT.get(host.target, 22)


def wanted(host):
    return bool(host.cfg["unlock"].get("rings"))


def ring_servers(fleet, resolve):
    """ring -> (host, port), for every ring a host of this fleet serves. `resolve(host)`
    fills in its settings; a host that cannot be resolved serves nothing."""
    out = {}
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


def _known_hosts(host):
    return host.fleet.resolve_path("secrets/known_hosts.initramfs")


def _initramfs_ssh(host, tgt):
    """SSH in the initramfs, and its host key on record while the machine can vouch for it:
    the boot partition is not encrypted, so that key is the only proof of who answers."""
    script = PREPARE_DROPBEAR % ssh_port(host) if host.target == "debian" else PREPARE_DRACUT_SSHD
    out = remote.run(tgt, script, ("\n".join(host.ssh_keys()) + "\n").encode())
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
    """Is any ring reached only through a port someone forwards in? It cannot help a first boot."""
    return bool(_forwarded_rings(host))


def _forwarded_rings(host):
    via = host.cfg["unlock"].get("via") or {}
    return [r for r in host.cfg["unlock"]["rings"] if str(via.get(r) or "").startswith("http://127.0.0.1:")]


def _forward(host, servers, ring=None):
    """(ring, what ssh -R needs) for a ring this operator's machine can reach."""
    name, inside = forwarded_ring(host, ring)
    if name not in servers:
        raise KiwiError("no host of this fleet serves the ring %s" % name)
    server, port = servers[name]
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
    """ssh into the initramfs: its own known_hosts, and the first option given is the one that counts."""
    return (["ssh", "-o", "UserKnownHostsFile=%s" % _known_hosts(host), "-o", "StrictHostKeyChecking=yes"] + remote.ssh_opts()
            + list(options or ["-p", str(ssh_port(host))]) + ["root@%s" % address] + list(command))


def type_key(host, address=None, timeout=300, runner=subprocess.run, clock=time.monotonic, sleep=time.sleep):
    """The disk's key, handed to the initramfs over SSH, for when no ring can help. It goes
    on stdin without a line end: Debian passes stdin to cryptsetup as it is."""
    if host.target != "debian":
        raise KiwiError("%s: typing the key over SSH is built for Debian; on %s use the console" % (host.name, host.target))
    key = luks.current_key(host)
    if not key:
        raise KiwiError("%s: the disk's key is not on record (secrets/luks/%s.key)" % (host.name, host.name))
    addresses = [address] if address else remote.addresses(host)
    deadline = clock() + timeout
    while clock() < deadline:
        for a in addresses:
            if not remote._answers(a, ssh_port(host)):
                continue
            r = runner(_initramfs_ssh_argv(host, a, command=["cryptroot-unlock"]), input=key.encode(), capture_output=True)
            if r.returncode == 0:
                util.say("%s: the disk is open; the machine goes on booting" % host.name)
                return True
            said = r.stderr.decode(errors="replace").strip().splitlines()[-1:] or ["no message"]
            raise KiwiError("%s: the initramfs did not take the key (%s) — it counts tries; see the console before trying again" % (host.name, said[0]))
        sleep(3)
    raise KiwiError("%s: no initramfs answered within %d s" % (host.name, timeout))


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
    # The session prints this once it is in and the port is forwarded. It ends when the
    # initramfs does, which can be seconds later: how long it lasted says nothing.
    command = ["echo %s; sleep 900" % _CONNECTED]
    deadline = clock() + timeout
    while clock() < deadline:
        for a in addresses:
            if not remote._answers(a, sshport):
                continue
            r = runner(_initramfs_ssh_argv(host, a, options, command), stdin=subprocess.DEVNULL, capture_output=True)
            if _CONNECTED.encode() in r.stdout:
                util.say("%s: the disk is open; the machine goes on booting" % host.name)
                return True
        sleep(3)
    raise KiwiError("%s: no initramfs answered within %d s" % (host.name, timeout))
