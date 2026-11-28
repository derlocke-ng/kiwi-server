"""Install media for a person's computer (docs/design/clients.md, "Install media"): Fedora's
installer with a kickstart that installs an image-based desktop straight from its registry
(anaconda's `ostreecontainer`), on an encrypted disk, and lets the computer join its network at its
first start with a one-time code on the stick. The disk's passphrase is typed at the install; it is
on the stick only when asked for (test machines).
"""
import os
import re
import shlex

from . import util
from .util import KiwiError

# the images a computer is installed from: Fedora's Silverblue, Universal Blue's Bluefin and
# Bluefin-DX (Bluefin with the developer's tools), each from its own registry
SYSTEMS = {
    "silverblue": "quay.io/fedora/fedora-silverblue:45",
    "bluefin": "ghcr.io/ublue-os/bluefin:stable",
    "bluefin-dx": "ghcr.io/ublue-os/bluefin-dx:stable",
}

JOIN_SCRIPT = """#!/bin/sh
# kiwi-server: this computer joins its network, once: the agent with its key, checked against the
# network's CA; the code is spent with it. A code that is used up or too old says so in the journal;
# then the computer joins with a new command from the control page (Kiwi Agent, kiwi-agent join).
s=$(curl -fsS --retry 60 --retry-delay 10 --retry-all-errors --cacert /etc/kiwi-client-join/ca.pem \\
    "$(cat /etc/kiwi-client-join/url)") || exit 1
printf '%s\\n' "$s" | sh || exit 1
rm -rf /etc/kiwi-client-join
systemctl disable kiwi-join-once.service
"""
JOIN_UNIT = """[Unit]
Description=kiwi-server: this computer joins its network, once
Wants=network-online.target
After=network-online.target
ConditionPathExists=/etc/kiwi-client-join/url

[Service]
Type=oneshot
ExecStart=/bin/sh /etc/kiwi-client-join/join.sh

[Install]
WantedBy=multi-user.target
"""


def signed(img):
    """Universal Blue's images are signed, and their own policy knows the key: after the install the
    computer is switched to the signed origin (what ublue's isogenerator did), so that every update
    is checked against it. Fedora's own image is not switched."""
    return img.startswith("ghcr.io/ublue-os/")


def image(system, override=None):
    if override:
        return override
    if system not in SYSTEMS:
        raise KiwiError("--system is one of %s" % ", ".join(SYSTEMS))
    return SYSTEMS[system]


def kickstart(name, img, join_url, ca_pem, keymap="us", timezone="UTC", lang="en_US.UTF-8", passphrase=None,
              managed=False):
    """The kickstart: the image onto an encrypted btrfs disk (every disk of the computer is wiped),
    the network by DHCP, no user (GNOME's first start asks for one: its person is its admin; a
    managed computer has none, its people log in with the network's accounts), and the join at the
    first start."""
    if not re.fullmatch(r"https://[^\s'\"]+/api/client/join/[A-Za-z0-9_-]{16,64}", join_url or ""):
        raise KiwiError("the join address is not one of the control page's")
    if "BEGIN CERTIFICATE" not in (ca_pem or ""):
        raise KiwiError("the network's CA is missing")
    for what, value in (("keymap", keymap), ("timezone", timezone), ("lang", lang)):
        if not re.fullmatch(r"[A-Za-z0-9_./+-]+", str(value)):
            raise KiwiError("%s %r is not a plain word" % (what, value))
    secret = ""
    if passphrase is not None:
        if not passphrase or "'" in passphrase or "\n" in passphrase:
            raise KiwiError("the passphrase from --passphrase-file is empty or has a quote or a line break")
        secret = " --passphrase=%s" % shlex.quote(passphrase)
    return """# kiwi-server: install media for %(name)s%(kind)s
graphical
lang %(lang)s
keyboard --vckeymap=%(keymap)s
timezone %(timezone)s --utc
network --bootproto=dhcp --device=link --activate --hostname=%(name)s
rootpw --lock
zerombr
clearpart --all --initlabel --disklabel=gpt
autopart --type=btrfs --encrypted --luks-version=luks2%(secret)s
ostreecontainer --url=%(img)s --transport=registry --no-signature-verification
firstboot --%(firstboot)s
reboot --eject

%%post --erroronfail
install -d -m 0700 /etc/kiwi-client-join
cat > /etc/kiwi-client-join/ca.pem <<'KIWI_EOF'
%(ca)sKIWI_EOF
printf '%%s\\n' '%(url)s' > /etc/kiwi-client-join/url
chmod 0600 /etc/kiwi-client-join/url
cat > /etc/kiwi-client-join/join.sh <<'KIWI_EOF'
%(script)sKIWI_EOF
cat > /etc/systemd/system/kiwi-join-once.service <<'KIWI_EOF'
%(unit)sKIWI_EOF
systemctl enable kiwi-join-once.service
%(sign)s%%end
""" % {"name": name, "lang": lang, "keymap": keymap, "timezone": timezone, "secret": secret, "img": img,
       "ca": ca_pem if ca_pem.endswith("\n") else ca_pem + "\n", "url": join_url, "unit": JOIN_UNIT,
       "script": JOIN_SCRIPT, "firstboot": "disable" if managed else "enable",
       "kind": " (managed: no local user, its people log in with the network's accounts)" if managed else "",
       "sign": ("# updates checked against the image's signature from now on\n"
                "bootc switch --mutate-in-place --enforce-container-sigpolicy --transport registry %s\n" % img)
       if signed(img) else ""}


def grub_with_kickstart(text, label):
    """A Fedora installer's grub.cfg whose entries read the kickstart on the stick, as mkksiso does:
    no media check (a remastered ISO has no checksum of its own: the check failed and the
    installer stopped, 28 Nov 2026), and the plain install as the default entry."""
    ks = "inst.ks=hd:LABEL=%s:/ks.cfg" % label.replace(" ", "\\x20")
    out = []
    for line in text.splitlines():
        if re.match(r"\s*(linux|linuxefi)\s", line):
            line = re.sub(r"\s+rd\.live\.check\b", "", line)
            if "inst.stage2=" in line and "inst.ks=" not in line:
                line = line.rstrip() + " " + ks
        if re.match(r"\s*set\s+default=", line):
            line = re.sub(r"default=\S+", 'default="0"', line)
        out.append(line)
    return "\n".join(out) + "\n"


def build(tc, base_iso, out_iso, ks_text, workdir):
    """The base installer ISO with the kickstart at its root and its boot entries pointing at it."""
    if not os.path.isfile(base_iso):
        raise KiwiError("no such file: %s (a Fedora installer ISO, Silverblue or Everything)" % base_iso)
    info = tc.run(["xorriso", "-indev", base_iso, "-pvd_info"], mounts=[util.parent(base_iso)], capture=True, check=False)
    label = next((line.split(":", 1)[1].strip() for line in (info.stdout or "").splitlines()
                  if line.startswith("Volume Id")), "")
    if not label:
        raise KiwiError("%s has no volume label: is it a Fedora installer ISO?" % base_iso)
    maps = {"/ks.cfg": os.path.join(workdir, "ks.cfg")}
    util.write_text(maps["/ks.cfg"], ks_text, 0o600)
    found = 0
    for inside in ("/EFI/BOOT/grub.cfg", "/boot/grub2/grub.cfg"):
        local = os.path.join(workdir, inside.strip("/").replace("/", "_"))
        r = tc.run(["xorriso", "-osirrox", "on", "-indev", base_iso, "-extract", inside, local],
                   mounts=[util.parent(base_iso), workdir], capture=True, check=False)
        if r.returncode == 0 and os.path.isfile(local):
            util.write_text(local, grub_with_kickstart(util.read_text(local), label))
            maps[inside] = local
            found += 1
    if not found:
        raise KiwiError("%s has no grub.cfg: is it a Fedora installer ISO?" % base_iso)
    argv = ["xorriso", "-indev", base_iso, "-outdev", out_iso, "-boot_image", "any", "replay"]
    for inside, local in maps.items():
        argv += ["-map", local, inside]
    argv += ["-end"]
    if os.path.exists(out_iso):
        os.unlink(out_iso)
    tc.run(argv, mounts=[util.parent(base_iso), util.parent(out_iso), workdir])
    return label
