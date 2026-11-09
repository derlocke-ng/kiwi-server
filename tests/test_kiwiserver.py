"""Unit tests for kiwiserver. Run: python3 -m unittest discover -s tests -v

Anything that needs a tool (shellcheck, butane, xorriso, cpio) uses it when it
is on $PATH and is skipped otherwise, so the suite runs anywhere; CI installs
all of them. No test touches the network.
"""
import contextlib
import http.server
import io
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import types
import unittest
import urllib.parse
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))
os.environ["KIWI_SERVER_HOME"] = ROOT

import yaml  # noqa: E402

from kiwiserver import VERSION, backup, certs, cli, config, iso, modules as modmod, remote, roles as rolesmod, router, wgeasy  # noqa: E402
from kiwiserver import facts, luks, mtu, store, unlock  # noqa: E402
from kiwiserver.targets import coreos as coreos_t, debian as debian_t  # noqa: E402
from kiwiserver.toolchain import Toolchain  # noqa: E402
from kiwiserver import util  # noqa: E402
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
            "a": {"role": "master", "master": {"vpn-server": {}, "dns": {"pihole_password": "p"}}},
            "b": {"role": "node-cloud", "node-cloud": self.node_cloud(**{"vpn-client": {"wireguard_config": "nope.conf"}})}}})
        with self.assertRaises(KiwiError) as cm:
            rolesmod.resolve_settings(self.roles["master"], f.hosts["a"], self.ms)
        self.assertIn("vpn-server.wg_host is required", str(cm.exception))
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
        helper = rolesmod.stack_cli()      # its own file since the split began: roles/common/kiwi-stack
        self.assertTrue(helper.startswith("#!/usr/bin/env bash"))
        self.assertIn("ks_stack_standalone \"$2\"", helper)          # kiwi-stack apply DIR
        self.assertIn("ks_file kiwi-stack /usr/local/bin/kiwi-stack 0755", lib)
        self.assertIn("ks_file kiwi-stack-lib /usr/local/lib/kiwi-stack/lib.sh 0644", lib)
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

    def test_one_time_key_without_a_passphrase(self):
        h, ps, _files = self.make(debian={"encrypt": True})
        path = luks.once_file(h)
        with open(path) as fh:
            key = fh.read()
        self.assertRegex(key, r"^[a-kmnp-x2-9]{14}$")        # typed at a console: no y/z, l/1, o/0
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        self.assertIn("d-i partman-crypto/passphrase password %s\n" % key, ps)
        self.assertIn("d-i partman-crypto/passphrase-again password %s\n" % key, ps)
        _h, ps, _files = self.make(debian={"encrypt": True})  # the same until the machine's first apply
        self.assertIn("d-i partman-crypto/passphrase password %s\n" % key, ps)

    def test_literal_passphrase_is_kept_and_warned_about(self):
        h, ps, _files = self.make(debian={"encrypt": True, "passphrase": "secret-pass"})
        self.assertIn("d-i partman-crypto/passphrase password secret-pass\n", ps)
        self.assertFalse(os.path.exists(luks.once_file(h)))
        self.assertTrue(any("debian.passphrase" in w for w in h.warnings), h.warnings)

    def test_preseed_powers_off_when_done(self):
        _h, ps, _files = self.make()
        self.assertIn("d-i debian-installer/exit/poweroff boolean true", ps)   # the default: remove the media first
        _h, ps, _files = self.make(install={"on_done": "reboot"})
        self.assertNotIn("exit/poweroff", ps)

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
        late = debian_t.late_script(h)
        self.assertIn("KEYMAP=y", late)                  # the LUKS prompt in the installed system's own layout
        self.assertIn("in-target update-initramfs -u", late)
        h2, _ps, _ = self.make()
        self.assertNotIn("KEYMAP", debian_t.late_script(h2))

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
        ign = os.path.join(self.tmp, "o", "a.ign")
        os.makedirs(os.path.dirname(ign))
        write(os.path.join(self.tmp, "o", "a.iso"), "stale")   # a rebuild: the old ISO must go first
        iso.coreos_iso(FakeTC(), f.hosts["a"], ign, os.path.join(self.tmp, "o", "a.iso"), self.tmp)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "o", "a.iso")))
        self.assertEqual(calls[0][:4], ["coreos-installer", "download", "-s", "stable"])
        # the live environment gets its own Ignition: root and the admin may log in, the disks are printed
        self.assertEqual(calls[1][:2], ["butane", "--strict"])
        with open(os.path.join(self.tmp, "o", "a.live.bu")) as fh:
            live = fh.read()
        self.assertIn("name: root", live)
        self.assertIn("password_hash: $6$", live)
        self.assertIn("lsblk -d", live)
        self.assertIn("Before=coreos-installer.service", live)
        cust = calls[2]
        self.assertEqual(cust[:3], ["coreos-installer", "iso", "customize"])
        self.assertTrue(cust[cust.index("--live-ignition") + 1].endswith("a.live.ign"))
        self.assertEqual(cust[cust.index("--dest-device") + 1], "/dev/sda")
        self.assertEqual(cust[cust.index("--dest-ignition") + 1], ign)
        self.assertEqual(cust[cust.index("--dest-console") + 1], "ttyS0,115200n8")
        self.assertEqual(cust[cust.index("--dest-karg-append") + 1], "mitigations=auto")
        # install.on_done: poweroff (the default): the live system skips its reboot and a
        # post-install script, next to the Ignition, turns the machine off
        self.assertEqual(cust[cust.index("--live-karg-append") + 1], "coreos.inst.skip_reboot")
        post = cust[cust.index("--post-install") + 1]
        self.assertEqual(os.path.dirname(post), os.path.dirname(ign))
        with open(post) as fh:
            self.assertIn("systemctl poweroff", fh.read())
        self.assertEqual(cust[-1], base)
        # install.on_done: reboot keeps the live ISO's own reboot
        f2 = self.fleet({"defaults": self.base_defaults(install={"on_done": "reboot"}), "hosts": {"a": {}}})
        calls.clear()
        iso.coreos_iso(FakeTC(), f2.hosts["a"], ign, os.path.join(self.tmp, "o", "a.iso"), self.tmp)
        self.assertNotIn("--post-install", calls[2])
        self.assertNotIn("--live-karg-append", calls[2])

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
        self.assertEqual(len(ms.names()), 20)
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
        self.assertIn('Environment="STARTUP_APPS=deck twofactor_totp tasks calendar contacts notes end_to_end_encryption"', ncq)
        self.assertIn("After=nextcloud-aio-database.service", ncq)
        self.assertIn("HealthCmd=/healthcheck.sh", ncq)
        self.assertIn("StopTimeout=600", ncq)
        self.assertIn("ShmSize=134217728", ncq)
        self.assertIn("Volume=/run/podman/podman.sock:/var/run/docker.sock", b.quadlets["kn-portainer.container"])
        self.assertIn("SecurityLabelDisable=true", b.quadlets["kn-portainer.container"])
        net = b.quadlets["knet-node.network"]
        self.assertIn("Subnet=172.128.0.0/24", net)
        self.assertIn("Gateway=172.128.0.1", net)
        self.assertIn("IPRange=172.128.0.128/26", net)     # the AIO containers draw from here, not from .2 upwards
        self.assertEqual(compose["networks"]["knet-node"]["ipam"]["config"][0]["ip_range"], "172.128.0.128/26")
        self.assertIn("Options=mtu=1500", net)     # the bridge carries the tunnel's packets plus their headers
        self.assertIn("WIREGUARD_MTU=1376", compose["services"]["kn-vpn-client"]["environment"])
        # a fixed address inside the dynamic range is refused
        host, role, spec = self.spec("sh3", role="node-cloud",
                                     **{"node-cloud": self.node_cloud(vault={"container_ip": "172.128.0.130"})})
        with self.assertRaises(KiwiError):
            modmod.Renderer(self.ms).render(spec)
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

    def test_auto_update_setting_and_host_resolver(self):
        host, role, spec = self.spec("m1", role="node", **{"node": self.node_gw(preset="gateway")})
        b = modmod.Renderer(self.ms).render(spec)
        self.assertIn("AutoUpdate=registry", b.quadlets["kn-pihole.container"])
        self.assertEqual(b.dns_ip, b.container_ips["dns"])
        host, role, spec = self.spec("m2", role="node", **{"node": self.node_gw(preset="gateway", auto_update="off")})
        b = modmod.Renderer(self.ms).render(spec)
        self.assertNotIn("AutoUpdate=", b.quadlets["kn-pihole.container"])
        self.assertEqual(b.auto_update, "off")
        host, role, spec = self.spec("sh3", role="node", **{"node": self.node_cloud(preset="cloud")})
        self.assertEqual(modmod.Renderer(self.ms).render(spec).dns_ip, "")   # no dns module: the host keeps its resolver
        host, role, spec = self.spec("gate", role="master", **{"master": self.master()})
        self.assertEqual(modmod.Renderer(self.ms).render(spec).dns_ip, "172.64.0.3")   # Pi-hole at the WireGuard server's address

    def test_port_ranges(self):
        self.assertEqual(modmod._port_range("27000-27100"), (27000, 27100))
        with self.assertRaisesRegex(KiwiError, "host port 27050/udp"):
            modmod.Renderer.check_ports({"a": {"ports": ["27000-27100:27000-27100/udp"]}, "b": {"ports": ["27050:27050/udp"]}})
        modmod.Renderer.check_ports({"a": {"ports": ["27000-27100:27000-27100/udp"]}, "b": {"ports": ["27050:27050/tcp"]}})
        host, role, spec = self.spec("sh3", role="node", **{"node": self.node_cloud(preset="cloud", **{
            "vpn-client": {"wireguard_config": "secrets/wg.conf", "extra_dnat_rules": ["27000-27100/udp:172.128.0.200", "2224/tcp:172.128.0.251:22"]}})})
        b = modmod.Renderer(self.ms).render(spec)
        rules = b.files["kn-vpn-client/post-rules.txt"][0]
        self.assertIn("-p udp --dport 27000:27100 -j DNAT --to-destination 172.128.0.200\n", rules)
        self.assertIn("-p tcp --dport 2224 -j DNAT --to-destination 172.128.0.251:22\n", rules)
        env = b.compose["services"]["kn-vpn-client"]["environment"]
        self.assertIn("FIREWALL_VPN_INPUT_PORTS=80,443,2224", env)      # gluetun takes single ports only

    def test_secrets_stay_out_of_the_unit_files(self):
        host, role, spec = self.spec("n", role="node", **{"node": self.node_gw(preset="gateway")})
        spec.module_config["dns"]["pihole_password"] = "Sup3rS3cretPassw0rd"
        spec.module_config["downloader"]["transmission_password"] = "tr4nsm1ssionPW"
        b = modmod.Renderer(self.ms).render(spec)
        for name, text in b.quadlets.items():
            self.assertNotIn("Sup3rS3cretPassw0rd", text, name)
            self.assertNotIn("tr4nsm1ssionPW", text, name)
        pihole = b.quadlets["kn-pihole.container"]
        self.assertIn("EnvironmentFile=/home/user/docker/kiwi/env/kn-pihole.env", pihole)
        self.assertIn("Environment=FTLCONF_dns_listeningMode=all", pihole)          # the rest stays in the unit
        env, mode = b.files["kiwi/env/kn-pihole.env"]
        self.assertEqual(mode, 0o600)
        self.assertIn("FTLCONF_webserver_api_password=Sup3rS3cretPassw0rd\n", env)
        self.assertIn("FTLCONF_webserver_api_password=Sup3rS3cretPassw0rd", b.files["docker-compose.yml"][0])
        # a one-letter secret moves only the value that is exactly it, never every value containing that letter
        spec.module_config["dns"]["pihole_password"] = "a"
        b = modmod.Renderer(self.ms).render(spec)
        self.assertIn("Environment=FTLCONF_dns_listeningMode=all", b.quadlets["kn-pihole.container"])
        self.assertIn("FTLCONF_webserver_api_password=a\n", b.files["kiwi/env/kn-pihole.env"][0])
        # the units survive a cold pull: no start limit, a pause between restarts
        self.assertIn("StartLimitIntervalSec=0", pihole)
        self.assertIn("RestartSec=15", pihole)
        self.assertEqual(b.images, sorted(set(b.images)))
        self.assertTrue(any("pihole" in i for i in b.images), b.images)

    def test_own_names_and_resolver_inside_the_containers(self):
        host, role, spec = self.spec("sh3", role="node", **{"node": self.node_cloud(preset="cloud")})
        b = modmod.Renderer(self.ms).render(spec)
        nc = b.compose["services"]["nextcloud-aio-nextcloud"]
        self.assertIn("cloud.sh3.kiwi:172.128.0.5", nc["extra_hosts"])     # the proxy, not loopback, not the mesh
        self.assertIn("AddHost=cloud.sh3.kiwi:172.128.0.5", b.quadlets["nextcloud-aio-nextcloud.container"])
        self.assertNotIn("dns", nc)                                           # no dns module on this preset
        self.assertNotIn("extra_hosts", b.compose["services"]["kn-vpn-client"] if "network_mode" in b.compose["services"]["kn-vpn-client"] else {})
        host, role, spec = self.spec("m1", role="node", **{"node": self.node_gw(preset="gateway")})
        b = modmod.Renderer(self.ms).render(spec)
        svcs = b.compose["services"]
        self.assertEqual(svcs["kn-portainer"]["dns"], [b.container_ips["dns"], "9.9.9.9"])
        self.assertNotIn("dns", svcs["kn-pihole"])
        self.assertNotIn("dns", svcs["kn-vpn-client"])
        self.assertIn("DNS=%s" % b.container_ips["dns"], b.quadlets["kn-portainer.container"])
        for svc, body in svcs.items():
            if body.get("network_mode"):
                self.assertNotIn("extra_hosts", body, svc)
                self.assertNotIn("dns", body, svc)
        # the master: Pi-hole answers at the WireGuard server's address, which it shares
        host, role, spec = self.spec("gate", role="master", **{"master": self.master()})
        b = modmod.Renderer(self.ms).render(spec)
        self.assertEqual(b.compose["services"]["km-vpn-client"]["dns"] if "dns" in b.compose["services"]["km-vpn-client"] else None, None)
        self.assertEqual(b.compose["services"]["km-vpn-server"]["dns"], ["172.64.0.3", "9.9.9.9"])
        # no masquerade on DNAT'd flows: the host routes the mesh back through the VPN client
        host, role, spec = self.spec("sh3", role="node", **{"node": self.node_cloud(preset="cloud")})
        rules = modmod.Renderer(self.ms).render(spec).files["kn-vpn-client/post-rules.txt"][0]
        self.assertNotIn("--ctstate DNAT -j MASQUERADE", rules)
        self.assertIn("-o tun0 -j MASQUERADE", rules)

    def test_fleet_ca_reaches_nextcloud(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {"sh3": {"role": "node", "node": self.node_cloud(preset="cloud")}}})
        host = f.hosts["sh3"]; role = self.prepare(host)
        pem = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
        b = modmod.Renderer(self.ms).render(rolesmod.stack_spec(host, role, ca_cert=pem))
        self.assertEqual(b.files["kiwi/ca/kiwiCA.crt"], (pem, 0o644))
        nc = b.compose["services"]["nextcloud-aio-nextcloud"]
        self.assertIn("/home/user/docker/kiwi/ca:/usr/local/share/ca-certificates:ro,z", nc["volumes"])
        self.assertIn("TRUSTED_CACERTS_DIR=/usr/local/share/ca-certificates", nc["environment"])
        self.assertIn("/home/user/docker/knnc-data:/mnt/ncdata:rw,z", nc["volumes"])
        b = modmod.Renderer(self.ms).render(rolesmod.stack_spec(host, role))
        self.assertNotIn("kiwi/ca/kiwiCA.crt", b.files)
        self.assertIn("TRUSTED_CACERTS_DIR=", b.compose["services"]["nextcloud-aio-nextcloud"]["environment"])

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
        self.assertIn("FTLCONF_misc_dnsmasq_lines=strict-order;no-hosts;server=/kiwi/10.8.0.1", svcs["kn-pihole"]["environment"])
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
        # the node's own names point at the proxy inside the containers — except in Pi-hole,
        # which would serve that container address to every client (problem 53)
        self.assertIn("AddHost=", b.quadlets["kn-nginx.container"])
        self.assertNotIn("AddHost=", b.quadlets["kn-pihole.container"])
        # gw.sh: -I inserts on top, so the DROP must be inserted last to be checked first
        inserts = [ln for ln in gw.splitlines() if ln.startswith("iptables -I FORWARD")]
        self.assertTrue(inserts[-1].endswith('-d "$DOCKER_SUBNET" -m conntrack --ctstate NEW -m conntrack ! --ctstate DNAT -j DROP'), inserts)
        # … and, standing on top, it must spare the replies to what the containers started
        # (the VPN client's handshake) and the LAN's connections to published ports
        self.assertRegex(b.files["kn-portainer/admin-password"][0], r"^[A-Za-z0-9]{32}\s*$")   # left out: generated, no open first-run page
        self.assertIn("--admin-password-file", svcs["kn-portainer"]["command"])
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
        self.assertIn("set $backend http://127.0.0.1:8081;", nginx)   # nginx owns 80/443 in the shared namespace
        self.assertIn("FTLCONF_webserver_port=8081", svcs["km-pihole"]["environment"])
        self.assertEqual(b.quadlets["km-pihole.container"].count("Network=container:km-vpn-server"), 1)
        self.assertEqual(modmod.Renderer(self.ms).service_names(spec), ["wg.gate.kiwi", "pihole.gate.kiwi"])
        self.assertEqual(svcs["km-tor"]["network_mode"], "service:km-vpn-server")
        self.assertEqual(svcs["km-vpn-server"]["networks"]["knet-master"]["ipv4_address"], "172.64.0.3")
        self.assertIn("127.0.0.1:8080:8081/tcp", svcs["km-vpn-server"]["ports"])
        # two modules in one namespace on one port is an error at render time, not on the machine
        with self.assertRaisesRegex(KiwiError, "both listen on 80/tcp inside km-vpn-server"):
            _h, _r, clash = self.spec("gate2", role="master", **{"master": self.master(dns={"pihole_password": "p", "web_port": 80})})
            modmod.Renderer(self.ms).render(clash)
        self.assertIn("51820:51820/udp", svcs["km-vpn-server"]["ports"])
        # the master resolves through its exit tunnel and is the authority for the fleet's names
        self.assertIn("FTLCONF_dns_upstreams=172.64.0.2;9.9.9.9;149.112.112.112", svcs["km-pihole"]["environment"])
        self.assertIn("FTLCONF_misc_dnsmasq_lines=strict-order;no-hosts;local=/kiwi/;hostsdir=/etc/mullvad-socks",
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
        self.assertIn("-j TCPMSS --set-mss 1336", b.files["km-vpn-client/post-rules.txt"][0])
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
        self.assertIn("FTLCONF_misc_dnsmasq_lines=strict-order;no-hosts;local=/kiwi/", b.compose["services"]["kn-pihole"]["environment"])
        self.assertNotIn("kn-nginx", b.compose["services"])

    def test_bridge_and_tunnel_mtus_are_two_settings(self):
        # the defaults: the bridge at 1500 carries the 1376 tunnels with their headers
        host, role, spec = self.spec("gate", role="master", target="debian", master=self.master())
        self.assertEqual((spec.bridge_mtu, spec.wg_mtu), (1500, 1376))
        b = modmod.Renderer(self.ms).render(spec)
        self.assertEqual(b.compose["networks"]["knet-master"]["driver_opts"]["com.docker.network.driver.mtu"], "1500")
        self.assertIn("WIREGUARD_MTU=1376", b.compose["services"]["km-vpn-client"]["environment"])
        self.assertIn("WG_MTU=1376", b.compose["services"]["km-vpn-server"]["environment"])
        start = b.files["km-vpn-server/start.sh"][0]
        # wg0 is set after the iptables-nft shim, before the MSS rule
        self.assertLess(start.index("ip link set dev wg0 mtu 1376"), start.index("TCPMSS --set-mss 1336"))
        self.assertLess(start.index("iptables-nft"), start.index("ip link set dev wg0 mtu"))
        self.assertEqual(host.warnings, [])
        # both set explicitly
        host, role, spec = self.spec("gate", role="master", target="debian",
                                     master=self.master(bridge_mtu=9000, wg_mtu=1420))
        self.assertEqual((spec.bridge_mtu, spec.wg_mtu), (9000, 1420))
        start = modmod.Renderer(self.ms).render(spec).files["km-vpn-server/start.sh"][0]
        self.assertIn("ip link set dev wg0 mtu 1420", start)
        self.assertIn("TCPMSS --set-mss 1380", start)
        # the old single setting is the tunnels' MTU; the bridge keeps 1500, and a warning says so
        host, role, spec = self.spec("sh3", role="node-cloud", **{"node-cloud": self.node_cloud(mtu=1360)})
        self.assertEqual((spec.bridge_mtu, spec.wg_mtu), (1500, 1360))
        self.assertEqual(len(host.warnings), 1)
        self.assertIn("mtu is the old name of wg_mtu", host.warnings[0])
        rolesmod.resolve_settings(role, host, self.ms)   # resolved again: still one warning
        self.assertEqual(len(host.warnings), 1)
        b = modmod.Renderer(self.ms).render(spec)
        self.assertIn("Options=mtu=1500", b.quadlets["knet-node.network"])
        self.assertIn("Environment=WIREGUARD_MTU=1360", b.quadlets["kn-vpn-client.container"])
        # the 1412 fleets set was the bridge's too; as the tunnels' it does not fit once padded
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {
            "sh3": {"role": "node-cloud", "node-cloud": self.node_cloud(mtu=1412)}}})
        errs = cli.settings_errors(self.roles, self.ms, f.hosts["sh3"])
        self.assertEqual(len(errs), 1)
        self.assertIn("mtu 1412 is the old name of wg_mtu", errs[0])
        self.assertIn("at most 1408", errs[0])
        # the old name and the new one together: which one is meant is not clear
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {
            "sh3": {"role": "node-cloud", "node-cloud": self.node_cloud(mtu=1360, wg_mtu=1376)}}})
        errs = cli.settings_errors(self.roles, self.ms, f.hosts["sh3"])
        self.assertEqual(len(errs), 1)
        self.assertIn("mtu and wg_mtu are both set", errs[0])
        # WireGuard pads a packet to a multiple of 16 when the peer's MTU is bigger: the padded
        # packet plus 80 bytes of headers must fit the bridge — an error `validate` reports
        for over, ok in (({"wg_mtu": 1408}, True), ({"wg_mtu": 1409}, False), ({"wg_mtu": 1420}, False),
                         ({"bridge_mtu": 1412, "wg_mtu": 1328}, True), ({"bridge_mtu": 1412, "wg_mtu": 1329}, False),
                         ({"bridge_mtu": 1412, "wg_mtu": 1352}, False), ({"mtu": 1412, "bridge_mtu": 1412}, False),
                         ({"wg_mtu": 1200}, False)):
            f = self.fleet({"defaults": self.base_defaults(), "hosts": {
                "sh3": {"role": "node-cloud", "node-cloud": self.node_cloud(**over)}}})
            errs = cli.settings_errors(self.roles, self.ms, f.hosts["sh3"])
            self.assertEqual(not errs, ok, (over, errs))
            if over == {"bridge_mtu": 1412, "wg_mtu": 1352}:
                # the v1 master: 1352 padded to 1360 by the exit, 1420 bytes on a 1412 bridge
                self.assertIn("wg_mtu 1352 does not fit the stack network", errs[0])
                self.assertIn("pads its packets to 1360", errs[0])
                self.assertIn("at most 1328", errs[0])
        self.assertEqual([modmod.wg_padded(m) for m in (1344, 1352, 1376, 1380, 1412)], [1344, 1360, 1376, 1392, 1424])
        self.assertEqual(modmod.wg_mtu_limit(1500), 1408)
        with self.assertRaisesRegex(KiwiError, "x: wg_mtu 1500 does not fit"):
            modmod.StackSpec("node", "x", ["vpn-client"], wg_mtu=1500)
        self.assertEqual(modmod.StackSpec("node", "x", ["vpn-client"], mtu=1400).wg_mtu, 1400)   # the old keyword

    def test_gateway_nodes_set_the_mss_by_default(self):
        # LAN clients routed through a gateway node advertise MSS 1460, more than the tunnel carries;
        # the rule sets wg_mtu - 40 both ways, not what the interface a SYN leaves by allows
        host, role, spec = self.spec("m1", role="node-gw", **{"node-gw": self.node_gw()})
        self.assertTrue(host.module_settings["vpn-client"]["mss_clamp"])
        post = modmod.Renderer(self.ms).render(spec).files["kn-vpn-client/post-rules.txt"][0]
        self.assertIn("-j TCPMSS --set-mss 1336", post)
        self.assertNotIn("-j TCPMSS --clamp-mss-to-pmtu", post)
        # the node role with the gateway module in its own list, no preset
        host, role, spec = self.spec("m2", role="node", node={
            "vpn_ip": "10.8.0.7", "vpn-client": {"wireguard_config": "secrets/wg.conf"},
            "modules": ["vpn-client", "dns", "gateway"], "dns": {"pihole_password": "p"}})
        self.assertTrue(host.module_settings["vpn-client"]["mss_clamp"])
        # the host still decides
        gw = self.node_gw()
        gw["vpn-client"] = dict(gw["vpn-client"], mss_clamp=False)
        host, role, spec = self.spec("m1", role="node-gw", **{"node-gw": gw})
        self.assertFalse(host.module_settings["vpn-client"]["mss_clamp"])
        self.assertNotIn("TCPMSS", modmod.Renderer(self.ms).render(spec).files["kn-vpn-client/post-rules.txt"][0])
        # a node without the gateway module keeps the setting's default
        host, role, spec = self.spec("sh3", role="node-cloud", **{"node-cloud": self.node_cloud()})
        self.assertFalse(host.module_settings["vpn-client"]["mss_clamp"])

    def test_namespace_sharers_are_never_recreated_alone(self):
        # compose 2.21 `up -d --force-recreate --no-deps kmpihole kmtor` (both on network_mode
        # service:kmvpn-server) created them with the literal network mode, docker refused to
        # start them, and the v1 master's Pi-hole — the VPN clients' DNS — was down 10 minutes.
        # Nothing the machine side runs (start, update, vpn-restart, apply) passes --no-deps …
        for rel in ("roles/common/kiwi-stack", "roles/common/lib.sh"):
            with open(os.path.join(ROOT, rel)) as fh:
                self.assertNotIn("--no-deps", fh.read(), rel)
        # … nor what the renderer writes, and under podman the sharers follow the container whose
        # namespace they use (Requires= restarts them with it)
        host, role, spec = self.spec("gate", role="master", target="debian", master=self.master())
        b = modmod.Renderer(self.ms).render(spec)
        rendered = [t for t, _m in b.files.values() if isinstance(t, str)] + list(b.units.values()) + list(b.quadlets.values())
        rendered.append(rolesmod.stack_manifest(host, role, VERSION, b))
        self.assertFalse([t for t in rendered if "--no-deps" in t])
        sharers = [n for n, svc in b.compose["services"].items() if svc.get("network_mode") == "service:km-vpn-server"]
        self.assertEqual(sorted(sharers), ["km-nginx", "km-pihole", "km-tor"])
        for n in sharers:
            unit = b.quadlets["%s.container" % n]
            self.assertIn("Network=container:km-vpn-server", unit)
            self.assertIn("Requires=km-vpn-server.service", unit)

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
        self.assertIn("FTLCONF_misc_dnsmasq_lines=strict-order;no-hosts;local=/kiwi/;hostsdir=/etc/mullvad-socks", env)
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
        self.assertIn("FTLCONF_misc_dnsmasq_lines=strict-order;no-hosts;server=/kiwi/10.8.0.1;hostsdir=/etc/mullvad-socks",
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
        self.assertEqual(lines, ["FTLCONF_misc_dnsmasq_lines=strict-order;no-hosts;local=/kiwi/;hostsdir=/etc/mullvad-socks;"
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

    def test_bundle_directory_is_a_second_source_of_files(self):
        # ks_file takes a key from the embedded KS_FILES, or from a bundle directory with the same layout
        lib = rolesmod.common_lib()
        libp = os.path.join(self.tmp, "lib.sh"); write(libp, lib)
        bdir = os.path.join(self.tmp, "bundle")
        write(os.path.join(bdir, "kn-nginx", "nginx.conf"), "nginx here\n")
        write(os.path.join(bdir, "host-units", "kn-gateway.service"), "[Unit]\n")
        write(os.path.join(bdir, "quadlets", "kn-nginx.container"), "[Container]\n")
        write(os.path.join(bdir, "kiwi", "ca", "kiwiCA.crt"), "CA\n")
        me = "%d:%d" % (os.getuid(), os.getgid())
        script = ("set -euo pipefail; . %s; KS_BUNDLE_DIR=%s; "
                  "ks_file stack/kn-nginx/nginx.conf %s/out/nginx.conf 0600 %s; "
                  "ks_file unit/kn-gateway.service %s/out/gw.service 0644 %s; "
                  "ks_file quadlet/kn-nginx.container %s/out/q.container 0600 %s; "
                  "ks_has_file ca_cert && echo has-ca; ks_has_file stack/missing || echo no-missing; "
                  "ks_file stack/missing %s/out/x 0600 %s" % (libp, bdir, self.tmp, me, self.tmp, me, self.tmp, me, self.tmp, me))
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("has-ca", r.stdout); self.assertIn("no-missing", r.stdout)
        self.assertIn("no file for 'stack/missing'", r.stderr)
        with open(os.path.join(self.tmp, "out", "nginx.conf")) as fh:
            self.assertEqual(fh.read(), "nginx here\n")
        self.assertEqual(oct(os.stat(os.path.join(self.tmp, "out", "nginx.conf")).st_mode & 0o777), "0o600")
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "out", "gw.service")))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "out", "q.container")))

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
        self.assertNotIn("# the config says MTU", split)
        # the fleet's wg_mtu (the master's wg0) wins over what an older config says
        fleet_mtu = router.openwrt_script("home", "10.8.0.1", "10.8.0.0/16", wg=wg, wg_mtu=1376)
        self.assertIn("uci set network.kiwi.mtu='1376'", fleet_mtu)
        self.assertIn("# the config says MTU 1412; the fleet's wg_mtu, the master's wg0, is 1376", fleet_mtu)
        no_mtu = router.parse_wireguard(WG_CONF.replace("MTU = 1412\n", ""))
        self.assertIn("uci set network.kiwi.mtu='1376'", router.openwrt_script("home", "10.8.0.1", "10.8.0.0/16", wg=no_mtu))
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
        self.assertIn("network.kiwi.mtu='1376'", out.getvalue())   # the master's wg_mtu, not the config's 1412
        out = StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["openwrt", f.path, "--via", "m1"]), 0)
        self.assertIn("kiwi_route.gateway='192.168.1.5'", out.getvalue())
        err = StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["openwrt", f.path, "--via", "d"]), 1)   # d has no static LAN address
        self.assertIn("static LAN address", err.getvalue())

    def test_apply_uses_the_fleets_own_key(self):
        # admin.ssh_keys names secrets/admin.pub and the private key lies next to it:
        # ssh offers that key and no other (the agent may hold keys the machines never saw)
        write(os.path.join(self.tmp, "secrets", "admin.pub"), KEY)
        write(os.path.join(self.tmp, "secrets", "admin"), "private")
        f = self.fleet({"defaults": self.base_defaults(admin={"user": "core", "password": "pw",
                                                             "ssh_keys": ["secrets/admin.pub", KEY]}),
                        "hosts": {"a": {}}})
        ids = cli.fleet_identities(f)
        self.assertEqual(ids, [os.path.join(self.tmp, "secrets", "admin")])
        remote.configure("/kh", ids)
        try:
            opts = remote.ssh_opts()
            self.assertIn("ControlMaster=no", opts)     # never through the operator's mux master (problem 54)
            self.assertIn("IdentitiesOnly=yes", opts)
            self.assertEqual(opts[opts.index("-i") + 1], ids[0])
        finally:
            remote.configure("")
        self.assertNotIn("-i", remote.ssh_opts())
        # a .pub without its private key, or a literal key: nothing to offer, the agent decides
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {"a": {}}})
        self.assertEqual(cli.fleet_identities(f), [])

    def test_bare_media_leaves_the_stack_for_apply(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {
            "gate": {"role": "master", "target": "debian", "master": self.master()},
            "m1": {"role": "node-gw", "target": "debian",
                   "node-gw": self.node_gw(**{"vpn-client": {"wireguard_config": "secrets/not-enrolled-yet.conf"}})}}})
        out = os.path.join(self.tmp, "out")
        # a node whose WireGuard config does not exist yet: refused with the role, fine as bare media
        with self.assertRaises(SystemExit) as cm:
            cli.main(["render", f.path, "m1", "--no-backup", "-o", out])
        self.assertEqual(cm.exception.code, cli.EXIT_INVALID)
        self.assertEqual(cli.main(["render", f.path, "m1", "--bare", "--no-backup", "-o", out]), 0)
        self.assertEqual(cli.main(["render", f.path, "gate", "--bare", "--no-backup", "-o", out]), 0)
        with open(os.path.join(out, "gate", "gate.role.sh")) as fh:
            script = fh.read()
        self.assertIn("KS_STACK=0", script)
        self.assertNotIn("KS_FILES[stack/docker-compose.yml]=", script)   # no compose file, no wg-easy password on the media
        self.assertNotIn("KS_FILES[kiwi-stack]=", script)
        self.assertEqual(cli.main(["render", f.path, "gate", "--no-backup", "-o", out]), 0)
        with open(os.path.join(out, "gate", "gate.role.sh")) as fh:
            script = fh.read()
        self.assertIn("KS_STACK=1", script)                  # the real role again, same output directory
        self.assertIn("KS_FILES[stack/docker-compose.yml]=", script)


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


class TestPrune(Base):
    FORGET = b'[{"keep": ["a", "b"], "remove": ["c", "d", "e"]}, {"keep": ["f"], "remove": null}]\n'

    def test_it_runs_on_the_node_and_the_password_comes_in_on_stdin(self):
        calls = []

        def runner(argv, input=None, capture_output=True):
            calls.append((argv[-1], input))
            out = self.FORGET if " forget " in argv[-1] else b"to delete: 1\ntotal prune:  3 blobs / 1.5 MiB\nremaining: x\n"
            return subprocess.CompletedProcess(argv, 0, out, b"")
        r = backup.prune_repository("core@n.kiwi", "podman", "/data/papaya.kiwi", "restic:1", "s3cret", runner=runner)
        self.assertEqual(r, {"kept": 3, "removed": 3, "freed": "3 blobs / 1.5 MiB"})
        self.assertEqual([c[1] for c in calls], [b"s3cret\n", b"s3cret\n"])
        self.assertNotIn("s3cret", " ".join(c[0] for c in calls))
        self.assertIn("--network none", calls[0][0])
        self.assertIn("--group-by host,tags", calls[0][0])
        self.assertIn("--keep-daily 7 --keep-weekly 4 --keep-monthly 6", calls[0][0])
        self.assertIn(" prune", calls[1][0])

    def test_a_dry_run_frees_nothing(self):
        calls = []

        def runner(argv, input=None, capture_output=True):
            calls.append(argv[-1])
            return subprocess.CompletedProcess(argv, 0, self.FORGET, b"")
        r = backup.prune_repository("core@n.kiwi", "podman", "/d/x", "restic:1", "p", dry_run=True, runner=runner)
        self.assertEqual((r["kept"], r["removed"]), (3, 3))
        self.assertEqual(len(calls), 1)
        self.assertIn("--dry-run", calls[0])

    def prune_fleet(self):
        node = {"vpn_ip": "10.8.0.9", "vpn-client": {"wireguard_config": "secrets/wg.conf"}}
        return self.fleet({"defaults": self.base_defaults(), "hosts": {
            "store": {"role": "node", "node": dict(node, modules=["vpn-client", "reverse-proxy", "backup-server"],
                                                   **{"backup-server": {"clients": ["papaya.kiwi", "elsewhere.kiwi"]}})},
            "papaya": {"role": "node", "node": dict(node, modules=["vpn-client", "reverse-proxy", "backup"],
                                                    backup={"server": "https://backup.store.kiwi"})},
            "plain": {"role": "node", "node": {"preset": "minimal"}}}})

    def test_the_command_finds_each_clients_password_and_repository(self):
        f = self.prune_fleet()
        calls = []

        def fake_run(tgt, script, data=None, runner=None):
            calls.append((tgt, script, data))
            return self.FORGET if " forget " in script else b"total prune:  3 blobs / 1.5 MiB\n"
        with mock.patch.object(remote, "run", fake_run):
            rc, out, err = TestManage.run_cli(self, "prune", f.path)
        self.assertEqual(rc, 0, err)
        self.assertEqual(calls[0][0], "core@store.kiwi")
        self.assertIn("/home/user/docker/kn-backup-server/data/papaya.kiwi:/repo", calls[0][1])
        self.assertEqual(calls[0][2], (f.derive_secret("papaya.kiwi", "backup", "repo_password") + "\n").encode())
        self.assertIn("3 snapshots kept, 3 removed, freed 3 blobs / 1.5 MiB", out + err)
        self.assertIn("elsewhere.kiwi is not a machine of this fleet", out + err)
        self.assertNotIn(calls[0][2].decode().strip(), out + err)

    def test_the_command_will_not_keep_nothing(self):
        rc, out, err = TestManage.run_cli(self, "prune", self.prune_fleet().path, "--keep-daily", "0", "--keep-weekly", "0", "--keep-monthly", "0")
        self.assertNotEqual(rc, 0)
        self.assertIn("would keep nothing", err)

    def test_the_command_needs_a_backup_server(self):
        rc, out, err = TestManage.run_cli(self, "prune", self.prune_fleet().path, "plain")
        self.assertNotEqual(rc, 0)
        self.assertIn("none of these hosts", err)

    def test_nothing_stored_yet_is_nothing_to_do(self):
        def runner(argv, input=None, capture_output=True):
            return subprocess.CompletedProcess(argv, 0, b"[]\n", b"")
        self.assertEqual(backup.prune_repository("c@n", "podman", "/d/x", "r:1", "p", runner=runner),
                         {"kept": 0, "removed": 0, "freed": ""})


class TestUnlock(Base):
    def unlock_fleet(self, rings=("infra",), threshold=1, lychee_net=None):
        node = {"vpn_ip": "10.8.0.9", "vpn-client": {"wireguard_config": "secrets/wg.conf"}}
        d = self.base_defaults(network={"dhcp": False, "gateway": "192.168.2.1", "dns": ["9.9.9.9"]})
        write(os.path.join(self.tmp, "secrets", "luks", "lychee.key"), "thediskkey\n")
        return self.fleet({"defaults": d, "hosts": {
            "papaya": {"network": {"address": "192.168.2.30/16"}, "disk": "/dev/sda", "role": "node",
                       "node": dict(node, modules=["vpn-client", "reverse-proxy", "tang"])},
            "lychee": {"target": "debian", "network": lychee_net or {"address": "192.168.2.61/16"}, "disk": "/dev/sda",
                       "debian": {"encrypt": True}, "unlock": {"rings": list(rings), "threshold": threshold},
                       "role": "node", "node": dict(node, preset="minimal")}}})

    def servers(self, f):
        return unlock.ring_servers(f, lambda h: rolesmod.resolve_settings(self.roles[h.role], h, self.ms))

    def fake_remote(self, listing="device /dev/vda5\n"):
        calls = []

        def fake_run(tgt, script, data=None, runner=None):
            calls.append((tgt, script, data))
            if "tang-show-keys" in script:
                return b"THUMBPRINT\n"
            if "/adv" in script:
                return b'{"payload":"x"}\n'
            if "clevis luks list" in script:
                return listing.encode()
            return b"updated\n" if "update-initramfs" in script else b""
        return calls, mock.patch.object(remote, "run", fake_run), mock.patch.object(remote, "reach", lambda h, o=None: "core@%s" % h.name)

    def test_it_binds_with_the_thumbprint_the_rings_own_host_reports(self):
        f = self.unlock_fleet()
        calls, a, b = self.fake_remote()
        with a, b:
            self.assertTrue(unlock.bind(f.hosts["lychee"], "core@lychee", self.servers(f)))
        self.assertEqual(calls[0][0], "core@papaya")
        self.assertIn("kn-tang-infra tang-show-keys", calls[0][1])
        key, dev, tree, old = calls[-1][2].decode().splitlines()
        self.assertEqual((key, dev, old), ("thediskkey", "/dev/vda5", ""))
        self.assertEqual(json.loads(tree), {"t": 1, "pins": {"tang": [{"url": "http://192.168.2.30:7500", "adv": {"payload": "x"}}]}})

    def test_the_initramfs_gets_the_hosts_own_address(self):
        f = self.unlock_fleet()
        calls, a, b = self.fake_remote()
        with a, b:
            unlock.bind(f.hosts["lychee"], "core@lychee", self.servers(f))
        prepare = next(c for c in calls if "update-initramfs" in c[1])
        self.assertEqual(prepare[2], b"192.168.2.61::192.168.2.1:255.255.0.0::IFACE:off\n")
        self.assertIn("clevis-initramfs", prepare[1])

    def test_what_is_already_bound_stays(self):
        f = self.unlock_fleet()
        calls, a, b = self.fake_remote("device /dev/vda5\n1: sss '{\"t\":1,\"pins\":{\"tang\":[{\"url\":\"http://192.168.2.30:7500\"}]}}'\n")
        with a, b:
            self.assertFalse(unlock.bind(f.hosts["lychee"], "core@lychee", self.servers(f)))
        self.assertFalse([c for c in calls if "luks bind" in c[1]])

    def test_another_binding_goes_after_the_new_one_is_in(self):
        f = self.unlock_fleet()
        calls, a, b = self.fake_remote("device /dev/vda5\n2: sss '{\"t\":1,\"pins\":{\"tang\":[{\"url\":\"http://10.0.0.1:7500\"}]}}'\n")
        with a, b:
            self.assertTrue(unlock.bind(f.hosts["lychee"], "core@lychee", self.servers(f)))
        script, data = calls[-1][1], calls[-1][2].decode().splitlines()
        self.assertEqual(data[3], "2")
        self.assertLess(script.index("luks bind"), script.index("luks unbind"))

    def test_the_disk_key_never_lands_on_a_command_line(self):
        f = self.unlock_fleet()
        calls, a, b = self.fake_remote()
        with a, b:
            unlock.bind(f.hosts["lychee"], "core@lychee", self.servers(f))
        self.assertFalse([c for c in calls if "thediskkey" in c[1]])

    def test_a_ring_nobody_serves_is_named(self):
        f = self.unlock_fleet(rings=("nope",))
        _calls, a, b = self.fake_remote()
        with a, b, self.assertRaisesRegex(KiwiError, "no host of this fleet serves the ring nope"):
            unlock.bind(f.hosts["lychee"], "core@lychee", self.servers(f))

    def test_a_ring_out_of_reach_is_named(self):
        f = self.unlock_fleet(lychee_net={"address": "10.20.0.5/24", "gateway": "10.20.0.1"})
        _calls, a, b = self.fake_remote()
        with a, b, self.assertRaisesRegex(KiwiError, "cannot reach the ring on papaya"):
            unlock.bind(f.hosts["lychee"], "core@lychee", self.servers(f))

    def coreos_host(self, network=None):
        write(os.path.join(self.tmp, "secrets", "x"), "")
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {"plum": {
            "disk": "/dev/vda", "unlock": {"rings": ["house"], "via": {"house": "http://192.168.2.61:7500"}},
            "network": network or {"dhcp": True}}}})
        h = f.hosts["plum"]
        h.unlock_pins = [{"url": "http://192.168.2.61:7500", "thp": "THP", "adv": '{"payload":"x"}'}]
        return h

    def test_ignition_binds_a_coreos_disk_to_the_ring(self):
        bu = coreos_t.build_butane(self.coreos_host(), "#!/bin/sh\n")
        self.assertEqual(bu["boot_device"]["luks"]["tang"], [{"url": "http://192.168.2.61:7500", "thumbprint": "THP", "advertisement": '{"payload":"x"}'}])
        self.assertEqual(bu["boot_device"]["luks"]["threshold"], 1)
        self.assertEqual(bu["kernel_arguments"]["should_exist"], ["rd.neednet=1"])

    def test_a_static_coreos_host_gets_its_address_in_the_initramfs(self):
        h = self.coreos_host({"dhcp": False, "address": "192.168.2.70/16", "gateway": "192.168.2.1", "interface": "eno1"})
        bu = coreos_t.build_butane(h, "#!/bin/sh\n")
        self.assertIn("ip=192.168.2.70::192.168.2.1:255.255.0.0::eno1:none", bu["kernel_arguments"]["should_exist"])

    def test_a_static_coreos_host_has_to_name_its_interface(self):
        h = self.coreos_host({"dhcp": False, "address": "192.168.2.70/16", "gateway": "192.168.2.1"})
        with self.assertRaisesRegex(KiwiError, "need network.interface"):
            coreos_t.build_butane(h, "#!/bin/sh\n")

    def test_a_coreos_disk_gets_a_key_for_the_console_from_its_own_binding(self):
        h = self.coreos_host()
        sent = []

        def fake_run(tgt, script, data=None, runner=None, options=()):
            sent.append((script, data))
            return b"added /dev/vda4\n"
        with mock.patch.object(remote, "run", fake_run):
            self.assertTrue(unlock._coreos_key(h, "core@plum", {}))
        key = util.read_bytes(luks.key_file(h)).decode().strip()
        self.assertEqual(sent[0][1], (key + "\n").encode())
        self.assertNotIn(key, sent[0][0])
        self.assertIn("clevis luks pass", sent[0][0])

    def test_via_names_the_way_to_a_ring_off_the_lan(self):
        f = self.unlock_fleet()
        h, srv = f.hosts["lychee"], f.hosts["papaya"]
        h.cfg["unlock"]["via"] = {"infra": "http://127.0.0.1:7500"}
        self.assertEqual(unlock.url(h, "infra", srv, 7500), "http://127.0.0.1:7500")

    def tunnel_host(self):
        f = self.unlock_fleet()
        h = f.hosts["lychee"]
        h.cfg["unlock"].update(via={"infra": "http://127.0.0.1:7500"}, ssh=True)
        return f, h

    def test_the_initramfs_key_goes_on_record_while_the_machine_can_vouch_for_it(self):
        f, h = self.tunnel_host()
        self.addCleanup(lambda: None)

        def fake_run(tgt, script, data=None, runner=None):
            return b"ssh-ed25519 AAAAhostkey\n"
        with mock.patch.object(remote, "run", fake_run):
            unlock._initramfs_ssh(h, "core@lychee")
        text = util.read_bytes(f.resolve_path("secrets/known_hosts.initramfs")).decode()
        self.assertIn("[192.168.2.61]:2222 ssh-ed25519 AAAAhostkey", text)

    def test_a_changed_initramfs_key_replaces_the_old_one(self):
        f, h = self.tunnel_host()
        write(f.resolve_path("secrets/known_hosts.initramfs"), "[192.168.2.61]:2222 ssh-ed25519 OLD\n")
        with mock.patch.object(remote, "run", lambda t, s, data=None, runner=None: b"ssh-ed25519 NEW\n"):
            unlock._initramfs_ssh(h, "core@lychee")
        text = util.read_bytes(f.resolve_path("secrets/known_hosts.initramfs")).decode()
        self.assertNotIn("OLD", text)
        self.assertIn("[192.168.2.61]:2222 ssh-ed25519 NEW", text)

    def test_the_ring_is_forwarded_and_the_host_key_must_match(self):
        f, h = self.tunnel_host()
        calls, a, b = self.fake_remote()

        def runner(argv, stdin=None, capture_output=True):
            calls.append(("ssh", argv))
            return subprocess.CompletedProcess(argv, 255, b"kiwi-unlock-connected\n", b"Connection reset")
        with a, b, mock.patch.object(remote, "_answers", lambda addr, port=22, timeout=2: addr == "192.168.2.61" or addr == "192.168.2.30"):
            self.assertTrue(unlock.open_disk(h, self.servers(f), runner=runner, clock=lambda: 0.0, sleep=lambda s: None))
        argv = [c[1] for c in calls if c[0] == "ssh"][0]
        self.assertIn("127.0.0.1:7500:192.168.2.30:7500", argv)
        self.assertEqual(argv[-2], "root@192.168.2.61")
        self.assertEqual(argv[argv.index("-p") + 1], "2222")
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertLess(argv.index("StrictHostKeyChecking=yes"), argv.index("BatchMode=yes"))     # the first one given counts

    def test_a_session_that_never_got_in_is_not_an_open_disk(self):
        f, h = self.tunnel_host()
        _calls, a, b = self.fake_remote()
        now = [0.0]

        def runner(argv, stdin=None, capture_output=True):
            now[0] += 1
            return subprocess.CompletedProcess(argv, 255, b"", b"Permission denied")

        def sleeper(s):
            now[0] += s
        with a, b, mock.patch.object(remote, "_answers", lambda addr, port=22, timeout=2: True), self.assertRaisesRegex(KiwiError, "no initramfs answered"):
            unlock.open_disk(h, self.servers(f), timeout=20, runner=runner, clock=lambda: now[0], sleep=sleeper)

    def test_the_key_goes_over_ssh_without_a_line_end(self):
        f, h = self.tunnel_host()
        seen = []

        def runner(argv, input=None, capture_output=True):
            seen.append((argv, input))
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        with mock.patch.object(remote, "_answers", lambda addr, port=22, timeout=2: True):
            self.assertTrue(unlock.type_key(h, runner=runner, sleep=lambda s: None))
        self.assertEqual(seen[0][1], b"thediskkey")
        self.assertEqual(seen[0][0][-1], "cryptroot-unlock")
        self.assertNotIn("thediskkey", " ".join(seen[0][0]))

    def test_a_refused_key_is_not_tried_again(self):
        f, h = self.tunnel_host()
        count = []

        def runner(argv, input=None, capture_output=True):
            count.append(1)
            return subprocess.CompletedProcess(argv, 1, b"", b"")
        with mock.patch.object(remote, "_answers", lambda addr, port=22, timeout=2: True), self.assertRaisesRegex(KiwiError, "counts tries"):
            unlock.type_key(h, runner=runner, sleep=lambda s: None)
        self.assertEqual(len(count), 1)

    def test_the_initramfs_brings_up_its_loopback(self):
        self.assertIn("ip link set lo up", unlock.PREPARE_DROPBEAR)

    def test_ignition_sizes_the_root_when_the_ring_is_only_forwarded(self):
        h = self.coreos_host()
        h.cfg["unlock"]["via"] = {"house": "http://127.0.0.1:7500"}
        bu = coreos_t.build_butane(h, "#!/bin/sh\n")
        self.assertEqual(bu["boot_device"]["luks"]["tang"][0]["url"], "http://192.168.2.61:7500")
        self.assertEqual(bu["storage"]["disks"][0]["partitions"], [{"label": "root", "number": 4, "size_mib": 0, "resize": True}])

    def test_a_reachable_ring_leaves_the_partition_to_coreos(self):
        self.assertNotIn("disks", coreos_t.build_butane(self.coreos_host(), "#!/bin/sh\n")["storage"])

    def test_a_ring_only_we_reach_is_forwarded_to_read_the_coreos_key(self):
        f = self.unlock_fleet()
        h = f.hosts["papaya"]
        h.target = "coreos"
        srv = f.hosts["lychee"]
        h.cfg["unlock"].update(rings=["house"], via={"house": "http://127.0.0.1:7500"})
        seen = []

        def fake_run(tgt, script, data=None, runner=None, options=()):
            seen.append(list(options))
            return b"added /dev/vda4\n"
        with mock.patch.object(remote, "run", fake_run), mock.patch.object(remote, "_answers", lambda a, p=22, timeout=2: True), \
                mock.patch.object(remote, "addresses", lambda x: ["192.168.2.61"]):
            self.assertTrue(unlock._coreos_key(h, "core@papaya", {"house": (srv, 7500)}))
        self.assertEqual(seen[0], ["-R", "127.0.0.1:7500:192.168.2.61:7500"])

    def test_coreos_gets_dracut_sshd_with_a_host_key_of_its_own(self):
        self.assertIn("dracut-sshd", unlock.PREPARE_DRACUT_SSHD)
        self.assertIn("/etc/ssh/dracut_ssh_host_ed25519_key", unlock.PREPARE_DRACUT_SSHD)
        self.assertNotIn("/etc/ssh/ssh_host", unlock.PREPARE_DRACUT_SSHD)

    def test_coreos_root_may_log_in_to_the_initramfs_with_a_key(self):
        self.assertIn("root:*:", unlock.PREPARE_DRACUT_SSHD)
        self.assertIn("--include --arg=$d/kiwi-sshd.conf", unlock.PREPARE_DRACUT_SSHD)
        self.assertNotIn("usermod", unlock.PREPARE_DRACUT_SSHD)       # the host's own root stays locked

    def test_nothing_to_forward_is_said(self):
        f = self.unlock_fleet()
        with self.assertRaisesRegex(KiwiError, "nothing to forward"):
            unlock.forwarded_ring(f.hosts["lychee"])

    def test_the_threshold_has_to_fit_the_rings(self):
        f = self.unlock_fleet(threshold=2)
        self.assertTrue([e for e in f.hosts["lychee"].validate(self.roles) if "unlock.threshold must be between 1" in e])


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

    def test_host_keys_live_with_the_fleet(self):
        calls = []

        def runner(argv, input=None, capture_output=True):
            calls.append(argv)
            return self.R(0, b"")
        remote.configure("")
        remote.run("core@a.kiwi", "true", runner=runner)
        self.assertNotIn("UserKnownHostsFile=/x/secrets/known_hosts", " ".join(calls[-1]))
        remote.configure("/x/secrets/known_hosts")
        try:
            remote.run("core@a.kiwi", "true", runner=runner)
            argv = calls[-1]
            self.assertEqual(argv[:3], ["ssh", "-o", "BatchMode=yes"])
            self.assertIn("StrictHostKeyChecking=accept-new", argv)
            self.assertIn("UserKnownHostsFile=/x/secrets/known_hosts", argv)

            def changed(argv, input=None, capture_output=True):
                return self.R(255, b"", b"@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@\nHost key verification failed.\n")
            with self.assertRaisesRegex(KiwiError, "reinstalled.*ssh-keygen -R a.kiwi -f /x/secrets/known_hosts"):
                remote.run("core@a.kiwi", "true", runner=changed)
        finally:
            remote.configure("")

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
        rc, out, err = self.run_cli("script", f.path, "sh3", "--offline")
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
        rc, out, err = self.run_cli("enroll", f.path, "../etc", "--api", api.url)
        self.assertEqual(rc, 1)
        self.assertIn("neither a fleet host nor a device name", err)
        self.assertEqual(api.calls, [])
        rc, out, err = self.run_cli("enroll", f.path, "phone", "--api", api.url)
        self.assertEqual(rc, 1)
        self.assertIn("401", err)
        self.assertIn("Incorrect password", err)
        self.assertEqual(api.clients, [])
        f = self.manage_fleet(**{"vpn-server": {"wg_host": "v"}})   # no password written down: the generated one is tried
        rc, out, err = self.run_cli("enroll", f.path, "phone", "--api", api.url)
        self.assertEqual(rc, 1)
        self.assertIn("401", err)
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {"a": {}}})
        rc, out, err = self.run_cli("enroll", f.path, "phone", "--api", api.url)
        self.assertEqual(rc, 1)
        self.assertIn("no master in the fleet", err)
        rc, out, err = self.run_cli("enroll", f.path, "phone", "--api", "http://127.0.0.1:1")
        self.assertEqual(rc, 1)

    ONCE = "abcdefghijk234"

    def luks_host(self, role="bare", **debian):
        host = {"role": role, "target": "debian", "debian": dict({"encrypt": True}, **debian)}
        if role == "master":
            host["master"] = self.master()
        return self.fleet({"defaults": self.base_defaults(), "hosts": {"d": host}})

    def luks_file(self, kind):
        return os.path.join(self.tmp, "secrets", "luks", "d." + kind)

    def luks_apply(self, f, state="swapped /dev/vda5\n", reencrypt_fails=False, pending=True, flags=()):
        if pending:
            write(self.luks_file("once"), self.ONCE)
        calls = []

        def fake_run(tgt, script, data=None, runner=None):
            calls.append((script, data))
            if "luksChangeKey" in script:
                return state.encode()
            if "cryptsetup reencrypt" in script and reencrypt_fails:
                raise KiwiError("the reencrypt was cut off")
            return b""
        with mock.patch.object(remote, "run", fake_run), \
                mock.patch.object(remote, "run_stream", lambda *a, **k: 0):
            rc, out, err = self.run_cli("apply", f.path, "d", "--ssh", "core@1.2.3.4", *flags)
        return rc, out, err, calls

    def sent_role(self, calls):
        return any("role.sh.tmp" in script for script, _data in calls)

    def test_apply_swaps_the_one_time_key_before_the_role_arrives(self):
        rc, out, err, calls = self.luks_apply(self.luks_host())
        self.assertEqual(rc, 0, err)
        script, data = calls[0]
        self.assertIn("luksChangeKey", script)
        self.assertNotIn(self.ONCE, script)                  # on stdin, never on a command line
        once, new, current = data.decode().split("\n")[:3]
        self.assertEqual((once, current), (self.ONCE, ""))
        self.assertRegex(new, r"^[a-kmnp-x2-9]{16}$")
        self.assertIn("role.sh.tmp", calls[1][0])            # the secrets come after the swap
        self.assertEqual(len(calls), 2)                      # and no new volume key on a non-master
        with open(self.luks_file("key")) as fh:
            self.assertEqual(fh.read(), new)
        self.assertEqual(oct(os.stat(self.luks_file("key")).st_mode & 0o777), "0o600")
        self.assertFalse(os.path.exists(self.luks_file("once")))
        self.assertFalse(os.path.exists(self.luks_file("next")))

    def test_a_master_gets_a_new_volume_key_before_the_role(self):
        rc, out, err, calls = self.luks_apply(self.luks_host(role="master"))
        self.assertEqual(rc, 0, err)
        new = calls[0][1].decode().split("\n")[1]
        self.assertIn("cryptsetup reencrypt", calls[1][0])
        self.assertNotIn("exec cryptsetup", calls[1][0])     # the EXIT trap removes the key; exec would skip it
        self.assertEqual(calls[1][1].decode(), "%s\n/dev/vda5\n" % new)
        self.assertIn("role.sh.tmp", calls[2][0])

    @unittest.skipUnless(have("shellcheck"), "shellcheck not installed")
    def test_luks_scripts_pass_shellcheck(self):
        for name, text in (("swap", luks.SWAP_SCRIPT), ("reencrypt", luks.REENCRYPT_SCRIPT)):
            path = os.path.join(self.tmp, name + ".sh")
            write(path, "#!/bin/sh\n" + text)
            r = subprocess.run(["shellcheck", "-S", "warning", path], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout)

    def test_reencrypt_setting_beats_the_default(self):
        _rc, _out, _err, calls = self.luks_apply(self.luks_host(role="master", reencrypt=False))
        self.assertFalse(any("cryptsetup reencrypt" in c[0] for c in calls))
        _rc, _out, _err, calls = self.luks_apply(self.luks_host(reencrypt=True))
        self.assertTrue(any("cryptsetup reencrypt" in c[0] for c in calls))

    def test_a_cut_off_run_sends_no_secrets_and_can_be_repeated(self):
        f = self.luks_host(role="master")
        rc, _out, err, calls = self.luks_apply(f, reencrypt_fails=True)
        self.assertEqual(rc, 1)
        self.assertFalse(self.sent_role(calls))
        self.assertTrue(os.path.exists(self.luks_file("once")))
        new = calls[0][1].decode().split("\n")[1]
        rc, _out, err, calls = self.luks_apply(f, state="already /dev/vda5\n")
        self.assertEqual(rc, 0, err)
        self.assertEqual(calls[0][1].decode().split("\n")[1], new)   # the same new key, not another
        self.assertTrue(self.sent_role(calls))
        self.assertFalse(os.path.exists(self.luks_file("once")))

    def test_no_disk_that_opens_stops_before_the_role(self):
        rc, _out, err, calls = self.luks_apply(self.luks_host(), state="none\n")
        self.assertEqual(rc, 1)
        self.assertIn("older ISO", err)
        self.assertFalse(self.sent_role(calls))
        self.assertTrue(os.path.exists(self.luks_file("once")))

    def test_a_key_made_for_a_later_install_is_left_alone(self):
        rc, _out, err, calls = self.luks_apply(self.luks_host(), state="current /dev/vda5\n")
        self.assertEqual(rc, 0, err)
        self.assertTrue(self.sent_role(calls))
        self.assertTrue(os.path.exists(self.luks_file("once")))
        self.assertFalse(os.path.exists(self.luks_file("next")))

    def test_nothing_to_swap(self):
        swapped = lambda calls: any("luksChangeKey" in c[0] for c in calls)   # noqa: E731
        _rc, _out, _err, calls = self.luks_apply(self.luks_host(), pending=False)   # replaced already
        self.assertFalse(swapped(calls))
        self.assertFalse(os.path.exists(self.luks_file("once")))     # and apply makes no new one
        _rc, _out, _err, calls = self.luks_apply(self.luks_host(), flags=("--no-run",))
        self.assertFalse(swapped(calls))
        _rc, _out, _err, calls = self.luks_apply(self.luks_host(passphrase="literal"))
        self.assertFalse(swapped(calls))

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
                return (b"applied: 2026-10-06T10:00:00Z\nuptime: up 3 days\nkiwi-stack: 2 containers running\n"
                        b"--- kiwi-facts ---\napplied=2026-10-06T10:00:00Z\nuptime=259200\nos=Debian\nclock=yes\n"
                        b"service=kn-nginx inactive\n")
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
            self.assertEqual((byname["gate"]["state"], byname["sh3"]["state"]), ("down", "bad"))
            self.assertEqual(byname["sh3"]["facts"]["uptime"], 259200)
            self.assertNotIn("--- kiwi-facts ---", byname["sh3"]["output"])
            rc, out, err = self.run_cli("status", f.path, "sh3")
            self.assertEqual(rc, 0, err)
            self.assertIn("== sh3 (core@sh3.kiwi)", out)
            self.assertIn("   uptime: up 3 days", out)
            self.assertIn("   ! kn-nginx is not running (inactive)", out)
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
@unittest.skipUnless(have("age") and have("age-keygen"), "age not installed")
class TestStore(Base):
    def setUp(self):
        super().setUp()
        self.run_dir, self.keyring, fakebin = [os.path.join(self.tmp, d) for d in ("run", "keyring", "bin")]
        for d in (self.run_dir, self.keyring, fakebin):
            os.makedirs(d)
        tool = os.path.join(fakebin, "secret-tool")
        write(tool, '#!/bin/sh\n[ -e "$FAKE_KEYRING/broken" ] && exit 1\ncase $1 in\n'
                    '  store) cat > "$FAKE_KEYRING/item" ;;\n'
                    '  lookup) [ -f "$FAKE_KEYRING/item" ] && cat "$FAKE_KEYRING/item" ;;\nesac\n')
        os.chmod(tool, 0o755)
        env = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.run_dir, "FAKE_KEYRING": self.keyring,
                                           "PATH": fakebin + os.pathsep + os.environ.get("PATH", "")})
        env.start()
        self.addCleanup(env.stop)
        timer = mock.patch.object(store, "schedule_lock", lambda *_a: False)
        timer.start()
        self.addCleanup(timer.stop)
        cancel = mock.patch.object(store, "cancel_lock", lambda *_a: None)
        cancel.start()
        self.addCleanup(cancel.stop)
        self.f = self.fleet({"defaults": self.base_defaults(), "hosts": {"a": {}}})
        self.secrets = os.path.join(self.tmp, "secrets")
        write(os.path.join(self.secrets, "seed"), "S" * 32)
        write(os.path.join(self.secrets, "deep", "key"), "private")
        os.chmod(os.path.join(self.secrets, "deep", "key"), 0o600)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = cli.main(list(args))
            except SystemExit as e:
                rc = e.code
        return rc, out.getvalue(), err.getvalue()

    def init(self):
        rc, out, err = self.run_cli("secrets", "init", self.f.path)
        self.assertEqual(rc, 0, err)
        return re.search(r"AGE-SECRET-KEY-1\S+", out).group(0)

    def read_secret(self, *path):
        with open(os.path.join(self.secrets, *path)) as fh:
            return fh.read()

    def test_init_moves_secrets_into_the_store(self):
        self.init()
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "secrets.age")))
        self.assertTrue(os.path.islink(self.secrets))                  # no plaintext left in the fleet directory
        self.assertTrue(os.path.realpath(self.secrets).startswith(os.path.realpath(self.run_dir)))
        self.assertEqual(self.read_secret("deep", "key"), "private")
        self.assertEqual(oct(os.stat(os.path.join(self.secrets, "deep", "key")).st_mode & 0o777), "0o600")
        with open(os.path.join(self.tmp, "secrets.age"), "rb") as fh:
            self.assertNotIn(b"private", fh.read())
        with open(os.path.join(self.tmp, "secrets.recipients")) as fh:
            self.assertEqual(len([ln for ln in fh if ln.startswith("age1")]), 2)

    def test_a_locked_store_stops_commands_and_makes_no_new_seed(self):
        self.init()
        rc, out, err = self.run_cli("secrets", "lock", self.f.path)
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.lexists(self.secrets))
        self.assertEqual(os.listdir(self.run_dir), [])                 # nothing left in memory
        rc, out, err = self.run_cli("validate", self.f.path)
        self.assertEqual(rc, 1)
        self.assertIn("locked", err)
        self.assertFalse(os.path.lexists(self.secrets))

    def test_unlock_from_the_keyring_brings_every_file_back(self):
        self.init()
        self.run_cli("secrets", "lock", self.f.path)
        rc, out, err = self.run_cli("secrets", "unlock", self.f.path)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.read_secret("seed"), "S" * 32)
        self.assertEqual(self.read_secret("deep", "key"), "private")
        self.assertEqual(oct(os.stat(os.path.join(self.secrets, "deep", "key")).st_mode & 0o777), "0o600")

    def test_the_recovery_key_opens_it_without_the_keyring(self):
        recovery = self.init()
        self.run_cli("secrets", "lock", self.f.path)
        os.remove(os.path.join(self.keyring, "item"))
        key = os.path.join(self.tmp, "recovery.txt")
        write(key, "# saved\n%s\n" % recovery)
        rc, out, err = self.run_cli("secrets", "unlock", self.f.path, "--identity", key)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.read_secret("seed"), "S" * 32)

    def test_a_change_is_saved_by_the_command_that_made_it(self):
        self.init()
        write(os.path.join(self.secrets, "luks", "a.key"), "only here")
        rc, out, err = self.run_cli("validate", self.f.path)
        self.assertEqual(rc, 0, err)
        shutil.rmtree(self.run_dir)                                    # a power cut: memory is gone
        os.makedirs(self.run_dir)
        rc, out, err = self.run_cli("secrets", "unlock", self.f.path)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.read_secret("luks", "a.key"), "only here")

    def test_without_a_keyring_it_says_so_and_asks_for_the_key(self):
        write(os.path.join(self.keyring, "broken"), "")
        self.init()
        self.run_cli("secrets", "lock", self.f.path)
        with mock.patch("sys.stdin", io.StringIO("")):
            rc, out, err = self.run_cli("secrets", "unlock", self.f.path)
        self.assertEqual(rc, 1)
        self.assertIn("--identity", err)

    def test_the_wrong_key_opens_nothing(self):
        self.init()
        self.run_cli("secrets", "lock", self.f.path)
        key = os.path.join(self.tmp, "other.txt")
        write(key, store.keygen()[0] + "\n")
        rc, out, err = self.run_cli("secrets", "unlock", self.f.path, "--identity", key)
        self.assertEqual(rc, 1)
        self.assertIn("does not open", err)
        self.assertFalse(os.path.lexists(self.secrets))

    def test_init_twice_is_refused_and_status_reports(self):
        rc, out, err = self.run_cli("secrets", "status", self.f.path)
        self.assertIn("no secret store", out)
        rc, out, err = self.run_cli("secrets", "status", self.f.path, "--porcelain")
        self.assertEqual(json.loads(out)["store"], False)
        self.init()
        rc, out, err = self.run_cli("secrets", "init", self.f.path)
        self.assertEqual(rc, 1)
        self.assertIn("already exists", err)
        rc, out, err = self.run_cli("secrets", "status", self.f.path)
        self.assertEqual(rc, 0, err)
        self.assertIn("open; opens with: desktop, recovery", out)
        rc, out, err = self.run_cli("secrets", "status", self.f.path, "--porcelain")
        self.assertEqual(json.loads(out), {"store": True, "open": True, "locks_at": None, "opens_with": ["desktop", "recovery"]})
        self.run_cli("secrets", "lock", self.f.path)
        rc, out, err = self.run_cli("secrets", "status", self.f.path)
        self.assertIn("locked", out)
        rc, out, err = self.run_cli("secrets", "status", self.f.path, "--porcelain")
        self.assertEqual(json.loads(out)["open"], False)

    def test_a_tar_that_escapes_is_refused(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo("../evil")
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
        with self.assertRaisesRegex(KiwiError, "not allowed"):
            store.extract(buf.getvalue(), os.path.join(self.tmp, "x"))


class TestMtu(Base):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = cli.main(list(args))
            except SystemExit as e:
                rc = e.code
        return rc, out.getvalue(), err.getvalue()

    def mtu_fleet(self):
        write(os.path.join(self.tmp, "secrets", "n.conf"),
              "[Interface]\nPrivateKey = k\nAddress = 10.8.0.7/24\n\n[Peer]\nPublicKey = p\nAllowedIPs = 0.0.0.0/0\nEndpoint = v:51820\n")
        write(os.path.join(self.tmp, "secrets", "devices", "phone.conf"), "[Interface]\nPrivateKey = x\nAddress = 10.8.3.5/24\n")
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {
            "gate": {"role": "master", "target": "debian", "master": self.master()},
            "n": {"role": "node", "target": "debian", "node": {"modules": ["vpn-client"], "vpn-client": {"wireguard_config": "secrets/n.conf"}}}}})
        return f

    def measure(self, f, *args, whole_up_to=1376, split_above=None, answers=True):
        calls = []

        def fake_run(tgt, script, data=None, runner=None):
            size = int(re.search(r"-s (\d+) ", script).group(1)) + 28
            calls.append((tgt, script, size))
            if not answers or size > whole_up_to:
                return b"received=0 reassembled=0\n"
            if split_above and size > split_above:
                return b"received=3 reassembled=3\n"
            return b"received=3 reassembled=0\n"
        with mock.patch.object(remote, "run", fake_run):
            rc, out, err = self.run_cli("mtu", f.path, *args)
        return rc, out, err, calls

    def test_a_device_that_fits_is_measured_from_the_master(self):
        rc, out, err, calls = self.measure(self.mtu_fleet(), "phone", "--ssh", "core@1.2.3.4")
        self.assertEqual(rc, 0, err)
        self.assertIn("1376 fits", out)
        tgt, script, size = calls[0]
        self.assertEqual((tgt, size), ("core@1.2.3.4", 84))
        self.assertIn("km-vpn-server", script)
        self.assertIn("10.8.3.5", script)

    def test_a_device_on_a_small_path_changes_only_itself(self):
        f = self.mtu_fleet()
        with open(f.path) as fh:
            before = fh.read()
        rc, out, err, _calls = self.measure(f, "phone", "--ssh", "c@h", "--write", whole_up_to=1320)
        self.assertEqual(rc, 0, err)
        self.assertIn("MTU = 1312", out)
        with open(f.path) as fh:
            self.assertEqual(fh.read(), before)

    def test_a_node_is_measured_from_its_own_side(self):
        rc, out, err, calls = self.measure(self.mtu_fleet(), "n", "--ssh", "core@5.6.7.8")
        self.assertEqual(rc, 0, err)
        tgt, script, _size = calls[0]
        self.assertEqual(tgt, "core@5.6.7.8")
        self.assertIn("kn-vpn-client", script)
        self.assertIn("10.8.0.1", script)                    # the master, which answers

    def test_a_reassembled_reply_does_not_count_as_fitting(self):
        rc, out, err, _calls = self.measure(self.mtu_fleet(), "phone", "--ssh", "c@h", split_above=1344)
        self.assertEqual(rc, 0, err)
        self.assertIn("MTU = 1344", out)
        self.assertIn("reassembled on the way", out)

    def test_every_size_tried_is_a_multiple_of_16(self):
        _rc, _out, _err, calls = self.measure(self.mtu_fleet(), "phone", "--ssh", "c@h", whole_up_to=1300)
        self.assertTrue(all(c[2] % 16 == 0 for c in calls[1:]), [c[2] for c in calls])

    def test_a_node_result_is_written_only_when_asked(self):
        f = self.mtu_fleet()
        with open(f.path) as fh:
            before = fh.read()
        rc, out, err, _calls = self.measure(f, "n", "--ssh", "c@h", whole_up_to=1360)
        self.assertEqual(rc, 0, err)
        self.assertIn("--write", out)
        with open(f.path) as fh:
            self.assertEqual(fh.read(), before)

    def test_write_sets_the_node_and_the_master_and_still_validates(self):
        f = self.mtu_fleet()
        rc, out, err, _calls = self.measure(f, "n", "--ssh", "c@h", "--write", whole_up_to=1360)
        self.assertEqual(rc, 0, err)
        with open(f.path) as fh:
            data = yaml.safe_load(fh)
        self.assertEqual(data["hosts"]["n"]["node"]["wg_mtu"], 1360)
        self.assertEqual(data["hosts"]["gate"]["master"]["wg_mtu"], 1360)
        rc, out, err = self.run_cli("validate", f.path)
        self.assertEqual(rc, 0, err)

    def test_set_wg_mtu_keeps_the_rest_of_the_file(self):
        text = ("hosts:\n  a:\n    node:\n      # why\n      wg_mtu: 1376   # measured\n      x: 1\n  b:\n    node:\n      y: 2\n")
        self.assertEqual(mtu.set_wg_mtu(text, "a", "node", 1344).split("\n")[4], "      wg_mtu: 1344   # measured")
        out = mtu.set_wg_mtu(text, "b", "node", 1344)
        self.assertEqual(yaml.safe_load(out)["hosts"]["b"]["node"], {"wg_mtu": 1344, "y": 2})
        self.assertEqual(yaml.safe_load(out)["hosts"]["a"]["node"]["wg_mtu"], 1376)
        self.assertIsNone(mtu.set_wg_mtu(text, "nosuch", "node", 1344))
        self.assertIsNone(mtu.set_wg_mtu("hosts:\n  a:\n    node: { x: 1 }\n", "a", "node", 1344))

    def test_nobody_answering_says_so(self):
        rc, _out, err, _calls = self.measure(self.mtu_fleet(), "phone", "--ssh", "c@h", answers=False)
        self.assertEqual(rc, 1)
        self.assertIn("no answer", err)

    def test_a_master_is_not_something_to_measure(self):
        rc, _out, err, _calls = self.measure(self.mtu_fleet(), "gate", "--ssh", "c@h")
        self.assertEqual(rc, 1)
        self.assertIn("not a node", err)

    def test_an_unknown_target(self):
        rc, _out, err, _calls = self.measure(self.mtu_fleet(), "nobody", "--ssh", "c@h")
        self.assertEqual(rc, 1)
        self.assertIn("neither a host", err)

    def test_nothing_fits_even_at_1280(self):
        rc, _out, err, _calls = self.measure(self.mtu_fleet(), "phone", "--ssh", "c@h", whole_up_to=1200)
        self.assertEqual(rc, 1)
        self.assertIn("even 1280", err)

    @unittest.skipUnless(have("shellcheck"), "shellcheck not installed")
    def test_the_measuring_script_passes_shellcheck(self):
        path = os.path.join(self.tmp, "probe.sh")
        write(path, "#!/bin/sh\n" + mtu._SCRIPT % {"container": "km-vpn-server", "addr": "10.8.0.1", "payload": 1000})
        r = subprocess.run(["shellcheck", "-S", "warning", path], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)


class FakeNetcup:
    """netcup's Keycloak and SCP API, as much as `kiwi-server vps` uses, plus the
    presigned storage URL. In-process, on a loopback port, no network."""

    def __init__(self, driver="SATA"):
        self.driver, self.calls, self.uploaded = driver, [], 0
        self.state, self.attached, self.polls_pending, self.polls_running = "RUNNING", None, 1, 0
        self.fail_task, self.refresh = False, "refresh-1"
        self.bootorder = ["CDROM", "HDD", "NETWORK"]
        fake = self
        AUTH, API = "/realms/scp/protocol/openid-connect", "/scp-core/api/v1"
        task = {"uuid": "t1", "state": "RUNNING"}

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _send(self, code, obj=None):
                payload = b"" if obj is None else json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _read(self):
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def _json(self):
                raw = self._read()
                return json.loads(raw) if raw else None

            def _server(self):
                return {"id": 7, "name": "v2202", "hostname": "crumble.internal", "nickname": "crumble", "architecture": "AMD64",
                        "rescueSystemActive": False, "ipv4Addresses": [{"ip": "203.0.113.9"}],
                        "serverLiveInfo": {"state": fake.state, "uefi": False, "cpuCount": 2, "maxServerMemoryInMiB": 2048,
                                           "bootorder": fake.bootorder}}

            def do_POST(self):
                path = self.path.split("?")[0]
                if path == AUTH + "/auth/device":
                    self._read()
                    return self._send(200, {"device_code": "dc", "verification_uri_complete": "https://login.example/x",
                                            "expires_in": 60, "interval": 1})
                if path == AUTH + "/token":
                    form = dict(urllib.parse.parse_qsl(self._read().decode()))
                    if form["grant_type"].endswith("device_code") and fake.polls_pending > 0:
                        fake.polls_pending -= 1
                        return self._send(400, {"error": "authorization_pending"})
                    if form["grant_type"] == "refresh_token" and form["refresh_token"] != fake.refresh:
                        return self._send(400, {"error": "invalid_grant"})
                    n = int(fake.refresh.split("-")[1]) + (form["grant_type"] == "refresh_token")
                    fake.refresh = "refresh-%d" % n
                    return self._send(200, {"access_token": "a%d" % n, "expires_in": 300, "refresh_token": fake.refresh})
                body = self._json()
                fake.calls.append(("POST", path, body))
                if "/" not in (self.headers.get("Content-Type") or ""):     # as the real one: even with no body
                    return self._send(400, {"message": "The HTTP header Content-Type must be 'type/subtype'"})
                if path == API + "/users/94774/isos/kiwi-x.iso":
                    return self._send(201, {"presignedUrl": fake.url + "/s3/kiwi-x.iso"})
                if path == API + "/servers/7/iso":
                    fake.attached = body["userIsoName"]
                    return self._send(202, task)
                self._send(404, {"message": "no such path"})

            def do_PUT(self):
                fake.uploaded = len(self._read())
                fake.calls.append(("PUT", self.path, None))
                self._send(200)

            def do_PATCH(self):
                body = self._json()
                fake.calls.append(("PATCH", self.path, body))
                if body.get("state"):
                    fake.state = "SHUTOFF" if body["state"] == "OFF" else "RUNNING"
                    fake.polls_running = 1
                if body.get("bootorder"):
                    fake.bootorder = body["bootorder"]
                self._send(202, task)

            def do_DELETE(self):
                fake.calls.append(("DELETE", self.path, None))
                if self.path.endswith("/servers/7/iso"):
                    fake.attached = None
                self._send(204)

            def do_GET(self):
                path = self.path.split("?")[0]
                if not (self.headers.get("Authorization") or "").startswith("Bearer a"):
                    return self._send(401, {"message": "no token"})
                if path == AUTH + "/userinfo":
                    return self._send(200, {"id": 94774})
                if path == API + "/servers":
                    return self._send(200, [self._server()])
                if path == API + "/servers/7":
                    s = self._server()
                    if fake.attached and fake.state == "RUNNING":       # the installer runs, then powers off
                        fake.polls_running -= 1
                        if fake.polls_running < 0:
                            fake.state = "SHUTOFF"
                    return self._send(200, s)
                if path == API + "/servers/7/disks":
                    return self._send(200, [{"name": "sda", "capacityInMiB": 40960, "storageDriver": fake.driver}])
                if path == API + "/servers/7/iso":
                    return self._send(200, {"isoAttached": bool(fake.attached), "iso": fake.attached})
                if path == API + "/tasks/t1":
                    if fake.fail_task:
                        return self._send(200, {"uuid": "t1", "state": "ERROR", "responseError": {"message": "boom"}})
                    return self._send(200, {"uuid": "t1", "state": "FINISHED"})
                self._send(404, {"message": "no such path"})

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()

    def seen(self):
        return [(m, p.split("/api/v1")[-1].split("?")[0]) for m, p, _b in self.calls]


class TestVps(Base):
    def setUp(self):
        super().setUp()
        self.api = FakeNetcup()
        self.addCleanup(self.api.close)
        patcher = mock.patch("kiwiserver.netcup.time.sleep", lambda _s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = cli.main(list(args) + ["--api", self.api.url])
            except SystemExit as e:
                rc = e.code
        return rc, out.getvalue(), err.getvalue()

    def vps_fleet(self, disk="/dev/sda"):
        return self.fleet({"defaults": self.base_defaults(), "hosts": {
            "x": {"role": "bare", "target": "debian", "disk": disk, "vps": {"server": 7}}}})

    def logged_in(self):
        write(os.path.join(self.tmp, "secrets", "netcup.token"), json.dumps({"refresh_token": "refresh-1"}))

    def built(self, stack=0):
        write(os.path.join(self.tmp, "output", "x", "x.iso"), "ISO" * 100)
        write(os.path.join(self.tmp, "output", "x", "x.role.sh"), "KS_STACK=%d\n" % stack)

    def test_login_keeps_only_the_refresh_token_private(self):
        f = self.vps_fleet()
        rc, out, err = self.run_cli("vps", "login", f.path)
        self.assertEqual(rc, 0, err)
        self.assertIn("https://login.example/x", out)
        path = os.path.join(self.tmp, "secrets", "netcup.token")
        with open(path) as fh:
            self.assertEqual(json.load(fh), {"refresh_token": "refresh-1"})
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")

    def test_a_used_refresh_token_is_replaced(self):
        f = self.vps_fleet()
        self.logged_in()
        rc, out, err = self.run_cli("vps", "list", f.path)
        self.assertEqual(rc, 0, err)
        self.assertIn("crumble", out)
        with open(os.path.join(self.tmp, "secrets", "netcup.token")) as fh:
            self.assertEqual(json.load(fh)["refresh_token"], "refresh-2")

    def test_not_logged_in_says_how(self):
        rc, out, err = self.run_cli("vps", "list", self.vps_fleet().path)
        self.assertEqual(rc, 1)
        self.assertIn("vps login", err)

    def test_probe_says_the_real_disk(self):
        f = self.vps_fleet()
        self.logged_in()
        rc, out, err = self.run_cli("vps", "probe", f.path, "x")
        self.assertEqual(rc, 0, err)
        self.assertIn("disk: /dev/sda", out)
        self.api.driver = "VIRTIO"
        rc, out, err = self.run_cli("vps", "probe", f.path, "7", "--porcelain")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["disks"][0]["device"], "/dev/vda")

    def test_a_server_found_by_name(self):
        f = self.vps_fleet()
        self.logged_in()
        rc, out, err = self.run_cli("vps", "probe", f.path, "crumble")
        self.assertEqual(rc, 0, err)
        rc, out, err = self.run_cli("vps", "probe", f.path, "nosuch")
        self.assertEqual(rc, 1)
        self.assertIn("names no netcup server", err)

    def test_install_runs_the_whole_sequence(self):
        f = self.vps_fleet()
        self.logged_in()
        self.built()
        rc, out, err = self.run_cli("vps", "install", f.path, "x", "--yes")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.api.seen(), [
            ("POST", "/users/94774/isos/kiwi-x.iso"), ("PUT", "/s3/kiwi-x.iso"),
            ("PATCH", "/servers/7"),            # off
            ("POST", "/servers/7/iso"),
            ("PATCH", "/servers/7"),            # on, into the installer
            ("DELETE", "/servers/7/iso"),
            ("PATCH", "/servers/7"),            # the boot order
            ("DELETE", "/users/94774/isos/kiwi-x.iso"),
            ("PATCH", "/servers/7")])           # on, from the disk
        self.assertEqual(self.api.uploaded, 300)
        states = [b for m, _p, b in self.api.calls if m == "PATCH"]
        self.assertEqual([s.get("state") for s in states], ["OFF", "ON", None, "ON"])
        self.assertEqual(states[2]["bootorder"][0], "HDD")
        self.assertIn("stateOption=POWEROFF", self.api.calls[2][1])
        self.assertEqual(self.api.calls[3][2], {"userIsoName": "kiwi-x.iso", "changeBootDeviceToCdrom": True})
        self.assertIn("next: kiwi-server apply", out)

    def assert_refused(self, needle, *flags, fleet=None):
        f = fleet or self.vps_fleet()
        rc, out, err = self.run_cli("vps", "install", f.path, "x", *flags)
        self.assertEqual(rc, 1, err)
        self.assertIn(needle, err)
        self.assertNotIn("PUT", [m for m, _p, _b in self.api.calls])     # nothing uploaded, nothing wiped
        self.assertEqual(self.api.state, "RUNNING")

    def test_install_refuses_the_wrong_disk(self):
        self.logged_in()
        self.built()
        self.assert_refused("set disk: /dev/sda", "--yes", fleet=self.vps_fleet(disk="/dev/vda"))

    def test_install_refuses_media_with_secrets(self):
        self.logged_in()
        self.built(stack=1)
        self.assert_refused("carries the role's secrets", "--yes")

    def test_install_refuses_without_an_iso(self):
        self.logged_in()
        self.assert_refused("no ISO for x", "--yes")

    def test_install_needs_yes_when_nobody_can_answer(self):
        self.logged_in()
        self.built()
        with mock.patch("sys.stdin", io.StringIO("")):
            self.assert_refused("add --yes")

    def test_a_failed_task_stops_the_install(self):
        f = self.vps_fleet()
        self.logged_in()
        self.built()
        self.api.fail_task = True
        rc, out, err = self.run_cli("vps", "install", f.path, "x", "--yes")
        self.assertEqual(rc, 1)
        self.assertIn("boom", err)
        self.assertNotIn(("DELETE", "/users/94774/isos/kiwi-x.iso"), self.api.seen())


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


class TestFacts(Base):
    SAMPLE = """now=1000
applied=2026-10-06T10:00:00Z
os=Fedora CoreOS 44
kernel=7.2
uptime=259200
load=0.50 0.40 0.30
cpus=2
mem=2000000 1000000
disk=/var 1000 100
disk=/var 1000 100
disk=/var/mnt/data 1000 960
clock=yes
failed=012c89533cccbc4fbd60eadec0d1bb3f13f79eb7ab9b218f8f8a4c02bbdefb94-7ee2adc215a743d0.service kiwi-backup.service
runtime=podman
container=kn-nginx|running|Up 3 days|docker.io/nginx:1
container=kn-cloud|running|Up 3 days (starting)|example/cloud
container=by-hand|exited|Exited (0) 2 days ago|example/x
service=kn-nginx active
service=kn-cloud active
service=kn-gone inactive
vpn=kn-nginx
tunnel=
forward=0
peers=4 3
"""

    def test_the_machine_is_read_into_data(self):
        f = facts.parse(self.SAMPLE, local_now=990)
        self.assertEqual((f["running"], f["expected"], f["skew"]), (2, 3, 10))
        self.assertEqual([d["mount"] for d in f["disks"]], ["/var", "/var/mnt/data"])
        self.assertEqual(f["memory"], {"total": 2000000, "available": 1000000})
        self.assertEqual(f["peers"], {"total": 4, "recent": 3})
        by = {s["name"]: s for s in f["services"]}
        self.assertEqual(by["kn-cloud"]["health"], "starting")
        self.assertFalse(by["by-hand"]["stack"])
        self.assertEqual(by["kn-gone"]["image"], "")

    def test_a_healthcheck_that_is_starting_is_not_a_failed_unit(self):
        self.assertEqual(facts.parse(self.SAMPLE)["failed_units"], ["kiwi-backup.service"])

    def test_what_is_wrong_is_said(self):
        f = facts.parse(self.SAMPLE, local_now=1000)
        state, problems, notes = facts.assess(f)
        self.assertEqual(state, "bad")
        texts = [p["text"] for p in problems]
        self.assertIn("kn-gone is not running (inactive)", texts)
        self.assertIn("the VPN client kn-nginx has no tunnel", texts)
        self.assertTrue(any("does not forward" in t for t in texts))
        self.assertIn("failed units: kiwi-backup.service", texts)
        self.assertIn("/var/mnt/data is 96% full", texts)
        self.assertEqual(notes, ["kn-cloud is still starting"])

    def test_a_healthy_machine_has_nothing_to_say(self):
        f = facts.parse("applied=x\nos=Debian\nclock=yes\nuptime=5\nservice=a active\ncontainer=a|running|Up 1 hour|i\n"
                        "disk=/ 1000 10\nmem=1000 900\n")
        self.assertEqual(facts.assess(f), ("ok", [], []))

    def test_a_machine_that_just_booted_is_only_late(self):
        f = facts.parse("applied=x\nos=D\nclock=yes\nuptime=40\nservice=a inactive\nservice=b failed\n")
        state, problems, notes = facts.assess(f)
        self.assertEqual([p["text"] for p in problems], ["b is not running (failed)"])
        self.assertEqual(notes, ["a has not started yet: the machine just booted"])

    def test_a_stopped_vpn_client_is_reported_once(self):
        f = facts.parse("applied=x\nos=D\nclock=yes\nservice=v inactive\nvpn=v\ntunnel=\nforward=\n")
        _state, problems, _notes = facts.assess(f)
        self.assertEqual([p["text"] for p in problems], ["v is not running (inactive)"])

    def test_the_clock_is_compared_with_this_computer(self):
        f = facts.parse("applied=x\nos=D\nclock=yes\nnow=1100\n", local_now=1000)
        self.assertEqual(facts.assess(f)[0], "warn")

    def test_status_text_and_probe_come_apart(self):
        human, data = facts.split("applied: x\n%s\nuptime=1\n" % facts.MARK)
        self.assertEqual((human, data), ("applied: x\n", "uptime=1\n"))
        self.assertEqual(facts.split("no probe\n"), ("no probe\n", ""))

    def test_the_probe_is_plain_sh_and_never_dumps_wireguard(self):
        path = os.path.join(self.tmp, "probe.sh")
        write(path, "#!/bin/sh\n" + facts.SCRIPT)
        self.assertEqual(subprocess.run(["sh", "-n", path]).returncode, 0)
        if have("shellcheck"):
            r = subprocess.run(["shellcheck", "-S", "warning", "-s", "sh", path], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("dump", facts.SCRIPT)
        self.assertIn("latest-handshakes", facts.SCRIPT)


class TestLanAddresses(Base):
    @staticmethod
    def host(**network):
        return types.SimpleNamespace(cfg={"network": network})

    def view(self, host, node_type="node"):
        r = cli.Renderer.__new__(cli.Renderer)
        r._scanned = True
        r._node_lan = [("10.9.0.2", ipaddress.ip_address("192.168.2.30")), ("10.9.0.3", ipaddress.ip_address("192.168.2.61")),
                       ("10.9.0.4", ipaddress.ip_address("10.20.0.5"))]
        return r.lan_addresses(host, types.SimpleNamespace(node_type=node_type))

    def test_only_nodes_on_this_lan_get_their_lan_address(self):
        self.assertEqual(self.view(self.host(dhcp=False, address="192.168.2.30/16")),
                         {"10.9.0.2": "192.168.2.30", "10.9.0.3": "192.168.2.61"})

    def test_a_master_answers_mesh_clients_and_keeps_the_mesh_addresses(self):
        self.assertEqual(self.view(self.host(dhcp=False, address="192.168.2.30/16"), "master"), {})

    def test_a_machine_with_no_known_lan_has_no_lan_view(self):
        self.assertEqual(self.view(self.host(dhcp=True)), {})
        self.assertEqual(self.view(self.host(dhcp=False, address=None)), {})

    def test_a_bare_address_has_no_neighbours(self):
        self.assertEqual(self.view(self.host(dhcp=False, address="192.168.2.30")), {"10.9.0.2": "192.168.2.30"})


class TestReach(Base):
    def host(self, **network):
        y = {"defaults": {"domain": "kiwi", "admin": {"user": "core"}},
             "hosts": {"a": {"network": dict({"dhcp": False, "address": "192.168.9.9/24"}, **network)}}}
        path = os.path.join(self.tmp, "fleet.yaml")
        write(path, yaml.safe_dump(y))
        return config.Fleet.load(path).hosts["a"]

    def on_record(self, *addresses):
        path = os.path.join(self.tmp, "known_hosts")
        key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJtmZ2FrZWZha2VmYWtlZmFrZWZha2VmYWtlZmFrZWZha2Vm"
        write(path, "".join("%s %s\n" % (a, key) for a in addresses))
        remote.configure(path)
        self.addCleanup(remote.configure, "")

    @unittest.skipUnless(have("ssh-keygen"), "ssh-keygen")
    def test_an_address_on_record_that_answers_is_used(self):
        self.on_record("192.168.9.9")
        with mock.patch.object(remote, "_answers", lambda a: a == "192.168.9.9"):
            self.assertEqual(remote.reach(self.host()), "core@192.168.9.9")

    @unittest.skipUnless(have("ssh-keygen"), "ssh-keygen")
    def test_an_address_never_met_gets_nothing_sent_to_it(self):
        self.on_record("10.0.0.1")
        with mock.patch.object(remote, "_answers", lambda a: True):
            self.assertEqual(remote.reach(self.host()), "core@a.kiwi")

    def test_an_address_given_wins(self):
        self.assertEqual(remote.reach(self.host(), "root@1.2.3.4"), "root@1.2.3.4")


class TestCli(Base):
    def test_a_reader_that_left_is_not_an_error(self):
        """`kiwi-server script … | head -1` under pipefail: the output still
        buffered at exit meets a closed pipe, and the interpreter's own flush
        used to turn that into exit 120 (CI buffers; a terminal session or
        PYTHONUNBUFFERED hides it)."""
        code = ("import sys, time\n"
                "sys.path.insert(0, %r)\n"
                "from kiwiserver import cli\n"
                "orig = cli.cmd_targets\n"
                "def slow(args):\n"
                "    orig(args)\n"
                "    time.sleep(0.5)\n"
                "cli.cmd_targets = slow\n"
                "sys.exit(cli.main(['targets']))\n") % os.path.join(ROOT, "lib")
        env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
        env["KIWI_SERVER_HOME"] = ROOT
        reader = subprocess.Popen(["true"], stdin=subprocess.PIPE)
        writer = subprocess.Popen([sys.executable, "-c", code], stdout=reader.stdin, stderr=subprocess.PIPE, env=env)
        reader.stdin.close()
        reader.wait()
        _out, err = writer.communicate(timeout=30)
        self.assertEqual(writer.returncode, 0, err.decode())
        self.assertNotIn(b"BrokenPipeError", err)

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
        self.assertEqual(len(json.loads(out)["modules"]), 20)
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
        self.assertEqual(hosts["u"]["sites"], [])
        self.assertIn("domain", json.loads(out))
        rc, out, _ = self.run_cli("show", f.path, "n", "--porcelain")
        data = json.loads(out)
        self.assertEqual(data["config"]["admin"]["password"], "********")
        self.assertEqual(data["role_settings"]["vpn_ip"], "10.8.0.25")
        self.assertEqual(data["modules"], ["vpn-client", "reverse-proxy", "cloud", "vault", "portainer"])
        self.assertNotIn("node-cloud", data["config"])
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "secrets", "ca", "kiwiCA.pem")))
        rc, out, _ = self.run_cli("script", f.path, "n", "--offline")
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
        self.assertIn("FTLCONF_misc_dnsmasq_lines=strict-order;no-hosts;server=/kiwi/10.8.0.1", b.compose["services"]["kn-pihole"]["environment"])
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
        rc, out, err = self.run_cli("script", f.path, "n", "--offline")   # a fresh fleet: the CA is created on the way
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

    def test_misspelled_keys_are_errors(self):
        f = self.fleet({"defaults": self.base_defaults(netwrok={"dhcp": False}),
                        "hosts": {"x": {"rol": "master", "tsl": {"auto": False}, "admin": {"shh_keys": []}}}})
        rc, out, err = self.run_cli("validate", f.path)
        self.assertEqual(rc, cli.EXIT_INVALID)
        self.assertIn("defaults: unknown key 'netwrok' (did you mean network?)", err)
        self.assertIn("hosts.x: unknown key 'rol' (did you mean role?)", err)
        self.assertIn("hosts.x: unknown key 'tsl' (did you mean tls?)", err)
        self.assertIn("hosts.x.admin: unknown key 'shh_keys' (did you mean ssh_keys?)", err)
        # a role block and the known blocks pass
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {"gate": {"role": "master", "master": self.master()}}})
        rc, out, err = self.run_cli("validate", f.path)
        self.assertEqual(rc, 0, err)

    def test_every_command_has_its_own_help(self):
        rc, out, err = self.run_cli("enroll", "-h")
        self.assertEqual(rc, 0, err)
        self.assertIn("kiwi-server enroll <fleet> <host|name>", out)
        self.assertIn("--group", out)
        self.assertIn("--split", out)
        self.assertEqual(out.count("kiwi-server init [fleet.yaml]"), 0)   # not the whole usage, twice

    def test_generated_secrets_do_not_depend_on_the_role_name(self):
        a = self.fleet({"defaults": self.base_defaults(), "hosts": {"sh3": {"role": "node", "node": self.node_cloud(preset="cloud")}}})
        self.prepare(a.hosts["sh3"])
        pw_a = a.hosts["sh3"].module_settings["cloud"]["database_password"]
        b = self.fleet({"defaults": self.base_defaults(), "hosts": {"sh3": {"role": "node-cloud", "node-cloud": self.node_cloud()}}})
        self.prepare(b.hosts["sh3"])
        self.assertEqual(pw_a, b.hosts["sh3"].module_settings["cloud"]["database_password"])
        c = self.fleet({"defaults": self.base_defaults(), "hosts": {"other": {"hostname": "sh3.kiwi", "role": "node", "node": self.node_cloud(preset="cloud")}}})
        self.prepare(c.hosts["other"])
        self.assertEqual(pw_a, c.hosts["other"].module_settings["cloud"]["database_password"])   # the host key does not matter
        self.assertEqual(len(c.seed_id()), 12)
        self.assertEqual(a.seed_id(), c.seed_id())           # one secrets/seed in the fleet directory
        rc, out, err = self.run_cli("script", c.path, "other", "--offline")   # the fleet file on disk is c's by now
        self.assertEqual(rc, 0, err)
        self.assertIn("KS_SEED_ID='%s'" % c.seed_id(), out)

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

    def unwritten_fleet(self):
        m = self.master(**{"vpn-server": {"wg_host": "vpn.example.org"}, "dns": {}})   # no passwords written down
        return self.fleet({"defaults": self.base_defaults(), "hosts": {"gate": {"role": "master", "target": "debian", "master": m}}})

    def test_passwords_left_out_are_generated_and_can_be_read(self):
        f = self.unwritten_fleet()
        rc, out, err = self.run_cli("secrets", "show", f.path, "gate")
        self.assertEqual(rc, 0, err)
        names = out.split()
        self.assertIn("vpn-server.wg_password", names)
        self.assertIn("dns.pihole_password", names)
        rc, out, err = self.run_cli("secrets", "show", f.path, "gate", "vpn-server.wg_password")
        self.assertEqual(rc, 0, err)
        value = out.strip()
        self.assertRegex(value, r"^[A-Za-z0-9]{32}$")
        rc, out, err = self.run_cli("secrets", "show", f.path, "gate", "wg_password")   # the same, the same every time
        self.assertEqual((rc, out.strip()), (0, value))

    def test_secrets_show_gives_a_written_password_and_refuses_an_unknown_name(self):
        f = self.media_fleet()
        rc, out, err = self.run_cli("secrets", "show", f.path, "gate", "wg_password")
        self.assertEqual((rc, out.strip()), (0, "w"), err)
        rc, out, err = self.run_cli("secrets", "show", f.path, "gate", "nope")
        self.assertEqual(rc, 1)
        self.assertIn("no secret of this host", err)

    def test_validate_warns_about_passwords_that_need_not_be_written(self):
        rc, out, err = self.run_cli("validate", self.media_fleet().path)
        self.assertEqual(rc, 0, err)
        self.assertIn("vpn-server.wg_password, dns.pihole_password: literal", err)
        rc, out, err = self.run_cli("validate", self.unwritten_fleet().path)
        self.assertEqual(rc, 0, err)
        self.assertNotIn("literal in the fleet file, and generated", err)

    def media_fleet(self):
        return self.fleet({"defaults": self.base_defaults(), "hosts": {
            "gate": {"role": "master", "target": "debian", "master": self.master()}}})

    def test_script_is_bare_unless_offline(self):
        f = self.media_fleet()
        rc, out, err = self.run_cli("script", f.path, "gate")
        self.assertEqual(rc, 0, err)
        self.assertTrue(out.startswith("#!/usr/bin/env bash\n"), out[:80])   # the note went to stderr
        self.assertIn("KS_STACK=0", out)
        self.assertNotIn("KS_FILES[stack/docker-compose.yml]=", out)
        self.assertIn("bare media", err)
        rc, out, err = self.run_cli("script", f.path, "gate", "--offline")
        self.assertEqual(rc, 0, err)
        self.assertIn("KS_STACK=1", out)
        self.assertIn("KS_FILES[stack/docker-compose.yml]=", out)
        self.assertIn("in the clear", err)

    def build_roles(self, *flags):
        """What `build` hands the builder, with the ISO tools taken out of the way."""
        f = self.media_fleet()
        iso = os.path.join(self.tmp, "fake.iso")
        write(iso, "iso")
        seen = []

        def fake_build(_renderer, host, _cache):
            seen.append(host.role)
            return mock.Mock(iso=iso)
        with mock.patch.object(cli.Renderer, "build", fake_build):
            rc, out, err = self.run_cli("build", f.path, "--no-backup", "-o", os.path.join(self.tmp, "o"),
                                        "--toolchain", "native", *flags)
        return rc, seen, err

    def test_build_is_bare_unless_offline(self):
        rc, seen, err = self.build_roles()
        self.assertEqual((rc, seen), (0, ["bare"]), err)
        self.assertNotIn("in the clear", err)
        rc, seen, err = self.build_roles("--offline")
        self.assertEqual((rc, seen), (0, ["master"]), err)
        self.assertIn("in the clear", err)

    def test_bare_and_offline_exclude_each_other(self):
        rc, seen, err = self.build_roles("--bare", "--offline")
        self.assertEqual((rc, seen), (1, []))
        self.assertIn("exclude each other", err)

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
