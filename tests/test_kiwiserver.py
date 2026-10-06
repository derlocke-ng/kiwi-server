"""Unit tests for kiwiserver. Run: python3 -m unittest discover -s tests -v

Anything that needs a tool (shellcheck, butane, xorriso, cpio) uses it when it
is on $PATH and is skipped otherwise, so the suite runs anywhere; CI installs
all of them. No test touches the network.
"""
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))
os.environ["KIWI_SERVER_HOME"] = ROOT

import yaml  # noqa: E402

from kiwiserver import VERSION, backup, certs, cli, config, iso, modules as modmod, remote, roles as rolesmod, router, wgeasy  # noqa: E402
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
        d = {"vpn_ip": "10.8.0.25", "vpn-client": {"wireguard_config": "secrets/wg.conf"},
             "cloud": {"admin_password": "a"}}
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

    def test_domain_advice_and_fleet_findings(self):
        self.assertIsNone(config.domain_advice("home"))
        self.assertIsNone(config.domain_advice("my.internal"))
        self.assertEqual(config.domain_advice("local")[0], "error")
        self.assertEqual(config.domain_advice("lan")[0], "warning")
        self.assertEqual(config.domain_advice("kiwi")[0], "warning")
        self.assertEqual(config.domain_advice("de")[0], "warning")
        f = self.fleet({"defaults": self.base_defaults(domain="home"), "hosts": {"a": {}}})
        self.assertEqual((f.errors, f.warnings), ([], []))
        self.assertEqual(f.hosts["a"].hostname, "a.home")
        f = self.fleet({"defaults": self.base_defaults(domain="dev", backup={"hosts": ["nope"], "keep": 0}), "hosts": {"a": {}}})
        self.assertTrue(any("public top-level domain" in w for w in f.warnings), f.warnings)
        self.assertTrue(any("no such host" in e for e in f.errors), f.errors)
        self.assertTrue(any("backup.keep" in e for e in f.errors), f.errors)
        f = self.fleet({"defaults": self.base_defaults(domain="local"), "hosts": {"a": {}}})
        self.assertTrue(any("mDNS" in e for e in f.errors), f.errors)
        d = self.base_defaults(); d.pop("domain")
        f = self.fleet({"defaults": d, "hosts": {"a": {}}})
        self.assertEqual(f.hosts["a"].hostname, "a.home")   # the fallback

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
        self.assertEqual(sorted(self.roles), ["bare", "master", "node", "node-cloud", "node-gw"])
        node = self.roles["node"]
        self.assertEqual(sorted(node.presets), ["cloud", "gateway", "minimal"])
        self.assertEqual(node.default_modules({"preset": "cloud"}), ["vpn-client", "reverse-proxy", "cloud", "vault", "portainer"])
        self.assertEqual(node.default_modules({}), node.presets["gateway"]["modules"])   # the first preset
        self.assertEqual(node.defaults_for("dns", {"preset": "gateway"}), {"expose_web_ui": True})
        self.assertEqual(node.defaults_for("dns", {"preset": "cloud"}), {})
        with self.assertRaisesRegex(KiwiError, "unknown preset"):
            node.default_modules({"preset": "nope"})
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
        self.assertIn("KS_STACK_MESH_VIA='172.128.0.2'", s)
        self.assertIn("KS_STACK_MESH_SUBNET='10.8.0.0/16'", s)
        self.assertIn("KS_STACK_RUNTIME='podman'", s)
        self.assertIn("KS_FILES[quadlet/kn-vpn-client.container]=", s)
        self.assertIn("KS_FILES[quadlet/kiwi-stack.target]=", s)
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
        self.assertIn('ip route replace "$STACK_MESH_SUBNET" via "$STACK_MESH_VIA"', helper)
        self.assertIn("podman auto-update", helper)
        self.assertIn("systemctl start kiwi-stack.target", helper)
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
                                                            ("10.8.0.6", "m1.kiwi"), ("10.8.0.1", "gate.kiwi")],
                                               master_ip="10.8.0.1")

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
        self.assertEqual(set(compose["services"]), {
            "kn-vpn-client", "kn-nginx", "kn-vault", "kn-portainer", "nextcloud-aio-apache", "nextcloud-aio-database",
            "nextcloud-aio-nextcloud", "nextcloud-aio-notify-push", "nextcloud-aio-redis", "nextcloud-aio-collabora",
            "nextcloud-aio-imaginary"})
        self.assertEqual(list(compose["networks"]), ["knet-node"])   # one stack network, AIO's containers on it too
        self.assertEqual(compose["services"]["nextcloud-aio-apache"]["networks"]["knet-node"]["ipv4_address"], "172.128.0.6")
        text = b.files["docker-compose.yml"][0]
        self.assertNotIn("docker.sock", text.replace("/run/podman/podman.sock:/var/run/docker.sock", ""))
        nc = compose["services"]["nextcloud-aio-nextcloud"]["environment"]
        self.assertIn("ADMIN_PASSWORD=a", nc)
        self.assertIn("NC_DOMAIN=cloud.sh3.kiwi", nc)
        self.assertIn("COLLABORA_ENABLED=yes", nc)
        self.assertIn("TALK_ENABLED=no", nc)
        self.assertNotIn("nextcloud-aio-talk", compose["services"])
        # generated secrets: letters and digits, the same at the next render, never in the fleet file
        dbpw = [e for e in nc if e.startswith("POSTGRES_PASSWORD=")][0].split("=", 1)[1]
        self.assertRegex(dbpw, r"^[A-Za-z0-9]{32}$")
        self.assertEqual(host.module_settings["cloud"]["database_password"], dbpw)
        host2, role2, spec2 = self.spec("sh3", role="node-cloud", **{"node-cloud": self.node_cloud()})
        self.assertEqual(host2.module_settings["cloud"]["database_password"], dbpw)
        self.assertNotEqual(host2.module_settings["cloud"]["redis_password"], dbpw)
        # the podman runtime: quadlets next to the compose file
        self.assertEqual(b.runtime, "podman")
        for name in ("kn-vpn-client.container", "kn-nginx.container", "nextcloud-aio-nextcloud.container",
                     "knet-node.network", "kiwi-stack.target"):
            self.assertIn(name, b.quadlets)
        vpn = b.quadlets["kn-vpn-client.container"]
        self.assertIn("Network=knet-node.network", vpn)
        self.assertIn("IP=172.128.0.2", vpn)
        self.assertIn("AddDevice=/dev/net/tun:/dev/net/tun", vpn)
        self.assertIn("AutoUpdate=registry", vpn)
        self.assertIn("WantedBy=kiwi-stack.target", vpn)
        self.assertIn("Image=docker.io/qmcgaw/gluetun", vpn)
        ncq = b.quadlets["nextcloud-aio-nextcloud.container"]
        self.assertIn('Environment="STARTUP_APPS=deck twofactor_totp tasks calendar contacts notes"', ncq)
        self.assertIn("After=nextcloud-aio-database.service", ncq)
        self.assertIn("HealthCmd=/healthcheck.sh", ncq)
        self.assertIn("StopTimeout=600", ncq)
        self.assertIn("ShmSize=134217728", ncq)
        self.assertIn("Volume=/run/podman/podman.sock:/var/run/docker.sock", b.quadlets["kn-portainer.container"])
        self.assertIn("SecurityLabelDisable=true", b.quadlets["kn-portainer.container"])
        net = b.quadlets["knet-node.network"]
        self.assertIn("Subnet=172.128.0.0/24", net)
        self.assertIn("Gateway=172.128.0.1", net)
        self.assertIn("Options=mtu=1412", net)
        env = compose["services"]["kn-vpn-client"]["environment"]
        self.assertIn("FIREWALL_VPN_INPUT_PORTS=80,443", env)
        self.assertNotIn("WIREGUARD_PRIVATE_KEY=", "\n".join(env))
        self.assertIn("/home/user/docker/kn-vpn-client/wg0.conf:/gluetun/wireguard/wg0.conf:z",
                      compose["services"]["kn-vpn-client"]["volumes"])
        post = b.files["kn-vpn-client/post-rules.txt"][0]
        self.assertIn("-d 10.8.0.25 -p tcp --dport 443 -j DNAT --to-destination 172.128.0.5:443", post)
        nginx = b.files["kn-nginx/nginx.conf"][0]
        for name in ("cloud.sh3.kiwi", "vault.sh3.kiwi", "portainer.sh3.kiwi"):
            self.assertIn("server_name %s;" % name, nginx)
        # names resolved per request through podman's DNS (the network's gateway)
        self.assertIn("resolver 172.128.0.1 valid=30s ipv6=off;", nginx)
        self.assertIn("set $backend http://nextcloud-aio-apache:11000;", nginx)
        self.assertIn("set $backend http://kn-vault:80;", nginx)   # the upstream's server, inlined
        self.assertNotIn("upstream ", nginx)
        self.assertNotIn("proxy_pass http", nginx)
        self.assertIn("proxy_pass $backend;", nginx)
        self.assertEqual(nginx.count("Strict-Transport-Security"), 3)   # hsts on, every server block
        self.assertEqual(compose["services"]["kn-portainer"]["security_opt"], ["label:disable"])
        self.assertNotIn("ESTABLISHED,RELATED,NEW", post)
        self.assertIn("-i tun0 -m conntrack --ctstate DNAT -j ACCEPT", post)
        self.assertLess(post.index("-i eth0 -o eth0 -j REJECT"), post.index("--ctstate ESTABLISHED,RELATED -j ACCEPT"))
        self.assertEqual(b.vpn_dependents, [])
        self.assertEqual(b.files["kn-vpn-client/wg0.conf"][1], 0o600)
        self.assertIn("kn-nginx", b.dirs)
        self.assertIn("knnc-data", b.dirs)  # inside the stack dir: kept relative
        self.assertEqual(sorted(b.ports), ["443/tcp", "80/tcp"])   # Talk's TURN port only with talk: true
        self.assertEqual(b.units, {})
        self.assertEqual([n for _ip, n in spec.dns_records][:2], ["sh3.kiwi", "cloud.sh3.kiwi"])

    def test_cloud_switches_and_docker_runtime(self):
        host, role, spec = self.spec("sh3", role="node-cloud", **{"node-cloud": self.node_cloud(runtime="docker", cloud={
            "admin_password": "a", "collabora": False, "talk": True, "fulltextsearch": True, "clamav": True})})
        b = modmod.Renderer(self.ms).render(spec)
        svcs = b.compose["services"]
        self.assertIn("nextcloud-aio-talk", svcs)
        self.assertIn("nextcloud-aio-fulltextsearch", svcs)
        self.assertNotIn("nextcloud-aio-collabora", svcs)
        self.assertIn("3478/udp", b.ports)
        self.assertEqual(svcs["nextcloud-aio-talk"]["ports"], ["3478:3478/tcp", "3478:3478/udp"])
        self.assertIn("nextcloud_aio_elasticsearch", b.compose["volumes"])
        # the docker runtime: compose only, docker's DNS, docker's socket
        self.assertEqual((b.runtime, b.quadlets), ("docker", {}))
        self.assertIn("resolver 127.0.0.11 valid=30s", b.files["kn-nginx/nginx.conf"][0])
        self.assertIn("/var/run/docker.sock:/var/run/docker.sock", svcs["kn-portainer"]["volumes"])
        with self.assertRaisesRegex(KiwiError, "collabora and onlyoffice"):
            host, role, spec = self.spec("sh3", role="node-cloud", **{"node-cloud": self.node_cloud(cloud={
                "admin_password": "a", "onlyoffice": True})})
            modmod.Renderer(self.ms).render(spec)
        with self.assertRaisesRegex(KiwiError, "runtime must be"):
            modmod.StackSpec("node", "x", ["vpn-client"], runtime="lxc")

    def test_quadlets_from_compose(self):
        from kiwiserver import quadlet
        compose = {"networks": {"knet-node": {"name": "knet-node", "driver": "bridge",
                                              "driver_opts": {"com.docker.network.driver.mtu": "1412"},
                                              "ipam": {"config": [{"subnet": "172.128.0.0/24", "gateway": "172.128.0.1"}]}}},
                   "services": {
                       "a": {"image": "docker.io/x/a", "container_name": "a", "networks": {"knet-node": {"ipv4_address": "172.128.0.9"}},
                             "environment": ["PASS=pa$$w0rd", "PCT=20%", "SP=a b"], "ports": ["127.0.0.1:8080:80/tcp"],
                             "cap_add": ["NET_ADMIN"], "sysctls": ["net.ipv4.ip_forward=1"], "restart": "unless-stopped",
                             "volumes": ["/data:/data:z", "vol:/v"], "security_opt": ["label:disable"], "init": True,
                             "healthcheck": {"test": ["CMD-SHELL", "curl -f http://localhost/ || exit 1"], "interval": "30s", "retries": 3},
                             "logging": {"driver": "json-file"}},
                       "b": {"image": "docker.io/x/b", "network_mode": "service:a", "depends_on": ["a"], "restart": "no"},
                       "c": {"image": "docker.io/x/c", "network_mode": "host", "environment": {"IP": "1.2.3.4"}}}}
        q = quadlet.from_compose(compose, "test stack")
        a = q["a.container"]
        self.assertIn("Environment=PASS=pa$w0rd", a)      # compose's $$ is $ again
        self.assertIn("Environment=PCT=20%%", a)           # % is a systemd specifier
        self.assertIn('Environment="SP=a b"', a)
        self.assertIn("PublishPort=127.0.0.1:8080:80/tcp", a)
        self.assertIn("Sysctl=net.ipv4.ip_forward=1", a)
        self.assertIn("Volume=vol:/v", a)
        self.assertIn("HealthCmd=\"curl -f http://localhost/ || exit 1\"", a)
        self.assertIn("HealthRetries=3", a)
        self.assertIn("Restart=always", a)
        b = q["b.container"]
        self.assertIn("Network=container:a", b)
        self.assertIn("Requires=a.service", b)
        self.assertIn("Restart=no", b)
        self.assertIn("Network=host", q["c.container"])
        self.assertIn("Environment=IP=1.2.3.4", q["c.container"])
        self.assertIn("NetworkName=knet-node", q["knet-node.network"])
        self.assertIn("WantedBy=multi-user.target", q["kiwi-stack.target"])
        bad = {"networks": {}, "services": {"x": {"image": "i", "pid": "host"}}}
        with self.assertRaisesRegex(KiwiError, "pid"):
            quadlet.from_compose(bad)
        bad = {"networks": {}, "services": {"x": {"image": "i", "network_mode": "service:nope"}}}
        with self.assertRaisesRegex(KiwiError, "not in the stack"):
            quadlet.from_compose(bad)

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
        # the master's Pi-hole first, Quad9 only while the master is unreachable; fleet names go to the master
        self.assertIn("FTLCONF_dns_upstreams=10.8.0.1;9.9.9.9;149.112.112.112", svcs["kn-pihole"]["environment"])
        self.assertIn("FTLCONF_misc_dnsmasq_lines=strict-order;server=/kiwi/10.8.0.1", svcs["kn-pihole"]["environment"])
        self.assertEqual((b.mesh_via, b.mesh_subnet), ("172.128.0.2", "10.8.0.0/16"))
        self.assertIn(("127.0.0.1", "m1.kiwi"), b.hosts)      # its own names point at itself
        self.assertIn(("10.8.0.25", "cloud.sh3.kiwi"), b.hosts)
        self.assertIn('VPN_SUBNET="10.8.0.0/16"', b.files["kiwi/gw.sh"][0])
        self.assertNotIn("command", svcs["kn-sftp"])   # the password is in users.conf, not in docker inspect
        self.assertEqual(b.files["kn-sftp/users.conf"], ("user:s:1000\n", 0o600))
        self.assertIn("/home/user/docker/kn-sftp/users.conf:/etc/sftp/users.conf:ro,z", svcs["kn-sftp"]["volumes"])
        self.assertIn("/mnt/dl:/home/user/Downloads:z", svcs["kn-sftp"]["volumes"])
        self.assertIn("kn-gateway.service", b.units)
        self.assertIn("/home/user/docker/kiwi/gw.sh start", b.units["kn-gateway.service"])
        # under podman there is no docker.service to require: systemd would refuse to start the unit
        self.assertNotIn("docker.service", b.units["kn-gateway.service"])
        self.assertIn("After=kiwi-stack.service network-online.target", b.units["kn-gateway.service"])
        _h, _r, dspec = self.spec("n2", role="node", **{"node": self.node_gw(preset="gateway", runtime="docker")})
        dunit = modmod.Renderer(self.ms).render(dspec).units["kn-gateway.service"]
        self.assertIn("Requires=docker.service", dunit)
        self.assertIn("After=docker.service kiwi-stack.service network-online.target", dunit)
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
        self.assertEqual(set(svcs), {"km-vpn-client", "km-vpn-server", "km-pihole", "km-tor", "km-nginx"})
        self.assertEqual(svcs["km-pihole"]["network_mode"], "service:km-vpn-server")
        # nginx inside the server's namespace: the admin pages by name, on the mesh address only
        self.assertEqual(svcs["km-nginx"]["network_mode"], "service:km-vpn-server")
        self.assertNotIn("ports", svcs["km-nginx"])
        nginx = b.files["km-nginx/nginx.conf"][0]
        self.assertIn("server_name wg.gate.kiwi;", nginx)
        self.assertIn("set $backend http://127.0.0.1:51821;", nginx)
        self.assertIn("server_name pihole.gate.kiwi;", nginx)
        self.assertIn("set $backend http://127.0.0.1:80;", nginx)
        self.assertEqual(modmod.Renderer(self.ms).service_names(spec), ["wg.gate.kiwi", "pihole.gate.kiwi"])
        self.assertEqual(svcs["km-tor"]["network_mode"], "service:km-vpn-server")
        self.assertEqual(svcs["km-vpn-server"]["networks"]["knet-master"]["ipv4_address"], "172.64.0.3")
        self.assertIn("127.0.0.1:8080:80/tcp", svcs["km-vpn-server"]["ports"])
        self.assertIn("51820:51820/udp", svcs["km-vpn-server"]["ports"])
        # the master resolves through its exit tunnel and is the authority for the fleet's names
        self.assertIn("FTLCONF_dns_upstreams=172.64.0.2;9.9.9.9;149.112.112.112", svcs["km-pihole"]["environment"])
        self.assertIn("FTLCONF_misc_dnsmasq_lines=strict-order;local=/kiwi/;hostsdir=/etc/mullvad-socks",
                      svcs["km-pihole"]["environment"])
        self.assertEqual(b.mesh_via, "172.64.0.3")
        self.assertIn("DOT_PROVIDERS=quad9", svcs["km-vpn-client"]["environment"])
        env = svcs["km-vpn-client"]["environment"]
        self.assertIn("VPN_SERVICE_PROVIDER=mullvad", env)
        self.assertIn("WIREGUARD_PRIVATE_KEY=k", env)
        self.assertIn("SERVER_COUNTRIES=Germany", env)
        self.assertNotIn("hostname", svcs["km-pihole"])   # not allowed together with network_mode
        start = b.files["km-vpn-server/start.sh"][0]
        self.assertIn("ip route add default via 172.64.0.2", start)
        self.assertIn("-s 172.64.0.0/24 -o wg0 -j MASQUERADE", start)
        # the default groups: guests reach the nodes and nothing else, before the wg0 accept-all
        self.assertLess(start.index("-s 10.8.1.0/24 -d 10.8.0.0/16 -j DROP"), start.index("-A FORWARD -i wg0 -j ACCEPT"))
        self.assertIn("-i wg0 -s 10.8.1.0/24 -p tcp --dport 51821 -j DROP", start)
        self.assertIn("-i wg0 -s 10.8.1.0/24 -p tcp --dport 443 -j DROP", start)
        self.assertNotIn("-i wg0 -s 10.8.3.0/24 -p tcp --dport 443 -j DROP", start)   # devices: admin: true
        self.assertIn("-s 10.8.2.0/24 -d 10.8.0.0/16 -j DROP", start)                 # pentest: nothing in the mesh
        self.assertNotIn("-s 10.8.2.0/24 -d 10.8.0.25 -j ACCEPT", start)
        self.assertIn("-s 10.8.4.0/24 -o eth0 -j MASQUERADE", start)                 # routers: internet through the exit
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
        self.assertEqual(b.ports, ["51820/udp"])   # nginx inside the server's namespace: nothing more on the host
        self.assertIn("wireguard", b.kernel_modules)
        self.assertEqual(b.files["docker-compose.yml"][1], 0o600)

    def test_subnet_math_and_container_ip_checks(self):
        small = self.node_cloud(docker_subnet="10.200.4.128/25", modules=["vpn-client", "reverse-proxy", "vault"])
        small.pop("cloud")
        host, role, spec = self.spec("n", role="node-cloud", **{"node-cloud": small})
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
            "vpn-client": {"wireguard_config": "secrets/wg.conf"}, "cloud": {"admin_password": "a"}}}}})
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

    def test_client_groups_and_legacy_isolation(self):
        groups = {"pentest": {"subnet": "10.8.2.0/24", "reach": ["sh3", "10.8.0.99"], "peers": False, "internet": False},
                  "family": {"subnet": "10.8.5.0/24", "reach": ["m1"], "peers": True}}
        host, role, spec = self.spec("gate", role="master", **{"master": self.master(**{"vpn-server": {
            "wg_host": "v", "wg_password": "w", "groups": groups, "isolated_subnet": "10.8.1.0/24", "isolated_allow": ["10.9.9.9"]}})})
        start = modmod.Renderer(self.ms).render(spec).files["km-vpn-server/start.sh"][0]
        self.assertIn("-s 10.8.2.0/24 -d 10.8.0.25 -j ACCEPT", start)      # sh3 by name
        self.assertIn("-s 10.8.2.0/24 -d 10.8.0.99 -j ACCEPT", start)      # an address as written
        self.assertIn("-s 10.8.2.0/24 -d 10.8.0.0/16 -j DROP", start)      # no other clients
        self.assertIn("-s 10.8.2.0/24 ! -d 10.8.0.0/16 -j DROP", start)    # no internet
        self.assertNotIn("-s 10.8.2.0/24 -o eth0 -j MASQUERADE", start)
        # family: m1 allowed, the other nodes dropped one by one, other clients allowed
        self.assertIn("-s 10.8.5.0/24 -d 10.8.0.6 -j ACCEPT", start)
        self.assertIn("-s 10.8.5.0/24 -d 10.8.0.25 -j DROP", start)
        self.assertNotIn("-s 10.8.5.0/24 -d 10.8.0.0/16 -j DROP", start)
        # the legacy isolated subnet is one more group
        self.assertIn("-s 10.8.1.0/24 -d 10.9.9.9 -j ACCEPT", start)
        self.assertIn("-s 10.8.1.0/24 -d 10.8.0.0/16 -j DROP", start)
        self.assertIn("-s 10.8.1.0/24 -d 10.8.0.1 -j ACCEPT", start)       # the server is always reachable
        p = os.path.join(self.tmp, "start.sh")
        write(p, start)
        self.assertEqual(subprocess.run(["sh", "-n", p]).returncode, 0)
        with self.assertRaisesRegex(KiwiError, "must be a mapping"):
            self.spec("gate", role="master", **{"master": self.master(**{"vpn-server": {"wg_host": "v", "wg_password": "w", "groups": "nope"}})})

    def test_node_role_presets_and_port_collisions(self):
        host, role, spec = self.spec("n", role="node", **{"node": self.node_gw(preset="gateway")})
        self.assertEqual(host.modules, self.roles["node"].presets["gateway"]["modules"])
        self.assertTrue(host.module_settings["dns"]["expose_web_ui"])     # the preset's module default
        host, role, spec = self.spec("n", role="node", **{"node": self.node_cloud(preset="cloud")})
        self.assertEqual(host.modules, ["vpn-client", "reverse-proxy", "cloud", "vault", "portainer"])
        b = modmod.Renderer(self.ms).render(spec)
        self.assertIn("kn-vault", b.compose["services"])
        # Pi-hole's web UI on Portainer's port: an error at render, not at the second `up`
        both = self.node_gw(preset="gateway", modules=self.roles["node"].presets["gateway"]["modules"] + ["cloud", "vault"],
                            cloud={"admin_password": "a"})
        both["dns"] = dict(both["dns"], web_ui_port=9443)
        host, role, spec = self.spec("n", role="node", **{"node": both})
        with self.assertRaisesRegex(KiwiError, "host port 9443/tcp is published by both"):
            modmod.Renderer(self.ms).render(spec)
        both["dns"] = dict(both["dns"], web_ui_port=8081)
        host, role, spec = self.spec("n", role="node", **{"node": both})
        self.assertIn("nextcloud-aio-apache", modmod.Renderer(self.ms).render(spec).compose["services"])
        with self.assertRaisesRegex(KiwiError, "preset must be one of gateway, cloud, minimal"):
            self.spec("n", role="node", **{"node": self.node_gw(preset="nope")})

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
        spec.master_ip = ""   # no master known: the VPN client's resolver first, nothing forwarded
        b = modmod.Renderer(self.ms).render(spec)
        self.assertNotIn("8080:80/tcp", b.compose["services"]["kn-pihole"]["ports"])
        self.assertIn("FTLCONF_dns_upstreams=172.128.0.2;9.9.9.9;149.112.112.112", b.compose["services"]["kn-pihole"]["environment"])
        self.assertIn("FTLCONF_misc_dnsmasq_lines=strict-order;local=/kiwi/", b.compose["services"]["kn-pihole"]["environment"])
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
        p = os.path.join(self.tmp, "mullvad-socks.sh")
        write(p, b.files["kiwi/mullvad-socks.sh"][0])
        self.assertEqual(subprocess.run(["bash", "-n", p]).returncode, 0)
        if have("shellcheck"):
            r = subprocess.run(["shellcheck", "-S", "warning", "-s", "bash", p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout)

    def test_mullvad_socks_records_on_the_master(self):
        host, role, spec = self.spec("gate", role="master", **{"master": self.master()})
        b = modmod.Renderer(self.ms).render(spec)
        env = b.compose["services"]["km-pihole"]["environment"]
        # dnsmasq watches the directory and reloads the file by itself
        self.assertIn("FTLCONF_misc_dnsmasq_lines=strict-order;local=/kiwi/;hostsdir=/etc/mullvad-socks", env)
        vols = b.compose["services"]["km-pihole"]["volumes"]
        # root writes there: read-only for the container, outside its writable /etc/dnsmasq.d
        self.assertIn("/home/user/docker/km-pihole/mullvad-socks:/etc/mullvad-socks:ro,z", vols)
        self.assertIn("/home/user/docker/km-pihole/etc-dnsmasq.d:/etc/dnsmasq.d:z", vols)
        # made at unpack, before Pi-hole starts (dnsmasq only watches a directory it found);
        # the rendered file in it keeps it root's instead of the service user's
        self.assertIn("km-pihole/mullvad-socks", b.dirs)
        self.assertIn("km-pihole/mullvad-socks/.kiwi-server", b.files)
        self.assertTrue(b.files["km-pihole/mullvad-socks/.kiwi-server"][0].startswith("# "))
        script, mode = b.files["kiwi/mullvad-socks.sh"]
        self.assertEqual(mode, 0o755)
        self.assertIn("url='https://raw.githubusercontent.com/derlocke-ng/mullvad-socks5/list/mullvad-socks.hosts'", script)
        self.assertIn("short='mullvad.kiwi'", script)
        self.assertIn("dir='/home/user/docker/km-pihole/mullvad-socks'", script)
        self.assertEqual(sorted(b.units), ["km-mullvad-socks.service", "km-mullvad-socks.timer"])
        self.assertIn("ExecStart=/usr/bin/bash /home/user/docker/kiwi/mullvad-socks.sh", b.units["km-mullvad-socks.service"])
        self.assertIn("TimeoutStartSec=", b.units["km-mullvad-socks.service"])
        self.assertIn("Unit=km-mullvad-socks.service", b.units["km-mullvad-socks.timer"])
        self.assertIn("OnUnitActiveSec=6h", b.units["km-mullvad-socks.timer"])
        # the role script carries and enables them like any host unit
        full = rolesmod.render_script(host, role, VERSION, bundle=b)
        self.assertIn("KS_STACK_UNITS=('km-mullvad-socks.service' 'km-mullvad-socks.timer')", full)
        self.assertIn("'kiwi/mullvad-socks.sh:0755'", full)

    def test_mullvad_socks_records_are_opt_in_elsewhere(self):
        host, role, spec = self.spec("m1", role="node-gw", **{"node-gw": self.node_gw()})
        b = modmod.Renderer(self.ms).render(spec)
        self.assertNotIn("hostsdir", "\n".join(b.compose["services"]["kn-pihole"]["environment"]))
        self.assertNotIn("mullvad", "\n".join(b.compose["services"]["kn-pihole"]["volumes"]))
        self.assertNotIn("kiwi/mullvad-socks.sh", b.files)
        self.assertEqual(sorted(b.units), ["kn-gateway.service"])
        self.assertFalse([d for d in b.dirs if "mullvad" in d])
        m = self.master()
        m["dns"]["mullvad_socks"] = False
        host, role, spec = self.spec("gate", role="master", **{"master": m})
        b = modmod.Renderer(self.ms).render(spec)
        self.assertNotIn("hostsdir", "\n".join(b.compose["services"]["km-pihole"]["environment"]))
        self.assertNotIn("mullvad", "\n".join(b.compose["services"]["km-pihole"]["volumes"]))
        self.assertEqual(b.units, {})
        self.assertFalse([f for f in b.files if "mullvad" in f])
        self.assertFalse([d for d in b.dirs if "mullvad" in d])
        node = self.node_gw()
        node["dns"]["mullvad_socks"] = True
        host, role, spec = self.spec("m1", role="node-gw", **{"node-gw": node})
        b = modmod.Renderer(self.ms).render(spec)
        self.assertIn("kn-mullvad-socks.timer", b.units)
        self.assertIn("FTLCONF_misc_dnsmasq_lines=strict-order;server=/kiwi/10.8.0.1;hostsdir=/etc/mullvad-socks",
                      b.compose["services"]["kn-pihole"]["environment"])

    def test_mullvad_socks_settings_are_checked(self):
        for bad in ("socks home", "socks.home\n", "a..b", "-bad", "*.home", "x'y"):
            m = self.master()
            m["dns"]["mullvad_socks_domain"] = bad
            f = self.fleet({"defaults": self.base_defaults(), "hosts": {"gate": {"role": "master", "master": m}}})
            with self.assertRaisesRegex(KiwiError, "mullvad_socks_domain"):
                rolesmod.resolve_settings(self.roles["master"], f.hosts["gate"], self.ms)
        for bad in ("raw.githubusercontent.com/x", "https://a b", "ftp://x/y", "http://plain.example/list"):
            m = self.master()
            m["dns"]["mullvad_socks_url"] = bad
            f = self.fleet({"defaults": self.base_defaults(), "hosts": {"gate": {"role": "master", "master": m}}})
            with self.assertRaisesRegex(KiwiError, "mullvad_socks_url"):
                rolesmod.resolve_settings(self.roles["master"], f.hosts["gate"], self.ms)
        # an empty URL is fine while the records are off, and refused while they are on
        m = self.master(); m["dns"]["mullvad_socks_url"] = ""
        host, role, spec = self.spec("gate", role="master", **{"master": m})
        with self.assertRaisesRegex(KiwiError, "mullvad_socks_url is required while mullvad_socks is on"):
            modmod.Renderer(self.ms).render(spec)
        m = self.master(); m["dns"]["mullvad_socks_url"] = ""; m["dns"]["mullvad_socks"] = False
        host, role, spec = self.spec("gate", role="master", **{"master": m})
        self.assertEqual(modmod.Renderer(self.ms).render(spec).units, {})

    def test_a_secret_is_never_echoed_by_a_pattern_error(self):
        from kiwiserver import schema
        s = schema.Setting("modules/x", {"key": "key", "type": "secret", "pattern": "[a-z]+"})
        with self.assertRaises(KiwiError) as e:
            schema.coerce(s, "S3cret-Value!", "host.x")
        self.assertNotIn("S3cret", str(e.exception))
        self.assertIn("host.x.key", str(e.exception))
        s = schema.Setting("modules/x", {"key": "name", "pattern": "[a-z]+"})
        with self.assertRaisesRegex(KiwiError, "'Not ok'"):
            schema.coerce(s, "Not ok", "host.x")

    def test_operator_dnsmasq_lines_join_the_modules(self):
        m = self.master()
        m["dns"]["extra_env"] = {"FTLCONF_misc_dnsmasq_lines": "rebind-domain-ok=example.org; address=/x.lan/192.168.1.5",
                                 "FTLCONF_dns_domainNeeded": "true"}
        host, role, spec = self.spec("gate", role="master", **{"master": m})
        env = modmod.Renderer(self.ms).render(spec).compose["services"]["km-pihole"]["environment"]
        lines = [e for e in env if e.upper().startswith("FTLCONF_MISC_DNSMASQ_LINES=")]
        self.assertEqual(lines, ["FTLCONF_misc_dnsmasq_lines=strict-order;local=/kiwi/;hostsdir=/etc/mullvad-socks;"
                                 "rebind-domain-ok=example.org;address=/x.lan/192.168.1.5"])
        self.assertIn("FTLCONF_dns_domainNeeded=true", env)

    def mullvad_socks_script(self, **dns):
        m = self.master()
        m["dns"].update(dns)
        host, role, spec = self.spec("gate", role="master", **{"master": m})
        spec.docker_dir = os.path.join(self.tmp, "docker")
        script = modmod.Renderer(self.ms).render(spec).files["kiwi/mullvad-socks.sh"][0]
        p = os.path.join(self.tmp, "mullvad-socks.sh")
        write(p, script)
        return p, os.path.join(spec.docker_dir, "km-pihole", "mullvad-socks", "mullvad-socks.hosts")

    @unittest.skipUnless(have("curl"), "curl not installed")
    def test_mullvad_socks_script_takes_only_mullvad_names(self):
        src = os.path.join(self.tmp, "list.hosts")
        p, hosts = self.mullvad_socks_script(mullvad_socks_url="file://" + src)
        write(src, "# Mullvad SOCKS5 proxies\n"
                   "10.124.0.53 de-fra-wg-socks5-001.relays.mullvad.net\r\n"
                   "10.124.0.53 de-fra-001.mullvad.home\n"           # the list's own short names: replaced
                   "10.124.2.22 US-QAS-WG-SOCKS5-101.relays.mullvad.net\n"
                   "10.124.0.53 de-fra-wg-socks5-001.relays.mullvad.net\n"
                   "10.0.0.1 bank.example.com\n"                     # never another name
                   "203.0.113.7 se-mma-wg-socks5-001.relays.mullvad.net\n"   # never a public address
                   "10.124.0.999 se-got-wg-socks5-001.relays.mullvad.net\n"
                   "010.124.0.1 se-sto-wg-socks5-001.relays.mullvad.net\n"   # octal to some resolvers
                   "10.124.1.7   odd-name.relays.mullvad.net   \n"
                   "\n")
        r = subprocess.run(["bash", p], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(hosts) as fh:
            self.assertEqual(fh.read(), "10.124.0.53 de-fra-wg-socks5-001.relays.mullvad.net\n"
                                        "10.124.2.22 us-qas-wg-socks5-101.relays.mullvad.net\n"
                                        "10.124.1.7 odd-name.relays.mullvad.net\n"
                                        "10.124.0.53 de-fra-001.mullvad.kiwi\n"
                                        "10.124.2.22 us-qas-101.mullvad.kiwi\n")
        self.assertEqual(os.stat(hosts).st_mode & 0o777, 0o644)
        self.assertEqual(os.stat(os.path.dirname(hosts)).st_mode & 0o777, 0o755)
        self.assertEqual(os.listdir(os.path.dirname(hosts)), ["mullvad-socks.hosts"])   # no temp files left
        before = os.stat(hosts).st_mtime_ns
        r = subprocess.run(["bash", p], capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (0, ""))          # unchanged: not rewritten
        self.assertEqual(os.stat(hosts).st_mtime_ns, before)
        # an error page, an empty list, a list without one Mullvad proxy: the records stay
        for bad in ("<html>rate limited</html>\n", "", "# nothing\n", "10.0.0.1 bank.example.com\n"):
            write(src, bad)
            r = subprocess.run(["bash", p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 1, bad)
            self.assertIn("keeping the current records", r.stderr)
            self.assertEqual(os.stat(hosts).st_mtime_ns, before)
        write(src, "10.124.0.53 de-fra-wg-socks5-001.relays.mullvad.net\n")
        p2, _ = self.mullvad_socks_script(mullvad_socks_url="file://" + src + ".missing")
        self.assertNotEqual(subprocess.run(["bash", p2], capture_output=True).returncode, 0)   # curl fails: kept
        self.assertEqual(os.stat(hosts).st_mtime_ns, before)

    @unittest.skipUnless(have("curl"), "curl not installed")
    def test_mullvad_socks_script_works_only_in_its_own_directory(self):
        src = os.path.join(self.tmp, "list.hosts")
        write(src, "10.124.0.53 de-fra-wg-socks5-001.relays.mullvad.net\n")
        p, hosts = self.mullvad_socks_script(mullvad_socks_url="file://" + src)
        d = os.path.dirname(hosts)
        # a link where the directory should be: nothing is written to, or chmodded at, its target
        target = os.path.join(self.tmp, "elsewhere")
        os.makedirs(target, mode=0o700)
        os.makedirs(os.path.dirname(d))
        os.symlink(target, d)
        r = subprocess.run(["bash", p], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("leaving it alone", r.stderr)
        self.assertEqual((os.listdir(target), os.stat(target).st_mode & 0o777), ([], 0o700))
        os.unlink(d)
        # a directory someone else may write to: refused, it could swap files under root's hands
        os.makedirs(d)
        os.chmod(d, 0o775)
        r = subprocess.run(["bash", p], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(os.listdir(d), [])
        os.chmod(d, 0o755)
        # a FIFO where the list goes does not hang the refresh; it is replaced
        os.mkfifo(hosts)
        r = subprocess.run(["bash", p], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isfile(hosts))
        # a directory where the list goes is neither replaced nor written into
        os.unlink(hosts)
        os.makedirs(hosts)
        r = subprocess.run(["bash", p], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(os.listdir(hosts), [])
        self.assertEqual(os.listdir(d), ["mullvad-socks.hosts"])   # temp files gone

    @unittest.skipUnless(have("curl"), "curl not installed")
    def test_mullvad_socks_short_names(self):
        src = os.path.join(self.tmp, "list.hosts")
        write(src, "10.124.0.53 de-fra-wg-socks5-001.relays.mullvad.net\n")
        p, hosts = self.mullvad_socks_script(mullvad_socks_url="file://" + src, mullvad_socks_domain="Socks.Home.")
        self.assertEqual(subprocess.run(["bash", p]).returncode, 0)
        with open(hosts) as fh:
            self.assertEqual(fh.read().splitlines()[-1], "10.124.0.53 de-fra-001.socks.home")
        with open(p) as fh:
            self.assertIn("short='socks.home'", fh.read())
        p, hosts = self.mullvad_socks_script(mullvad_socks_url="file://" + src, mullvad_socks_domain="")
        with open(p) as fh:
            self.assertIn("short='mullvad.kiwi'", fh.read())   # empty: under the fleet's domain

    def test_host_units_no_longer_rendered_are_removed(self):
        """Turning dns.mullvad_socks off (or dropping a module) and applying again stops and
        removes the units the last apply installed, which kiwi-stack would no longer touch."""
        units = os.path.join(self.tmp, "units")
        os.makedirs(units)
        for u in ("km-mullvad-socks.service", "km-mullvad-socks.timer", "kn-gateway.service"):
            write(os.path.join(units, u), "[Unit]\n")
        env = os.path.join(self.tmp, "stack.env")
        write(env, 'STACK_UNITS="km-mullvad-socks.service km-mullvad-socks.timer kn-gateway.service ../../x.service"\n')
        log = os.path.join(self.tmp, "systemctl.log")
        lib = os.path.join(ROOT, "roles", "common", "lib.sh")
        r = subprocess.run(["bash", "-c", 'set -euo pipefail; source "$1"; KS_STACK_ENV=$2; KS_SYSTEMD_DIR=$3; '
                            'log=$4; systemctl() { echo "$*" >>"$log"; }; KS_STACK_UNITS=(kn-gateway.service); ks_stack_drop_units',
                            "_", lib, env, units, log], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(os.listdir(units)), ["kn-gateway.service"])
        with open(log) as fh:
            calls = fh.read()
        self.assertIn("disable --now km-mullvad-socks.service", calls)
        self.assertIn("disable --now km-mullvad-socks.timer", calls)
        self.assertNotIn("kn-gateway", calls)
        self.assertNotIn("x.service", calls)      # only plain unit names from stack.env

    def test_when_skips_a_file_setting_output_and_roles_lists_preset_defaults(self):
        mdir = os.path.join(self.tmp, "modules")
        shutil.copytree(modmod.modules_dir(), mdir)
        y = os.path.join(mdir, "vpn-client", "module.yaml")
        with open(y) as fh:
            meta = yaml.safe_load(fh)
        meta["outputs"]["wireguard_config"]["when"] = "nope"
        write(y, yaml.safe_dump(meta, sort_keys=False))
        ms = modmod.ModuleSet(mdir)
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {"n": {"role": "node-gw", "node-gw": self.node_gw()}}})
        host = f.hosts["n"]
        rolesmod.resolve_settings(self.roles["node-gw"], host, ms)
        b = modmod.Renderer(ms).render(rolesmod.stack_spec(host, self.roles["node-gw"]))
        self.assertNotIn("kn-vpn-client/wg0.conf", b.files)
        out = subprocess.run([os.path.join(ROOT, "bin", "kiwi-server"), "roles", "-v"], capture_output=True, text=True).stdout
        master = out.split("\nnode-gw")[0]
        self.assertRegex(master, r"dns\.mullvad_socks +bool +default=True")
        self.assertRegex(out.split("\nnode-gw")[1], r"dns\.mullvad_socks +bool +default=False")

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


WG_CONF = """# wg-easy client config for the router
[Interface]
PrivateKey = cHJpdmF0ZS1rZXktdGVzdA==
Address = 10.8.4.2/24
DNS = 10.8.0.1
MTU = 1412

[Peer]
PublicKey = cHVibGljLWtleS10ZXN0
PresharedKey = cHNrLXRlc3Q=
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
Endpoint = vpn.example.org:51820
"""


class TestRouter(Base):
    def test_parse_wireguard(self):
        wg = router.parse_wireguard(WG_CONF)
        self.assertEqual(wg["interface"]["address"], ["10.8.4.2/24"])
        self.assertEqual(wg["interface"]["mtu"], "1412")
        self.assertEqual(wg["peers"][0]["endpoint"], "vpn.example.org:51820")
        self.assertEqual(wg["peers"][0]["allowedips"], ["0.0.0.0/0", "::/0"])
        for bad in ("hello\n", "[Interface]\nPrivateKey = x\n", "[Interface]\nPrivateKey = x\nAddress = 10.8.4.2/24\n[Peer]\nPublicKey = p\nEndpoint = host\n"):
            with self.assertRaises(KiwiError):
                router.parse_wireguard(bad)

    def test_openwrt_scripts(self):
        wg = router.parse_wireguard(WG_CONF)
        split = router.openwrt_script("home", "10.8.0.1", "10.8.0.0/16", wg=wg)
        for line in ("uci set network.kiwi.proto='wireguard'", "uci set network.kiwi.private_key='cHJpdmF0ZS1rZXktdGVzdA=='",
                     "uci add_list network.kiwi.addresses='10.8.4.2/24'", "uci set network.kiwi.mtu='1412'",
                     "uci set network.kiwi_peer=wireguard_kiwi", "uci set network.kiwi_peer.endpoint_host='vpn.example.org'",
                     "uci set network.kiwi_peer.endpoint_port='51820'", "uci set network.kiwi_peer.preshared_key='cHNrLXRlc3Q='",
                     "uci add_list network.kiwi_peer.allowed_ips='10.8.0.0/16'", "uci set firewall.kiwi.masq='1'",
                     "uci set firewall.kiwi_fwd.src='lan'", "uci add_list dhcp.@dnsmasq[0].server='/home/10.8.0.1'",
                     "uci set dhcp.@dnsmasq[0].rebind_domain='home'", "uci commit network"):
            self.assertIn(line, split)
        self.assertNotIn("0.0.0.0/0", split)
        self.assertNotIn("noresolv", split)
        full = router.openwrt_script("home", "10.8.0.1", "10.8.0.0/16", wg=wg, full=True)
        self.assertIn("uci add_list network.kiwi_peer.allowed_ips='0.0.0.0/0'", full)
        self.assertIn("uci add_list dhcp.@dnsmasq[0].server='10.8.0.1'", full)
        self.assertIn("uci set dhcp.@dnsmasq[0].noresolv='1'", full)
        via = router.openwrt_script("home", "10.8.0.1", "10.8.0.0/16", via="192.168.1.5")
        self.assertIn("uci set network.kiwi_route.target='10.8.0.0/16'", via)
        self.assertIn("uci set network.kiwi_route.gateway='192.168.1.5'", via)
        self.assertNotIn("wireguard", via)
        self.assertIn("server='/home/10.8.0.1'", via)
        p = os.path.join(self.tmp, "kiwi.sh")
        write(p, split)
        self.assertEqual(subprocess.run(["sh", "-n", p]).returncode, 0)
        if have("shellcheck"):
            r = subprocess.run(["shellcheck", "-S", "warning", "-s", "sh", p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout)
        with self.assertRaises(KiwiError):
            router.openwrt_script("home", "10.8.0.1", "10.8.0.0/16", wg=wg, via="192.168.1.5")
        with self.assertRaises(KiwiError):
            router.openwrt_script("home", "10.8.0.1", "10.8.0.0/16", via="not-an-ip")

    def test_openwrt_command_uses_the_fleet(self):
        write(os.path.join(self.tmp, "secrets", "router.conf"), WG_CONF)
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {
            "gate": {"role": "master", "target": "debian", "master": self.master()},
            "m1": {"role": "node-gw", "network": {"dhcp": False, "address": "192.168.1.5/24", "gateway": "192.168.1.1"},
                   "node-gw": self.node_gw()},
            "d": {"role": "node-gw", "node-gw": self.node_gw()}}})
        from io import StringIO
        import contextlib
        out = StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["openwrt", f.path, "--wireguard", "secrets/router.conf"]), 0)
        self.assertIn("server='/kiwi/10.8.0.1'", out.getvalue())   # the master's address from the fleet
        out = StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["openwrt", f.path, "--via", "m1"]), 0)
        self.assertIn("kiwi_route.gateway='192.168.1.5'", out.getvalue())
        err = StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["openwrt", f.path, "--via", "d"]), 1)   # d has no static LAN address
        self.assertIn("static LAN address", err.getvalue())


@unittest.skipUnless(have("openssl"), "openssl not installed")
class TestBackup(Base):
    def test_archive_roundtrip_and_exclusions(self):
        write(os.path.join(self.tmp, "secrets", "ca", "kiwiCA.key"), "KEY")
        write(os.path.join(self.tmp, "output", "x", "x.role.sh"), "no")
        write(os.path.join(self.tmp, "big.iso"), "no")
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {"a": {}}})
        names = [arc for _p, arc in backup.fleet_files(f)]
        self.assertEqual(names, ["fleet.yaml", "secrets/ca/kiwiCA.key", "secrets/wg.conf"])
        data = backup.create(f, "pw")
        self.assertNotIn(b"PrivateKey", data)
        into = os.path.join(self.tmp, "restored")
        self.assertEqual(backup.restore(data, "pw", into), names)
        with open(os.path.join(into, "secrets", "wg.conf")) as fh:
            self.assertEqual(fh.read(), "[Interface]\nPrivateKey=x\n")
        self.assertEqual(oct(os.stat(os.path.join(into, "secrets", "wg.conf")).st_mode & 0o777), "0o600")
        with self.assertRaisesRegex(KiwiError, "passphrase"):
            backup.restore(data, "other", os.path.join(self.tmp, "r2"))
        with self.assertRaisesRegex(KiwiError, "not a kiwi-server"):
            backup.restore(backup.encrypt(b"hello", "pw"), "pw", os.path.join(self.tmp, "r3"))
        self.assertRegex(backup.archive_name(f, 0), r"^fleet-fleet-19700101T000000Z\.tar\.enc$")

    def test_store_and_fetch_through_ssh(self):
        calls = []

        class R:
            def __init__(self, out):
                self.returncode, self.stdout, self.stderr = 0, out, b""

        def runner(argv, input=None, capture_output=True):
            calls.append((argv, input))
            if "cat /var/lib" in argv[-1]:
                return R(b"DATA")
            return R(b"/var/lib/kiwi-server/backups/fleet-a.tar.enc\n/var/lib/kiwi-server/backups/fleet-b.tar.enc\n")
        kept = backup.store_remote(b"DATA", "core@m1.home", "fleet-x.tar.enc", keep=3, runner=runner)
        argv, data = calls[-1]
        self.assertEqual(argv[:3], ["ssh", "-o", "BatchMode=yes"])
        self.assertEqual(argv[-2], "core@m1.home")
        self.assertTrue(argv[-1].startswith("sudo sh -c "))
        self.assertIn("install -d -m 0700", argv[-1])
        self.assertIn("tail -n +4", argv[-1])          # keep three
        self.assertIn("fleet-x.tar.enc", argv[-1])
        self.assertEqual(data, b"DATA")
        self.assertEqual(len(kept), 2)
        self.assertEqual(backup.list_remote("core@m1.home", runner=runner), ["fleet-a.tar.enc", "fleet-b.tar.enc"])
        self.assertEqual(backup.fetch_remote("core@m1.home", runner=runner), ("fleet-a.tar.enc", b"DATA"))
        with self.assertRaises(KiwiError):
            backup.fetch_remote("core@m1.home", "../etc/passwd", runner=runner)

        def failing(argv, input=None, capture_output=True):
            r = R(b""); r.returncode, r.stderr = 255, b"ssh: connect to host m1.home port 22: No route to host\n"
            return r
        with self.assertRaisesRegex(KiwiError, "No route to host"):
            backup.store_remote(b"x", "core@m1.home", "fleet-x.tar.enc", runner=failing)


class FakeWgEasy:
    """The weejewel wg-easy API, as much of it as enroll uses: a session
    cookie for the password, clients as JSON, an address change, the client
    config as text. In-process, on a loopback port, no network."""

    def __init__(self, password="w"):
        self.password = password
        self.clients = []
        self.calls = []
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"null") if n else None

            def _send(self, code, payload=b"", ctype="application/json", cookie=None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                if cookie:
                    self.send_header("Set-Cookie", cookie)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _authed(self):
                return "connect.sid=ok" in (self.headers.get("Cookie") or "")

            def do_POST(self):
                body = self._body()
                fake.calls.append(("POST", self.path, body))
                if self.path == "/api/session":
                    if (body or {}).get("password") != fake.password:
                        return self._send(401, b'{"error":"Incorrect password"}')
                    return self._send(200, b'{"success":true}', cookie="connect.sid=ok; Path=/; HttpOnly")
                if not self._authed():
                    return self._send(401, b'{"error":"Not Logged In"}')
                if self.path == "/api/wireguard/client":
                    n = len(fake.clients) + 2
                    fake.clients.append({"id": "id-%d" % n, "name": body["name"], "address": "10.8.0.%d" % n,
                                         "enabled": True, "publicKey": "PUB-%d" % n})
                    return self._send(200, b'{"success":true}')
                self._send(404, b'{"error":"no such route"}')

            def do_GET(self):
                fake.calls.append(("GET", self.path, None))
                if not self._authed():
                    return self._send(401, b'{"error":"Not Logged In"}')
                if self.path == "/api/wireguard/client":
                    return self._send(200, json.dumps(fake.clients).encode())
                m = re.match(r"^/api/wireguard/client/([^/]+)/configuration$", self.path)
                c = fake.by_id(m.group(1)) if m else None
                if c is None:
                    return self._send(404, b'{"error":"no such route"}')
                text = ("[Interface]\nPrivateKey = PRIV-%s\nAddress = %s/24\nDNS = 10.8.0.1\n\n[Peer]\n"
                        "PublicKey = SERVERPUB\nPresharedKey = PSK\nAllowedIPs = 0.0.0.0/0, ::/0\n"
                        "PersistentKeepalive = 25\nEndpoint = vpn.example.org:51820\n") % (c["id"], c["address"])
                self._send(200, text.encode(), ctype="text/plain")

            def do_PUT(self):
                body = self._body()
                fake.calls.append(("PUT", self.path, body))
                if not self._authed():
                    return self._send(401, b'{"error":"Not Logged In"}')
                m = re.match(r"^/api/wireguard/client/([^/]+)/address$", self.path)
                c = fake.by_id(m.group(1)) if m else None
                if c is None:
                    return self._send(404, b'{"error":"no such route"}')
                c["address"] = body["address"]
                self._send(200, b'{"success":true}')

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def by_id(self, cid):
        for c in self.clients:
            if c["id"] == cid:
                return c
        return None

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)


class TestRemote(Base):
    class R:
        def __init__(self, rc=0, out=b"", err=b""):
            self.returncode, self.stdout, self.stderr = rc, out, err

    def test_target_run_and_put(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {"a": {}}})
        self.assertEqual(remote.target(f.hosts["a"]), "core@a.kiwi")
        self.assertEqual(remote.target(f.hosts["a"], "root@10.0.0.5"), "root@10.0.0.5")
        calls = []

        def runner(argv, input=None, capture_output=True):
            calls.append((argv, input))
            return self.R(0, b"hello\n")
        self.assertEqual(remote.run("core@a.kiwi", "echo hello", runner=runner), b"hello\n")
        argv, data = calls[-1]
        self.assertEqual(argv[:3], ["ssh", "-o", "BatchMode=yes"])
        self.assertEqual(argv[-2:], ["core@a.kiwi", "sudo sh -c 'echo hello'"])
        self.assertIsNone(data)
        remote.put("core@a.kiwi", b"#!/bin/sh\n", "/var/lib/kiwi-server/role.sh", "0700", runner=runner)
        argv, data = calls[-1]
        self.assertEqual(data, b"#!/bin/sh\n")
        script = argv[-1]
        self.assertIn("install -d -m 0700", script)
        self.assertIn("cat > /var/lib/kiwi-server/role.sh.tmp", script)
        self.assertIn("chmod 0700 /var/lib/kiwi-server/role.sh.tmp", script)
        self.assertIn("mv -f /var/lib/kiwi-server/role.sh.tmp /var/lib/kiwi-server/role.sh", script)

        def failing(argv, input=None, capture_output=True):
            return self.R(255, b"", b"ssh: connect to host a.kiwi port 22: No route to host\n")
        with self.assertRaisesRegex(KiwiError, r"core@a\.kiwi: ssh: connect .* No route to host"):
            remote.run("core@a.kiwi", "true", runner=failing)

        def stream(argv, stdin=None):
            calls.append((argv, stdin))
            return self.R(3)
        self.assertEqual(remote.run_stream("core@a.kiwi", "bash /x --force", runner=stream), 3)
        argv, stdin = calls[-1]
        self.assertEqual(argv[-1], "sudo sh -c 'bash /x --force'")
        self.assertEqual(stdin, subprocess.DEVNULL)

        def missing(argv, **_kw):
            raise FileNotFoundError("ssh")
        with self.assertRaisesRegex(KiwiError, "ssh is not installed"):
            remote.run("core@a.kiwi", "true", runner=missing)

    def test_tunnel_with_a_fake_ssh(self):
        fakebin = os.path.join(self.tmp, "bin")
        ssh = os.path.join(fakebin, "ssh")
        write(ssh, "#!/usr/bin/env python3\n"
                   "import socket, sys, time\n"
                   "if 'denied@x' in sys.argv:\n"
                   "    sys.stderr.write('core@x: Permission denied (publickey).\\n'); sys.exit(255)\n"
                   "spec = sys.argv[sys.argv.index('-L') + 1]\n"
                   "s = socket.socket(); s.bind(('127.0.0.1', int(spec.split(':')[0]))); s.listen(1)\n"
                   "time.sleep(60)\n")
        os.chmod(ssh, 0o755)
        with mock.patch.dict(os.environ, {"PATH": fakebin + os.pathsep + os.environ.get("PATH", "")}):
            with remote.Tunnel("core@x", 51821) as url:
                self.assertRegex(url, r"^http://127\.0\.0\.1:\d+$")
                port = int(url.rsplit(":", 1)[1])
                socket_mod = __import__("socket")
                c = socket_mod.create_connection(("127.0.0.1", port), timeout=2)
                c.close()
            with self.assertRaisesRegex(KiwiError, "tunnel to port 51821 failed: .*Permission denied"):
                with remote.Tunnel("denied@x", 51821):
                    pass

    def test_next_address_and_split_tunnel(self):
        self.assertEqual(wgeasy.next_address("10.8.2.0/24", []), "10.8.2.2")        # .1 is the server's
        self.assertEqual(wgeasy.next_address("10.8.2.0/24", ["10.8.2.2/32", "10.8.2.3"]), "10.8.2.4")
        self.assertEqual(wgeasy.next_address("10.8.2.0/24", ["10.8.2.2"], exclude=["10.8.2.3"]), "10.8.2.4")
        with self.assertRaisesRegex(KiwiError, "no free address"):
            wgeasy.next_address("10.8.2.0/30", ["10.8.2.2"])
        conf = "[Interface]\nAddress = 10.8.3.2/24\n\n[Peer]\nAllowedIPs = 0.0.0.0/0, ::/0\nEndpoint = v:51820\n"
        out = wgeasy.split_tunnel(conf, "10.8.0.0/16")
        self.assertIn("AllowedIPs = 10.8.0.0/16\n", out)
        self.assertNotIn("0.0.0.0/0", out)
        self.assertIn("Endpoint = v:51820\n", out)
        with self.assertRaisesRegex(KiwiError, "no AllowedIPs"):
            wgeasy.split_tunnel("[Interface]\nAddress = 1.2.3.4\n", "10.8.0.0/16")


class TestManage(Base):
    """apply, status and enroll: the commands that talk to running machines,
    here against a fake wg-easy and fake ssh runners."""

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

    def manage_fleet(self, **master_over):
        return self.fleet({"defaults": self.base_defaults(),
                           "hosts": {"gate": {"role": "master", "master": self.master(**master_over)},
                                     "sh3": {"role": "node", "node": {"preset": "minimal"}}}})

    def test_enroll_a_node_then_devices(self):
        api = FakeWgEasy("w")
        self.addCleanup(api.stop)
        f = self.manage_fleet()
        # the node has no config yet: it does not even validate (no vpn_ip to derive)
        rc, out, err = self.run_cli("validate", f.path)
        self.assertEqual(rc, cli.EXIT_INVALID)
        self.assertIn("needs wireguard_config", err)
        self.assertIn("kiwi-server enroll", err)
        rc, out, err = self.run_cli("enroll", f.path, "sh3", "--api", api.url)
        self.assertEqual(rc, 0, err)
        dest = os.path.join(self.tmp, "secrets", "sh3.kiwi.conf")
        self.assertTrue(os.path.isfile(dest))
        self.assertEqual(oct(os.stat(dest).st_mode & 0o777), "0o600")
        with open(dest) as fh:
            conf = fh.read()
        self.assertIn("Address = 10.8.0.2/24", conf)
        self.assertIn("AllowedIPs = 0.0.0.0/0", conf)               # a node sends everything through the mesh
        self.assertEqual([c["name"] for c in api.clients], ["sh3.kiwi"])   # the client is named after the host
        self.assertEqual(api.calls[0], ("POST", "/api/session", {"password": "w"}))
        self.assertNotIn(("PUT", "/api/wireguard/client/id-2/address", {"address": "10.8.0.2"}), api.calls)
        # the fleet picks it up without a line in fleet.yaml: vpn_ip derived, the config embedded
        rc, out, err = self.run_cli("validate", f.path)
        self.assertEqual(rc, 0, err)
        rc, out, err = self.run_cli("script", f.path, "sh3")
        self.assertEqual(rc, 0, err)
        self.assertIn("10.8.0.2", out)
        import base64
        self.assertIn(base64.b64encode(conf.encode()).decode(), out)
        # twice is a mistake unless the config is only to be fetched again
        rc, out, err = self.run_cli("enroll", f.path, "sh3", "--api", api.url)
        self.assertEqual(rc, 1)
        self.assertIn("already has a client named sh3.kiwi", err)
        rc, out, err = self.run_cli("enroll", f.path, "sh3", "--api", api.url, "--existing", "--split")
        self.assertEqual(rc, 0, err)
        with open(dest) as fh:
            self.assertIn("AllowedIPs = 10.8.0.0/16\n", fh.read())
        self.assertEqual(len(api.clients), 1)
        # a phone in the devices group gets the next address of that group's range
        rc, out, err = self.run_cli("enroll", f.path, "phone", "--group", "devices", "--api", api.url)
        self.assertEqual(rc, 0, err)
        self.assertIn(("PUT", "/api/wireguard/client/id-3/address", {"address": "10.8.3.2"}), api.calls)
        with open(os.path.join(self.tmp, "secrets", "devices", "phone.conf")) as fh:
            self.assertIn("Address = 10.8.3.2/24", fh.read())
        rc, out, err = self.run_cli("enroll", f.path, "laptop", "--group", "devices", "--api", api.url)
        self.assertEqual(rc, 0, err)
        self.assertEqual(api.by_id("id-4")["address"], "10.8.3.3")
        rc, out, err = self.run_cli("enroll", f.path, "router", "--address", "10.8.4.10", "--api", api.url)
        self.assertEqual(rc, 0, err)
        self.assertEqual(api.by_id("id-5")["address"], "10.8.4.10")
        rc, out, err = self.run_cli("enroll", f.path, "x", "--group", "nope", "--api", api.url)
        self.assertEqual(rc, 1)
        self.assertIn("no client group 'nope'", err)
        self.assertIn("devices", err)

    def test_enroll_refusals(self):
        api = FakeWgEasy("other")
        self.addCleanup(api.stop)
        f = self.manage_fleet()
        rc, out, err = self.run_cli("enroll", f.path, "phone", "--api", api.url)
        self.assertEqual(rc, 1)
        self.assertIn("401", err)
        self.assertIn("Incorrect password", err)
        self.assertEqual(api.clients, [])
        f = self.manage_fleet(**{"vpn-server": {"wg_host": "v"}})
        rc, out, err = self.run_cli("enroll", f.path, "phone", "--api", api.url)
        self.assertEqual(rc, 1)
        self.assertIn("wg_password", err)
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {"a": {}}})
        rc, out, err = self.run_cli("enroll", f.path, "phone", "--api", api.url)
        self.assertEqual(rc, 1)
        self.assertIn("no master in the fleet", err)
        rc, out, err = self.run_cli("enroll", f.path, "phone", "--api", "http://127.0.0.1:1")
        self.assertEqual(rc, 1)

    def test_apply_and_status_through_fake_ssh(self):
        write(os.path.join(self.tmp, "secrets", "sh3.kiwi.conf"),
              "[Interface]\nPrivateKey = k\nAddress = 10.8.0.7/24\n\n[Peer]\nPublicKey = p\nAllowedIPs = 0.0.0.0/0\nEndpoint = v:51820\n")
        f = self.manage_fleet()
        calls = []

        def fake_run(tgt, script, data=None, runner=None):
            calls.append(("run", tgt, script, data))
            if "role.done" in script:
                if tgt.endswith("gate.kiwi"):
                    raise KiwiError("%s: ssh: connect to host gate.kiwi port 22: Connection refused" % tgt)
                return b"applied: 2026-10-06T10:00:00Z\nuptime: up 3 days\nkiwi-stack: 2 containers running\n"
            return b""

        def fake_stream(tgt, script, runner=None):
            calls.append(("stream", tgt, script))
            return 0 if "--force" in script else 2
        with mock.patch.object(remote, "run", fake_run), mock.patch.object(remote, "run_stream", fake_stream):
            rc, out, err = self.run_cli("apply", f.path, "sh3", "--no-run")
            self.assertEqual(rc, 0, err)
            kind, tgt, script, data = calls[-1]
            self.assertEqual((kind, tgt), ("run", "core@sh3.kiwi"))
            self.assertIn("/var/lib/kiwi-server/role.sh.tmp", script)
            self.assertIn("chmod 0700", script)
            self.assertTrue(data.startswith(b"#!/usr/bin/env bash"), data[:40])
            self.assertIn(b"KS_HOSTNAME=", data)
            self.assertIn("sudo bash /var/lib/kiwi-server/role.sh --force", out)
            rc, out, err = self.run_cli("apply", f.path, "sh3", "--ssh", "admin@192.168.1.7")
            self.assertEqual(rc, 0, err)
            self.assertEqual(calls[-1], ("stream", "admin@192.168.1.7", "bash /var/lib/kiwi-server/role.sh --force"))
            self.assertEqual(calls[-2][1], "admin@192.168.1.7")
            self.assertIn("sh3: applied", out)
            rc, out, err = self.run_cli("apply", f.path, "--ssh", "x@y")
            self.assertEqual(rc, 1)
            self.assertIn("exactly one host", err)
            rc, out, err = self.run_cli("status", f.path, "--porcelain")
            self.assertEqual(rc, 0, err)
            data = json.loads(out)
            byname = {h["host"]: h for h in data["hosts"]}
            self.assertEqual(byname["gate"]["ok"], False)
            self.assertIn("Connection refused", byname["gate"]["output"])
            self.assertEqual(byname["sh3"]["ok"], True)
            self.assertIn("applied: 2026-10-06T10:00:00Z", byname["sh3"]["output"])
            self.assertEqual(byname["sh3"]["target"], "core@sh3.kiwi")
            rc, out, err = self.run_cli("status", f.path, "sh3")
            self.assertEqual(rc, 0, err)
            self.assertIn("== sh3 (core@sh3.kiwi)", out)
            self.assertIn("   uptime: up 3 days", out)
        # a role script that fails is reported, not swallowed
        with mock.patch.object(remote, "run", fake_run), mock.patch.object(remote, "run_stream", lambda *a, **k: 7):
            rc, out, err = self.run_cli("apply", f.path, "sh3")
            self.assertEqual(rc, 1)
            self.assertIn("exited with 7", err)
            self.assertIn("failed: sh3", err)

    def test_secrets_fallback_only_when_the_file_exists(self):
        f = self.fleet({"defaults": self.base_defaults(),
                        "hosts": {"n": {"role": "node", "node": {"preset": "minimal", "vpn_ip": "10.8.0.9"}}}})
        rc, out, err = self.run_cli("validate", f.path)
        self.assertEqual(rc, cli.EXIT_INVALID)
        self.assertIn("needs wireguard_config", err)
        write(os.path.join(self.tmp, "secrets", "n.kiwi.conf"), "[Interface]\nPrivateKey = k\nAddress = 10.8.0.9/24\n")
        rc, out, err = self.run_cli("validate", f.path)
        self.assertEqual(rc, 0, err)
        host = f.hosts["n"]
        rolesmod.resolve_settings(self.roles["node"], host, self.ms)
        self.assertEqual(host.module_settings["vpn-client"]["wireguard_config"], "n.kiwi.conf")   # a file setting keeps its basename
        self.assertIn(b"Address = 10.8.0.9/24", host.module_files["vpn-client/wireguard_config"])
        # the master's exit: a commercial provider needs its key and address
        g = self.fleet({"defaults": self.base_defaults(),
                        "hosts": {"gate": {"role": "master", "master": self.master(**{"vpn-client": {
                            "vpn_provider": "mullvad", "wireguard_private_key": "k"}})}}})
        rc, out, err = self.run_cli("validate", g.path)
        self.assertEqual(rc, cli.EXIT_INVALID)
        self.assertIn("provider mullvad needs wireguard_private_key and wireguard_addresses", err)


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
        self.assertEqual(sorted(r["name"] for r in data["roles"]), ["bare", "master", "node", "node-cloud", "node-gw"])
        node = [r for r in data["roles"] if r["name"] == "node"][0]
        self.assertEqual(list(node["presets"]), ["gateway", "cloud", "minimal"])
        self.assertIn("cloud", [m["name"] for m in node["module_schemas"]])
        self.assertIn("vpn-client", [m["name"] for m in data["modules"]])
        master = [r for r in data["roles"] if r["name"] == "master"][0]
        self.assertEqual([m["name"] for m in master["module_schemas"]], ["vpn-client", "vpn-server", "dns", "tor", "reverse-proxy"])
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
                    ("10.8.0.6", "m1.kiwi"), ("10.8.0.6", "pihole.m1.kiwi"), ("10.8.0.1", "gate.kiwi"),
                    ("10.8.0.1", "wg.gate.kiwi"), ("10.8.0.1", "pihole.gate.kiwi")):
            self.assertIn(rec, recs)
        self.assertNotIn(("10.8.0.25", "*.sh3.kiwi"), recs)
        m1, b = r.script(f.hosts["m1"])
        self.assertIn("KS_STACK_NO_RESOLVED_STUB=1", m1)
        # the master's address comes from the fleet's master host: DNS upstream, forward, hosts block
        self.assertIn("FTLCONF_dns_upstreams=10.8.0.1;9.9.9.9;149.112.112.112", b.compose["services"]["kn-pihole"]["environment"])
        self.assertIn("FTLCONF_misc_dnsmasq_lines=strict-order;server=/kiwi/10.8.0.1", b.compose["services"]["kn-pihole"]["environment"])
        self.assertIn("'10.8.0.1 gate.kiwi'", m1)
        self.assertIn("'127.0.0.1 m1.kiwi'", m1)
        self.assertIn("'10.8.0.25 cloud.sh3.kiwi'", m1)
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

    def test_backup_restore_and_domain_through_the_cli(self):
        pf = os.path.join(self.tmp, "pass")
        write(pf, "correct horse\n")
        f = self.fleet({"defaults": self.base_defaults(backup={"hosts": []}), "hosts": {"a": {}}})
        store = os.path.join(self.tmp, "store")
        rc, out, err = self.run_cli("backup", f.path, "--local", store, "--passphrase-file", pf)
        self.assertEqual(rc, 0, err)
        archives = os.listdir(store)
        self.assertEqual(len(archives), 1)
        self.assertEqual(oct(os.stat(os.path.join(store, archives[0])).st_mode & 0o777), "0o600")
        into = os.path.join(self.tmp, "fresh")
        rc, out, err = self.run_cli("restore", os.path.join(store, archives[0]), "--into", into, "--passphrase-file", pf)
        self.assertEqual(rc, 0, err)
        self.assertTrue(os.path.isfile(os.path.join(into, "fleet.yaml")))
        self.assertTrue(os.path.isfile(os.path.join(into, "secrets", "wg.conf")))
        rc, out, err = self.run_cli("validate", os.path.join(into, "fleet.yaml"))
        self.assertEqual(rc, 0, err)
        rc, out, err = self.run_cli("backup", f.path)
        self.assertEqual(rc, 1)
        self.assertIn("nowhere to back up", err)
        # a render with backup.hosts but no passphrase warns and still succeeds
        f = self.fleet({"defaults": self.base_defaults(backup={"hosts": ["a"]}), "hosts": {"a": {}}})
        rc, out, err = self.run_cli("render", f.path, "-o", os.path.join(self.tmp, "o"), "--toolchain", "native")
        self.assertEqual(rc, 0, err)
        self.assertIn("passphrase_file", err)
        # the domain: init --domain, warnings and the .local refusal
        dest = os.path.join(self.tmp, "lan.yaml")
        rc, out, err = self.run_cli("init", dest, "--domain", "lan")
        self.assertEqual(rc, 0, err)
        with open(dest) as fh:
            self.assertIn("domain: lan", fh.read())
        self.assertIn("OpenWrt", err)
        rc, out, err = self.run_cli("init", os.path.join(self.tmp, "bad.yaml"), "--domain", "local")
        self.assertEqual(rc, 1)
        self.assertIn("mDNS", err)
        f = self.fleet({"defaults": self.base_defaults(domain="local"), "hosts": {"a": {}}})
        rc, out, err = self.run_cli("validate", f.path)
        self.assertEqual(rc, cli.EXIT_INVALID)
        self.assertIn("mDNS", err)

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
        write(os.path.join(self.tmp, "secrets", "m1.home.conf"), "[Interface]\nPrivateKey = x\nAddress = 10.8.0.6/24\n")
        write(os.path.join(self.tmp, "secrets", "sh3.home.conf"), "[Interface]\nPrivateKey = x\nAddress = 10.8.0.25/24\n")
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
