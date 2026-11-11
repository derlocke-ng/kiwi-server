"""coreos-ram: a master whose system runs from RAM (docs/design/ram-master.md).

The Fedora CoreOS live ISO with the host's live Ignition. Ignition runs at every boot of
a live system, so every boot starts from the image. What makes the machine this master
lives on one partition, LABEL=kiwi-state on disk:, which kiwi-state.service mounts and
binds into place; kiwi-role.service then runs the role script from it, as on a first
boot. The ISO carries no role and no secret: apply makes the partition and sends the role.
"""
import os
import shlex
import subprocess

from .. import remote, sshca, util
from ..util import KiwiError
from . import ROLE_DIR, ROLE_SCRIPT, role_unit
from . import coreos as coreos_t

LABEL = "kiwi-state"
MOUNT = "/var/mnt/kiwi-state"
STATE_SCRIPT = "/etc/kiwi-ram/state.sh"
FROMRAM = "coreos.liveiso.fromram"
DEFAULT_STACK_DIR = "/home/user/docker"

STATE_SH = r"""#!/usr/bin/bash
# kiwi-server: the state of a master that lives in RAM (docs/design/ram-master.md).
#   state.sh mount                at every boot (kiwi-state.service): the partition into place
#   state.sh init DISK STACK_DIR  from kiwi-server apply: the partition made on DISK (wiped) when
#                                 it is not there, with what this boot has so far
#   state.sh save                 from kiwi-server apply: what it put into /etc/ssh, kept like the keys
#   state.sh status               what is where
set -euo pipefail
LABEL=__LABEL__
MNT=__MOUNT__
IMAGE_CACHE=__IMAGE_CACHE__
KEEP_LOGS=__KEEP_LOGS__
SSH_KEEP="__SSH_KEEP__"     # the SSH CA's files beside the host keys and their certificate

say() { echo "kiwi-state: $*"; }
die() { echo "kiwi-state: $*" >&2; exit 1; }

binds() { # NAME PATH, one per line: a directory on the partition and where it goes
    echo "kiwi-server /var/lib/kiwi-server"
    if [[ -s $MNT/stack-dir ]]; then echo "docker $(cat "$MNT/stack-dir")"; fi
    if (( IMAGE_CACHE )); then echo "containers /var/lib/containers"; fi
    if (( KEEP_LOGS )); then echo "journal /var/log/journal"; fi
}

keys() { # the SSH host keys stay, so the fleet's known_hosts does
    install -d -m 0700 "$MNT/ssh"
    if compgen -G "$MNT/ssh/ssh_host_*_key" >/dev/null; then
        cp -p "$MNT"/ssh/ssh_host_* /etc/ssh/
        if [[ -d $MNT/ssh/etc ]]; then cp -a "$MNT/ssh/etc/." /etc/ssh/; fi
        restorecon -RF /etc/ssh 2>/dev/null || true
    else
        ssh-keygen -A >/dev/null
        cp -p /etc/ssh/ssh_host_* "$MNT/ssh/"
    fi
}

place() { # every directory of the partition over the one it stands for
    local name path
    keys
    while read -r name path; do
        mkdir -p "$MNT/$name" "$path"
        mountpoint -q "$path" && continue
        # a bound directory carries its own SELinux label: the one the system gave the original
        chcon --reference="$path" "$MNT/$name" 2>/dev/null || true
        if [[ $name == journal ]]; then
            journalctl --relinquish-var 2>/dev/null || true
            mount --bind "$MNT/$name" "$path"
            journalctl --flush 2>/dev/null || true
        else
            mount --bind "$MNT/$name" "$path"
        fi
    done < <(binds)
    # the system is new at every boot: the role runs again (kiwi-role.service)
    rm -f /var/lib/kiwi-server/role.done
}

device() { blkid -L "$LABEL" 2>/dev/null; }

do_mount() {
    local dev
    udevadm settle --timeout=30 || true
    if ! dev=$(device); then
        say "no partition labelled $LABEL: this boot keeps nothing. kiwi-server apply makes it on disk:"
        return 0
    fi
    mkdir -p "$MNT"
    mountpoint -q "$MNT" || mount -o noatime "$dev" "$MNT"
    place
    say "$dev in place: $(binds | awk '{print $2}' | tr '\n' ' ')"
}

booted_from() { # DISK: true when the medium this system started from is on it
    local src parent
    src=$(findmnt -no SOURCE /run/media/iso 2>/dev/null) || return 1
    [[ $src == "$1" ]] && return 0
    parent=$(lsblk -no PKNAME "$src" 2>/dev/null | head -1)
    [[ -n $parent && /dev/$parent == "$(readlink -f "$1")" ]]
}

do_init() {
    local disk=$1 stack=$2 part p
    [[ -n $disk && -n $stack ]] || die "usage: state.sh init DISK STACK_DIR"
    if device >/dev/null; then
        mkdir -p "$MNT"
        mountpoint -q "$MNT" || mount -o noatime "$(device)" "$MNT"
        if [[ ! -s $MNT/stack-dir ]]; then echo "$stack" > "$MNT/stack-dir"; fi
        if [[ $(cat "$MNT/stack-dir") != "$stack" ]]; then
            die "the partition holds the stack for $(cat "$MNT/stack-dir"), the role says $stack: move it by hand first"
        fi
        place
        say "$(device) is there already"
        return 0
    fi
    [[ -b $disk ]] || die "$disk is not a disk here (lsblk): set disk: in the fleet file"
    booted_from "$disk" && die "$disk holds the medium this system booted from"
    if lsblk -lno MOUNTPOINTS "$disk" 2>/dev/null | grep -q .; then die "something on $disk is mounted"; fi
    say "wiping $disk for the partition $LABEL"
    for p in $(lsblk -lnpo NAME,TYPE "$disk" | awk '$2 == "part" {print $1}'); do wipefs -a -q "$p"; done
    wipefs -a -q "$disk"
    printf 'label: gpt\nname=%s\n' "$LABEL" | sfdisk -q --wipe always --wipe-partitions always "$disk"
    udevadm settle --timeout=30 || true
    part=$(lsblk -lnpo NAME,TYPE "$disk" | awk '$2 == "part" {print $1; exit}')
    [[ -n $part ]] || die "$disk has no partition after sfdisk"
    mkfs.xfs -q -f -L "$LABEL" "$part"
    udevadm settle --timeout=30 || true
    mkdir -p "$MNT"
    mount -o noatime "$part" "$MNT"
    echo "$stack" > "$MNT/stack-dir"
    # what this boot has so far goes onto it before the binds cover it
    mkdir -p "$MNT/kiwi-server" "$MNT/docker"
    if [[ -d /var/lib/kiwi-server ]]; then cp -a /var/lib/kiwi-server/. "$MNT/kiwi-server/"; fi
    if [[ -d $stack ]]; then cp -a "$stack/." "$MNT/docker/"; fi
    install -d -m 0700 "$MNT/ssh"
    cp -p /etc/ssh/ssh_host_* "$MNT/ssh/"
    place
    say "$part made and in place"
}

do_save() {
    local f
    mountpoint -q "$MNT" || { say "no state partition: nothing kept"; return 0; }
    install -d -m 0700 "$MNT/ssh"
    cp -p /etc/ssh/ssh_host_* "$MNT/ssh/"
    for f in $SSH_KEEP; do
        [[ -e $f ]] || continue
        mkdir -p "$(dirname "$MNT/ssh/etc/${f#/etc/ssh/}")"
        cp -p "$f" "$MNT/ssh/etc/${f#/etc/ssh/}"
    done
}

do_status() {
    local name path
    if ! device >/dev/null; then say "no partition labelled $LABEL"; return 0; fi
    say "$(device) at $MNT, $(df -h --output=used,size "$MNT" 2>/dev/null | tail -1 | xargs) used"
    while read -r name path; do
        if mountpoint -q "$path"; then say "  $path <- $name/"; else say "  $path: not bound"; fi
    done < <(binds)
}

case ${1:-} in
    mount)  do_mount ;;
    init)   do_init "${2:-}" "${3:-}" ;;
    save)   do_save ;;
    status) do_status ;;
    *)      die "usage: state.sh mount | init DISK STACK_DIR | save | status" ;;
esac
"""


def settings(host):
    r = dict(host.cfg.get("ram") or {})
    return {k: bool(r.get(k)) for k in ("copy_to_ram", "image_cache", "keep_logs")}


def state_script(host):
    s = settings(host)
    return (STATE_SH.replace("__LABEL__", LABEL).replace("__MOUNT__", MOUNT)
            .replace("__IMAGE_CACHE__", "1" if s["image_cache"] else "0")
            .replace("__KEEP_LOGS__", "1" if s["keep_logs"] else "0")
            .replace("__SSH_KEEP__", " ".join((sshca.USER_CA, sshca.DROP_IN))))


def state_unit():
    return "\n".join([
        "[Unit]",
        "Description=kiwi-server: this master's state partition (%s)" % LABEL,
        "Documentation=https://github.com/derlocke-ng/kiwi-server/blob/main/docs/design/ram-master.md",
        # the host keys are in place before the generators look: they start together otherwise
        "Before=%s sshd.service kiwi-role.service" % " ".join("sshd-keygen@%s.service" % k for k in ("rsa", "ecdsa", "ed25519")),
        "",
        "[Service]",
        "Type=oneshot",
        "RemainAfterExit=yes",
        "ExecStart=/usr/bin/bash %s mount" % STATE_SCRIPT,
        "StandardOutput=journal+console",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ])


def journald_conf(host):
    if settings(host)["keep_logs"]:
        return "[Journal]\nStorage=persistent\nSystemMaxUse=256M\n"
    # in RAM only, and not much of it: gone at every reboot
    return "[Journal]\nStorage=volatile\nRuntimeMaxUse=64M\n"


def build_butane(host):
    """The live system's Ignition: the base system, the state partition, the role unit.
    No role script: that one is on the partition, put there by apply."""
    co = host.cfg["coreos"]
    files, dirs, links, units = [], [], [], []
    user = coreos_t.admin_user(host)
    coreos_t.base_system(host, files, links)
    files.append(coreos_t._file("/etc/systemd/journald.conf.d/50-kiwi-ram.conf", journald_conf(host)))
    files.append(coreos_t._file(STATE_SCRIPT, state_script(host), mode=0o700))
    units.append({"name": "kiwi-state.service", "enabled": True, "contents": state_unit()})
    units.append({"name": "kiwi-role.service", "enabled": True,
                  "contents": role_unit(host, extra_conditions=[ROLE_SCRIPT],
                                        after=("network-online.target", "kiwi-state.service"))})
    dirs.append({"path": ROLE_DIR, "mode": 0o700})
    coreos_t.passthrough(co, files, dirs, units)
    return coreos_t.assemble(user, files, dirs, links, units)


def render(host):
    return coreos_t.to_yaml(build_butane(host))


def live_kargs(host):
    co = host.cfg["coreos"]
    kargs = [FROMRAM] if settings(host)["copy_to_ram"] else []
    kargs += [str(k) for k in (co.get("kernel_arguments") or [])]
    if co.get("console"):
        kargs.append("console=%s" % co["console"])
    return kargs


def iso(tc, host, ign_path, out_iso, cache_dir):
    """The stock live ISO with this host's live Ignition: the whole system, no installer."""
    from .. import iso as isomod
    base = isomod.fcos_base_iso(tc, host.cfg["coreos"]["stream"], cache_dir)
    argv = ["coreos-installer", "iso", "customize", "--live-ignition", ign_path]
    for k in live_kargs(host):
        argv += ["--live-karg-append", k]
    argv += ["-o", out_iso, base]
    if os.path.exists(out_iso):
        os.unlink(out_iso)   # coreos-installer will not replace an existing output file
    tc.run(argv, mounts=[util.parent(ign_path), util.parent(out_iso), cache_dir])
    return out_iso


def stack_dir(host):
    return str((host.role_settings or {}).get("docker_dir") or DEFAULT_STACK_DIR)


def prepare(host, tgt, runner=subprocess.run):
    """Before apply sends the role: the state partition, made on disk: when it is not
    there, so the role script, the WireGuard identity and the stack land on it."""
    disk = host.cfg.get("disk")
    if not disk:
        raise KiwiError("%s: coreos-ram keeps its state on disk:, which is not set" % host.name)
    out = remote.run(tgt, "bash %s init %s %s" % (STATE_SCRIPT, shlex.quote(str(disk)), shlex.quote(stack_dir(host))),
                     runner=runner)
    return out.decode(errors="replace").strip()


def save(host, tgt, runner=subprocess.run):
    """After apply wrote into /etc/ssh (the SSH CA): onto the partition, or gone at the next boot.
    An image from before `save` existed says so, and the apply goes on."""
    try:
        remote.run(tgt, "bash %s save" % STATE_SCRIPT, runner=runner)
    except KiwiError as e:
        util.warn("%s: the SSH CA's files are not on the state partition and go at the next boot: %s" % (host.name, e))


def problems(host):
    """What cannot work on this target, as validate errors."""
    c = host.cfg
    errs = []
    r = c.get("ram") or {}
    for k in ("copy_to_ram", "image_cache", "keep_logs"):
        if not isinstance(r.get(k), bool):
            errs.append("ram.%s must be true or false" % k)
    if (c.get("unlock") or {}).get("rings"):
        errs.append("unlock.rings: a coreos-ram master has no encrypted disk to unlock")
    for k in ("luks", "boot_device"):
        if c["coreos"].get(k):
            errs.append("coreos.%s: a coreos-ram master installs nothing to a disk" % k)
    return errs
