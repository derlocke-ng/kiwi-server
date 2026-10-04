"""Debian stable: a preseed plus a small directory of files the ISO carries.

The preseed answers every installer question. At the end it runs
/cdrom/kiwi-server/late.sh, which copies the role script, the first-boot unit,
the update policy and the admin's SSH keys into the installed system. Keeping
those as files instead of one giant late_command line keeps the preseed
readable — and the same directory is what `render` leaves next to it.
"""
import ipaddress

from . import ROLE_DIR, ROLE_SCRIPT, on_calendar, role_unit

CDROM_DIR = "/cdrom/kiwi-server"
KERNEL_ARGS = "auto=true priority=critical"


def _netmask(cidr):
    return str(ipaddress.ip_interface(cidr).network.netmask)


def preseed(host, version):
    c = host.cfg
    d = c["debian"]
    a = host.admin
    u = host.updates()
    net = host.static_network()
    short, domain = host.short_name, host.domain
    L = [
        "# kiwi-server %s — Debian preseed for host %s (%s)" % (version, host.name, host.hostname),
        "# Made for the ISO built next to it: the late command reads %s." % CDROM_DIR,
        "",
        "### localisation",
        "d-i debian-installer/locale string %s" % c["locale"],
        "d-i keyboard-configuration/xkb-keymap select %s" % c["keyboard"],
        "d-i hw-detect/load_firmware boolean true",
        "",
        "### network",
        "d-i netcfg/enable boolean true",
        "d-i netcfg/choose_interface select %s" % (c["network"].get("interface") or "auto"),
        "d-i netcfg/link_wait_timeout string 15",
        "d-i netcfg/dhcp_timeout string 60",
        "d-i netcfg/wireless_wep string",
    ]
    if net:
        L += [
            "d-i netcfg/disable_autoconfig boolean true",
            "d-i netcfg/get_ipaddress string %s" % str(net["address"]).split("/")[0],
            "d-i netcfg/get_netmask string %s" % _netmask(net["address"]),
            "d-i netcfg/get_gateway string %s" % net["gateway"],
            "d-i netcfg/get_nameservers string %s" % " ".join(net["dns"] or [net["gateway"]]),
            "d-i netcfg/confirm_static boolean true",
        ]
    L += [
        "d-i netcfg/get_hostname string %s" % short,
        "d-i netcfg/get_domain string %s" % domain,
        "d-i netcfg/hostname string %s" % short,
        "",
        "### mirror",
        "d-i mirror/country string manual",
        "d-i mirror/http/hostname string %s" % d["mirror"],
        "d-i mirror/http/directory string %s" % d["directory"],
        ("d-i mirror/http/proxy string %s" % (d.get("proxy") or "")).rstrip(),
        "d-i mirror/suite string %s" % d["release"],
        "",
        "### accounts — root is disabled; the admin user has passwordless sudo,",
        "### the same as the core user on Fedora CoreOS",
        "d-i passwd/root-login boolean false",
        "d-i passwd/user-fullname string %s" % a["user"],
        "d-i passwd/username string %s" % a["user"],
        "d-i passwd/user-password-crypted password %s" % (host.password_hash() or "!"),
        "d-i passwd/user-default-groups string sudo",
        "d-i user-setup/allow-password-weak boolean true",
        "",
        "### clock",
        "d-i clock-setup/utc boolean true",
        "d-i time/zone string %s" % c["timezone"],
        "d-i clock-setup/ntp boolean true",
        "",
        "### partitioning — THE WHOLE DISK IS WIPED WITHOUT ASKING",
        "d-i partman-auto/disk string %s" % (c.get("disk") or "/dev/sda"),
        "d-i partman-auto/method string %s" % ("crypto" if d.get("encrypt") else d["partitioning"]),
        "d-i partman-auto-lvm/guided_size string max",
        "d-i partman-lvm/device_remove_lvm boolean true",
        "d-i partman-md/device_remove_md boolean true",
        "d-i partman-lvm/confirm boolean true",
        "d-i partman-lvm/confirm_nooverwrite boolean true",
        "d-i partman-auto/choose_recipe select atomic",
        "d-i partman-partitioning/confirm_write_new_label boolean true",
        "d-i partman/choose_partition select finish",
        "d-i partman/confirm boolean true",
        "d-i partman/confirm_nooverwrite boolean true",
        "d-i partman-efi/non_efi_system boolean true",
    ]
    if d.get("encrypt") or d["partitioning"] == "crypto":
        L += [
            "d-i partman-crypto/passphrase password %s" % d["passphrase"],
            "d-i partman-crypto/passphrase-again password %s" % d["passphrase"],
            "d-i partman-auto-crypto/erase_disks boolean false",
        ]
    pkgs = ["sudo", "curl", "ca-certificates", "git", "gnupg", "openssh-server"]
    pkgs += [str(p) for p in (d.get("packages") or []) if str(p) not in pkgs]
    L += [
        "",
        "### packages",
        "d-i base-installer/kernel/image string linux-image-amd64",
        "d-i apt-setup/non-free-firmware boolean %s" % ("true" if d.get("non_free_firmware", True) else "false"),
        "d-i apt-setup/services-select multiselect security, updates",
        "d-i apt-setup/security_host string security.debian.org",
        "tasksel tasksel/first multiselect standard, ssh-server",
        "d-i pkgsel/include string %s" % " ".join(pkgs),
        "d-i pkgsel/upgrade select full-upgrade",
        "d-i pkgsel/update-policy select %s" % ("unattended-upgrades" if u["enabled"] else "none"),
        "popularity-contest popularity-contest/participate boolean false",
        "",
        "### boot loader",
        "d-i grub-installer/only_debian boolean true",
        "d-i grub-installer/with_other_os boolean false",
        "d-i grub-installer/bootdev string %s" % (c.get("disk") or "default"),
    ]
    kargs = [str(k) for k in (d.get("kernel_arguments") or [])]
    if kargs:
        L.append("d-i debian-installer/add-kernel-opts string %s" % " ".join(kargs))
    L += [
        "",
        "### finish — role script, first-boot unit, ssh keys, update policy",
        "d-i preseed/late_command string sh %s/late.sh" % CDROM_DIR,
        "d-i finish-install/reboot_in_progress note",
        "d-i cdrom-detect/eject boolean true",
        "",
    ]
    return "\n".join(L)


def late_script(host):
    a = host.admin
    u = host.updates()
    L = [
        "#!/bin/sh",
        "# kiwi-server late command — runs inside the installer, /target is the new system",
        "set -e",
        "S=%s" % CDROM_DIR,
        "T=/target",
        "U=%s" % a["user"],
        "",
        "mkdir -p $T%s" % ROLE_DIR,
        "cp $S/role.sh $T%s" % ROLE_SCRIPT,
        "chmod 0700 $T%s $T%s" % (ROLE_DIR, ROLE_SCRIPT),
        "cp $S/kiwi-role.service $T/etc/systemd/system/kiwi-role.service",
        "cp $S/kiwi-reboot-if-required.service $T/etc/systemd/system/",
        "cp $S/kiwi-reboot-if-required.timer $T/etc/systemd/system/",
        "cp $S/sshd-kiwi.conf $T/etc/ssh/sshd_config.d/50-kiwi.conf",
        "cp $S/sudoers-kiwi $T/etc/sudoers.d/kiwi-admin",
        "chmod 0440 $T/etc/sudoers.d/kiwi-admin",
        "if [ -s $S/motd ]; then cp $S/motd $T/etc/motd; fi",
        "if [ -s $S/authorized_keys ]; then",
        "    mkdir -p $T/home/$U/.ssh",
        "    cp $S/authorized_keys $T/home/$U/.ssh/authorized_keys",
        "    chmod 0700 $T/home/$U/.ssh",
        "    chmod 0600 $T/home/$U/.ssh/authorized_keys",
        "    in-target chown -R \"$U:$U\" \"/home/$U/.ssh\"",
        "fi",
    ]
    if u["enabled"]:
        L += [
            "cp $S/20auto-upgrades $T/etc/apt/apt.conf.d/20auto-upgrades",
            "cp $S/52kiwi-updates $T/etc/apt/apt.conf.d/52kiwi-updates",
            "in-target systemctl enable kiwi-reboot-if-required.timer",
        ]
    L += ["in-target systemctl enable kiwi-role.service", "exit 0", ""]
    return "\n".join(L)


def reboot_units(host):
    u = host.updates()
    svc = ("[Unit]\nDescription=Kiwi Server: reboot if an update requires it\n\n"
           "[Service]\nType=oneshot\n"
           "ExecStart=/bin/sh -c 'if [ -e /var/run/reboot-required ]; then "
           "echo \"reboot required by an update, rebooting\"; systemctl reboot; "
           "else echo \"no reboot required\"; fi'\n")
    timer = ("[Unit]\nDescription=Kiwi Server: update reboot window\n\n"
             "[Timer]\nOnCalendar=%s\nPersistent=false\nRandomizedDelaySec=5min\n\n"
             "[Install]\nWantedBy=timers.target\n" % on_calendar(u["days"], u["time"]))
    return svc, timer


def iso_files(host, role_script):
    """Everything under /kiwi-server on the ISO: relpath -> (text, mode)."""
    a = host.admin
    svc, timer = reboot_units(host)
    sshd = ["# kiwi-server", "PermitRootLogin no",
            "PasswordAuthentication %s" % ("yes" if a.get("ssh_password_auth") else "no"), ""]
    out = {
        "late.sh": (late_script(host), 0o755),
        "role.sh": (role_script, 0o600),
        "kiwi-role.service": (role_unit(host), 0o644),
        "kiwi-reboot-if-required.service": (svc, 0o644),
        "kiwi-reboot-if-required.timer": (timer, 0o644),
        "sshd-kiwi.conf": ("\n".join(sshd), 0o644),
        "sudoers-kiwi": ("%s ALL=(ALL) NOPASSWD: ALL\n" % a["user"], 0o644),
        "authorized_keys": ("".join(k + "\n" for k in host.ssh_keys()), 0o600),
        "motd": ((str(host.cfg.get("motd") or "").rstrip("\n") + "\n") if host.cfg.get("motd") else "", 0o644),
        "20auto-upgrades": ("APT::Periodic::Update-Package-Lists \"1\";\n"
                            "APT::Periodic::Unattended-Upgrade \"1\";\n"
                            "APT::Periodic::AutocleanInterval \"7\";\n", 0o644),
        "52kiwi-updates": ("// kiwi-server: updates install daily; the reboot happens in the\n"
                           "// fleet's window (kiwi-reboot-if-required.timer), not here.\n"
                           "Unattended-Upgrade::Automatic-Reboot \"false\";\n"
                           "Unattended-Upgrade::Remove-Unused-Dependencies \"true\";\n"
                           "Unattended-Upgrade::Remove-New-Unused-Dependencies \"true\";\n"
                           "Unattended-Upgrade::Remove-Unused-Kernel-Packages \"true\";\n", 0o644),
    }
    return out
