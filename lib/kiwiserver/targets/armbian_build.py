"""An encrypted board's image, built with Armbian's build framework (CRYPTROOT_ENABLE) on a
machine with Docker: `armbian.builder`, its SSH name as this computer's ssh knows it.

The framework wants a privileged container; its own Docker mode also prunes the machine's images
and volumes, so it runs natively in one container of kiwi-server's (`kiwi-armbian-build`, Ubuntu
24.04) and nothing of the builder is removed. The builder gets the board's one-time disk key, on a
tmpfs inside the container and removed after the build (never on its disk; checked on sh3, 22 Nov
2026: in no log or file of the build), and public keys: the fleet admin's, for the boot system's SSH
(port 2222: kiwi-server's unlock key), where `kiwi-server unlock --manual` gives the one-time key at
the first start. With a
fixed address the boot system takes it too. The rest of the fleet comes over SSH: `adopt` with the
board's own login, then `apply`, which replaces the one-time key (luks.swap) and binds the rings.

Built on an x86 machine, the framework runs ARM programs through QEMU: their handlers are
registered with the builder's kernel (gone at its next restart).
"""
import ipaddress
import os
import shlex
import subprocess

from .. import luks, util
from ..util import KiwiError

CONTAINER = "kiwi-armbian-build"
IMAGE = "docker.io/library/ubuntu:24.04"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]

# on the builder: the framework's checkout at the ref, the container, its tools, the QEMU handlers
PREPARE = r'''
set -eu
w=%(dir)s; ref=%(ref)s; c=%(c)s
command -v docker >/dev/null || { echo "armbian.builder: no docker on $(hostname)" >&2; exit 1; }
[ -d "$w/.git" ] || git clone -q https://github.com/armbian/build "$w"
git -C "$w" fetch -q --depth 1 origin "$ref" && git -C "$w" -c advice.detachedHead=false checkout -q FETCH_HEAD
mkdir -p "$w/userpatches"
if ! docker inspect "$c" >/dev/null 2>&1; then
    docker run -d --privileged --name "$c" --tmpfs /secret:size=1m,mode=0700 -v /dev:/dev -v "$w:/armbian" %(image)s sleep infinity >/dev/null
fi
src=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/armbian"}}{{.Source}}{{end}}{{end}}' "$c")
[ "$src" = "$w" ] || { echo "armbian.builder: the container $c builds in $src, not $w" >&2; exit 1; }
[ "$(docker inspect -f '{{.State.Running}}' "$c")" = true ] || docker start "$c" >/dev/null
docker exec -i "$c" bash -s <<'IN'
set -eu
export DEBIAN_FRONTEND=noninteractive
command -v update-binfmts >/dev/null && command -v qemu-aarch64-static >/dev/null && command -v git >/dev/null || {
    apt-get update -q >/dev/null && apt-get install -y -q git sudo curl ca-certificates locales qemu-user-static binfmt-support >/dev/null; }
# Ubuntu 24.04's QEMU has systemd's handler files only; the framework wants binfmt-support's
for a in arm aarch64; do
    [ "$(uname -m)" = "$a" ] && continue
    if [ ! -e /usr/share/binfmts/qemu-$a ]; then
        IFS=: read -r _ _name _type offset magic mask interp flags < <(grep -v '^#' /usr/lib/binfmt.d/qemu-$a.conf | head -1)
        { echo "package qemu-user-static"; echo "interpreter $interp"; echo "magic $magic"; echo "offset ${offset:-0}"
          echo "mask $mask"; echo "credentials yes"
          case "$flags" in *F*) echo "fix_binary yes" ;; *) echo "fix_binary no" ;; esac; echo "preserve yes"
        } > /usr/share/binfmts/qemu-$a
    fi
    [ -e /proc/sys/fs/binfmt_misc/qemu-$a ] || update-binfmts --import qemu-$a
done
IN
'''

# the build itself; stdin of the first command is the one-time key, into the tmpfs
BUILD = r'''
set -u
c=%(c)s
docker exec "$c" bash -c 'cd /armbian && CRYPTROOT_PASSPHRASE="$(cat /secret/pass)" ./compile.sh build %(args)s; rc=$?; rm -f /secret/pass; exit $rc'
'''


def _ssh(builder, script, stdin=None, stream=False):
    cmd = SSH + [builder, "bash -s"]
    if stream:
        r = subprocess.run(cmd, input=(script.encode() if stdin is None else None))
        return r.returncode, ""
    r = subprocess.run(cmd, input=script.encode(), capture_output=True)
    return r.returncode, (r.stdout + r.stderr).decode(errors="replace")


def customize(host):
    """userpatches/customize-image.sh: with a fixed address, the boot system takes it (the first
    unlock at a known address); root takes the fleet admin's key, so `adopt` needs no password,
    and Armbian's first-login questions are skipped (adopt locks root's password). Nothing secret."""
    net = host.cfg.get("network") or {}
    lines = ["#!/bin/bash", "# kiwi-server: for %s" % host.name, "set -e"]
    pub = host.fleet.resolve_path("secrets/admin_ed25519.pub")
    if os.path.isfile(pub):
        key = " ".join(util.read_text(pub).split()[:2])
        lines += ["install -d -m 0700 /root/.ssh", "echo %s >> /root/.ssh/authorized_keys" % shlex.quote(key),
                  "chmod 0600 /root/.ssh/authorized_keys", "rm -f /root/.not_logged_in_yet"]
    if not net.get("dhcp", True) and net.get("address"):
        iface = ipaddress.ip_interface(str(net["address"]))
        gw = str(net.get("gateway") or "")
        dev = str(net.get("interface") or "")       # a board with two ports: the one with the cable (eth0, eth1)
        arg = "ip=%s::%s:%s:%s:%s:off" % (iface.ip, gw, iface.network.netmask, host.hostname.split(".")[0], dev)
        lines.append("sed -i '/^extraargs=/d' /boot/armbianEnv.txt")
        lines.append("echo 'extraargs=%s' >> /boot/armbianEnv.txt" % arg)
        # the system takes the same address from its first start, on that port (any Ethernet port
        # without one), before netplan's DHCP (10-netplan-*): no lease, no hunt for its address
        dns = "".join("DNS=%s\\n" % d for d in (net.get("dns") or []))
        unit = ("# kiwi-server: the fixed address of this machine, as the fleet file says (network.address)\\n"
                "[Match]\\nName=%s\\n\\n[Network]\\nAddress=%s\\nGateway=%s\\n%sDHCP=no\\n"
                % (dev or "e*", iface, gw, dns))
        lines.append("install -d /etc/systemd/network")
        lines.append("printf '%s' > /etc/systemd/network/05-kiwi-static.network" % unit)
    return "\n".join(lines) + "\n"


def compile_args(cfg):
    return " ".join("%s=%s" % (k, shlex.quote(str(v))) for k, v in (
        ("BOARD", cfg["board"]), ("BRANCH", cfg.get("branch") or "current"), ("RELEASE", cfg.get("release") or "trixie"),
        ("BUILD_MINIMAL", "yes" if (cfg.get("variant") or "minimal") == "minimal" else "no"), ("BUILD_DESKTOP", "no"),
        ("KERNEL_CONFIGURE", "no"), ("CRYPTROOT_ENABLE", "yes"), ("CRYPTROOT_SSH_UNLOCK", "yes"),
        ("CRYPTROOT_SSH_UNLOCK_PORT", "2222"), ("PREFER_DOCKER", "no"), ("COMPRESS_OUTPUTIMAGE", "sha,img")))


def image(host, out_img):
    """Build the board's encrypted image on its builder and fetch it to `out_img`."""
    cfg = host.cfg["armbian"]
    builder, wdir = str(cfg["builder"]), str(cfg.get("builder_dir") or "/var/tmp/kiwi-armbian-build")
    if not cfg.get("board"):
        raise KiwiError("%s: armbian.board is needed to build its image" % host.name)
    q = {"dir": shlex.quote(wdir), "ref": shlex.quote(str(cfg.get("build_ref") or "main")), "c": CONTAINER,
         "image": IMAGE}
    util.say("%s: preparing Armbian's build framework on %s (%s)" % (host.name, builder, wdir))
    rc, out = _ssh(builder, PREPARE % q)
    if rc != 0:
        raise KiwiError("%s: the builder %s is not ready: %s" % (host.name, builder, out.strip()[-600:]))
    from .. import unlock
    keys = unlock.authorized_line(host)      # kiwi-server's unlock key, as apply writes it later (22 Nov 2026: the admin's was refused)
    for name, text, mode in (("dropbear_authorized_keys", keys.rstrip("\n") + "\n", "0644"), ("customize-image.sh", customize(host), "0755")):
        r = subprocess.run(SSH + [builder, "cat > %s && chmod %s %s" % (shlex.quote(wdir + "/userpatches/" + name), mode,
                                                                          shlex.quote(wdir + "/userpatches/" + name))],
                           input=text.encode(), capture_output=True)
        if r.returncode != 0:
            raise KiwiError("%s: could not write %s on %s" % (host.name, name, builder))
    key = luks.install_key(host)           # the one-time key: the first apply replaces it
    r = subprocess.run(SSH + [builder, "docker exec -i %s sh -c 'umask 077; cat > /secret/pass'" % CONTAINER],
                       input=key.encode(), capture_output=True)
    if r.returncode != 0:
        raise KiwiError("%s: the key did not reach the build container on %s" % (host.name, builder))
    util.say("%s: building its encrypted image on %s (minutes; the kernel comes from Armbian's cache)" % (host.name, builder))
    rc, _ = _ssh(builder, BUILD % {"c": CONTAINER, "args": compile_args(cfg)}, stream=True)
    if rc != 0:
        raise KiwiError("%s: Armbian's build failed on %s (its log: %s/output/logs)" % (host.name, builder, wdir))
    board = str(cfg["board"]).lower()
    rc, out = _ssh(builder, "ls -t %s/output/images/*.img | grep -i -- '_%s_' | grep -- '-crypt_' | head -1"
                   % (shlex.quote(wdir), board.replace("'", "")))
    src = out.strip().splitlines()[-1] if rc == 0 and out.strip() else ""
    if not src:
        raise KiwiError("%s: no encrypted image of %s in %s/output/images" % (host.name, board, wdir))
    util.say("%s: fetching %s" % (host.name, os.path.basename(src)))
    os.makedirs(os.path.dirname(out_img), exist_ok=True)
    r = subprocess.run(["scp", "-q", "-o", "BatchMode=yes", "%s:%s" % (builder, src), out_img + ".part"])
    if r.returncode != 0:
        raise KiwiError("%s: the image could not be fetched from %s" % (host.name, builder))
    os.chmod(out_img + ".part", 0o600)
    os.replace(out_img + ".part", out_img)
    util.say("%s: %s. Flash it; at its first start its boot system asks for the key in %s: "
             "kiwi-server unlock <fleet> %s --manual, then kiwi-server adopt and apply"
             % (host.name, out_img, luks.once_file(host), host.name))
    return out_img
