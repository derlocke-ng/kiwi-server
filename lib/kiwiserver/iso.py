"""Unattended install media.

CoreOS / uCore: the stock live ISO plus `coreos-installer iso customize`,
which makes the ISO install to the given disk, embed the host's Ignition into
the installed system and reboot — no prompts, no network needed for the
install itself.

Debian: the stock netinst ISO, with the preseed appended to the installer's
initrd (so it is honoured from the very first question) and the boot menus
rewritten to start the unattended entry after one second. Only the files that
change are extracted and mapped back in; `-boot_image any replay` keeps the
original El Torito / isohybrid boot setup, so the result boots on BIOS and UEFI
exactly like the stock image.
"""
import hashlib
import json
import os
import re
import secrets
import shutil
from urllib.parse import urlparse

from . import util
from .util import KiwiError
from .targets import debian as debian_target

DEBIAN_BASE = "https://cdimage.debian.org/debian-cd/current/%s/iso-cd/"
INSTALL_DIR = {"amd64": "/install.amd", "arm64": "/install.a64"}   # where each netinst keeps its installer


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---- Fedora CoreOS -----------------------------------------------------------------

# The LAN's cache of install images (the cache module, files: cache.<hostname>/files/<server>/<path>),
# set by the CLI when the fleet has one: {"name", "address", "ca"} (ca: a copy of the fleet CA's
# certificate inside the image folder, so the toolchain sees it). The owner, 22 Nov 2026: one
# download for the LAN, at the LAN's speed.
LAN_CACHE = None


def through_cache(tc, url, path, cache_dir):
    """`url` fetched to `path` from the LAN's cache, when there is one and it answers: True. Each
    image is checked where it is used (a signature, a checksum from its own server), so the copy
    needs no trust of its own. False: nothing there, the caller fetches it from its server."""
    c = LAN_CACHE
    if not c:
        return False
    u = urlparse(url)
    via = "https://%s/files/%s%s" % (c["name"], u.netloc, u.path)
    r = tc.run(["curl", "-fL", "--retry", "2", "--connect-timeout", "3", "--resolve", "%s:443:%s" % (c["name"], c["address"]),
                "--cacert", c["ca"], "-o", path + ".part", via], mounts=[cache_dir], capture=True, check=False)
    if r.returncode != 0:
        if os.path.exists(path + ".part"):
            os.unlink(path + ".part")
        return False
    os.replace(path + ".part", path)
    util.say("%s from the LAN's cache (%s)" % (os.path.basename(path), c["name"]))
    return True


def _fcos_from_cache(tc, stream, cache_dir):
    """The stream's metal ISO and its signature into `cache_dir` from the LAN's cache, where
    coreos-installer download finds them and checks Fedora's signature as for its own."""
    if not LAN_CACHE:
        return
    r = tc.run(["curl", "-fsSL", "--retry", "2", "https://builds.coreos.fedoraproject.org/streams/%s.json" % stream],
               mounts=[cache_dir], capture=True, check=False)
    try:
        iso = json.loads(r.stdout)["architectures"]["x86_64"]["artifacts"]["metal"]["formats"]["iso"]["disk"]
    except (ValueError, KeyError, TypeError):
        return
    path = os.path.join(cache_dir, os.path.basename(urlparse(iso["location"]).path))
    if os.path.isfile(path) and os.path.isfile(path + ".sig"):
        return
    if through_cache(tc, iso["signature"], path + ".sig", cache_dir) and not through_cache(tc, iso["location"], path, cache_dir):
        os.unlink(path + ".sig")


def fcos_base_iso(tc, stream, cache_dir):
    """coreos-installer download verifies the Fedora signature and prints the
    path; an already present, valid file is reused (one the LAN's cache gave too)."""
    os.makedirs(cache_dir, exist_ok=True)
    _fcos_from_cache(tc, stream, cache_dir)
    r = tc.run(["coreos-installer", "download", "-s", stream, "-p", "metal", "-f", "iso",
                "-C", cache_dir, "--fetch-retries", "3"], mounts=[cache_dir], capture=True)
    lines = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
    if not lines or not os.path.isfile(lines[-1]):
        raise KiwiError("coreos-installer download did not report an ISO path:\n%s" % (r.stdout or r.stderr))
    return lines[-1]


# ---- a stick left in the machine must not install it again ---------------------------
# Install media wipe the disk without asking. Left in a machine whose firmware starts from them
# first, they wiped it again at every start. So every install media has an id of its own (a
# new one each time it is made), the installer leaves it on the installed system's boot
# partition (MARK), and before anything is erased the installer looks for it there (the guard).
# Found: nothing is installed. On UEFI the disk is put first and the machine restarts into it;
# elsewhere it says so and powers off. To install again on purpose: make the media again (a new
# id), or add kiwi.reinstall to the kernel line at the media's boot menu.
MARK = "kiwi-installed"


def media_id():
    return secrets.token_hex(8)


GUARD_SCRIPT = r"""#!/bin/bash
# kiwi-server: the guard. This install media installed this machine already: not again.
kiwi_guard() {
    local id=@ID@ src parent live p m f hit= disk esp num label arch entry t
    case " $(cat /proc/cmdline) " in *" kiwi.reinstall "*) return 0 ;; esac
    udevadm settle --timeout=30 2>/dev/null || true
    src=$(findmnt -no SOURCE /run/media/iso 2>/dev/null || true)
    if [ -n "$src" ]; then parent=$(lsblk -no PKNAME "$src" 2>/dev/null | head -1); live=${parent:+/dev/$parent}; live=${live:-$src}; fi
    m=$(mktemp -d)
    while read -r p; do
        [ -n "$p" ] || continue
        [ "/dev/$(lsblk -no PKNAME "$p" 2>/dev/null | head -1)" = "${live:-none}" ] && continue
        mount -o ro "$p" "$m" 2>/dev/null || continue
        for f in "$m/@MARK@" "$m/boot/@MARK@"; do
            if [ -f "$f" ] && grep -q " $id " "$f"; then hit=$(head -n 1 "$f"); disk=/dev/$(lsblk -no PKNAME "$p" | head -1); fi
        done
        umount "$m" 2>/dev/null || true
        [ -n "$hit" ] && break
    done < <(lsblk -lnpo NAME,FSTYPE | awk '$2 == "ext4" { print $1 }')
    rmdir "$m" 2>/dev/null || true
    [ -n "$hit" ] || return 0
    say() { for t in /dev/console /dev/tty1 /dev/ttyS0; do printf '%s\n' "$1" > "$t" 2>/dev/null || true; done; }
    say ""
    say "kiwi-server: @HOST@ is installed already (${hit##* }), by this install media: nothing is installed again."
    say "Take the install media out. To install it again on purpose: make the install media again."
    if [ -d "${KIWI_EFIVARS:-/sys/firmware/efi/efivars}" ] && command -v efibootmgr >/dev/null 2>&1; then
        # UEFI: the disk first from now on, so the machine starts by itself with the media left in
        label="kiwi-server disk"
        case "$(uname -m)" in aarch64) arch=aa64 ;; *) arch=x64 ;; esac
        esp=$(lsblk -lnpo NAME,PARTTYPE "$disk" | awk 'tolower($2) == "c12a7328-f81f-11d2-ba4b-00a0c93ec93b" { print $1; exit }')
        entry=$(efibootmgr 2>/dev/null | sed -n "s/^Boot\([0-9A-Fa-f]\{4\}\)[* ] *$label.*/\1/p" | head -n 1)
        if [ -z "$entry" ] && [ -n "$esp" ]; then
            num=$(cat "/sys/class/block/${esp##*/}/partition" 2>/dev/null)
            efibootmgr -q -c -d "$disk" -p "$num" -L "$label" -l "\EFI\fedora\shim$arch.efi" 2>/dev/null                 && entry=$(efibootmgr 2>/dev/null | sed -n "s/^Boot\([0-9A-Fa-f]\{4\}\)[* ] *$label.*/\1/p" | head -n 1)
        fi
        if [ -n "$entry" ] && efibootmgr -q -n "$entry" 2>/dev/null; then
            say "It starts from its disk now."
            sleep 5
            systemctl --no-block reboot
            exit 1
        fi
    fi
    say "It is switched off in a minute: take the media out, then switch it on."
    sleep 60
    systemctl --no-block poweroff
    exit 1
}
kiwi_guard
"""

MARK_SCRIPT = r"""#!/bin/bash
# kiwi-server: the install is done. This media's id goes onto the installed system's boot
# partition: the guard finds it there when the media is started in this machine again.
kiwi_mark() {
    local src parent live p m
    udevadm settle --timeout=30 2>/dev/null || true
    src=$(findmnt -no SOURCE /run/media/iso 2>/dev/null || true)
    if [ -n "$src" ]; then parent=$(lsblk -no PKNAME "$src" 2>/dev/null | head -1); live=${parent:+/dev/$parent}; live=${live:-$src}; fi
    m=$(mktemp -d)
    while read -r p; do
        [ -n "$p" ] || continue
        [ "/dev/$(lsblk -no PKNAME "$p" 2>/dev/null | head -1)" = "${live:-none}" ] && continue
        if mount "$p" "$m" 2>/dev/null; then
            printf '%s %s %s\n' "@HOST@" "@ID@" "$(date -u +%Y-%m-%dT%H:%MZ)" > "$m/@MARK@" && echo "kiwi-server: marked $p as installed by this media"
            umount "$m" 2>/dev/null || true
        fi
    done < <(lsblk -lnpo NAME,FSTYPE,LABEL | awk '$2 == "ext4" && $3 == "boot" { print $1 }')
    rmdir "$m" 2>/dev/null || true
}
kiwi_mark
"""


def guard_script(host, mid):
    return GUARD_SCRIPT.replace("@ID@", mid).replace("@HOST@", host.name).replace("@MARK@", MARK)


def mark_script(host, mid):
    return MARK_SCRIPT.replace("@ID@", mid).replace("@HOST@", host.name).replace("@MARK@", MARK)


POWEROFF_SCRIPT = """#!/bin/bash
# kiwi-server: the install is done — turn the machine off so the install media
# can be removed before it boots again (install.on_done: poweroff)
echo "kiwi-server: installed — powering off; remove the install media, then power on"
sleep 3
systemctl poweroff
"""


# disk: auto on CoreOS. Run by the live system before the installer starts: the machine's one
# disk that is not the install media becomes the target; with none or several the install
# stops and the console says which disks there are.
# The disk is encrypted with the machine's TPM standing in for the rings (docs/design/encryption.md):
# without a TPM 2.0, or with less memory than encrypting a CoreOS root takes at the first boot, the
# install stops here with what to do, instead of a first boot that fails
TPM_CHECK_SCRIPT = r"""#!/bin/bash
set -u
why=
if [ "$(cat /sys/class/tpm/tpm0/tpm_version_major 2>/dev/null)" != 2 ]; then
    why="it has no TPM 2.0 chip that is switched on (in the firmware settings: TPM, fTPM or PTT)"
elif [ "$(awk '/^MemTotal:/ { print $2 }' /proc/meminfo)" -lt 3500000 ]; then
    why="it has less than 4 GB of memory, which encrypting its disk takes at the first start"
fi
if [ -n "$why" ]; then
    for t in /dev/console /dev/tty1 /dev/ttyS0; do
        printf '\nkiwi-server: @HOST@ is not installed: its disk cannot be encrypted here: %s.\nSwitch the TPM on and start again from this stick, or set encryption: false for @HOST@ in the fleet file (installed WITHOUT encryption) and build the stick again.\n' "$why" > "$t" 2>/dev/null || true
    done
    exit 1
fi
"""

AUTO_DISK_SCRIPT = r"""#!/bin/bash
set -u
src=$(findmnt -no SOURCE /run/media/iso 2>/dev/null || true)
live=
if [ -n "$src" ]; then
    parent=$(lsblk -no PKNAME "$src" 2>/dev/null | head -1)
    live=${parent:+/dev/$parent}
    live=${live:-$src}
fi
# a disk on USB is never chosen by itself: a backup disk left plugged in must not be erased
# when the machine's own disk is not seen (set disk: to install on a USB disk on purpose)
mapfile -t disks < <(lsblk -dnbpo NAME,TYPE,RM,RO,SIZE,TRAN | awk -v live="$live" \
    '$2=="disk" && $3==0 && $4==0 && $5>=8000000000 && $1!=live && $1!~/zram|loop/ && $6!="usb" {print $1}')
if [ "${#disks[@]}" -ne 1 ]; then
    {
        echo
        echo "kiwi-server: the install stopped. disk: auto needs a machine with one disk (USB disks do not count); this one has ${#disks[@]}:"
        lsblk -d -o NAME,SIZE,MODEL,TRAN
        echo "Set the disk in the machine's settings (like /dev/nvme0n1) and make the install media again."
    } > /dev/console
    exit 1
fi
mkdir -p /etc/coreos/installer.d
printf 'dest-device: %s\n' "${disks[0]}" > /etc/coreos/installer.d/0090-kiwi-disk.yaml
echo "kiwi-server: installing to ${disks[0]}" > /dev/console
"""


def live_butane(host):
    """The live installer environment: root and the admin user with the fleet's
    console password and keys, so a stopped install has a shell, and the
    machine's disks printed on the console before the installer looks for
    `disk:` (the live ISO's own root is locked and no user exists otherwise)."""
    from .targets import coreos as coreos_t
    users = []
    ph = host.password_hash()
    keys = host.ssh_keys()
    if ph:
        users.append({"name": "root", "password_hash": ph})
    admin = {"name": host.admin["user"]}
    if ph:
        admin["password_hash"] = ph
    if keys:
        admin["ssh_authorized_keys"] = keys
    users.append(admin)
    unit = "\n".join([
        "[Unit]",
        "Description=kiwi-server: this machine's disks, for the console",
        "DefaultDependencies=no",
        "After=systemd-udev-settle.service",
        "Before=coreos-installer.service",
        "[Service]",
        "Type=oneshot",
        "StandardOutput=tty",
        "TTYPath=/dev/console",
        "ExecStart=/bin/sh -c 'echo \"kiwi-server: the fleet file says disk: %s — the disks here:\"; lsblk -d -o NAME,SIZE,MODEL,TRAN'"
        % (host.cfg.get("disk") or "?"),
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ])
    return coreos_t.to_yaml({"variant": coreos_t.BUTANE_VARIANT, "version": coreos_t.BUTANE_VERSION,
                             "passwd": {"users": users},
                             "systemd": {"units": [{"name": "kiwi-live-disks.service", "enabled": True, "contents": unit}]}})


def coreos_iso(tc, host, ign_path, out_iso, cache_dir):
    disk = host.cfg.get("disk")
    if not disk:
        raise KiwiError("%s: disk: is required to build an unattended ISO" % host.name)
    base = fcos_base_iso(tc, host.cfg["coreos"]["stream"], cache_dir)
    argv = ["coreos-installer", "iso", "customize", "--dest-ignition", ign_path]
    mid = media_id()
    pre_text = guard_script(host, mid)          # first of all: this media installed the machine already?
    if getattr(host, "unlock_standin", False):
        pre_text += TPM_CHECK_SCRIPT.replace("@HOST@", host.name).split("\n", 1)[1]
    if disk == "auto":
        pre_text += AUTO_DISK_SCRIPT.split("\n", 1)[1]
    else:
        argv += ["--dest-device", disk]
    pre = os.path.join(util.parent(ign_path), host.name + ".pre-install.sh")
    util.write_text(pre, pre_text, mode=0o700)
    argv += ["--pre-install", pre]
    co = host.cfg["coreos"]
    if co.get("console"):
        argv += ["--dest-console", str(co["console"])]
    for k in co.get("kernel_arguments") or []:
        argv += ["--dest-karg-append", str(k)]
    post = os.path.join(util.parent(ign_path), host.name + ".post-install.sh")
    post_text = mark_script(host, mid)
    if (host.cfg.get("install") or {}).get("on_done", "poweroff") == "poweroff":
        # the live system would reboot into the installed disk — with the stick
        # or the provider's ISO still first in the boot order, that boots the
        # media again (the guard then stops it). Skip the reboot and power off
        # from the post-install script instead, so the media can be taken out.
        post_text += POWEROFF_SCRIPT.split("\n", 1)[1]
        argv += ["--live-karg-append", "coreos.inst.skip_reboot"]
    util.write_text(post, post_text, mode=0o700)
    argv += ["--post-install", post]
    from .targets import coreos as coreos_t
    live_bu = os.path.join(util.parent(ign_path), host.name + ".live.bu")
    live_ign = os.path.join(util.parent(ign_path), host.name + ".live.ign")
    util.write_text(live_bu, live_butane(host), mode=0o600)
    coreos_t.to_ignition(tc, live_bu, live_ign)
    argv += ["--live-ignition", live_ign]
    argv += ["-f", "-o", out_iso, base]
    if os.path.exists(out_iso):
        os.unlink(out_iso)   # coreos-installer will not replace an existing output file, -f or not
    tc.run(argv, mounts=[util.parent(ign_path), util.parent(out_iso), cache_dir])
    if os.path.isfile(out_iso):
        os.chmod(out_iso, 0o600)   # it carries the Ignition config and every secret in it
    util.write_text(out_iso + ".id", mid + "\n", 0o644)     # which media these are (vps install: not the same twice)
    return out_iso


# ---- Debian --------------------------------------------------------------------------

def resolve_netinst(sha256sums_text, arch="amd64"):
    """(filename, sha256) of the netinst for `arch` listed in a SHA256SUMS file."""
    for line in sha256sums_text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].endswith("-%s-netinst.iso" % arch):
            return parts[1], parts[0]
    raise KiwiError("no *-%s-netinst.iso in SHA256SUMS" % arch)


CURRENT_RELEASES = ("stable", "trixie")   # what debian-cd/current/ holds


def debian_base_iso(tc, cfg, cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    arch = cfg.get("arch") or "amd64"
    base = DEBIAN_BASE % arch
    url = cfg.get("iso_url")
    if url:
        name, sha = os.path.basename(url), None
    elif str(cfg.get("release") or "stable") not in CURRENT_RELEASES:
        raise KiwiError("debian.release is %s but only the current stable (%s) is at %s — "
                        "set debian.iso_url to that release's netinst image"
                        % (cfg.get("release"), "/".join(CURRENT_RELEASES), base))
    else:
        r = tc.run(["curl", "-fsSL", "--retry", "3", base + "SHA256SUMS"],
                   mounts=[cache_dir], capture=True)
        name, sha = resolve_netinst(r.stdout, arch)
        url = base + name
    path = os.path.join(cache_dir, name)
    if os.path.isfile(path) and (sha is None or util.sha256_file(path) == sha):
        util.say("using cached %s" % name)
        return path
    if through_cache(tc, url, path, cache_dir) and (sha is None or util.sha256_file(path) == sha):
        return path         # checked against the SHA256SUMS of Debian's own server
    util.say("downloading %s" % url)
    tc.run(["curl", "-fL", "--retry", "3", "-o", path + ".part", url], mounts=[cache_dir])
    if sha and util.sha256_file(path + ".part") != sha:
        os.unlink(path + ".part")
        raise KiwiError("checksum mismatch for %s — download corrupted?" % name)
    os.replace(path + ".part", path)
    return path


def _extract(tc, iso, iso_path, dest, required=True):
    r = tc.run(["xorriso", "-osirrox", "on", "-indev", iso, "-extract", iso_path, dest],
               mounts=[util.parent(iso), util.parent(dest)], capture=True, check=False)
    if r.returncode != 0 or not os.path.isfile(dest):
        if required:
            raise KiwiError("%s: cannot extract %s from the ISO" % (os.path.basename(iso), iso_path))
        return False
    os.chmod(dest, 0o644)
    return True


def rewrite_isolinux_cfg(text):
    """Boot our label directly after one second — no menu, no prompt."""
    out = []
    for ln in text.splitlines():
        if re.match(r"^\s*(default|timeout|prompt)\b", ln, re.I):
            continue
        out.append(ln)
    return "\n".join(out + ["default kiwi-auto", "prompt 0", "timeout 10", ""])


def auto_txt_cfg(original, title, inst="/install.amd"):
    entry = [
        "label kiwi-auto",
        "    menu label ^%s" % title,
        "    kernel %s/vmlinuz" % inst,
        "    append vga=788 initrd=%s/initrd.gz %s --- quiet" % (inst, debian_target.KERNEL_ARGS),
        "",
    ]
    return "\n".join(entry) + original


def auto_grub_cfg(original, title, inst="/install.amd"):
    body = [ln for ln in original.splitlines()
            if not re.match(r"^\s*set\s+(default|timeout)\s*=", ln)]
    entry = [
        "",
        "menuentry '%s' --id kiwi-auto {" % title,
        "    set background_color=black",
        "    linux    %s/vmlinuz%s %s --- quiet" % (inst, " vga=788" if inst == "/install.amd" else "",
                                                   debian_target.KERNEL_ARGS),
        "    initrd   %s/initrd.gz" % inst,
        "}",
        "",
    ]
    return "set default=kiwi-auto\nset timeout=1\n" + "\n".join(body) + "\n" + "\n".join(entry)


def update_md5sums(text, changed):
    """changed: iso path ('./install.amd/initrd.gz') -> local file."""
    lines = [ln for ln in text.splitlines()
             if not any(ln.rstrip().endswith(" " + p) for p in changed)]
    for p, local in sorted(changed.items()):
        lines.append("%s  %s" % (_md5(local), p))
    return "\n".join(lines) + "\n"


def debian_iso(tc, host, preseed_path, files_dir, out_iso, base_iso, workdir):
    if not host.cfg.get("disk"):
        raise KiwiError("%s: disk: is required to build an unattended ISO" % host.name)
    if os.path.isdir(workdir):
        shutil.rmtree(workdir)
    os.makedirs(workdir, mode=0o700)
    # this media's own id (a new one each time it is made): the installer leaves it on the installed
    # system, and its guard looks for it before anything is erased
    mid = media_id()
    util.write_text(os.path.join(files_dir, "media-id"), mid + "\n", 0o644)
    try:
        _debian_iso(tc, host, preseed_path, files_dir, out_iso, base_iso, workdir)
    finally:
        # the work directory holds a copy of the preseed (password hash, LUKS passphrase)
        shutil.rmtree(workdir, ignore_errors=True)
    if os.path.isfile(out_iso):
        os.chmod(out_iso, 0o600)
    util.write_text(out_iso + ".id", mid + "\n", 0o644)     # which media these are (vps install: not the same twice)
    return out_iso


def _debian_iso(tc, host, preseed_path, files_dir, out_iso, base_iso, workdir):
    title = "Install %s (kiwi-server, unattended: wipes %s)" % (
        host.hostname, "its disk" if host.cfg["disk"] == "auto" else host.cfg["disk"])

    inst = INSTALL_DIR[host.cfg["debian"].get("arch") or "amd64"]
    initrd_gz = os.path.join(workdir, "initrd.gz")
    _extract(tc, base_iso, inst + "/initrd.gz", initrd_gz)
    grub = os.path.join(workdir, "grub.cfg")
    _extract(tc, base_iso, "/boot/grub/grub.cfg", grub)
    isolinux = os.path.join(workdir, "isolinux.cfg")
    has_isolinux = _extract(tc, base_iso, "/isolinux/isolinux.cfg", isolinux, required=False)
    txt = os.path.join(workdir, "txt.cfg")
    if has_isolinux:
        _extract(tc, base_iso, "/isolinux/txt.cfg", txt, required=False) or util.write_text(txt, "")
    md5 = os.path.join(workdir, "md5sum.txt")
    has_md5 = _extract(tc, base_iso, "/md5sum.txt", md5, required=False)

    # preseed.cfg at the root of the initrd is read before any question is asked
    tc.run(["gzip", "-d", "-f", initrd_gz], mounts=[workdir])
    initrd = os.path.join(workdir, "initrd")
    util.write_text(os.path.join(workdir, "preseed.cfg"), util.read_text(preseed_path), 0o600)
    tc.run(["sh", "-c", "echo preseed.cfg | cpio -H newc -o -A -F initrd"], mounts=[workdir], cwd=workdir)
    tc.run(["gzip", "-9", "-f", initrd], mounts=[workdir])

    util.write_text(grub, auto_grub_cfg(util.read_text(grub), title, inst))
    maps = {inst + "/initrd.gz": initrd_gz, "/boot/grub/grub.cfg": grub,
            "/preseed.cfg": preseed_path, "/kiwi-server": files_dir}
    if has_isolinux:
        util.write_text(isolinux, rewrite_isolinux_cfg(util.read_text(isolinux)))
        util.write_text(txt, auto_txt_cfg(util.read_text(txt), title, inst))
        maps["/isolinux/isolinux.cfg"] = isolinux
        maps["/isolinux/txt.cfg"] = txt
    if has_md5:
        changed = {"./" + k.lstrip("/"): v for k, v in maps.items() if os.path.isfile(v)}
        for root, _dirs, files in os.walk(files_dir):
            for f in files:
                local = os.path.join(root, f)
                changed["./kiwi-server/" + os.path.relpath(local, files_dir)] = local
        util.write_text(md5, update_md5sums(util.read_text(md5), changed))
        maps["/md5sum.txt"] = md5

    argv = ["xorriso", "-indev", base_iso, "-outdev", out_iso, "-boot_image", "any", "replay"]
    for iso_path, local in maps.items():
        argv += ["-map", local, iso_path]
    argv += ["-end"]
    if os.path.exists(out_iso):
        os.unlink(out_iso)
    tc.run(argv, mounts=[util.parent(base_iso), util.parent(out_iso), workdir,
                         util.parent(preseed_path), files_dir])
