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

from kiwiserver import VERSION, certs, cli, config, iso, modules as modmod, roles as rolesmod  # noqa: E402
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
        rolesmod.resolve_settings(self.roles[host.role], host, self.ms)
        return rolesmod.render_script(host, self.roles[host.role], VERSION)

    @property
    def ms(self):
        if not hasattr(self, "_ms"):
            self._ms = modmod.discover()
        return self._ms

    def node_cloud(self, **extra):
        d = {"vpn_ip": "10.8.0.25", "vpn-client": {"wireguard_config": "secrets/wg.conf"}}
        d.update(extra)
        return d

    def node_gw(self, **extra):
        d = {"vpn_ip": "10.8.0.6", "pub_iface": "eth0",
             "vpn-client": {"wireguard_config": "secrets/wg.conf"},
             "dns": {"pihole_password": "p"},
             "downloader": {"download_dir": "/mnt/dl", "transmission_password": "t"},
             "sftp": {"sftp_password": "s"}}
        d.update(extra)
        return d

    def master(self, **extra):
        d = {"vpn-client": {"wireguard_private_key": "k", "wireguard_addresses": "10.66.1.2/32",
                            "server_countries": "Germany"},
             "vpn-server": {"wg_host": "vpn.example.org", "wg_password": "w"},
             "dns": {"pihole_password": "p"}}
        d.update(extra)
        return d

    def prepare(self, host):
        self.assertEqual(host.validate(self.roles), [])
        rolesmod.resolve_settings(self.roles[host.role], host, self.ms)
        return self.roles[host.role]

    def full_script(self, f, name):
        host = f.hosts[name]
        role = self.prepare(host)
        bundle = modmod.Renderer(self.ms).render(rolesmod.stack_spec(host, role)) if role.stack else None
        return rolesmod.render_script(host, role, VERSION, bundle=bundle), bundle



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
            "a": {"role": "node-cloud", "node-cloud": self.node_cloud(bogus=1)},
            "b": {"role": "node-cloud", "node-cloud": self.node_cloud(cloud={"nope": 1})},
            "c": {"role": "node-cloud", "node-cloud": self.node_cloud(downloader={"download_dir": "/x"})}}})
        with self.assertRaises(KiwiError) as cm:
            rolesmod.resolve_settings(self.roles["node-cloud"], f.hosts["a"], self.ms)
        self.assertIn("bogus", str(cm.exception))
        with self.assertRaises(KiwiError) as cm:
            rolesmod.resolve_settings(self.roles["node-cloud"], f.hosts["b"], self.ms)
        self.assertIn("nope", str(cm.exception))
        with self.assertRaises(KiwiError) as cm:
            rolesmod.resolve_settings(self.roles["node-cloud"], f.hosts["c"], self.ms)
        self.assertIn("not enabled here", str(cm.exception))

    def test_required_role_setting_and_missing_file(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {
            "a": {"role": "master", "master": {"vpn-server": {"wg_host": "x"}, "dns": {"pihole_password": "p"}}},
            "b": {"role": "node-cloud", "node-cloud": self.node_cloud(**{"vpn-client": {"wireguard_config": "nope.conf"}})}}})
        with self.assertRaises(KiwiError) as cm:
            rolesmod.resolve_settings(self.roles["master"], f.hosts["a"], self.ms)
        self.assertIn("vpn-server.wg_password is required", str(cm.exception))
        with self.assertRaises(KiwiError) as cm:
            rolesmod.resolve_settings(self.roles["node-cloud"], f.hosts["b"], self.ms)
        self.assertIn("file not found", str(cm.exception))

    def test_old_key_and_tls_values_are_errors(self):
        f = self.fleet({"defaults": self.base_defaults(coreos={"systemd_units": []}, tls={"cert_days": 0}),
                        "hosts": {"a": {}}})
        errs = f.hosts["a"].validate(self.roles)
        self.assertTrue(any("coreos.units" in e for e in errs), errs)
        self.assertTrue(any("tls.cert_days" in e for e in errs), errs)

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
        self.assertEqual(sorted(self.roles), ["bare", "master", "node-cloud", "node-gw"])
        nc = self.roles["node-cloud"]
        self.assertTrue(nc.stack)
        self.assertIn("vpn_ip", [s.key for s in nc.settings])
        self.assertEqual(self.ms.resolve(nc.modules), ["vpn-client", "reverse-proxy", "cloud", "vault", "portainer"])
        self.assertTrue(json.dumps(nc.as_dict(self.ms)))

    def test_script_has_settings_files_and_runs_main(self):
        f = self.fleet({"defaults": self.base_defaults(post_script="echo hi"), "hosts": {
            "n": {"role": "node-cloud", "node-cloud": self.node_cloud(variables={"FOO": "bar baz"})}}})
        host = f.hosts["n"]
        role = self.prepare(host)
        bundle = modmod.Renderer(self.ms).render(rolesmod.stack_spec(host, role))
        s = rolesmod.render_script(host, role, VERSION, bundle=bundle)
        self.assertIn("KS_HOSTNAME='n.kiwi'", s)
        self.assertIn("KS_ROLE_VPN_IP='10.8.0.25'", s)
        self.assertIn("KS_FILES[stack/kn-vpn-client/wg0.conf]=", s)
        self.assertIn("KS_FILES[stack/docker-compose.yml]=", s)
        self.assertIn("KS_STACK=1", s)
        self.assertIn("declare -A KS_ROLE_VARIABLES=(['FOO']='bar baz')", s)
        self.assertIn("ks_role_apply()", s)
        self.assertIn("\necho hi\n", s)            # pasted as written: a heredoc inside must still work
        self.assertIn("KS_STACK_USER='core'", s)    # the admin user unless service_user is set
        self.assertIn("KS_STACK_VPN_DEPENDENTS=()", s)
        self.assertTrue(s.rstrip().endswith('ks_main "$@"'))
        # the embedded file round-trips
        import base64, re
        m = re.search(r"KS_FILES\[stack/kn-vpn-client/wg0.conf\]='?([A-Za-z0-9+/=]+)'?", s)
        self.assertEqual(base64.b64decode(m.group(1)).decode(), "[Interface]\nPrivateKey=x\n")

    def test_bash_parses_and_shellcheck_passes_every_role(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {
            "bare": {"role": "bare", "target": "debian", "bare": {"packages": ["vim"]}},
            "node": {"role": "node-cloud", "node-cloud": self.node_cloud()},
            "gw": {"role": "node-gw", "node-gw": self.node_gw()},
            "gate": {"role": "master", "target": "debian", "master": self.master()}}})
        for name in f.hosts:
            s, _b = self.full_script(f, name)
            p = os.path.join(self.tmp, name + ".sh")
            write(p, s)
            r = subprocess.run(["bash", "-n", p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            if have("shellcheck"):
                r = subprocess.run(["shellcheck", "-S", "warning", p], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_kiwi_stack_helper_parses_and_shellchecks(self):
        lib = rolesmod.common_lib()
        helper = lib[lib.index("<<'KIWI_STACK'\n") + len("<<'KIWI_STACK'\n"):lib.index("\nKIWI_STACK\n")]
        self.assertTrue(helper.startswith("#!/usr/bin/env bash"))
        self.assertIn("host_units restart --no-block", helper)   # never a blocking restart from inside kiwi-stack.service
        self.assertIn("${STACK_VPN_DEPENDENTS:-}", helper)
        p = os.path.join(self.tmp, "kiwi-stack")
        write(p, helper)
        self.assertEqual(subprocess.run(["bash", "-n", p], capture_output=True).returncode, 0)
        if have("shellcheck"):
            r = subprocess.run(["shellcheck", "-S", "warning", p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout)
        # the library itself: a re-run restarts the stack, the docker group comes from /usr/lib/group
        self.assertIn("systemctl restart kiwi-stack.service", lib)
        self.assertIn("/usr/lib/group", lib)
        self.assertNotIn("systemctl enable --now kiwi-stack.service", lib)

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

    def test_any_day_means_every_day_inside_the_window(self):
        _, bu = self.butane({"defaults": self.base_defaults(target="coreos", updates={"days": []}),
                             "hosts": {"a": {}}}, "a")
        z = [f for f in bu["storage"]["files"] if "zincati" in f["path"]][0]["contents"]["inline"]
        self.assertIn('strategy = "periodic"', z)
        self.assertNotIn("immediate", z)
        self.assertIn('days = [ "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun" ]', z)

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
                                  "n": {"role": "node-cloud", "node-cloud": self.node_cloud()}}})
        for name in f.hosts:
            bu = os.path.join(self.tmp, name + ".bu")
            script, _b = self.full_script(f, name)
            write(bu, coreos_t.render(f.hosts[name], script))
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

    def test_preseed_admin_groups(self):
        _, ps, _ = self.make(admin={"groups": ["docker", "video"]})
        # docker does not exist at install time; the role adds it once docker is installed
        self.assertIn("d-i passwd/user-default-groups string sudo video\n", ps)

    def test_late_sh_is_posix_sh(self):
        _, _, files = self.make()
        p = os.path.join(self.tmp, "late.sh")
        write(p, files["late.sh"][0])
        self.assertEqual(subprocess.run(["sh", "-n", p]).returncode, 0)
        if have("shellcheck"):
            r = subprocess.run(["shellcheck", "-S", "warning", "-s", "sh", p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout)


class TestIso(Base):
    def test_other_release_needs_iso_url(self):
        with self.assertRaisesRegex(KiwiError, "debian.iso_url"):
            iso.debian_base_iso(None, {"release": "bookworm"}, self.tmp)

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
        # (% binds tighter than +: "… % self.tmp + '/tree'" once cd'd into the wrong directory)
        subprocess.run("cd %s/install.amd && echo hello | cpio -H newc -o 2>/dev/null | gzip > initrd.gz && rm hello"
                       % tree, shell=True, check=True)
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


class TestModules(Base):
    def spec(self, name, **over):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {name: over}})
        host = f.hosts[name]
        self.assertEqual(host.validate(self.roles), [])
        role = self.roles[host.role]
        rolesmod.resolve_settings(role, host, self.ms)
        return host, role, rolesmod.stack_spec(host, role, [("10.8.0.25", "sh3.kiwi"), ("10.8.0.25", "cloud.sh3.kiwi"),
                                                            ("10.8.0.6", "m1.kiwi"), ("10.8.0.1", "gate.kiwi")])

    def test_loader_and_resolution(self):
        ms = self.ms
        self.assertEqual(len(ms.names()), 12)
        self.assertEqual(ms.resolve(["cloud"]), ["vpn-client", "reverse-proxy", "cloud"])
        self.assertEqual(ms.resolve(["dhcp-relay"]), ["dns", "dhcp-relay"])
        with self.assertRaises(KiwiError):
            ms.resolve(["nope"])
        with self.assertRaises(KiwiError):
            ms.resolve(["vpn-server"], "node")   # master only
        with self.assertRaises(KiwiError):
            ms.resolve(["cloud"], "master")      # node only

    def test_node_cloud_bundle(self):
        host, role, spec = self.spec("sh3", role="node-cloud", **{"node-cloud": self.node_cloud()})
        b = modmod.Renderer(self.ms).render(spec)
        self.assertEqual(b.container_ips["vpn-client"], "172.128.0.2")
        self.assertEqual(b.container_ips["reverse-proxy"], "172.128.0.5")
        self.assertEqual(b.container_ips["portainer"], "172.128.0.250")
        compose = b.compose
        self.assertEqual(set(compose["services"]), {"kn-vpn-client", "kn-nginx", "nextcloud-aio-mastercontainer",
                                                    "kn-vault", "kn-portainer"})
        self.assertIn("nextcloud-aio", compose["networks"])
        self.assertIn("nextcloud-aio", compose["services"]["kn-nginx"]["networks"])
        self.assertEqual(compose["services"]["nextcloud-aio-mastercontainer"]["networks"]["knet-node"]["ipv4_address"],
                         "172.128.0.6")
        env = compose["services"]["kn-vpn-client"]["environment"]
        self.assertIn("FIREWALL_VPN_INPUT_PORTS=80,443", env)
        self.assertNotIn("WIREGUARD_PRIVATE_KEY=", "\n".join(env))
        self.assertIn("/home/user/docker/kn-vpn-client/wg0.conf:/gluetun/wireguard/wg0.conf:z",
                      compose["services"]["kn-vpn-client"]["volumes"])
        post = b.files["kn-vpn-client/post-rules.txt"][0]
        self.assertIn("-d 10.8.0.25 -p tcp --dport 443 -j DNAT --to-destination 172.128.0.5:443", post)
        nginx = b.files["kn-nginx/nginx.conf"][0]
        for name in ("cloud.sh3.kiwi", "nc-admin.sh3.kiwi", "vault.sh3.kiwi", "portainer.sh3.kiwi"):
            self.assertIn("server_name %s;" % name, nginx)
        # names resolved per request: nginx starts before AIO's containers exist
        self.assertIn("resolver 127.0.0.11 valid=30s ipv6=off;", nginx)
        self.assertIn("set $backend http://nextcloud-aio-apache:11000;", nginx)
        self.assertIn("set $backend https://nextcloud-aio-mastercontainer:8080;", nginx)
        self.assertIn("set $backend http://kn-vault:80;", nginx)   # the upstream's server, inlined
        self.assertNotIn("upstream ", nginx)
        self.assertNotIn("proxy_pass http", nginx)
        self.assertIn("proxy_pass $backend;", nginx)
        self.assertIn("proxy_ssl_verify off;", nginx)
        self.assertEqual(nginx.count("Strict-Transport-Security"), 4)   # hsts on, every server block
        aio = compose["services"]["nextcloud-aio-mastercontainer"]
        self.assertEqual(aio["security_opt"], ["label:disable"])
        self.assertEqual(aio["ports"], ["127.0.0.1:8080:8080"])
        self.assertEqual(compose["services"]["kn-portainer"]["security_opt"], ["label:disable"])
        self.assertNotIn("ESTABLISHED,RELATED,NEW", post)
        self.assertIn("-i tun0 -m conntrack --ctstate DNAT -j ACCEPT", post)
        self.assertLess(post.index("-i eth0 -o eth0 -j REJECT"), post.index("--ctstate ESTABLISHED,RELATED -j ACCEPT"))
        self.assertEqual(b.vpn_dependents, [])
        self.assertEqual(b.files["kn-vpn-client/wg0.conf"][1], 0o600)
        self.assertIn("kn-nginx", b.dirs)
        self.assertIn("knnc-data", b.dirs)  # inside the stack dir: kept relative
        self.assertEqual(sorted(b.ports), ["3478/tcp", "3478/udp", "443/tcp", "80/tcp"])
        self.assertEqual(b.units, {})
        self.assertEqual([n for _ip, n in spec.dns_records][:2], ["sh3.kiwi", "cloud.sh3.kiwi"])

    def test_node_gw_bundle(self):
        host, role, spec = self.spec("m1", role="node-gw", **{"node-gw": self.node_gw()})
        b = modmod.Renderer(self.ms).render(spec)
        svcs = b.compose["services"]
        self.assertEqual(svcs["kn-transmission"]["network_mode"], "service:kn-vpn-client")
        self.assertEqual(svcs["kn-dhcphelper"]["network_mode"], "host")
        self.assertEqual(svcs["kn-dhcphelper"]["environment"]["IP"], "172.128.0.4")
        self.assertIn("127.0.0.1:8080:80/tcp", svcs["kn-pihole"]["ports"])  # the preset exposes the web UI, locally
        self.assertEqual(svcs["kn-pihole"]["hostname"], "pihole")
        self.assertIn("FTLCONF_webserver_api_password=p", svcs["kn-pihole"]["environment"])
        self.assertIn("FTLCONF_dns_upstreams=172.128.0.2", svcs["kn-pihole"]["environment"])
        self.assertNotIn("command", svcs["kn-sftp"])   # the password is in users.conf, not in docker inspect
        self.assertEqual(b.files["kn-sftp/users.conf"], ("user:s:1000\n", 0o600))
        self.assertIn("/home/user/docker/kn-sftp/users.conf:/etc/sftp/users.conf:ro,z", svcs["kn-sftp"]["volumes"])
        self.assertIn("/mnt/dl:/home/user/Downloads:z", svcs["kn-sftp"]["volumes"])
        self.assertIn("kn-gateway.service", b.units)
        self.assertIn("/home/user/docker/kiwi/gw.sh start", b.units["kn-gateway.service"])
        gw = b.files["kiwi/gw.sh"][0]
        self.assertIn('PUB_IFACE="eth0"', gw)
        self.assertIn('CONTAINER_IP="172.128.0.2"', gw)
        self.assertIn("ip route add table 128 throw", gw)
        self.assertEqual(b.files["kiwi/gw.sh"][1], 0o755)
        self.assertEqual(b.sysctl, {"net.ipv4.ip_forward": "1"})
        post = b.files["kn-vpn-client/post-rules.txt"][0]
        self.assertIn("--dport 2224 -j DNAT --to-destination 172.128.0.251:22", post)
        hosts = [e for e in svcs["kn-pihole"]["environment"] if e.startswith("FTLCONF_dns_hosts=")][0]
        self.assertIn("10.8.0.25 cloud.sh3.kiwi;", hosts)    # pihole.toml dns.hosts, not custom.list
        self.assertNotIn("kn-pihole/etc-pihole/hosts/custom.list", b.files)
        self.assertEqual(b.vpn_dependents, ["kn-transmission", "kn-jdownloader"])
        jd = svcs["kn-jdownloader"]["environment"]
        self.assertIn("WEB_AUTHENTICATION=1", jd)
        self.assertIn("WEB_AUTHENTICATION_PASSWORD=t", jd)   # the transmission password unless set
        self.assertIn("SECURE_CONNECTION=1", jd)
        nginx = b.files["kn-nginx/nginx.conf"][0]
        self.assertIn("set $backend https://kn-vpn-client:5800;", nginx)
        self.assertIn("-i eth0 -o tun0 -j ACCEPT", post)      # LAN clients the gateway routes here
        # gw.sh: -I inserts on top, so the DROP must be inserted last to be checked first
        inserts = [ln for ln in gw.splitlines() if ln.startswith("iptables -I FORWARD")]
        self.assertTrue(inserts[-1].endswith('-d "$DOCKER_SUBNET" -j DROP'), inserts)
        self.assertEqual(b.files["kn-portainer/admin-password"][0], "")
        self.assertNotIn("command", svcs["kn-portainer"])
        self.assertIn("2224/tcp", b.ports)
        self.assertTrue(any("dl.m1.kiwi" in s for s in [b.files["kn-nginx/nginx.conf"][0]]))

    def test_master_bundle(self):
        host, role, spec = self.spec("gate", role="master", **{"master": self.master()})
        self.assertEqual(spec.vpn_ip, "10.8.0.1")
        b = modmod.Renderer(self.ms).render(spec)
        svcs = b.compose["services"]
        self.assertEqual(set(svcs), {"km-vpn-client", "km-vpn-server", "km-pihole", "km-tor"})
        self.assertEqual(svcs["km-pihole"]["network_mode"], "service:km-vpn-server")
        self.assertEqual(svcs["km-tor"]["network_mode"], "service:km-vpn-server")
        self.assertEqual(svcs["km-vpn-server"]["networks"]["knet-master"]["ipv4_address"], "172.64.0.3")
        self.assertIn("127.0.0.1:8080:80/tcp", svcs["km-vpn-server"]["ports"])
        self.assertIn("51820:51820/udp", svcs["km-vpn-server"]["ports"])
        env = svcs["km-vpn-client"]["environment"]
        self.assertIn("VPN_SERVICE_PROVIDER=mullvad", env)
        self.assertIn("WIREGUARD_PRIVATE_KEY=k", env)
        self.assertIn("SERVER_COUNTRIES=Germany", env)
        self.assertNotIn("hostname", svcs["km-pihole"])   # not allowed together with network_mode
        start = b.files["km-vpn-server/start.sh"][0]
        self.assertIn("ip route add default via 172.64.0.2", start)
        # the isolation rules come before the wg0 accept-all, or they never match
        self.assertLess(start.index("-s 10.8.1.0/24 -d 10.8.0.0/24 -j DROP"), start.index("-A FORWARD -i wg0 -j ACCEPT"))
        self.assertIn("-i wg0 -s 10.8.1.0/24 -p tcp --dport 51821 -j DROP", start)
        self.assertNotIn("-A INPUT -i wg0 -p tcp --dport 51821 -j DROP", start)   # admin_from_mesh: true
        post = b.files["km-vpn-client/post-rules.txt"][0]
        self.assertNotIn("ctstate DNAT -j ACCEPT", post)   # nothing is DNAT'd on a master
        self.assertNotIn("ESTABLISHED,RELATED,NEW", post)
        self.assertIn("-s 10.8.1.0/24 -d 10.8.0.25 -j ACCEPT", start)
        self.assertIn("-s 10.8.1.0/24 -d 10.8.0.1 -j ACCEPT", start)
        self.assertIn("-d 172.64.0.0/24 -j DROP", start)
        torrc = b.files["km-tor/etc/torrc"][0]
        self.assertIn("SOCKSPort 10.8.0.1:9050", torrc)
        self.assertIn("EntryNodes {de},{ch},{at},{nl},{fr}", torrc)
        self.assertIn("ExcludeNodes {ad},{ae}", torrc)
        self.assertNotIn("{de},{ch},{at},{nl},{fr},{ga}", torrc)
        self.assertIn("mss-to-pmtu", b.files["km-vpn-client/post-rules.txt"][0])
        self.assertEqual(b.ports, ["51820/udp"])
        self.assertIn("wireguard", b.kernel_modules)
        self.assertEqual(b.files["docker-compose.yml"][1], 0o600)

    def test_subnet_math_and_container_ip_checks(self):
        host, role, spec = self.spec("n", role="node-cloud", **{"node-cloud": self.node_cloud(
            docker_subnet="10.200.4.128/25", modules=["vpn-client", "reverse-proxy", "vault"])})
        b = modmod.Renderer(self.ms).render(spec)
        self.assertEqual(b.container_ips, {"vpn-client": "10.200.4.130", "reverse-proxy": "10.200.4.133",
                                           "vault": "10.200.4.135"})
        self.assertEqual(b.compose["networks"]["knet-node"]["ipam"]["config"][0]["gateway"], "10.200.4.129")
        host, role, spec = self.spec("n", role="node-cloud", **{"node-cloud": self.node_cloud(docker_subnet="10.200.4.128/25")})
        with self.assertRaisesRegex(KiwiError, "no room for portainer"):
            modmod.Renderer(self.ms).render(spec)
        for bad, msg in (({"container_ip": "10.9.9.9"}, "outside"), ({"container_ip": "172.128.0.5"}, "already used"),
                         ({"container_ip": "172.128.0.1"}, "gateway"), ({"container_ip": "nope"}, "not an IPv4")):
            host, role, spec = self.spec("n", role="node-cloud", **{"node-cloud": self.node_cloud(vault=bad)})
            with self.assertRaisesRegex(KiwiError, msg):
                modmod.Renderer(self.ms).render(spec)
        host, role, spec = self.spec("n", role="node-cloud", **{"node-cloud": self.node_cloud(docker_subnet="10.0.0.0/30")})
        with self.assertRaisesRegex(KiwiError, "too small"):
            modmod.Renderer(self.ms).render(spec)

    def test_vpn_ip_required_derived_and_checked(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {"n": {"role": "node-cloud", "node-cloud": {
            "vpn-client": {"wireguard_config": "secrets/wg.conf"}}}}})
        host = f.hosts["n"]
        role = self.prepare(host)
        self.assertEqual(host.role_settings["vpn_ip"], "")
        with self.assertRaisesRegex(KiwiError, "vpn_ip .*cloud, vault"):
            modmod.Renderer(self.ms).render(rolesmod.stack_spec(host, role))
        # a wg-easy client config carries the mesh address
        write(os.path.join(self.tmp, "secrets", "wg.conf"), "[Interface]\nPrivateKey = x\nAddress = 10.8.0.77/24\n")
        f = config.Fleet.load(f.path)
        host = f.hosts["n"]
        self.prepare(host)
        self.assertEqual(host.role_settings["vpn_ip"], "10.8.0.77")
        self.assertEqual(host.warnings, [])
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {"n": {"role": "node-cloud", "node-cloud": self.node_cloud()}}})
        host = f.hosts["n"]
        self.prepare(host)
        self.assertEqual(host.role_settings["vpn_ip"], "10.8.0.25")
        self.assertTrue(any("Address is 10.8.0.77" in w for w in host.warnings), host.warnings)

    def test_hsts_setting_is_the_fallback(self):
        host, role, spec = self.spec("n", role="node-cloud", **{"node-cloud": self.node_cloud(**{"reverse-proxy": {"hsts": False}})})
        nginx = modmod.Renderer(self.ms).render(spec).files["kn-nginx/nginx.conf"][0]
        self.assertEqual(nginx.count("Strict-Transport-Security"), 1)   # only the cloud block asks for it itself

    def test_null_setting_means_the_default_and_dollar_is_escaped(self):
        host, role, spec = self.spec("m1", role="node-gw", **{"node-gw": self.node_gw(dns={"pihole_password": "pa$$w0rd", "web_ui_port": None})})
        self.assertEqual(host.module_settings["dns"]["web_ui_port"], 8080)
        b = modmod.Renderer(self.ms).render(spec)
        self.assertIn("FTLCONF_webserver_api_password=pa$$$$w0rd", b.files["docker-compose.yml"][0])

    def test_pihole_dhcp_and_extra_env(self):
        host, role, spec = self.spec("m1", role="node-gw", **{"node-gw": self.node_gw(dns={
            "pihole_password": "p", "dhcp_enabled": True, "dhcp_start": "192.168.1.100", "dhcp_end": "192.168.1.200",
            "dhcp_router": "192.168.1.1", "extra_env": {"FTLCONF_dns_domainNeeded": "true"}})})
        env = modmod.Renderer(self.ms).render(spec).compose["services"]["kn-pihole"]["environment"]
        for e in ("FTLCONF_dhcp_active=true", "FTLCONF_dhcp_start=192.168.1.100", "FTLCONF_dhcp_router=192.168.1.1",
                  "FTLCONF_dhcp_leaseTime=24h", "FTLCONF_dns_domainNeeded=true"):
            self.assertIn(e, env)

    def test_portainer_admin_password_and_master_admin_pages(self):
        host, role, spec = self.spec("m1", role="node-gw", **{"node-gw": self.node_gw(portainer={"admin_password": "adm"})})
        b = modmod.Renderer(self.ms).render(spec)
        self.assertEqual(b.files["kn-portainer/admin-password"], ("adm", 0o600))
        self.assertEqual(b.compose["services"]["kn-portainer"]["command"], "--admin-password-file /run/kiwi/portainer-admin")
        host, role, spec = self.spec("gate", role="master", **{"master": self.master(**{"vpn-server": {
            "wg_host": "v", "wg_password": "w", "admin_from_mesh": False}})})
        start = modmod.Renderer(self.ms).render(spec).files["km-vpn-server/start.sh"][0]
        self.assertIn("-A INPUT -i wg0 -p tcp --dport 51821 -j DROP", start)

    def test_module_errors_are_readable(self):
        """A broken module.yaml is a KiwiError naming the place, never a traceback;
        two modules installing the same host unit is an error like two files are."""
        mdir = os.path.join(self.tmp, "modules")
        shutil.copytree(modmod.modules_dir(), mdir)
        shutil.copytree(os.path.join(mdir, "gateway"), os.path.join(mdir, "gateway2"))
        y = os.path.join(mdir, "gateway2", "module.yaml")
        with open(y) as fh:
            text = fh.read()
        write(y, text.replace("name: gateway", "name: gateway2").replace("kiwi/gw.sh", "kiwi/gw2.sh"))
        os.makedirs(os.path.join(mdir, "broken"))
        write(os.path.join(mdir, "broken", "module.yaml"),
              "module: {name: broken}\nnginx: {servers: [{server_name: x}]}\n"
              "storage: {bind_mounts: [{host: '{{ nope(', type: dir, required: false}]}\n")
        ms = modmod.ModuleSet(mdir)
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {"n": {"role": "node-gw", "node-gw": self.node_gw()}}})
        host = f.hosts["n"]
        rolesmod.resolve_settings(self.roles["node-gw"], host, ms)
        spec = rolesmod.stack_spec(host, self.roles["node-gw"])
        spec.modules = ms.resolve(["vpn-client", "gateway", "gateway2"])
        with self.assertRaisesRegex(KiwiError, "host unit kn-gateway.service"):
            modmod.Renderer(ms).render(spec)
        spec.modules = ms.resolve(["vpn-client", "reverse-proxy", "broken"])
        with self.assertRaisesRegex(KiwiError, "modules/broken/module.yaml nginx"):
            modmod.Renderer(ms).render(spec)
        write(os.path.join(mdir, "broken", "module.yaml"),
              "module: {name: broken}\nstorage: {bind_mounts: [{host: '{{ nope(', type: dir, required: false}]}\n")
        ms = modmod.ModuleSet(mdir)
        spec.modules = ms.resolve(["vpn-client", "broken"])
        with self.assertRaisesRegex(KiwiError, "template error"):   # optional, but broken is broken
            modmod.Renderer(ms).render(spec)

    def test_modules_override_and_module_defaults(self):
        host, role, spec = self.spec("g", role="node-gw", **{"node-gw": {
            "vpn_ip": "10.8.0.6", "vpn-client": {"wireguard_config": "secrets/wg.conf"},
            "modules": ["vpn-client", "dns", "gateway"], "dns": {"pihole_password": "p", "expose_web_ui": False}}})
        self.assertEqual(host.modules, ["vpn-client", "dns", "gateway"])
        b = modmod.Renderer(self.ms).render(spec)
        self.assertNotIn("8080:80/tcp", b.compose["services"]["kn-pihole"]["ports"])
        self.assertNotIn("kn-nginx", b.compose["services"])

    def test_rendered_scripts_are_clean(self):
        host, role, spec = self.spec("m1", role="node-gw", **{"node-gw": self.node_gw()})
        b = modmod.Renderer(self.ms).render(spec)
        for rel, shell in (("kiwi/gw.sh", "bash"),):
            p = os.path.join(self.tmp, os.path.basename(rel))
            write(p, b.files[rel][0])
            self.assertEqual(subprocess.run([shell, "-n", p]).returncode, 0)
            if have("shellcheck"):
                r = subprocess.run(["shellcheck", "-S", "warning", "-s", shell, p], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stdout)
        host, role, spec = self.spec("gate", role="master", **{"master": self.master()})
        b = modmod.Renderer(self.ms).render(spec)
        p = os.path.join(self.tmp, "start.sh")
        write(p, b.files["km-vpn-server/start.sh"][0])
        self.assertEqual(subprocess.run(["sh", "-n", p]).returncode, 0)

    @unittest.skipUnless(have("docker"), "docker cli not installed")
    def test_compose_config_validates(self):
        r = subprocess.run(["docker", "compose", "version"], capture_output=True)
        if r.returncode != 0:
            self.skipTest("docker compose plugin not installed")
        for name, role, block in (("sh3", "node-cloud", self.node_cloud()), ("m1", "node-gw", self.node_gw()),
                                  ("gate", "master", self.master())):
            host, r_, spec = self.spec(name, role=role, **{role: block})
            b = modmod.Renderer(self.ms).render(spec)
            p = os.path.join(self.tmp, name + "-compose.yml")
            write(p, b.files["docker-compose.yml"][0])
            res = subprocess.run(["docker", "compose", "-f", p, "config", "-q"], capture_output=True, text=True,
                                 env={**os.environ, "DOCKER_HOST": "unix:///nonexistent"})
            self.assertEqual(res.returncode, 0, res.stderr)


@unittest.skipUnless(have("openssl"), "openssl not installed")
class TestCerts(Base):
    def test_ca_and_host_cert(self):
        ca = os.path.join(self.tmp, "ca")
        key, pem = certs.ensure_ca(ca)
        self.assertEqual(oct(os.stat(key).st_mode & 0o777), "0o600")
        self.assertEqual((key, pem), certs.ensure_ca(ca))  # reused
        hkey, full = certs.ensure_host_cert(ca, "sh3.kiwi")
        self.assertEqual(set(certs.cert_names(os.path.join(ca, "hosts", "sh3.kiwi", "cert.pem"))),
                         {"sh3.kiwi", "*.sh3.kiwi"})
        r = subprocess.run(["openssl", "verify", "-CAfile", pem, os.path.join(ca, "hosts", "sh3.kiwi", "cert.pem")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(full) as fh:
            self.assertEqual(fh.read().count("BEGIN CERTIFICATE"), 2)
        self.assertEqual((hkey, full), certs.ensure_host_cert(ca, "sh3.kiwi"))  # reused
        crt = os.path.join(ca, "hosts", "sh3.kiwi", "cert.pem")
        self.assertTrue(certs.cert_valid(crt, 800))     # 825 days, not the CA's ten years
        self.assertFalse(certs.cert_valid(crt, 830))

    def test_name_constraints(self):
        ca = os.path.join(self.tmp, "ca2")
        _key, pem = certs.ensure_ca(ca, name_constraints=["kiwi", ".lan"])
        text = subprocess.run(["openssl", "x509", "-in", pem, "-noout", "-text"], capture_output=True, text=True).stdout
        self.assertIn("Name Constraints", text)
        self.assertIn("DNS:.kiwi", text)
        self.assertIn("DNS:.lan", text)


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
        data = json.loads(out)
        self.assertEqual(sorted(r["name"] for r in data["roles"]), ["bare", "master", "node-cloud", "node-gw"])
        self.assertIn("vpn-client", [m["name"] for m in data["modules"]])
        master = [r for r in data["roles"] if r["name"] == "master"][0]
        self.assertEqual([m["name"] for m in master["module_schemas"]], ["vpn-client", "vpn-server", "dns", "tor"])
        rc, out, _ = self.run_cli("modules", "--porcelain")
        self.assertEqual(len(json.loads(out)["modules"]), 12)
        rc, out, _ = self.run_cli("targets", "--porcelain")
        self.assertEqual(set(json.loads(out)["targets"]), {"coreos", "ucore", "debian"})

    def test_validate_render_list_show(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {
            "u": {}, "d": {"target": "debian", "role": "bare"},
            "n": {"role": "node-cloud", "node-cloud": self.node_cloud()}}})
        out_dir = os.path.join(self.tmp, "out")
        rc, out, err = self.run_cli("validate", f.path)
        self.assertEqual(rc, 0, err)
        rc, out, err = self.run_cli("render", f.path, "-o", out_dir, "--toolchain", "native")
        self.assertEqual(rc, 0, err)
        for p in ("u/u.role.sh", "u/u.bu", "d/d.role.sh", "d/d.preseed.cfg", "d/d.kiwi-server/late.sh",
                  "n/n.bu", "n/README.txt", "n/n.stack/docker-compose.yml", "n/n.stack/kn-nginx/nginx.conf",
                  "n/n.stack/kn-nginx/fullchain.pem"):
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
        self.assertEqual(data["role_settings"]["vpn_ip"], "10.8.0.25")
        self.assertEqual(data["modules"], ["vpn-client", "reverse-proxy", "cloud", "vault", "portainer"])
        self.assertNotIn("node-cloud", data["config"])
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "secrets", "ca", "kiwiCA.pem")))
        rc, out, _ = self.run_cli("script", f.path, "n")
        self.assertEqual(rc, 0)
        self.assertIn("ks_role_apply", out)

    def test_fleet_scan_records_ca_and_warnings(self):
        """The fleet CA exists before the first script is rendered (whatever the
        order), every Pi-hole gets every host's names, the master lets isolated
        clients reach every node, and a second validation does not repeat warnings."""
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {
            "lab": {"target": "coreos", "role": "bare", "disk": None},
            "sh3": {"role": "node-cloud", "node-cloud": self.node_cloud()},
            "m1": {"role": "node-gw", "node-gw": self.node_gw()},
            "gate": {"role": "master", "target": "debian", "master": self.master()}}})
        r = cli.Renderer(f, self.roles, os.path.join(self.tmp, "out"), Toolchain(mode="native"), self.ms)
        lab, _ = r.script(f.hosts["lab"])          # rendered first, before any reverse proxy
        self.assertIn("KS_FILES[ca_cert]=", lab)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "secrets", "ca", "kiwiCA.pem")))
        recs = r.dns_records()
        for rec in (("10.8.0.25", "sh3.kiwi"), ("10.8.0.25", "cloud.sh3.kiwi"), ("10.8.0.25", "portainer.sh3.kiwi"),
                    ("10.8.0.6", "m1.kiwi"), ("10.8.0.6", "pihole.m1.kiwi"), ("10.8.0.1", "gate.kiwi")):
            self.assertIn(rec, recs)
        self.assertNotIn(("10.8.0.25", "*.sh3.kiwi"), recs)
        m1, b = r.script(f.hosts["m1"])
        self.assertIn("KS_STACK_NO_RESOLVED_STUB=1", m1)
        hosts = [e for e in b.compose["services"]["kn-pihole"]["environment"] if e.startswith("FTLCONF_dns_hosts=")][0]
        self.assertIn("10.8.0.25 cloud.sh3.kiwi", hosts)
        self.assertIn("10.8.0.1 gate.kiwi", hosts)
        sh3, _ = r.script(f.hosts["sh3"])
        self.assertIn("KS_STACK_NO_RESOLVED_STUB=0", sh3)
        self.assertIn("KS_FILES[stack/kn-nginx/privkey.pem]=", sh3)
        _gate, gb = r.script(f.hosts["gate"])
        start = gb.files["km-vpn-server/start.sh"][0]
        self.assertIn("-s 10.8.1.0/24 -d 10.8.0.25 -j ACCEPT", start)
        self.assertIn("-s 10.8.1.0/24 -d 10.8.0.6 -j ACCEPT", start)
        self.assertEqual([w for w in f.hosts["lab"].warnings if "no disk" in w], ["no disk: set — configs render, but an ISO cannot be built"])

    def test_tls_settings_are_validated(self):
        write(os.path.join(self.tmp, "secrets", "full.pem"), "x")
        f = self.fleet({"defaults": self.base_defaults(tls={"auto": False}), "hosts": {
            "n": {"role": "node-cloud", "node-cloud": self.node_cloud()},
            "h": {"role": "node-cloud", "node-cloud": self.node_cloud(**{"reverse-proxy": {"tls_fullchain": "secrets/full.pem"}})}}})
        rc, out, err = self.run_cli("validate", f.path)
        self.assertEqual(rc, cli.EXIT_INVALID)
        self.assertIn("n: reverse-proxy needs tls_fullchain and tls_privkey", err)
        self.assertIn("h: reverse-proxy: tls_fullchain and tls_privkey go together", err)
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {
            "h": {"role": "node-cloud", "node-cloud": self.node_cloud(**{"reverse-proxy": {"tls_privkey": "secrets/full.pem"}})}}})
        rc, out, err = self.run_cli("validate", f.path)
        self.assertEqual(rc, cli.EXIT_INVALID)
        self.assertIn("go together", err)

    def test_script_prints_only_the_script(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {"n": {"role": "node-cloud", "node-cloud": self.node_cloud()}}})
        rc, out, err = self.run_cli("script", f.path, "n")   # a fresh fleet: the CA is created on the way
        self.assertEqual(rc, 0, err)
        self.assertTrue(out.startswith("#!/usr/bin/env bash\n"), out[:80])
        self.assertNotIn("\n:: ", out)
        self.assertNotIn("creating the fleet CA", out)

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
        # the example validates once its secrets exist (throw-away ones here)
        home = os.path.join(self.tmp, "home")
        write(os.path.join(home, ".ssh", "id_ed25519.pub"), KEY + "\n")
        write(os.path.join(self.tmp, "secrets", "m1.kiwi.conf"), "[Interface]\nPrivateKey = x\nAddress = 10.8.0.6/24\n")
        write(os.path.join(self.tmp, "secrets", "sh3.kiwi.conf"), "[Interface]\nPrivateKey = x\nAddress = 10.8.0.25/24\n")
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            rc, out, err = self.run_cli("validate", dest, "--porcelain")
        finally:
            os.environ["HOME"] = old_home
        self.assertEqual(rc, 0, err)
        data = json.loads(out)
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["errors"], [])


if __name__ == "__main__":
    unittest.main()
