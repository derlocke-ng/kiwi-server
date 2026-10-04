"""Unit tests for kiwiserver. Run: python3 -m unittest discover -s tests -v

Anything that needs a tool (shellcheck, butane, xorriso, cpio) uses it when it
is on $PATH and is skipped otherwise, so the suite runs anywhere; CI installs
all of them. No test touches the network.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))
os.environ["KIWI_SERVER_HOME"] = ROOT

import yaml  # noqa: E402

from kiwiserver import VERSION, cli, config, iso, roles as rolesmod  # noqa: E402
from kiwiserver.targets import coreos as coreos_t, debian as debian_t  # noqa: E402
from kiwiserver.toolchain import Toolchain  # noqa: E402
from kiwiserver.util import KiwiError  # noqa: E402

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEh+test+kiwi+server+key+0000000000000000000 test@kiwi"


def have(tool):
    return shutil.which(tool) is not None


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kiwi-server-test-")
        write(os.path.join(self.tmp, "secrets", "wg.conf"), "[Interface]\nPrivateKey=x\n")
        self.roles = rolesmod.discover()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fleet(self, data):
        path = os.path.join(self.tmp, "fleet.yaml")
        write(path, yaml.safe_dump(data, sort_keys=False))
        return config.Fleet.load(path)

    def base_defaults(self, **over):
        d = {"target": "ucore", "role": "bare", "domain": "kiwi", "timezone": "Europe/Berlin",
             "disk": "/dev/sda", "admin": {"user": "core", "password": "pw", "ssh_keys": [KEY]}}
        d.update(over)
        return d

    def render(self, fleet, name):
        host = fleet.hosts[name]
        errs = host.validate(self.roles)
        self.assertEqual(errs, [])
        rolesmod.resolve_settings(self.roles[host.role], host)
        return rolesmod.render_script(host, self.roles[host.role], VERSION)


class TestConfig(Base):
    def test_defaults_merge_and_hostname(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {"a": {}, "b": {"hostname": "x.y.z"}}})
        self.assertEqual(f.hosts["a"].hostname, "a.kiwi")
        self.assertEqual(f.hosts["b"].hostname, "x.y.z")
        self.assertEqual(f.hosts["b"].short_name, "x")
        self.assertEqual(f.hosts["b"].domain, "y.z")
        self.assertEqual(f.hosts["a"].cfg["timezone"], "Europe/Berlin")

    def test_lists_extend_for_keys_and_replace_elsewhere(self):
        f = self.fleet({"defaults": self.base_defaults(debian={"packages": ["vim"]}),
                        "hosts": {"a": {"admin": {"ssh_keys": [KEY.replace("test@", "two@")]},
                                        "debian": {"packages": ["htop"]},
                                        "updates": {"days": ["Mon"]}}}})
        h = f.hosts["a"]
        self.assertEqual(len(h.ssh_keys()), 2)
        self.assertEqual(h.cfg["debian"]["packages"], ["vim", "htop"])
        self.assertEqual(h.updates()["days"], ["Mon"])

    def test_iprange_assignment_skips_used(self):
        f = self.fleet({"defaults": self.base_defaults(network={"dhcp": False, "gateway": "10.0.0.1",
                                                                "iprange": "10.0.0.10-10.0.0.12"}),
                        "hosts": {"a": {}, "b": {"network": {"address": "10.0.0.11/24"}}, "c": {}}})
        self.assertEqual(f.hosts["a"].cfg["network"]["address"], "10.0.0.10/29")  # .8-.15 covers .10-.12
        self.assertEqual(f.hosts["b"].cfg["network"]["address"], "10.0.0.11/24")
        self.assertEqual(f.hosts["c"].cfg["network"]["address"], "10.0.0.12/29")
        self.assertEqual(f.validate(self.roles), [])

    def test_iprange_exhausted(self):
        with self.assertRaises(KiwiError):
            self.fleet({"defaults": self.base_defaults(network={"dhcp": False, "gateway": "10.0.0.1",
                                                                "iprange": "10.0.0.10-10.0.0.10"}),
                        "hosts": {"a": {}, "b": {}}})

    def test_validation_errors(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {
            "bad": {"target": "windows", "role": "nope", "hostname": "has_underscore",
                    "admin": {"user": "Root!", "password": None, "ssh_keys": []},
                    "network": {"dhcp": False}, "updates": {"days": ["Funday"], "time": "25:00"},
                    "disk": "sda", "debian": {"partitioning": "zfs"}}}})
        errs = "\n".join(f.validate(self.roles))
        for needle in ("target must be", "unknown role", "not a valid DNS name", "unix user name",
                       "static (dhcp: false) needs", "updates.days",
                       "updates.time", "device path", "partitioning"):
            self.assertIn(needle, errs)
        # ssh keys EXTEND the defaults, so a host cannot drop them: the no-login
        # check needs a fleet whose defaults have neither keys nor a password
        f = self.fleet({"defaults": {"target": "ucore", "role": "bare", "disk": "/dev/sda",
                                     "admin": {"user": "core"}}, "hosts": {"a": {}}})
        self.assertIn("nobody could log in", "\n".join(f.validate(self.roles)))

    def test_role_target_support_and_unknown_setting(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {
            "a": {"role": "node-cloud", "node-cloud": {"vpn_config": "secrets/wg.conf", "vpn_ip": "10.8.0.2",
                                                       "bogus": 1}}}})
        h = f.hosts["a"]
        self.assertEqual(h.validate(self.roles), [])
        with self.assertRaises(KiwiError) as cm:
            rolesmod.resolve_settings(self.roles["node-cloud"], h)
        self.assertIn("bogus", str(cm.exception))

    def test_required_role_setting_and_missing_file(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {
            "a": {"role": "node-cloud", "node-cloud": {"vpn_ip": "10.8.0.2"}},
            "b": {"role": "node-cloud", "node-cloud": {"vpn_ip": "10.8.0.2", "vpn_config": "nope.conf"}}}})
        with self.assertRaises(KiwiError) as cm:
            rolesmod.resolve_settings(self.roles["node-cloud"], f.hosts["a"])
        self.assertIn("vpn_config is required", str(cm.exception))
        with self.assertRaises(KiwiError) as cm:
            rolesmod.resolve_settings(self.roles["node-cloud"], f.hosts["b"])
        self.assertIn("file not found", str(cm.exception))

    def test_password_hash_is_deterministic_sha512(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {"a": {}}})
        h1 = f.hosts["a"].password_hash()
        h2 = config.Fleet.load(f.path).hosts["a"].password_hash()
        self.assertTrue(h1.startswith("$6$"))
        self.assertEqual(h1, h2)

    def test_ssh_key_from_file(self):
        write(os.path.join(self.tmp, "id.pub"), KEY + "\n")
        f = self.fleet({"defaults": self.base_defaults(admin={"user": "core", "ssh_keys": ["id.pub"]}),
                        "hosts": {"a": {}}})
        self.assertEqual(f.hosts["a"].ssh_keys(), [KEY])


class TestRoles(Base):
    def test_discover(self):
        self.assertEqual(sorted(self.roles), ["bare", "master", "node-cloud"])
        nc = self.roles["node-cloud"]
        self.assertIn("vpn_config", [s.key for s in nc.settings])
        self.assertTrue(json.dumps(nc.as_dict()))

    def test_script_has_settings_files_and_runs_main(self):
        f = self.fleet({"defaults": self.base_defaults(post_script="echo hi"), "hosts": {
            "n": {"role": "node-cloud", "node-cloud": {"vpn_config": "secrets/wg.conf", "vpn_ip": "10.8.0.2",
                                                       "env": {"FOO": "bar baz"}}}}})
        s = self.render(f, "n")
        self.assertIn("KS_HOSTNAME='n.kiwi'", s)
        self.assertIn("KS_ROLE_VPN_IP='10.8.0.2'", s)
        self.assertIn("KS_FILES[vpn_config]=", s)
        self.assertIn("declare -A KS_ROLE_ENV=(['FOO']='bar baz')", s)
        self.assertIn("ks_role_apply()", s)
        self.assertIn("    echo hi", s)
        self.assertTrue(s.rstrip().endswith('ks_main "$@"'))
        # the embedded file round-trips
        import base64, re
        m = re.search(r"KS_FILES\[vpn_config\]='?([A-Za-z0-9+/=]+)'?", s)
        self.assertEqual(base64.b64decode(m.group(1)).decode(), "[Interface]\nPrivateKey=x\n")

    def test_bash_parses_and_shellcheck_passes_every_role(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {
            "bare": {"role": "bare", "target": "debian", "bare": {"packages": ["vim"]}},
            "node": {"role": "node-cloud", "node-cloud": {"vpn_config": "secrets/wg.conf", "vpn_ip": "10.8.0.2"}},
            "gate": {"role": "master", "target": "debian",
                     "master": {"wg_host": "vpn.example.org", "wg_admin_password": "p", "pihole_password": "q"}}}})
        for name in f.hosts:
            s = self.render(f, name)
            p = os.path.join(self.tmp, name + ".sh")
            write(p, s)
            r = subprocess.run(["bash", "-n", p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            if have("shellcheck"):
                r = subprocess.run(["shellcheck", "-S", "warning", p], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_script_refuses_without_root_and_respects_marker(self):
        """Run the rendered script with a fake environment: not root -> refuses."""
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {"a": {}}})
        p = os.path.join(self.tmp, "a.sh")
        write(p, self.render(f, "a"))
        if os.geteuid() == 0:
            self.skipTest("running as root")
        r = subprocess.run(["bash", p], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("must run as root", r.stderr)


class TestCoreos(Base):
    def butane(self, data, name):
        f = self.fleet(data)
        s = self.render(f, name)
        return f.hosts[name], yaml.safe_load(coreos_t.render(f.hosts[name], s))

    def test_ucore_butane_structure(self):
        h, bu = self.butane({"defaults": self.base_defaults(
            network={"dhcp": False, "address": "192.168.1.5/24", "gateway": "192.168.1.1", "dns": ["1.1.1.1"]},
            coreos={"services": ["cockpit"], "kernel_arguments": ["quiet"]},
            ucore={"image": "ghcr.io/ublue-os/ucore-hci:stable"}), "hosts": {"a": {}}}, "a")
        self.assertEqual(bu["variant"], "fcos")
        self.assertEqual(bu["version"], "1.6.0")
        user = bu["passwd"]["users"][0]
        self.assertEqual(user["name"], "core")
        self.assertTrue(user["password_hash"].startswith("$6$"))
        self.assertEqual(user["ssh_authorized_keys"], [KEY])
        paths = [f["path"] for f in bu["storage"]["files"]]
        self.assertIn("/etc/hostname", paths)
        self.assertIn("/etc/NetworkManager/system-connections/kiwi-static.nmconnection", paths)
        self.assertIn("/var/lib/kiwi-server/role.sh", paths)
        self.assertNotIn("/etc/zincati/config.d/55-updates-strategy.toml", paths)  # ucore: no zincati
        units = {u["name"]: u for u in bu["systemd"]["units"]}
        for n in ("ucore-unsigned-autorebase.service", "ucore-signed-autorebase.service",
                  "kiwi-role.service", "kiwi-staged-reboot.timer", "cockpit.socket"):
            self.assertIn(n, units)
        self.assertIn("/var/lib/kiwi-server/staged-reboot.sh", paths)
        # the role script must stay readable in the .bu: block style, not one escaped line
        self.assertIn("inline: |\n        #!/usr/bin/env bash", coreos_t.render(h, self.render(
            config.Fleet.load(h.fleet.path), "a")))
        self.assertIn("ostree-image-signed:docker://ghcr.io/ublue-os/ucore-hci:stable",
                      units["ucore-signed-autorebase.service"]["contents"])
        self.assertIn("ConditionPathExists=/etc/ucore-autorebase/signed", units["kiwi-role.service"]["contents"])
        self.assertIn("OnCalendar=Sun *-*-* 03:30:00", units["kiwi-staged-reboot.timer"]["contents"])
        self.assertEqual(bu["kernel_arguments"]["should_exist"], ["quiet"])
        self.assertEqual(bu["storage"]["links"][0]["target"], "../usr/share/zoneinfo/Europe/Berlin")
        nm = [f for f in bu["storage"]["files"] if f["path"].endswith(".nmconnection")][0]
        self.assertIn("address1=192.168.1.5/24,192.168.1.1", nm["contents"]["inline"])
        self.assertEqual(nm["mode"], 0o600)

    def test_coreos_zincati_window_and_no_rebase(self):
        h, bu = self.butane({"defaults": self.base_defaults(target="coreos",
                                                            updates={"days": ["Sat", "Sun"], "time": "22:30",
                                                                     "length_minutes": 60}),
                             "hosts": {"a": {}}}, "a")
        z = [f for f in bu["storage"]["files"] if "zincati" in f["path"]][0]["contents"]["inline"]
        self.assertIn('strategy = "periodic"', z)
        self.assertIn('days = [ "Sat", "Sun" ]', z)
        self.assertIn('start_time = "22:30"', z)
        names = [u["name"] for u in bu["systemd"]["units"]]
        self.assertNotIn("ucore-unsigned-autorebase.service", names)
        self.assertNotIn("ConditionPathExists=/etc/ucore-autorebase",
                         [u for u in bu["systemd"]["units"] if u["name"] == "kiwi-role.service"][0]["contents"])

    def test_updates_disabled(self):
        _, bu = self.butane({"defaults": self.base_defaults(target="coreos", updates={"enabled": False}),
                             "hosts": {"a": {}}}, "a")
        z = [f for f in bu["storage"]["files"] if "zincati" in f["path"]][0]["contents"]["inline"]
        self.assertIn("enabled = false", z)

    def test_non_core_admin_gets_sudo_groups(self):
        _, bu = self.butane({"defaults": self.base_defaults(admin={"user": "ops", "password": "x", "groups": ["docker"]}),
                             "hosts": {"a": {}}}, "a")
        self.assertEqual(bu["passwd"]["users"][0]["groups"], ["wheel", "sudo", "docker"])

    @unittest.skipUnless(have("butane"), "butane not installed")
    def test_butane_strict_accepts_every_target_variant(self):
        f = self.fleet({"defaults": self.base_defaults(coreos={"boot_device": {"luks": {"tpm2": True}}}),
                        "hosts": {"u": {}, "c": {"target": "coreos"},
                                  "n": {"role": "node-cloud", "node-cloud": {"vpn_config": "secrets/wg.conf",
                                                                             "vpn_ip": "10.8.0.2"}}}})
        for name in f.hosts:
            bu = os.path.join(self.tmp, name + ".bu")
            write(bu, coreos_t.render(f.hosts[name], self.render(f, name)))
            r = subprocess.run(["butane", "--strict", "--pretty", bu], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            ign = json.loads(r.stdout)
            self.assertEqual(ign["ignition"]["version"], "3.5.0")
            self.assertTrue(any(fl["path"] == "/var/lib/kiwi-server/role.sh" for fl in ign["storage"]["files"]))


class TestDebian(Base):
    def make(self, **host):
        f = self.fleet({"defaults": self.base_defaults(target="debian", keyboard="de",
                                                       debian={"packages": ["vim"]}), "hosts": {"d": host}})
        s = self.render(f, "d")
        return f.hosts["d"], debian_t.preseed(f.hosts["d"], VERSION), debian_t.iso_files(f.hosts["d"], s)

    def test_preseed_dhcp(self):
        h, ps, files = self.make()
        for needle in ("d-i debian-installer/locale string en_US.UTF-8",
                       "d-i keyboard-configuration/xkb-keymap select de",
                       "d-i netcfg/get_hostname string d", "d-i netcfg/get_domain string kiwi",
                       "d-i passwd/username string core", "d-i passwd/user-password-crypted password $6$",
                       "d-i partman-auto/disk string /dev/sda", "d-i partman-auto/method string lvm",
                       "d-i pkgsel/include string sudo curl ca-certificates git gnupg openssh-server vim",
                       "d-i pkgsel/update-policy select unattended-upgrades",
                       "d-i grub-installer/bootdev string /dev/sda",
                       "d-i preseed/late_command string sh /cdrom/kiwi-server/late.sh",
                       "d-i time/zone string Europe/Berlin"):
            self.assertIn(needle, ps)
        self.assertNotIn("netcfg/disable_autoconfig", ps)
        self.assertEqual(set(files) >= {"late.sh", "role.sh", "kiwi-role.service", "authorized_keys",
                                        "20auto-upgrades", "52kiwi-updates", "sudoers-kiwi"}, True)
        self.assertEqual(files["authorized_keys"][0], KEY + "\n")
        self.assertIn("core ALL=(ALL) NOPASSWD: ALL", files["sudoers-kiwi"][0])
        self.assertIn("OnCalendar=Sun *-*-* 03:30:00", files["kiwi-reboot-if-required.timer"][0])
        self.assertIn('Automatic-Reboot "false"', files["52kiwi-updates"][0])
        self.assertIn("in-target systemctl enable kiwi-role.service", files["late.sh"][0])
        self.assertEqual(files["role.sh"][1], 0o600)

    def test_preseed_static_and_crypto(self):
        h, ps, _ = self.make(network={"dhcp": False, "address": "10.1.2.3/16", "gateway": "10.1.0.1",
                                      "dns": ["9.9.9.9"]},
                             debian={"encrypt": True, "passphrase": "secret-pass"})
        for needle in ("d-i netcfg/disable_autoconfig boolean true",
                       "d-i netcfg/get_ipaddress string 10.1.2.3",
                       "d-i netcfg/get_netmask string 255.255.0.0",
                       "d-i netcfg/get_gateway string 10.1.0.1",
                       "d-i netcfg/get_nameservers string 9.9.9.9",
                       "d-i partman-auto/method string crypto",
                       "d-i partman-crypto/passphrase password secret-pass"):
            self.assertIn(needle, ps)

    def test_late_sh_is_posix_sh(self):
        _, _, files = self.make()
        p = os.path.join(self.tmp, "late.sh")
        write(p, files["late.sh"][0])
        self.assertEqual(subprocess.run(["sh", "-n", p]).returncode, 0)
        if have("shellcheck"):
            r = subprocess.run(["shellcheck", "-S", "warning", "-s", "sh", p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout)


class TestIso(Base):
    def test_resolve_netinst(self):
        text = ("abc  debian-13.1.0-amd64-netinst.iso\n"
                "def  debian-edu-13.1.0-amd64-netinst.iso\n"
                "ghi  debian-mac-13.1.0-amd64-netinst.iso\n")
        self.assertEqual(iso.resolve_netinst(text), ("debian-13.1.0-amd64-netinst.iso", "abc"))
        with self.assertRaises(KiwiError):
            iso.resolve_netinst("x  something-else.iso\n")

    def test_cfg_rewrites(self):
        iso_cfg = "# D-I config version 2.0\ninclude menu.cfg\ndefault vesamenu.c32\nprompt 0\ntimeout 0\n"
        out = iso.rewrite_isolinux_cfg(iso_cfg)
        self.assertIn("default kiwi-auto", out)
        self.assertIn("timeout 10", out)
        self.assertNotIn("vesamenu", out)
        txt = iso.auto_txt_cfg("label install\n\tmenu label ^Install\n", "T")
        self.assertTrue(txt.startswith("label kiwi-auto"))
        self.assertIn("auto=true priority=critical", txt)
        grub = iso.auto_grub_cfg("set default=0\nset timeout=5\nmenuentry 'Install' {\n}\n", "T")
        self.assertTrue(grub.startswith("set default=kiwi-auto\nset timeout=1\n"))
        self.assertNotIn("set timeout=5", grub)
        self.assertIn("--id kiwi-auto", grub)

    def test_update_md5sums(self):
        p = os.path.join(self.tmp, "x")
        write(p, "hello\n")
        out = iso.update_md5sums("111  ./a\n222  ./install.amd/initrd.gz\n", {"./install.amd/initrd.gz": p})
        self.assertIn("111  ./a", out)
        self.assertNotIn("222", out)
        self.assertIn("b1946ac92492d2347c6235b4d2611184  ./install.amd/initrd.gz", out)

    def test_coreos_customize_argv(self):
        f = self.fleet({"defaults": self.base_defaults(coreos={"console": "ttyS0,115200n8",
                                                               "kernel_arguments": ["mitigations=auto"]}),
                        "hosts": {"a": {}}})
        base = os.path.join(self.tmp, "fcos.iso")
        write(base, "")
        calls = []

        class FakeTC:
            def run(self, argv, **kw):
                calls.append(list(argv))
                return type("R", (), {"stdout": base + "\n", "stderr": ""})()
        iso.coreos_iso(FakeTC(), f.hosts["a"], "/o/a.ign", "/o/a.iso", self.tmp)
        self.assertEqual(calls[0][:4], ["coreos-installer", "download", "-s", "stable"])
        cust = calls[1]
        self.assertEqual(cust[:3], ["coreos-installer", "iso", "customize"])
        self.assertEqual(cust[cust.index("--dest-device") + 1], "/dev/sda")
        self.assertEqual(cust[cust.index("--dest-ignition") + 1], "/o/a.ign")
        self.assertEqual(cust[cust.index("--dest-console") + 1], "ttyS0,115200n8")
        self.assertEqual(cust[cust.index("--dest-karg-append") + 1], "mitigations=auto")
        self.assertEqual(cust[-1], base)

    def test_iso_needs_disk(self):
        f = self.fleet({"defaults": self.base_defaults(disk=None), "hosts": {"a": {}}})
        with self.assertRaises(KiwiError):
            iso.coreos_iso(None, f.hosts["a"], "x", "y", self.tmp)

    @unittest.skipUnless(have("xorriso") and have("cpio") and have("gzip"), "xorriso/cpio/gzip not installed")
    def test_debian_iso_rebuild_on_mock_netinst(self):
        """A mock netinst with the files d-i media has; after the rebuild the
        initrd carries preseed.cfg, the menus boot kiwi-auto, and the host's
        files sit under /kiwi-server."""
        tree = os.path.join(self.tmp, "tree")
        os.makedirs(os.path.join(tree, "install.amd"))
        os.makedirs(os.path.join(tree, "isolinux"))
        os.makedirs(os.path.join(tree, "boot", "grub"))
        write(os.path.join(tree, "install.amd", "hello"), "hi\n")
        subprocess.run("cd %s/install.amd && echo hello | cpio -H newc -o 2>/dev/null | gzip > initrd.gz && rm hello"
                       % self.tmp + "/tree", shell=True, check=True)
        write(os.path.join(tree, "isolinux", "isolinux.cfg"), "include menu.cfg\ndefault vesamenu.c32\nprompt 0\ntimeout 0\n")
        write(os.path.join(tree, "isolinux", "txt.cfg"), "label install\n\tmenu label ^Install\n\tkernel /install.amd/vmlinuz\n")
        write(os.path.join(tree, "boot", "grub", "grub.cfg"), "set theme=/boot/grub/theme/1\nmenuentry 'Install' {\n}\n")
        write(os.path.join(tree, "md5sum.txt"), "0  ./install.amd/initrd.gz\n")
        base = os.path.join(self.tmp, "netinst.iso")
        subprocess.run(["xorriso", "-as", "mkisofs", "-quiet", "-r", "-J", "-V", "Debian", "-o", base, tree],
                       check=True, capture_output=True)

        f = self.fleet({"defaults": self.base_defaults(target="debian"), "hosts": {"d": {}}})
        tc = Toolchain(mode="native")
        host = f.hosts["d"]
        script = self.render(f, "d")
        out_dir = os.path.join(self.tmp, "out")
        preseed = os.path.join(out_dir, "d.preseed.cfg")
        write(preseed, debian_t.preseed(host, VERSION))
        files_dir = os.path.join(out_dir, "d.kiwi-server")
        for rel, (text, mode) in debian_t.iso_files(host, script).items():
            write(os.path.join(files_dir, rel), text)
        out_iso = os.path.join(out_dir, "d.iso")
        iso.debian_iso(tc, host, preseed, files_dir, out_iso, base, os.path.join(out_dir, ".work"))
        self.assertTrue(os.path.isfile(out_iso))

        ex = os.path.join(self.tmp, "ex")
        subprocess.run(["xorriso", "-osirrox", "on", "-indev", out_iso, "-extract", "/", ex],
                       check=True, capture_output=True)
        listing = subprocess.run("gzip -dc %s/install.amd/initrd.gz | cpio -t 2>/dev/null" % ex,
                                 shell=True, capture_output=True, text=True).stdout
        self.assertIn("preseed.cfg", listing)
        self.assertIn("hello", listing)
        with open(os.path.join(ex, "isolinux", "isolinux.cfg")) as fh:
            self.assertIn("default kiwi-auto", fh.read())
        with open(os.path.join(ex, "isolinux", "txt.cfg")) as fh:
            self.assertIn("label kiwi-auto", fh.read())
        with open(os.path.join(ex, "boot", "grub", "grub.cfg")) as fh:
            g = fh.read()
            self.assertIn("set default=kiwi-auto", g)
            self.assertIn("--id kiwi-auto", g)
        self.assertTrue(os.path.isfile(os.path.join(ex, "kiwi-server", "late.sh")))
        self.assertTrue(os.path.isfile(os.path.join(ex, "kiwi-server", "role.sh")))
        self.assertTrue(os.path.isfile(os.path.join(ex, "preseed.cfg")))
        with open(os.path.join(ex, "md5sum.txt")) as fh:
            m = fh.read()
            self.assertIn("./kiwi-server/late.sh", m)
            self.assertNotIn("\n0  ./install.amd/initrd.gz", "\n" + m)


class TestCli(Base):
    def run_cli(self, *args):
        from io import StringIO
        import contextlib
        out, err = StringIO(), StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = cli.main(list(args))
            except SystemExit as e:
                rc = e.code
        return rc, out.getvalue(), err.getvalue()

    def test_roles_targets_porcelain(self):
        rc, out, _ = self.run_cli("roles", "--porcelain")
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(r["name"] for r in json.loads(out)["roles"]), ["bare", "master", "node-cloud"])
        rc, out, _ = self.run_cli("targets", "--porcelain")
        self.assertEqual(set(json.loads(out)["targets"]), {"coreos", "ucore", "debian"})

    def test_validate_render_list_show(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {
            "u": {}, "d": {"target": "debian", "role": "bare"},
            "n": {"role": "node-cloud", "node-cloud": {"vpn_config": "secrets/wg.conf", "vpn_ip": "10.8.0.2"}}}})
        out_dir = os.path.join(self.tmp, "out")
        rc, out, err = self.run_cli("validate", f.path)
        self.assertEqual(rc, 0, err)
        rc, out, err = self.run_cli("render", f.path, "-o", out_dir, "--toolchain", "native")
        self.assertEqual(rc, 0, err)
        for p in ("u/u.role.sh", "u/u.bu", "d/d.role.sh", "d/d.preseed.cfg", "d/d.kiwi-server/late.sh",
                  "n/n.bu", "n/README.txt"):
            self.assertTrue(os.path.isfile(os.path.join(out_dir, p)), p)
        self.assertEqual(oct(os.stat(os.path.join(out_dir, "u/u.role.sh")).st_mode & 0o777), "0o600")
        if have("butane"):
            self.assertTrue(os.path.isfile(os.path.join(out_dir, "u/u.ign")))
        rc, out, _ = self.run_cli("list", f.path, "-o", out_dir, "--porcelain")
        hosts = {h["name"]: h for h in json.loads(out)["hosts"]}
        self.assertTrue(hosts["u"]["status"]["script"] and hosts["u"]["status"]["config"])
        self.assertEqual(hosts["d"]["errors"], [])
        rc, out, _ = self.run_cli("show", f.path, "n", "--porcelain")
        data = json.loads(out)
        self.assertEqual(data["config"]["admin"]["password"], "********")
        self.assertEqual(data["role_settings"]["vpn_ip"], "10.8.0.2")
        self.assertNotIn("node-cloud", data["config"])
        rc, out, _ = self.run_cli("script", f.path, "n")
        self.assertEqual(rc, 0)
        self.assertIn("ks_role_apply", out)

    def test_validate_exit_code_on_errors(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {"x": {"target": "nope"}}})
        rc, out, err = self.run_cli("validate", f.path)
        self.assertEqual(rc, cli.EXIT_INVALID)
        self.assertIn("target must be", err)
        rc, out, err = self.run_cli("validate", f.path, "--porcelain")
        self.assertFalse(json.loads(out)["ok"])

    def test_build_refuses_without_disk(self):
        f = self.fleet({"defaults": self.base_defaults(disk=None), "hosts": {"u": {}}})
        rc, out, err = self.run_cli("build", f.path, "-o", os.path.join(self.tmp, "o"), "--toolchain", "native")
        self.assertEqual(rc, 1)
        self.assertIn("disk", err)

    def test_init_and_example_fleet_validates(self):
        dest = os.path.join(self.tmp, "new.yaml")
        rc, out, err = self.run_cli("init", dest)
        self.assertEqual(rc, 0, err)
        self.assertTrue(os.path.isfile(dest))
        rc, _, _ = self.run_cli("init", dest)
        self.assertEqual(rc, 1)  # never overwrites


if __name__ == "__main__":
    unittest.main()
