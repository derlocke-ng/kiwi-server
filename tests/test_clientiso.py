"""Install media for a person's computer (clientiso.py): the kickstart, the installer's boot entries,
and the remaster of an ISO with xorriso."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

from kiwiserver import clientiso  # noqa: E402
from kiwiserver.util import KiwiError  # noqa: E402

CA = "-----BEGIN CERTIFICATE-----\nTEST\n-----END CERTIFICATE-----\n"
URL = "https://control.papaya.home/api/client/join/abcdefghijklmnop_QRST"


class TestClientIso(unittest.TestCase):
    def test_the_kickstart_installs_the_image_encrypted_and_joins_once(self):
        ks = clientiso.kickstart("desk", clientiso.image("bluefin-dx"), URL, CA, keymap="de")
        self.assertIn("ostreecontainer --url=ghcr.io/ublue-os/bluefin-dx:stable --transport=registry", ks)
        self.assertIn("autopart --type=btrfs --encrypted --luks-version=luks2\n", ks)    # the passphrase is asked
        self.assertIn("keyboard --vckeymap=de\n", ks)
        self.assertIn("firstboot --enable", ks)                                         # GNOME asks for its person
        self.assertIn("\npoweroff\n", ks)                                               # the stick out first, then on
        self.assertNotIn("reboot", ks)
        self.assertNotIn("\nuser ", ks)
        self.assertIn("printf '%s\\n' '" + URL + "' > /etc/kiwi-client-join/url", ks)
        self.assertIn("ExecStart=/bin/sh /etc/kiwi-client-join/join.sh", ks)
        self.assertIn("systemctl enable kiwi-join-once.service", ks)
        self.assertIn("bootc switch --mutate-in-place --enforce-container-sigpolicy --transport registry "
                      "ghcr.io/ublue-os/bluefin-dx:stable", ks)                           # signed updates from then on
        self.assertNotIn("bootc switch", clientiso.kickstart("desk", clientiso.image("silverblue"), URL, CA))
        managed = clientiso.kickstart("desk", clientiso.image("bluefin"), URL, CA, managed=True,
                                      admin_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK0wmN it@company")
        self.assertIn('user --name=kiwi --groups=wheel --lock', managed)
        gecos = managed.split('--gecos="', 1)[1].split('"', 1)[0]
        self.assertNotIn(":", gecos)                # /etc/passwd's separator: the installer stopped on it
        self.assertIn('sshkey --username=kiwi "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK0wmN it@company"', managed)
        self.assertIn("systemctl enable sshd.service", managed)
        self.assertIn("printf 'kiwi ALL=(ALL) NOPASSWD: ALL\\n' > /etc/sudoers.d/kiwi-it", managed)
        self.assertNotIn("sshd", clientiso.kickstart("desk", "x", URL, CA))
        with self.assertRaisesRegex(KiwiError, "no SSH public key"):
            clientiso.kickstart("desk", "x", URL, CA, admin_key="-----BEGIN OPENSSH PRIVATE KEY-----")
        self.assertIn("firstboot --disable\n", managed)                                    # no local admin on it
        self.assertIn("--passphrase=kiwitest", clientiso.kickstart("desk", clientiso.image("silverblue"), URL, CA,
                                                                    passphrase="kiwitest"))

    def test_what_does_not_belong_is_refused(self):
        with self.assertRaisesRegex(KiwiError, "join address"):
            clientiso.kickstart("desk", "x", "https://evil.example/x", CA)
        with self.assertRaisesRegex(KiwiError, "CA is missing"):
            clientiso.kickstart("desk", "x", URL, "")
        with self.assertRaisesRegex(KiwiError, "not a plain word"):
            clientiso.kickstart("desk", "x", URL, CA, keymap="de; rm -rf /")
        with self.assertRaisesRegex(KiwiError, "quote"):
            clientiso.kickstart("desk", "x", URL, CA, passphrase="it's")
        with self.assertRaisesRegex(KiwiError, "one of silverblue"):
            clientiso.image("windows")
        self.assertEqual(clientiso.image("bluefin", "ghcr.io/ublue-os/aurora:stable"), "ghcr.io/ublue-os/aurora:stable")

    def test_the_join_script_is_a_shell_script(self):
        r = subprocess.run(["sh", "-n"], input=clientiso.JOIN_SCRIPT, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('printf \'%s\\n\' "$s" | sh', clientiso.JOIN_SCRIPT)

    def test_the_installers_entries_read_the_kickstart(self):
        grub = ('set default="1"\n'
                "menuentry 'Install Fedora 45' --class fedora {\n"
                "\tlinux /images/pxeboot/vmlinuz inst.stage2=hd:LABEL=Fedora-SB-ostree-x86_64-45 quiet\n"
                "\tinitrd /images/pxeboot/initrd.img\n}\n"
                "menuentry 'Rescue' {\n\tlinuxefi /images/pxeboot/vmlinuz inst.stage2=hd:LABEL=Fedora-SB-ostree-x86_64-45 inst.rescue\n}\n"
                "menuentry 'Memory test' {\n\tlinux /boot/memtest\n}\n")
        out = clientiso.grub_with_kickstart(grub, "Fedora-SB-ostree-x86_64-45")
        self.assertEqual(out.count("inst.ks=hd:LABEL=Fedora-SB-ostree-x86_64-45:/ks.cfg"), 2)
        self.assertIn("linux /boot/memtest\n", out)
        self.assertNotIn("rd.live.check", clientiso.grub_with_kickstart(grub.replace("quiet", "rd.live.check quiet"), "X"))
        self.assertTrue(out.startswith('set default="0"\n'))                                 # the install itself
        self.assertEqual(clientiso.grub_with_kickstart(out, "Fedora-SB-ostree-x86_64-45"), out)      # once
        self.assertIn("LABEL=My\\x20Disk:/ks.cfg", clientiso.grub_with_kickstart(grub, "My Disk"))

    @unittest.skipUnless(shutil.which("xorriso"), "no xorriso here")
    def test_an_iso_is_remastered_with_the_kickstart(self):
        d = tempfile.mkdtemp()
        tree = os.path.join(d, "tree")
        os.makedirs(os.path.join(tree, "EFI", "BOOT"))
        with open(os.path.join(tree, "EFI", "BOOT", "grub.cfg"), "w") as f:
            f.write("menuentry 'Install' {\n\tlinux /images/pxeboot/vmlinuz inst.stage2=hd:LABEL=TEST-ISO quiet\n}\n")
        base = os.path.join(d, "base.iso")
        subprocess.run(["xorriso", "-as", "mkisofs", "-quiet", "-V", "TEST-ISO", "-o", base, tree], check=True, capture_output=True)

        class Here:
            def run(self, argv, mounts=(), capture=False, check=True, **kw):
                return subprocess.run(argv, capture_output=True, text=True, check=check)
        work = os.path.join(d, "work")
        os.makedirs(work)
        out = os.path.join(d, "out.iso")
        label = clientiso.build(Here(), base, out, "# the kickstart\n", work)
        self.assertEqual(label, "TEST-ISO")
        got = os.path.join(d, "got")
        subprocess.run(["xorriso", "-osirrox", "on", "-indev", out, "-extract", "/", got], check=True, capture_output=True)
        with open(os.path.join(got, "ks.cfg")) as f:
            self.assertEqual(f.read(), "# the kickstart\n")
        with open(os.path.join(got, "EFI", "BOOT", "grub.cfg")) as f:
            self.assertIn("inst.ks=hd:LABEL=TEST-ISO:/ks.cfg", f.read())
        with self.assertRaisesRegex(KiwiError, "no such file"):
            clientiso.build(Here(), os.path.join(d, "none.iso"), out, "x", work)


if __name__ == "__main__":
    unittest.main()
