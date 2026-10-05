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
import os
import re
import shutil

from . import util
from .util import KiwiError
from .targets import debian as debian_target

DEBIAN_BASE = "https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/"


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---- Fedora CoreOS -----------------------------------------------------------------

def fcos_base_iso(tc, stream, cache_dir):
    """coreos-installer download verifies the Fedora signature and prints the
    path; an already present, valid file is reused."""
    os.makedirs(cache_dir, exist_ok=True)
    r = tc.run(["coreos-installer", "download", "-s", stream, "-p", "metal", "-f", "iso",
                "-C", cache_dir, "--fetch-retries", "3"], mounts=[cache_dir], capture=True)
    lines = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
    if not lines or not os.path.isfile(lines[-1]):
        raise KiwiError("coreos-installer download did not report an ISO path:\n%s" % (r.stdout or r.stderr))
    return lines[-1]


def coreos_iso(tc, host, ign_path, out_iso, cache_dir):
    disk = host.cfg.get("disk")
    if not disk:
        raise KiwiError("%s: disk: is required to build an unattended ISO" % host.name)
    base = fcos_base_iso(tc, host.cfg["coreos"]["stream"], cache_dir)
    argv = ["coreos-installer", "iso", "customize",
            "--dest-ignition", ign_path, "--dest-device", disk]
    co = host.cfg["coreos"]
    if co.get("console"):
        argv += ["--dest-console", str(co["console"])]
    for k in co.get("kernel_arguments") or []:
        argv += ["--dest-karg-append", str(k)]
    argv += ["-f", "-o", out_iso, base]
    tc.run(argv, mounts=[util.parent(ign_path), util.parent(out_iso), cache_dir])
    if os.path.isfile(out_iso):
        os.chmod(out_iso, 0o600)   # it carries the Ignition config and every secret in it
    return out_iso


# ---- Debian --------------------------------------------------------------------------

def resolve_netinst(sha256sums_text):
    """(filename, sha256) of the amd64 netinst listed in a SHA256SUMS file."""
    for line in sha256sums_text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].endswith("-amd64-netinst.iso"):
            return parts[1], parts[0]
    raise KiwiError("no *-amd64-netinst.iso in SHA256SUMS")


CURRENT_RELEASES = ("stable", "trixie")   # what debian-cd/current/ holds


def debian_base_iso(tc, cfg, cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    url = cfg.get("iso_url")
    if url:
        name, sha = os.path.basename(url), None
    elif str(cfg.get("release") or "stable") not in CURRENT_RELEASES:
        raise KiwiError("debian.release is %s but only the current stable (%s) is at %s — "
                        "set debian.iso_url to that release's netinst image"
                        % (cfg.get("release"), "/".join(CURRENT_RELEASES), DEBIAN_BASE))
    else:
        r = tc.run(["curl", "-fsSL", "--retry", "3", DEBIAN_BASE + "SHA256SUMS"],
                   mounts=[cache_dir], capture=True)
        name, sha = resolve_netinst(r.stdout)
        url = DEBIAN_BASE + name
    path = os.path.join(cache_dir, name)
    if os.path.isfile(path) and (sha is None or util.sha256_file(path) == sha):
        util.say("using cached %s" % name)
        return path
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


def auto_txt_cfg(original, title):
    entry = [
        "label kiwi-auto",
        "    menu label ^%s" % title,
        "    kernel /install.amd/vmlinuz",
        "    append vga=788 initrd=/install.amd/initrd.gz %s --- quiet" % debian_target.KERNEL_ARGS,
        "",
    ]
    return "\n".join(entry) + original


def auto_grub_cfg(original, title):
    body = [ln for ln in original.splitlines()
            if not re.match(r"^\s*set\s+(default|timeout)\s*=", ln)]
    entry = [
        "",
        "menuentry '%s' --id kiwi-auto {" % title,
        "    set background_color=black",
        "    linux    /install.amd/vmlinuz vga=788 %s --- quiet" % debian_target.KERNEL_ARGS,
        "    initrd   /install.amd/initrd.gz",
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
    try:
        _debian_iso(tc, host, preseed_path, files_dir, out_iso, base_iso, workdir)
    finally:
        # the work directory holds a copy of the preseed (password hash, LUKS passphrase)
        shutil.rmtree(workdir, ignore_errors=True)
    if os.path.isfile(out_iso):
        os.chmod(out_iso, 0o600)
    return out_iso


def _debian_iso(tc, host, preseed_path, files_dir, out_iso, base_iso, workdir):
    title = "Install %s (kiwi-server, unattended: wipes %s)" % (host.hostname, host.cfg["disk"])

    initrd_gz = os.path.join(workdir, "initrd.gz")
    _extract(tc, base_iso, "/install.amd/initrd.gz", initrd_gz)
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

    util.write_text(grub, auto_grub_cfg(util.read_text(grub), title))
    maps = {"/install.amd/initrd.gz": initrd_gz, "/boot/grub/grub.cfg": grub,
            "/preseed.cfg": preseed_path, "/kiwi-server": files_dir}
    if has_isolinux:
        util.write_text(isolinux, rewrite_isolinux_cfg(util.read_text(isolinux)))
        util.write_text(txt, auto_txt_cfg(util.read_text(txt), title))
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
