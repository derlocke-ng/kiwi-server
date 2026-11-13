"""Armbian on a board (Odroid, Banana Pi, ...): the board's own image from dl.armbian.com, with
what a first boot needs written into its root filesystem by debugfs — no installer, no
mount, no root on this computer. Flash the result to an SD card or the eMMC and boot:

  armbian-firstrun   grows the filesystem, makes new SSH host keys (Armbian's own)
  kiwi-adopt         the admin user with the fleet's keys and password, sshd and update
                     settings, root's password locked — the same script `kiwi-server adopt`
                     runs over SSH on a board that is already running
  kiwi-role          the role, as on every other target

Armbian's interactive first login is skipped (its marker file is removed), so nobody types
a root password at the console. The disk is not encrypted: that needs an image built with
Armbian's build framework (CRYPTROOT_ENABLE), which comes later.
"""
import json
import os
import shlex
import shutil

from .. import adopt as adoptmod
from .. import util
from ..util import KiwiError
from . import ROLE_DIR, ROLE_SCRIPT, role_unit

DL = "https://dl.armbian.com/%s/%s_%s_%s"
ADOPT_DIR = ROLE_DIR + "/adopt"
ADOPT_DONE = ROLE_DIR + "/adopt.done"
# GPT root (arm64, arm), GPT Linux data, MBR Linux
LINUX_TYPES = ("B921B045-1DF0-41C3-AF44-4C6F280D3FAE", "69DAD710-2CE4-4E3C-B16C-21A1D49ABED3",
               "0FC63DAF-8483-4772-8E79-3D69D8477DE4", "83")

AUTOLOGIN = ("/etc/systemd/system/getty@.service.d/override.conf",
             "/etc/systemd/system/serial-getty@.service.d/override.conf")

ADOPT_UNIT = """[Unit]
Description=Kiwi Server: the fleet's admin user and settings on this board (%(host)s)
After=network-online.target armbian-firstrun.service
Wants=network-online.target
ConditionPathExists=!%(done)s

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh %(dir)s/adopt.sh
ExecStartPost=/bin/touch %(done)s
Restart=on-failure
RestartSec=30
StandardOutput=journal+console
StandardError=journal+console

[Install]
WantedBy=multi-user.target
"""


def download_url(cfg):
    if cfg.get("image_url"):
        return cfg["image_url"]
    if not cfg.get("board"):
        raise KiwiError("armbian.board is not set (the board's name at dl.armbian.com, e.g. odroidn2)")
    return DL % (cfg["board"], str(cfg["release"]).capitalize(), cfg["branch"], cfg["variant"])


def base_image(tc, cfg, cache_dir):
    """The board's image (.img.xz), downloaded once and checked against Armbian's .sha file."""
    os.makedirs(cache_dir, exist_ok=True)
    url = download_url(cfg)
    r = tc.run(["curl", "-fsSL", "--retry", "3", "-o", "/dev/null", "-w", "%{url_effective}", url],
               mounts=[cache_dir], capture=True)
    url = (r.stdout or "").strip() or url
    name = os.path.basename(url)
    if not name.endswith(".img.xz"):
        raise KiwiError("%s is not an Armbian .img.xz (is armbian.board right?)" % url)
    sha = tc.run(["curl", "-fsSL", "--retry", "3", url + ".sha"], mounts=[cache_dir], capture=True).stdout.split()[0]
    path = os.path.join(cache_dir, name)
    if os.path.isfile(path) and util.sha256_file(path) == sha:
        util.say("using cached %s" % name)
        return path
    util.say("downloading %s" % url)
    tc.run(["curl", "-fL", "--retry", "3", "-o", path + ".part", url], mounts=[cache_dir])
    if util.sha256_file(path + ".part") != sha:
        os.unlink(path + ".part")
        raise KiwiError("checksum mismatch for %s — download corrupted?" % name)
    os.replace(path + ".part", path)
    return path


def files(host, role_script):
    """What goes into the root filesystem: absolute path -> (text, mode)."""
    out = {}
    for name, (text, mode) in adoptmod.bundle_files(host, lock_root=True).items():
        out["%s/%s" % (ADOPT_DIR, name)] = (text, mode)
    out[ROLE_SCRIPT] = (role_script, 0o700)
    out["/etc/systemd/system/kiwi-adopt.service"] = (
        ADOPT_UNIT % {"host": host.hostname, "dir": ADOPT_DIR, "done": ADOPT_DONE}, 0o644)
    out["/etc/systemd/system/kiwi-role.service"] = (
        role_unit(host, after=("network-online.target", "kiwi-adopt.service")), 0o644)
    return out


def root_partition(table):
    """(start, size) in sectors of the largest Linux partition in sfdisk's JSON."""
    parts = [p for p in table["partitions"] if str(p.get("type", "")).upper() in LINUX_TYPES]
    if not parts:
        raise KiwiError("no Linux partition in the image")
    p = max(parts, key=lambda p: p["size"])
    return p["start"], p["size"]


def debugfs_commands(entries, existing=()):
    """entries: absolute path -> local file. Directories first, then files, owned by root;
    the units enabled; Armbian's first-login wizard skipped."""
    dirs = sorted({os.path.dirname(p) for p in entries} | {ROLE_DIR})
    L, L_made = [], set()
    for d in dirs:
        parts = d.strip("/").split("/")
        for i in range(1, len(parts) + 1):
            sub = "/" + "/".join(parts[:i])
            # never one that exists: debugfs 1.47's mkdir of an existing directory still counts a
            # link on its parent, and the filesystem no longer checks clean (found on the N2+ image)
            if sub not in existing and sub not in L_made:
                L.append("mkdir %s" % sub)
                L_made.add(sub)
    for d in dirs:
        if d.startswith(ROLE_DIR):
            L += ["sif %s mode 040700" % d, "sif %s uid 0" % d, "sif %s gid 0" % d]
    for path, (local, mode) in sorted(entries.items()):
        L += ["rm %s" % path, "write %s %s" % (local, path),
              "sif %s mode 0%o" % (path, 0o100000 | mode), "sif %s uid 0" % path, "sif %s gid 0" % path]
    for unit in ("kiwi-adopt.service", "kiwi-role.service"):
        link = "/etc/systemd/system/multi-user.target.wants/" + unit
        L += ["rm %s" % link, "symlink %s /etc/systemd/system/%s" % (link, unit)]
    L.append("rm /root/.not_logged_in_yet")
    # Armbian logs root in on the consoles at first boot, for its wizard: nobody should be
    for getty in AUTOLOGIN:
        L.append("rm %s" % getty)
    return "\n".join(L) + "\n"


def image(tc, host, role_script, base_xz, out_img, workdir):
    """The flashable image: the board's Armbian with our files in its root filesystem."""
    if os.path.isdir(workdir):
        shutil.rmtree(workdir)
    os.makedirs(workdir, mode=0o700)
    try:
        _image(tc, host, role_script, base_xz, out_img, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)      # it holds the role's secrets
    os.chmod(out_img, 0o600)
    return out_img


def _image(tc, host, role_script, base_xz, out_img, workdir):
    part = out_img + ".part"
    mounts = [util.parent(base_xz), util.parent(out_img), workdir]
    tc.run(["sh", "-c", "xz -dc %s > %s" % (shlex.quote(base_xz), shlex.quote(part))], mounts=mounts)
    table = json.loads(tc.run(["sfdisk", "-J", part], mounts=mounts, capture=True).stdout)["partitiontable"]
    start, size = root_partition(table)
    root = os.path.join(workdir, "root.ext4")
    dd = "dd if=%s of=%s bs=512 skip=%d count=%d status=none"
    tc.run(["sh", "-c", dd % (shlex.quote(part), shlex.quote(root), start, size)], mounts=mounts)
    entries = {}
    for i, (path, (text, mode)) in enumerate(sorted(files(host, role_script).items())):
        local = os.path.join(workdir, "f%03d" % i)
        util.write_text(local, text, 0o600)
        entries[path] = (local, mode)
    existing = set()
    for d in sorted({os.path.dirname(p) for p in entries} | {ROLE_DIR}):
        parts = d.strip("/").split("/")
        for i in range(1, len(parts) + 1):
            sub = "/" + "/".join(parts[:i])
            if sub not in existing:
                r = tc.run(["debugfs", "-R", "stat " + sub, root], mounts=mounts, capture=True, check=False)
                if "Type: directory" in (r.stdout or ""):
                    existing.add(sub)
    cmds = os.path.join(workdir, "debugfs.cmds")
    util.write_text(cmds, debugfs_commands(entries, existing), 0o600)
    tc.run(["debugfs", "-w", "-f", cmds, root], mounts=mounts, capture=True)
    r = tc.run(["e2fsck", "-fn", root], mounts=mounts, capture=True, check=False)
    if r.returncode != 0:
        raise KiwiError("the image's root filesystem does not check clean after writing:\n%s" % (r.stdout or "")[-800:])
    tc.run(["sh", "-c", "dd if=%s of=%s bs=512 seek=%d conv=notrunc status=none" % (
        shlex.quote(root), shlex.quote(part), start)], mounts=mounts)
    os.replace(part, out_img)
