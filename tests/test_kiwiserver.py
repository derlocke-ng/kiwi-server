"""Unit tests for kiwiserver. Run: python3 -m unittest discover -s tests -v

Anything that needs a tool (shellcheck, butane, xorriso, cpio) uses it when it
is on $PATH and is skipped otherwise, so the suite runs anywhere; CI installs
all of them. No test touches the network.
"""
import contextlib
import http.server
import io
import ipaddress
import argparse
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import types
import unittest
import urllib.parse
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))
os.environ["KIWI_SERVER_HOME"] = ROOT

import yaml  # noqa: E402

from kiwiserver import VERSION, backup, certs, cli, config, federation as fedmod, iso, modules as modmod, remote, roles as rolesmod, router, wgeasy  # noqa: E402
from kiwiserver import address as addressmod, adopt as adoptmod, control as controlmod, controlsync, facts, form as formmod, luks, mtu, plan as planmod, setup as setupmod, sshca, sshport, store, unlock  # noqa: E402
from kiwiserver.targets import armbian as armbian_t, coreos as coreos_t, debian as debian_t  # noqa: E402
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

    def test_disk_auto_lets_the_installer_find_the_one_disk(self):
        _h, ps, _files = self.make(disk="auto")
        self.assertNotIn("d-i partman-auto/disk string", ps)
        self.assertIn("d-i partman/early_command string D=$(for d in $(list-devices disk)", ps)
        self.assertIn('debconf-set partman-auto/disk "$D"', ps)       # only when there is exactly one
        self.assertIn("d-i grub-installer/bootdev string default", ps)

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


class TestAdopt(Base):
    def host(self, **over):
        f = self.fleet({"defaults": self.base_defaults(target="debian", admin={"user": "kiwi", "password": "pw",
                                                                            "ssh_keys": [KEY], "groups": ["wheel"]}),
                        "hosts": {"fig": dict({"hostname": "fig.home"}, **over)}})
        return f.hosts["fig"]

    def test_script_and_bundle(self):
        h = self.host()
        s = adoptmod.script(h)
        self.assertIn("U=kiwi\n", s)
        self.assertIn("hostnamectl set-hostname fig", s)
        self.assertIn("usermod -p ", s)                     # the fleet's console password, as on a fresh install
        self.assertIn("unattended-upgrades", s)
        self.assertIn("for g in wheel;", s)
        import tarfile, io as _io
        names = tarfile.open(fileobj=_io.BytesIO(adoptmod.bundle(h)), mode="r:gz").getnames()
        self.assertIn("adopt.sh", names)
        self.assertIn("authorized_keys", names)
        self.assertNotIn("role.sh", names)                  # apply sends the role, not adopt

    def test_admin_target_and_other_targets(self):
        h = self.host()
        self.assertEqual(adoptmod.admin_target(h, "odroid@192.168.2.40"), "kiwi@192.168.2.40")
        self.assertEqual(adoptmod.admin_target(h, "ssh://root@127.0.0.1:2260"), "ssh://kiwi@127.0.0.1:2260")
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {"u": {}}})
        with self.assertRaisesRegex(KiwiError, "target: debian"):
            adoptmod.run(f.hosts["u"], "root@x", runner=None)

    def test_new_host_key_forgets_the_address(self):
        calls = []
        with mock.patch.object(remote, "KNOWN_HOSTS", "/k"):
            adoptmod.forget_host_key("ssh://board@127.0.0.1:2260", lambda argv, **kw: calls.append(argv))
            adoptmod.forget_host_key("odroid@192.168.2.40", lambda argv, **kw: calls.append(argv))
        self.assertEqual(calls, [["ssh-keygen", "-R", "[127.0.0.1]:2260", "-f", "/k"],
                                 ["ssh-keygen", "-R", "192.168.2.40", "-f", "/k"]])

    def test_arm64_preseed_and_bad_arch(self):
        h = self.host(debian={"arch": "arm64"})
        self.assertIn("linux-image-arm64", debian_t.preseed(h, VERSION))
        h = self.host(debian={"arch": "armhf"})
        self.assertIn("fig: debian.arch must be amd64 or arm64", h.validate(self.roles))


class TestArmbian(Base):
    def host(self, **over):
        f = self.fleet({"defaults": self.base_defaults(target="armbian", role="bare",
                                                       admin={"user": "kiwi", "password": "pw", "ssh_keys": [KEY]}),
                        "hosts": {"n2": dict({"hostname": "n2.home", "armbian": {"board": "odroidn2"}}, **over)}})
        return f.hosts["n2"]

    def test_checks(self):
        self.assertEqual(self.host().validate(self.roles), [])
        self.assertIn("n2: armbian.board is missing (its name at dl.armbian.com, e.g. odroidn2 or odroidm1)",
                      self.host(armbian={"board": None}).validate(self.roles))
        self.assertTrue(any("no encrypted disk" in e for e in self.host(unlock={"rings": ["infra"]}).validate(self.roles)))
        self.assertEqual(armbian_t.download_url(self.host().cfg["armbian"]),
                         "https://dl.armbian.com/odroidn2/Trixie_current_minimal")

    def test_first_boot_files(self):
        fs = armbian_t.files(self.host(), "#!/bin/bash\necho role\n")
        self.assertIn("passwd -l root", fs["/var/lib/kiwi-server/adopt/adopt.sh"][0])   # Armbian's root is 1234
        self.assertIn("After=network-online.target kiwi-adopt.service", fs["/etc/systemd/system/kiwi-role.service"][0])
        self.assertIn("After=network-online.target armbian-firstrun.service", fs["/etc/systemd/system/kiwi-adopt.service"][0])
        self.assertEqual(fs["/var/lib/kiwi-server/role.sh"][1], 0o700)
        cmds = armbian_t.debugfs_commands({"/var/lib/kiwi-server/role.sh": ("/tmp/x", 0o700)})
        self.assertIn("sif /var/lib/kiwi-server/role.sh mode 0100700", cmds)
        self.assertIn("symlink /etc/systemd/system/multi-user.target.wants/kiwi-role.service", cmds)
        self.assertIn("rm /root/.not_logged_in_yet", cmds)
        self.assertNotIn("mkdir /var\n", armbian_t.debugfs_commands({"/var/lib/kiwi-server/role.sh": ("/tmp/x", 0o700)},
                                                                     {"/var", "/var/lib"}))   # existing: never again
        self.assertIn("mkdir /var/lib/kiwi-server\n", cmds)
        self.assertIn("rm /etc/systemd/system/serial-getty@.service.d/override.conf", cmds)   # no root on the console
        self.assertEqual(armbian_t.root_partition({"partitions": [
            {"start": 8192, "size": 500, "type": "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"},
            {"start": 9000, "size": 9000, "type": "B921B045-1DF0-41C3-AF44-4C6F280D3FAE"}]}), (9000, 9000))

    @unittest.skipUnless(all(shutil.which(t) for t in ("debugfs", "mkfs.ext4", "sfdisk", "xz", "e2fsck")),
                         "needs e2fsprogs, sfdisk and xz")
    def test_image_is_written_into_the_root_filesystem(self):
        w = os.path.join(self.tmp, "img")
        os.makedirs(os.path.join(w, "tree", "root"))
        write(os.path.join(w, "tree", "root", ".not_logged_in_yet"), "")
        os.makedirs(os.path.join(w, "tree", "etc", "systemd", "system", "multi-user.target.wants"))
        raw, fs = os.path.join(w, "board.img"), os.path.join(w, "root.ext4")
        subprocess.run(["truncate", "-s", "40M", raw], check=True)
        subprocess.run(["sfdisk", "-q", raw], input="label: dos\nstart=2048, type=83\n", text=True, check=True)
        subprocess.run(["mkfs.ext4", "-q", "-F", "-d", os.path.join(w, "tree"), fs, "38M"], check=True)
        subprocess.run(["dd", "if=" + fs, "of=" + raw, "bs=512", "seek=2048", "conv=notrunc", "status=none"], check=True)
        subprocess.run(["xz", "-0", raw], check=True)
        out = os.path.join(w, "out", "n2.img")
        os.makedirs(os.path.dirname(out))
        armbian_t.image(Toolchain("native"), self.host(), "#!/bin/bash\necho role\n", raw + ".xz", out, os.path.join(w, "work"))
        self.assertEqual(oct(os.stat(out).st_mode & 0o777), "0o600")
        self.assertFalse(os.path.exists(os.path.join(w, "work")))      # the copy of the role is gone
        back = os.path.join(w, "back.ext4")
        subprocess.run(["dd", "if=" + out, "of=" + back, "bs=512", "skip=2048", "status=none"], check=True)

        def dbg(cmd):
            return subprocess.run(["debugfs", "-R", cmd, back], capture_output=True, text=True).stdout
        self.assertEqual(dbg("cat /var/lib/kiwi-server/role.sh"), "#!/bin/bash\necho role\n")
        self.assertIn("passwd -l root", dbg("cat /var/lib/kiwi-server/adopt/adopt.sh"))
        self.assertIn("kiwi-adopt.service", dbg("ls /etc/systemd/system/multi-user.target.wants"))
        self.assertNotIn(".not_logged_in_yet", dbg("ls -l /root"))
        stat = dbg("stat /var/lib/kiwi-server/role.sh")
        self.assertIn("Mode:  0700", stat)
        self.assertRegex(stat, r"User:\s+0\s+Group:\s+0")


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
        self.assertEqual(iso.resolve_netinst("abc  debian-13.1.0-arm64-netinst.iso\n" + text, "arm64"),
                         ("debian-13.1.0-arm64-netinst.iso", "abc"))

    def test_arm64_installer_paths(self):
        grub = iso.auto_grub_cfg("menuentry 'Install' {\n}\n", "T", "/install.a64")
        self.assertIn("linux    /install.a64/vmlinuz auto=true", grub)     # no vga= on arm64
        self.assertIn("initrd   /install.a64/initrd.gz", grub)

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
        # disk: auto: no device is named; a script of the live system finds the machine's one disk
        f3 = self.fleet({"defaults": self.base_defaults(disk="auto", install={"on_done": "reboot"}), "hosts": {"a": {}}})
        self.assertEqual(f3.hosts["a"].validate(self.roles), [])
        calls.clear()
        iso.coreos_iso(FakeTC(), f3.hosts["a"], ign, os.path.join(self.tmp, "o", "a.iso"), self.tmp)
        self.assertNotIn("--dest-device", calls[2])
        pre = calls[2][calls[2].index("--pre-install") + 1]
        script = util.read_text(pre)
        self.assertIn("dest-device: %s", script)
        self.assertIn("disk: auto needs a machine with one disk", script)
        self.assertEqual(subprocess.run(["bash", "-n", pre]).returncode, 0)

    def test_an_apply_waits_until_a_fresh_ucore_machine_has_switched(self):
        answers = iter([b"switching\n", KiwiError("no route to host"), b"switching\n", b""])
        slept, said = [], []

        def run(tgt, script):
            a = next(answers)
            if isinstance(a, Exception):
                raise a
            return a
        self.assertTrue(coreos_t.wait_for_ucore("papaya", "core@p", said.append, run, slept.append))
        self.assertEqual((len(slept), len(said)), (3, 1))        # said once, through a restart, until it is done
        self.assertIn("restarts twice by itself", said[0])
        self.assertFalse(coreos_t.wait_for_ucore("papaya", "core@p", said.append, lambda t, s: b"", slept.append))
        with self.assertRaisesRegex(KiwiError, "ucore-signed-autorebase failed"):
            coreos_t.wait_for_ucore("papaya", "core@p", said.append,
                                    lambda t, s: b"switching\nfailed ucore-signed-autorebase\n", slept.append)
        with self.assertRaisesRegex(KiwiError, "no route"):      # it never answered: that is no restart
            coreos_t.wait_for_ucore("papaya", "core@p", said.append,
                                    lambda t, s: (_ for _ in ()).throw(KiwiError("no route to host")), slept.append)
        with self.assertRaisesRegex(KiwiError, "still switching to uCore after"):
            coreos_t.wait_for_ucore("papaya", "core@p", said.append, lambda t, s: b"switching\n", slept.append, tries=3)

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
        self.assertEqual(len(ms.names()), 23)
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
        self.assertIn("FIREWALL_VPN_INPUT_PORTS=80,443,22", env)
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
        self.assertIn("BindsTo=a.service", b)
        self.assertIn("WantedBy=a.service", b)
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
        self.assertIn("FIREWALL_VPN_INPUT_PORTS=80,443,22,2224", env)      # gluetun takes single ports only

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
        self.assertTrue(inserts[-2].endswith('-d "$DOCKER_SUBNET" -m conntrack --ctstate NEW -m conntrack ! --ctstate DNAT -j DROP'), inserts)
        # the kill switch above it all: what a LAN client sends never leaves by the card it came in on
        self.assertEqual(inserts[-1], 'iptables -I FORWARD -i "$PUB_IFACE" -o "$PUB_IFACE" -j DROP')
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
        self.assertEqual(set(svcs), {"km-vpn-client", "km-vpn-server", "km-pihole", "km-tor", "km-nginx", "km-fail2ban"})
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
        # namespace they use: BindsTo= restarts them with it and stops them when it goes, WantedBy=
        # brings them back when it starts
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
            self.assertIn("BindsTo=km-vpn-server.service", unit)
            self.assertIn("WantedBy=km-vpn-server.service", unit)

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
        self.assertEqual(sorted(b.units), ["km-mullvad-socks.service", "km-mullvad-socks.timer", "km-pihole-blocklist.service",
                                           "km-pihole-blocklist.timer", "kn-sshd-log.service"])
        self.assertIn("ExecStart=/usr/bin/bash /home/user/docker/kiwi/mullvad-socks.sh", b.units["km-mullvad-socks.service"])
        self.assertIn("TimeoutStartSec=", b.units["km-mullvad-socks.service"])
        self.assertIn("Unit=km-mullvad-socks.service", b.units["km-mullvad-socks.timer"])
        self.assertIn("OnUnitActiveSec=6h", b.units["km-mullvad-socks.timer"])
        # the role script carries and enables them like any host unit
        full = rolesmod.render_script(host, role, VERSION, bundle=b)
        units = re.search(r"^KS_STACK_UNITS=\((.*)\)$", full, re.M).group(1).replace("'", "").split()
        self.assertEqual(sorted(units), ["km-mullvad-socks.service", "km-mullvad-socks.timer", "km-pihole-blocklist.service",
                                         "km-pihole-blocklist.timer", "kn-sshd-log.service"])
        self.assertIn("'kiwi/mullvad-socks.sh:0755'", full)

    def test_mullvad_socks_records_are_opt_in_elsewhere(self):
        host, role, spec = self.spec("m1", role="node-gw", **{"node-gw": self.node_gw()})
        b = modmod.Renderer(self.ms).render(spec)
        self.assertNotIn("hostsdir", "\n".join(b.compose["services"]["kn-pihole"]["environment"]))
        self.assertNotIn("mullvad", "\n".join(b.compose["services"]["kn-pihole"]["volumes"]))
        self.assertNotIn("kiwi/mullvad-socks.sh", b.files)
        self.assertEqual(sorted(b.units), ["kn-gateway.service", "kn-pihole-blocklist.service", "kn-pihole-blocklist.timer"])
        self.assertFalse([d for d in b.dirs if "mullvad" in d])
        m = self.master()
        m["dns"]["mullvad_socks"] = False
        host, role, spec = self.spec("gate", role="master", **{"master": m})
        b = modmod.Renderer(self.ms).render(spec)
        self.assertNotIn("hostsdir", "\n".join(b.compose["services"]["km-pihole"]["environment"]))
        self.assertNotIn("mullvad", "\n".join(b.compose["services"]["km-pihole"]["volumes"]))
        self.assertEqual(sorted(b.units), ["km-pihole-blocklist.service", "km-pihole-blocklist.timer",
                                           "kn-sshd-log.service"])          # the blocklist's, and fail2ban's from the master preset
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
        self.assertEqual(sorted(modmod.Renderer(self.ms).render(spec).units),
                         ["km-pihole-blocklist.service", "km-pihole-blocklist.timer", "kn-sshd-log.service"])

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


class TestSetup(Base):
    ANSWERS = {"domain": "home", "timezone": "Europe/Berlin", "keyboard": "de", "admin_key": "generate",
               "master_name": "trifle", "master_where": "netcup", "master_address": "203.0.113.7", "exit": "direct",
               "lan_subnet": "192.168.0.0/16", "lan_gateway": "192.168.2.1",
               "nodes": [{"name": "mango", "preset": "gateway", "address": "192.168.2.30/16"},
                         {"name": "quince", "preset": "cloud", "target": "debian"}], "store": False}

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = cli.main(list(args))
            except SystemExit as e:
                rc = e.code
        return rc, out.getvalue(), err.getvalue()

    @unittest.skipUnless(have("ssh-keygen"), "ssh-keygen not installed")
    def test_answers_make_a_fleet_that_waits_only_for_its_master(self):
        answers = os.path.join(self.tmp, "answers.json")
        write(answers, json.dumps(self.ANSWERS))
        dest = os.path.join(self.tmp, "fleet.yaml")
        rc, out, err = self.run_cli("init", dest, "--answers", answers)
        self.assertEqual(rc, 0, err)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "secrets", "admin_ed25519")))
        self.assertIn("kiwi-server next", out)                   # one command goes on from here
        self.assertEqual(yaml.safe_load(util.read_text(dest))["hosts"]["trifle"]["disk"], "auto")
        rc, out, err = self.run_cli("list", dest, "--porcelain")
        self.assertEqual(rc, 0, err)
        hosts = {h["name"]: h for h in json.loads(out)["hosts"]}
        self.assertEqual(hosts["trifle"]["errors"], [])
        self.assertEqual((hosts["trifle"]["phase"], hosts["mango"]["phase"], hosts["quince"]["phase"]),
                         ("build", "enroll", "enroll"))
        self.assertIn("once trifle runs: kiwi-server enroll", hosts["mango"]["next"])
        self.assertNotIn("file not found", err)
        # enrolled: the same node is ready to be built
        write(os.path.join(self.tmp, "secrets", "mango.home.conf"), "[Interface]\nPrivateKey = x\nAddress = 10.8.0.2/24\n")
        rc, out, err = self.run_cli("list", dest, "--porcelain")
        hosts = {h["name"]: h for h in json.loads(out)["hosts"]}
        self.assertEqual(hosts["mango"]["phase"], "build", hosts["mango"]["next"])

    @unittest.skipUnless(have("ssh-keygen"), "ssh-keygen not installed")
    def test_next_sets_a_fresh_machine_up_at_its_address(self):
        answers = os.path.join(self.tmp, "answers.json")
        write(answers, json.dumps(self.ANSWERS))
        dest = os.path.join(self.tmp, "fleet.yaml")
        self.assertEqual(self.run_cli("init", dest, "--answers", answers, "--no-store")[0], 0)
        calls, real_main = [], cli.main

        def phases(fleet, roles, host, errors, out):
            return ("install", "x") if host.name == "mango" else ("applied", "")

        def fake_main(argv):
            calls.append(argv)
            return 0
        with mock.patch.object(cli, "next_step", phases), mock.patch.object(remote, "_answers", lambda *a, **k: True), \
                mock.patch.object(cli, "main", fake_main), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            real_main(["next", dest, "--yes"])
        # not by its name, which resolves on the VPN only; and a key on record for that address is an older install's
        self.assertEqual(calls, [["apply", dest, "mango", "--ssh", "core@192.168.2.30", "--new-host-key"]])

    def test_a_backup_server_with_a_new_client_is_named_until_it_is_applied(self):
        answers = os.path.join(self.tmp, "answers.json")
        write(answers, json.dumps(self.ANSWERS))
        dest = os.path.join(self.tmp, "fleet.yaml")
        self.assertEqual(self.run_cli("init", dest, "--answers", answers, "--no-store")[0], 0)
        fleet = config.Fleet.load(dest)
        server = fleet.hosts["mango"]
        server.role_settings_raw["backup-server"] = {"clients": ["a.home"]}
        self.assertEqual(cli.backup_untold(fleet), {})          # never applied by this release: nothing is known
        cli.record_backup_clients(fleet, server)
        self.assertEqual(cli.backup_untold(fleet), {})
        server.role_settings_raw["backup-server"]["clients"].append("b.home")
        self.assertEqual(cli.backup_untold(fleet), {"mango": ["b.home"]})
        cli.record_backup_clients(fleet, server)
        self.assertEqual(cli.backup_untold(fleet), {})

    FOUND = {"subnet": "192.168.1.0/24", "gateway": "192.168.1.1", "timezone": "Europe/Vienna", "keyboard": "at",
             "locale": "de_AT.UTF-8", "ssh_key": "", "addresses": ["192.168.1.31/24", "192.168.1.32/24"]}

    def ask(self, typed, **kw):
        typed, said = iter(typed), []
        a = setupmod.ask(dict(self.FOUND), read=lambda prompt: next(typed), show=said.append, **kw)
        return a, " ".join("\n".join(said).split())          # (the lines are wrapped: compared as one text)

    def test_the_terminal_asks_little_and_enter_takes_the_suggestion(self):
        a, said = self.ask(["",                    # the hub: at netcup
                            "198.51.100.4", "",    # its address, the direct exit
                            "", "", "",            # a machine at home: yes, files and passwords, the address that looks free
                            ""])                   # no second one
        self.assertEqual((a["timezone"], a["keyboard"], a["locale"], a["domain"]), ("Europe/Vienna", "at", "de_AT.UTF-8", "home"))
        self.assertEqual((a["master_where"], a["exit"], a["master_disk"]), ("netcup", "direct", "auto"))
        self.assertEqual(a["nodes"], [{"preset": "cloud", "address": "192.168.1.31/24", "name": "papaya",
                                       "target": "ucore", "disk": "auto"}])
        self.assertIn("You need three things", said)             # said before anything is asked
        self.assertIn("time zone Europe/Vienna", said)           # found, shown, not asked
        self.assertNotIn("Name ending", said)
        data, _made = setupmod.make(dict(a, admin_key="~/.ssh/x.pub"), self.tmp)
        self.assertEqual(data["hosts"]["papaya"]["network"], {"dhcp": False, "address": "192.168.1.31/24", "gateway": "192.168.1.1"})
        self.assertEqual((data["hosts"]["papaya"]["disk"], data["defaults"]["locale"]), ("auto", "de_AT.UTF-8"))

    def test_a_wrong_answer_is_asked_again_and_nothing_is_lost(self):
        a, said = self.ask(["7", "2",                       # no such choice, then: a rented server elsewhere
                            "192.168.1.9", "vpn", "vpn.example.org",    # an address at home, no name, a name
                            "",
                            "", "2", "192.168.1.1", "10.0.0.5", "-",    # a gateway: the router's address, another network, none
                            "n"])
        self.assertIn("A number from 1 to 3", said)
        self.assertIn("inside a home network", said)
        self.assertIn("neither an address", said)
        self.assertIn("is your router's address", said)
        self.assertIn("is not in your home network", said)
        self.assertEqual((a["master_where"], a["master_address"]), ("vps", "vpn.example.org"))
        self.assertEqual((a["nodes"][0]["preset"], a["nodes"][0]["address"]), ("gateway", ""))

    def test_everything_is_asked_on_request_and_no_machine_at_home_is_said(self):
        a, said = self.ask(["", "", "", "internal", "", "", "",          # the basics, the name ending changed
                            "3", "home.example.org", "", "parfait", "/dev/sda",
                            "n"], everything=True)
        self.assertEqual((a["domain"], a["master_name"], a["master_disk"], a["nodes"]), ("internal", "parfait", "/dev/sda", []))
        self.assertIn("no place for your files yet", said)

    def test_a_netcup_server_is_picked_from_a_list_instead_of_typed(self):
        rows = [{"id": 7, "address": "198.51.100.7", "label": "crumble · v2202 · 198.51.100.7"},
                {"id": 9, "address": "198.51.100.9", "label": "trifle · v2203 · 198.51.100.9"}]
        a, said = self.ask(["", "", "5", "2",              # netcup, sign in: yes, no such number, the second server
                            "", "n"],                      # the direct exit, no machine at home
                           lookup=lambda provider, show: (rows, "/run/x.token"))
        self.assertEqual((a["master_address"], a["provider_server"], a["provider_token"]), ("198.51.100.9", 9, "/run/x.token"))
        self.assertNotIn("Its address on the Internet", said)    # not asked: netcup said it
        data, _made = setupmod.make(dict(a, admin_key="~/.ssh/x.pub"), self.tmp)
        master = data["hosts"][a["master_name"]]
        self.assertEqual((master["vps"], master["master"]["vpn-server"]["wg_host"]), ({"provider": "netcup", "server": 9}, "198.51.100.9"))

        def fails(provider, show):
            raise KiwiError("netcup is not reachable")
        a, said = self.ask(["", "", "198.51.100.4", "", "n"], lookup=fails)      # then the address is typed after all
        self.assertIn("Type the address instead", said)
        self.assertEqual(a["master_address"], "198.51.100.4")
        a, said = self.ask(["", "n", "198.51.100.4", "", "n"], lookup=fails)     # or the sign-in is declined
        self.assertEqual((a["master_address"], a.get("provider_server")), ("198.51.100.4", None))

    def test_the_app_signs_in_to_netcup_and_lists_the_servers(self):
        api = FakeNetcup()
        self.addCleanup(api.close)
        token = os.path.join(self.tmp, "t", "netcup.token")
        rc, out, err = self.run_cli("init", "--provider", "netcup", "--provider-login", token, "--api", api.url)
        self.assertEqual((rc, out.strip()), (0, "https://login.example/x"), err)     # the page to open, and nothing else
        self.assertTrue(os.path.isfile(token))
        rc, out, err = self.run_cli("init", "--provider-servers", token, "--api", api.url)
        self.assertEqual(json.loads(out), [{"id": 7, "address": "203.0.113.9", "label": "crumble · v2202 · 203.0.113.9"}], err)
        # the answers carry where the token is: it becomes the network's own
        answers = os.path.join(self.tmp, "answers.json")
        write(answers, json.dumps(dict(self.ANSWERS, provider_server=7, provider_token=token)))
        dest = os.path.join(self.tmp, "net", "fleet.yaml")
        os.makedirs(os.path.dirname(dest))
        self.assertEqual(self.run_cli("init", dest, "--answers", answers, "--no-store")[0], 0)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "net", "secrets", "netcup.token")))
        self.assertFalse(os.path.exists(token))
        made = yaml.safe_load(util.read_text(dest))["hosts"]["trifle"]
        self.assertEqual(made["vps"], {"provider": "netcup", "server": 7})    # the install knows where and which
        from kiwiserver import providers
        self.assertEqual(list(providers.PROVIDERS), ["netcup"])               # the first; another is a class there
        with self.assertRaisesRegex(KiwiError, "no provider kiwi-server knows"):
            providers.get("elsewhere")

    def test_addresses_that_answer_are_not_suggested(self):
        def run(argv, timeout=10):
            if argv[0] == "ping":
                return "1 received" if argv[-1] == "192.168.1.30" else ""
            return "192.168.1.32 dev eth0 lladdr aa:bb:cc:dd:ee:ff STALE" if argv[-1] == "192.168.1.32" else ""
        self.assertEqual(setupmod.free_addresses("192.168.1.0/24", "192.168.1.1", want=3, run=run),
                         ["192.168.1.31/24", "192.168.1.33/24", "192.168.1.34/24"])

    def test_the_questions_and_what_is_refused(self):
        rc, out, err = self.run_cli("init", "--questions")
        self.assertEqual(rc, 0, err)
        d = json.loads(out)
        qs = {q["id"]: q for st in d["steps"] for q in st["questions"]}
        for need in ("domain", "admin_key", "master_name", "master_where", "master_address", "exit", "nodes"):
            self.assertIn(need, qs)
        self.assertNotIn("store", qs)                            # the keys are always encrypted
        self.assertTrue(qs["domain"]["advanced"] and qs["master_disk"]["advanced"])
        self.assertFalse(qs["master_address"].get("advanced"))
        self.assertEqual([st["id"] for st in d["steps"]], ["start", "master", "home", "keys"])
        self.assertIn("subnet", d["found"])
        self.assertEqual(setupmod.check(dict(self.ANSWERS)), {})
        bad = setupmod.check(dict(self.ANSWERS, master_address="10.1.2.3", exit="mullvad",
                                  nodes=[{"name": "trifle"}, {"name": "mango", "address": "192.168.2.1", "disk": "sda"}]))
        self.assertEqual(sorted(bad), ["master_address", "mullvad_address", "mullvad_key", "nodes.0.name",
                                       "nodes.1.address", "nodes.1.disk"])
        answers = os.path.join(self.tmp, "a.json")
        write(answers, json.dumps(dict(self.ANSWERS, master_address="")))
        rc, out, err = self.run_cli("init", "--check", answers)              # what the app asks at every Next
        self.assertEqual(list(json.loads(out)), ["master_address"])
        with self.assertRaisesRegex(KiwiError, "Two machines are called"):
            setupmod.make(dict(self.ANSWERS, nodes=[{"name": "trifle"}]), self.tmp)
        with self.assertRaisesRegex(KiwiError, "PrivateKey"):
            setupmod.make(dict(self.ANSWERS, exit="mullvad"), self.tmp)
        with self.assertRaisesRegex(KiwiError, "mDNS"):
            setupmod.make(dict(self.ANSWERS, domain="local"), self.tmp)
        with mock.patch.object(sys.stdin, "isatty", lambda: False):
            rc, out, err = self.run_cli("init", os.path.join(self.tmp, "new", "fleet.yaml"))
        self.assertIn("need a terminal", err)                    # said, not the example written silently
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "new")))
        self.assertEqual(setupmod.suggest("192.168.0.0/16", 0, "192.168.2.1"), "192.168.2.30/16")
        self.assertEqual(setupmod.suggest("10.0.0.0/24", 2), "10.0.0.32/24")

    @unittest.skipUnless(have("ssh-keygen"), "ssh-keygen not installed")
    def test_new_hosts_get_a_free_fruit_or_fruit_dish(self):
        answers = os.path.join(self.tmp, "answers.json")
        write(answers, json.dumps(dict(self.ANSWERS, master_name="sorbet", nodes=[{"name": "papaya", "preset": "minimal"}])))
        dest = os.path.join(self.tmp, "fleet.yaml")
        self.assertEqual(self.run_cli("init", dest, "--answers", answers)[0], 0)
        with open(dest, "a") as fh:
            fh.write("  # a comment at the end stays\n")
        rc, out, err = self.run_cli("names", dest)
        self.assertEqual(rc, 0, err)
        names = json.loads(out)
        self.assertEqual((names["master"], names["node"]), (setupmod.MASTERS[1], setupmod.NODES[1]))
        self.assertIn("sorbet", names["masters"])
        self.assertEqual(self.run_cli("add", dest, "node")[0], 0)
        self.assertEqual(self.run_cli("add", dest, "master")[0], 0)
        rc, _out, err = self.run_cli("add", dest, "node", "lab-pi", "--preset", "gateway")
        self.assertEqual(rc, 0, err)
        rc, _out, err = self.run_cli("add", dest, "node", "papaya")
        self.assertNotEqual(rc, 0)
        self.assertIn("already a host called papaya", err)
        with open(dest) as fh:
            text = fh.read()
        self.assertIn("# a comment at the end stays", text)
        hosts = yaml.safe_load(text)["hosts"]
        node, master = hosts[setupmod.NODES[1]], hosts[setupmod.MASTERS[1]]
        self.assertEqual((node["role"], node["node"]["preset"]), ("node", "minimal"))
        self.assertEqual(node["node"]["vpn-client"]["wireguard_config"], "secrets/%s.home.conf" % setupmod.NODES[1])
        self.assertEqual((master["role"], master["target"]), ("master", "debian"))
        self.assertEqual(hosts["lab-pi"]["node"]["preset"], "gateway")
        self.assertEqual(setupmod.free_name("node", set(setupmod.NODES)), setupmod.NODES[0] + "2")

    def test_status_remembers_machines_an_older_release_applied(self):
        fleet = self.fleet({"defaults": self.base_defaults(), "hosts": {"gate": {"role": "master", "target": "debian",
                                                                                  "master": self.master()}}})
        cli.record_applied(fleet, "gate", only_new=True)
        first = cli.fleet_state(fleet)["applied"]["gate"]
        cli.record_applied(fleet, "gate", only_new=True)         # seen again: the time stays
        self.assertEqual(cli.fleet_state(fleet)["applied"]["gate"], first)
        elsewhere = os.path.join(os.path.expanduser("~"), "kiwi-elsewhere", "a.iso")    # not below the tests' cwd
        self.assertEqual(cli.shown_path(elsewhere), "~/kiwi-elsewhere/a.iso")
        self.assertEqual(cli.shown_path("/srv/a.iso"), "/srv/a.iso")
        self.assertEqual(cli.shown_path(os.path.join(os.getcwd(), "output", "a.iso")), os.path.join("output", "a.iso"))


@unittest.skipUnless(have("ssh-keygen"), "ssh-keygen not installed")
class TestSshCa(Base):
    def gate(self):
        return self.fleet({"defaults": self.base_defaults(), "hosts": {
            "gate": {"role": "master", "target": "debian", "master": self.master()}}})

    def test_a_host_certificate_names_the_machine_and_its_sshd_takes_user_certificates(self):
        fleet = self.gate()
        sshca.init(fleet)
        with self.assertRaisesRegex(KiwiError, "exists already"):
            sshca.init(fleet)
        key = os.path.join(self.tmp, "host")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key], check=True)
        scripts = []

        def fake_run(tgt, script, data=None, runner=None):
            scripts.append(script)
            if not script.startswith("cat "):
                return b""
            with open(key + ".pub", "rb") as fh:
                return fh.read()
        h = fleet.hosts["gate"]
        with mock.patch.object(remote, "run", fake_run):
            sshca.install(fleet, h, "core@gate.kiwi")
        self.assertIn("HostCertificate /etc/ssh/ssh_host_ed25519_key-cert.pub", scripts[1])
        self.assertIn("TrustedUserCAKeys /etc/ssh/kiwi-user-ca.pub", scripts[1])
        self.assertIn("sshd -t", scripts[1])
        cert = re.search(r"printf '%s\\n' '([^']+)' > /etc/ssh/ssh_host_ed25519_key-cert.pub", scripts[1]).group(1)
        write(key + "-cert.pub", cert + "\n")
        shown = subprocess.run(["ssh-keygen", "-L", "-f", key + "-cert.pub"], capture_output=True, text=True).stdout
        self.assertIn("host certificate", shown)
        self.assertIn(h.hostname, shown)
        self.assertNotIn("PRIVATE", "".join(scripts))
        with open(fleet.resolve_path("secrets/known_hosts")) as fh:
            known = fh.read()
        self.assertEqual(known.count("@cert-authority "), 1)
        self.assertIn("*.%s" % fleet.defaults["domain"], known)

    def test_a_key_is_let_in_for_a_while(self):
        fleet = self.gate()
        sshca.init(fleet)
        key = os.path.join(self.tmp, "laptop")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key], check=True)
        rc, out, err = TestManage.run_cli(self, "sshca", fleet.path, "sign", key + ".pub", "--days", "2")
        self.assertEqual(rc, 0, err)
        shown = subprocess.run(["ssh-keygen", "-L", "-f", key + "-cert.pub"], capture_output=True, text=True).stdout
        self.assertIn("user certificate", shown)
        self.assertIn(fleet.defaults["admin"]["user"], shown)
        known = os.path.join(self.tmp, "kh")
        write(known, "other.example ssh-ed25519 AAAA\n")
        for _ in range(2):
            TestManage.run_cli(self, "sshca", fleet.path, "trust", "--known-hosts", known)
        with open(known) as fh:
            lines = fh.read().splitlines()
        self.assertEqual((lines[0], sum(x.startswith("@cert-authority") for x in lines)), ("other.example ssh-ed25519 AAAA", 1))

    def test_an_address_met_nowhere_counts_only_with_a_certificate(self):
        h = self.gate().hosts["gate"]
        with mock.patch.object(remote, "_on_record", lambda a, *p: False), \
             mock.patch.object(remote, "_answers", lambda a, *p: True), \
             mock.patch.object(remote, "_ca_known", lambda: True), \
             mock.patch.object(remote, "addresses", lambda host: ["gate.kiwi", "198.51.100.7"]):
            with mock.patch.object(remote, "_certified", lambda tgt: tgt.endswith("198.51.100.7")):
                self.assertTrue(remote.reach(h).endswith("@198.51.100.7"))
            with mock.patch.object(remote, "_certified", lambda tgt: False):
                self.assertTrue(remote.reach(h).endswith("@gate.kiwi"))


class TestSshPort(Base):
    def test_targets_carry_the_port(self):
        self.assertEqual(remote.with_port("core@203.0.113.7", 41022), "ssh://core@203.0.113.7:41022")
        self.assertEqual(remote.with_port("ssh://core@203.0.113.7:41022", 22), "core@203.0.113.7")
        self.assertEqual(remote.port_of("ssh://core@203.0.113.7:41022"), 41022)
        self.assertEqual(remote.port_of("core@203.0.113.7"), 22)
        self.assertEqual(remote.address_of("ssh://core@203.0.113.7:41022"), "203.0.113.7")
        self.assertEqual(remote.address_of("core@gate.kiwi"), "gate.kiwi")

    def test_a_move_keeps_22_until_the_new_port_is_proven(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {
            "gate": {"role": "master", "target": "debian", "admin": {"ssh_port": 41022}, "master": self.master()}}})
        h = f.hosts["gate"]
        scripts = []
        with mock.patch.object(remote, "run", lambda tgt, script, **kw: scripts.append((tgt, script)) or b""), \
             mock.patch.object(remote, "run_ok", lambda tgt, timeout=30: True):
            now = sshport.ensure(h, "core@203.0.113.7")
        self.assertEqual(now, "ssh://core@203.0.113.7:41022")
        self.assertEqual(scripts[0][0], "core@203.0.113.7")
        self.assertIn("keep22=1", scripts[0][1])            # first both ports
        self.assertEqual(scripts[1][0], "ssh://core@203.0.113.7:41022")
        self.assertIn("keep22=0", scripts[1][1])            # 22 goes only over the new port
        scripts.clear()
        with mock.patch.object(remote, "run", lambda tgt, script, **kw: scripts.append((tgt, script)) or b""), \
             mock.patch.object(remote, "run_ok", lambda tgt, timeout=30: False):
            now = sshport.ensure(h, "core@203.0.113.7")
        self.assertEqual((now, len(scripts)), ("core@203.0.113.7", 1))   # not proven: 22 stays
        self.assertIn("sshd -t", scripts[0][1])

    def test_a_forwarded_port_in_front_of_22_is_kept(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {"fig": {"target": "debian"}}})
        h = f.hosts["fig"]
        with mock.patch.object(remote, "run", lambda tgt, script, **kw: b""):
            self.assertEqual(sshport.ensure(h, "ssh://core@127.0.0.1:2260"), "ssh://core@127.0.0.1:2260")
        with mock.patch.object(remote, "run", lambda tgt, script, **kw: b"moved-back\n"):
            self.assertEqual(sshport.ensure(h, "ssh://core@203.0.113.7:41022"), "core@203.0.113.7")

    def test_the_port_is_a_port(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {
            "gate": {"role": "master", "target": "debian", "admin": {"ssh_port": 70000}, "master": self.master()}}})
        self.assertTrue(any("ssh_port" in e for e in f.hosts["gate"].validate(None)))


class TestWatchStore(unittest.TestCase):
    def test_a_locked_store_is_copied_for_one_look_and_the_link_left_alone(self):
        calls, seen = [], []

        class Fake:
            def __init__(self, open_):
                self.open_ = open_

            def exists(self):
                return True

            def is_open(self):
                return self.open_

            def identity(self, path=None):
                return "AGE-SECRET-KEY-FROM-KEYRING"

            def private_copy(self, key):
                calls.append(("copy", key))
                return {"tree": "/run/user/1000/kiwi-private-x/secrets"}

            def save_private(self, copy):
                calls.append(("save",))

            def unlock(self, key):
                calls.append(("unlock",))          # the shared link: never

            def lock(self):
                calls.append(("lock",))
        args = argparse.Namespace(fleet="x/fleet.yaml", timer=None, once=True, interval=1)
        look = lambda a: seen.append(os.environ.get("KIWI_SECRETS_DIR"))   # noqa: E731
        with mock.patch.object(cli.store.Store, "for_fleet", lambda p: Fake(False)), \
             mock.patch.object(cli, "_watch_once", look):
            cli.cmd_watch(args)
        self.assertEqual(calls, [("copy", "AGE-SECRET-KEY-FROM-KEYRING"), ("save",)])
        self.assertEqual(seen, ["/run/user/1000/kiwi-private-x/secrets"])
        self.assertIsNone(os.environ.get("KIWI_SECRETS_DIR"))
        calls.clear()
        with mock.patch.object(cli.store.Store, "for_fleet", lambda p: Fake(True)), \
             mock.patch.object(cli, "_watch_once", look):
            cli.cmd_watch(args)
        self.assertEqual(calls, [])          # opened by the owner: used as it is


class TestSeedGuard(Base):
    def test_no_new_seed_beside_a_locked_store(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {
            "gate": {"role": "master", "target": "debian", "master": self.master()}}})
        seed = f.resolve_path("secrets/seed")
        if os.path.exists(seed):
            os.remove(seed)
        write(os.path.join(self.tmp, "secrets.age"), "x")             # a store, not open
        with self.assertRaisesRegex(KiwiError, "secret store is locked"):
            f.seed()
        self.assertFalse(os.path.exists(seed))


class TestLocalCache(Base):
    def test_nodes_on_the_caches_lan_use_it_and_nobody_else(self):
        node = {"vpn-client": {"wireguard_config": "secrets/wg.conf"}}
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {
            "gate": {"role": "master", "target": "debian", "master": self.master()},
            "store": {"role": "node", "network": {"dhcp": False, "address": "192.168.7.30/24", "gateway": "192.168.7.1"},
                      "node": dict(node, vpn_ip="10.8.0.9", modules=["vpn-client", "reverse-proxy", "cache"],
                                   cache={"registries": ["docker.io", "ghcr.io"]})},
            "near": {"role": "node", "target": "debian",
                     "network": {"dhcp": False, "address": "192.168.7.31/24", "gateway": "192.168.7.1"},
                     "node": dict(node, vpn_ip="10.8.0.10", preset="minimal")},
            "far": {"role": "node", "target": "debian",
                    "network": {"dhcp": False, "address": "10.44.0.5/24", "gateway": "10.44.0.1"},
                    "node": dict(node, vpn_ip="10.8.0.11", preset="minimal", apt_proxy="http://10.44.0.9:3142")}}})
        r = cli.Renderer(f, rolesmod.discover(), self.tmp, None, modmod.discover())
        got = {}
        for name in ("gate", "store", "near", "far"):
            h = f.hosts[name]
            role = r.prepare(h)
            r.use_cache(h, role)
            got[name] = (h.role_settings.get("registry_mirrors") or {}, h.role_settings.get("apt_proxy") or "")
        self.assertEqual(got["near"], ({"docker.io": "cache.store.kiwi/docker.io", "ghcr.io": "cache.store.kiwi/ghcr.io"},
                                       "http://192.168.7.30:3142"))
        self.assertEqual(got["store"][0]["docker.io"], "cache.store.kiwi/docker.io")      # its own mirrors too
        self.assertEqual(got["far"], ({}, "http://10.44.0.9:3142"))                       # another LAN; its own setting stays
        self.assertEqual(got["gate"], ({}, ""))                                           # a master is nowhere near


class TestCheck(Base):
    def test_check_reports_each_probe_and_fails_on_a_dead_name(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {
            "gate": {"role": "master", "target": "debian", "master": self.master()}}})
        seen = []

        def fake_run(tgt, script, data=None, runner=None):
            seen.append(script)
            return (b"https wg.gate.kiwi 403\nhttps pihole.gate.kiwi 000\ndns gate.kiwi 10.8.0.1\n"
                    b"dns quad9.net none\nexit km-vpn-client 198.51.100.4\n")
        with mock.patch.object(remote, "run", fake_run), mock.patch.object(remote, "reach", lambda h, o=None: "core@gate"):
            rc, out, err = TestManage.run_cli(self, "check", f.path)
        self.assertEqual(rc, 1)
        self.assertIn("2 checks failed", err)
        self.assertRegex(out, r"ok +gate +https wg.gate.kiwi 403")
        self.assertRegex(out, r"FAILED gate +https pihole.gate.kiwi 000")
        self.assertRegex(out, r"FAILED gate +dns +quad9.net none")
        self.assertIn("NAMES=", seen[0])


class TestFederation(Base):
    def test_the_link_key_is_wireguards(self):
        priv = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")   # RFC 7748
        self.assertEqual(fedmod._x25519_base(priv).hex(), "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")
        self.assertEqual(fedmod.public_key("secret-for-test"), "dmZZPI0kvXBkZN/0+t4ACkEhunuvL8QeZowWG6x3QU4=")  # wg pubkey said so

    def test_a_node_lets_a_partner_open_only_what_is_shared(self):
        found = fedmod.with_addresses({
            "b": {"mesh": "10.10.0.0/16", "share": ["cloud.papaya", "lychee"], "from": []},
            "c": {"mesh": "10.11.0.0/16", "share": ["papaya:22"], "from": []}}, "10.9.0.0/16")
        self.assertEqual((found["b"]["as"], found["c"]["as"]), ("10.9.255.1", "10.9.255.2"))
        papaya = fedmod.node_rules(found, "papaya", "home")
        self.assertEqual(papaya["names"]["cloud.papaya.home"]["allow"], ["10.9.255.1"])
        self.assertEqual(papaya["deny"], ["10.9.255.1"])          # c has the whole node (a port of it): no name rules
        self.assertEqual(fedmod.node_rules(found, "lychee", "home")["deny"], ["10.9.255.2"])

    def test_federate_links_two_fleets_and_leaves_the_sharing_to_them(self):
        a = self.fleet({"defaults": dict(self.base_defaults(), master={"mesh_subnet": "10.9.0.0/16"}),
                        "hosts": {"gate": {"role": "master", "target": "debian", "master": self.master()}}})
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        b_path = os.path.join(other, "fleet.yaml")
        with open(a.path) as fh:
            data = yaml.safe_load(fh)
        data["defaults"]["domain"] = "internal"
        data["defaults"]["master"] = {"mesh_subnet": "10.10.0.0/16"}
        data["hosts"]["gate"]["master"]["vpn-server"]["wg_host"] = "198.51.100.9"
        for k in ("admin",):
            data["defaults"][k]["ssh_keys"] = [os.path.join(self.tmp, k) for k in data["defaults"][k].get("ssh_keys") or []] or []
        write(b_path, yaml.safe_dump(data))
        for d in ("secrets",):
            shutil.copytree(os.path.join(self.tmp, d), os.path.join(other, d), dirs_exist_ok=True) if os.path.isdir(os.path.join(self.tmp, d)) else None
        rc, out, err = TestManage.run_cli(self, "federate", a.path, b_path)
        self.assertEqual(rc, 0, err)
        with open(os.path.join(self.tmp, "federation.yaml")) as fh:
            mine = yaml.safe_load(fh)
        partner = mine["other"]
        self.assertEqual((partner["mesh"], partner["domain"], partner["share"]), ("10.10.0.0/16", "internal", []))
        loaded = config.Fleet.load(a.path)
        self.assertEqual(loaded.hosts["gate"].cfg["master"]["vpn-server"]["federation"]["other"]["as"], "10.9.255.1")


class TestPlan(unittest.TestCase):
    def test_a_container_restarts_when_its_quadlet_or_a_file_it_mounts_changed(self):
        class B:
            files = {"kiwi/a.conf": ("new a", 0o644), "kiwi/b.env": ("b", 0o600)}
            quadlets = {"kn-a.container": "[Container]\nVolume=/d/kiwi/a.conf:/etc/a.conf:ro\n",
                        "kn-b.container": "[Container]\nEnvironmentFile=/d/kiwi/b.env\n",
                        "kn-c.container": "[Container]\nImage=c:2\n", "kiwi-stack.target": "[Unit]\n"}
            units = {}
            services = ["kn-a", "kn-b", "kn-c"]

        class H:
            role_settings = {"docker_dir": "/d"}
        want = planmod.wanted(H, B)
        self.assertIn("/etc/systemd/system/kiwi-stack.target", want)
        have = dict(want)
        have["/d/kiwi/a.conf"] = "old"                                  # a mounted file changed
        del have["/etc/containers/systemd/kn-c.container"]              # a new container
        x = planmod.compare(B, want, have, ["kn-a", "kn-b", "kn-gone"])
        self.assertEqual((x["restart"], x["new_services"], x["removed_services"]), (["kn-a"], ["kn-c"], ["kn-gone"]))
        self.assertEqual(planmod.compare(B, want, dict(want), B.services)["changed"], [])


class TestBackups(unittest.TestCase):
    def test_snapshots_per_server_newest_first(self):
        line = "backup.papaya.home\t" + json.dumps([
            {"short_id": "aaa", "time": "2026-11-10T11:13:25Z", "tags": ["portainer", "frozen"]},
            {"short_id": "bbb", "time": "2026-11-10T19:01:12Z", "tags": ["tang", "bulk"]}])
        servers = cli.parse_snapshots(line + "\nbackup.lychee.home\tnull\n")
        self.assertEqual([s["server"] for s in servers], ["backup.papaya.home", "backup.lychee.home"])
        self.assertEqual([(x["id"], x["module"], x["kind"]) for x in servers[0]["snapshots"]],
                         [("bbb", "tang", "bulk"), ("aaa", "portainer", "frozen")])
        self.assertEqual((servers[1]["ok"], servers[1]["snapshots"]), (False, []))


class TestGuiHelp(unittest.TestCase):
    def test_every_base_field_of_the_app_has_help(self):
        """The app shows a (?) with the help beside every field; it reads the module and role
        settings' help from their yaml, the base fields' from its own BASE_GROUPS."""
        import ast
        with open(os.path.join(ROOT, "gui", "kiwi-server-gui")) as fh:
            tree = ast.parse(fh.read())
        groups = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                      and any(getattr(t, "id", "") == "BASE_GROUPS" for t in n.targets))
        fields = [c for c in ast.walk(groups) if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "dict"
                  and any(k.arg == "path" for k in c.keywords)]
        self.assertGreater(len(fields), 30)
        missing = [ast.unparse(next(k.value for k in c.keywords if k.arg == "path")) for c in fields
                   if not any(k.arg == "help" for k in c.keywords)]
        self.assertEqual(missing, [])


class TestForm(Base):
    """form.py: the settings a person can edit, without a page around them."""
    FLEET = {"defaults": {"domain": "home", "timezone": "Europe/Berlin", "updates": {"time": "03:30"},
                          "admin": {"user": "core", "password": "the-defaults-s3cr3t"}},
             "hosts": {"gate": {"role": "master", "target": "debian", "master": {
                           "modules": ["vpn-server", "dns"], "vpn-server": {"wg_host": "vpn.example.org", "wg_password": "wg-s3cr3t"}}},
                       "papaya": {"role": "node", "node": {"preset": "cloud"}, "network": {"dhcp": False, "address": "192.168.1.30/24"}}}}

    def schema(self):
        return {"roles": [r.as_dict(self.ms) for r in self.roles.values()], "modules": [m.as_dict() for m in self.ms.modules.values()]}

    def groups(self, data, host):
        return formmod.groups(self.schema(), list(config.TARGETS), data, host)

    def field(self, groups, name):
        return next(f for g in groups for f in g["fields"] if f["name"] == name)

    def test_the_app_and_the_page_have_the_same_base_fields(self):
        """The app (gui/kiwi-server-gui) still has its own copy of the base fields. Until it
        reads them from `roles --porcelain` ("base"), this holds the two copies equal: a field
        added to one and not the other fails here."""
        import ast
        with open(os.path.join(ROOT, "gui", "kiwi-server-gui")) as fh:
            tree = ast.parse(fh.read())

        def of_the_app(name):
            node = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                        and any(getattr(t, "id", "") == name for t in n.targets))
            # literals and dict(...) calls only: evaluated with nothing else in reach
            return eval(compile(ast.Expression(node), "kiwi-server-gui", "eval"), {"__builtins__": {}, "dict": dict})
        self.assertEqual(of_the_app("BASE_GROUPS"), formmod.BASE_GROUPS)
        self.assertEqual(of_the_app("SECTION_TARGETS"), formmod.SECTION_TARGETS)

    def test_text_and_values(self):
        for ftype, value, text in (("list", ["Sat", "Sun"], "Sat Sun"), ("lines", ["a b", "c"], "a b\nc"),
                                   ("map", {"A": "1", "B": "x=y"}, "A=1\nB=x=y"), ("int", 22, "22"), ("string", "x", "x"),
                                   ("object", {"guests": {"reach": ["nodes"]}}, "guests:\n  reach:\n  - nodes")):
            self.assertEqual(formmod.to_text(ftype, value), text)
            self.assertEqual(formmod.from_text(ftype, text + "\n"), value)
        for ftype in ("string", "int", "list", "lines", "map", "text", "object"):
            self.assertEqual(formmod.to_text(ftype, None), "")
            self.assertIsNone(formmod.from_text(ftype, "  "))
        self.assertIsNone(formmod.from_text("int", "abc"))
        self.assertEqual(formmod.from_text("text", " indented\n\n"), " indented")

    def test_a_machine_inherits_what_it_does_not_set(self):
        doc = formmod.Doc(yaml.safe_load(yaml.safe_dump(self.FLEET)))
        self.assertIsNone(doc.get("papaya", ("timezone",)))
        self.assertEqual(doc.inherited("papaya", ("timezone",), "UTC"), "Europe/Berlin")
        self.assertEqual(doc.inherited(None, ("timezone",), "UTC"), "UTC")                  # the defaults: the built-in value
        self.assertEqual(doc.inherited("papaya", ("locale",), "en_US.UTF-8"), "en_US.UTF-8")
        doc.set("papaya", ("timezone",), "Europe/Vienna")
        doc.set("papaya", ("updates", "time"), "04:15")
        self.assertEqual((doc.get("papaya", ("timezone",)), doc.get("papaya", ("updates",))), ("Europe/Vienna", {"time": "04:15"}))
        doc.set("papaya", ("timezone",), "Europe/Berlin")           # what it inherits anyway: no override
        doc.set("papaya", ("updates", "time"), None)                # empty: the override goes, and the empty block with it
        self.assertEqual((doc.get("papaya", ("timezone",)), doc.get("papaya", ("updates",))), (None, None))
        doc.set(None, ("locale",), "en_US.UTF-8", "en_US.UTF-8")    # the built-in value is not written either
        self.assertNotIn("locale", doc.data["defaults"])
        doc.set("papaya", ("debian", "release"), None)              # nothing there: nothing made
        self.assertNotIn("debian", doc.data["hosts"]["papaya"])
        self.assertEqual(doc.apply("papaya", {("keyboard",): "de", "updates.days": ["Sat"], ("motd",): None}), ["keyboard", "updates.days"])
        with self.assertRaises(KiwiError):
            doc.set("nobody", ("timezone",), "UTC")
        text = doc.dump()
        self.assertEqual(text.splitlines()[0][:13], "# fleet.yaml ")
        self.assertTrue(text.splitlines()[1].startswith("# "))
        self.assertEqual(list(yaml.safe_load(text)), ["defaults", "hosts"])             # hosts stays the last section
        self.assertEqual(yaml.safe_load(text)["hosts"]["papaya"]["updates"], {"days": ["Sat"]})
        self.assertEqual(list(formmod.Doc({"hosts": {"a": None}}).data), ["defaults", "hosts"])
        with self.assertRaises(KiwiError):
            formmod.Doc.parse("defaults: [1")

    def test_a_form_for_a_machine_and_for_the_defaults(self):
        data = yaml.safe_load(yaml.safe_dump(self.FLEET))
        found = self.groups(data, "papaya")
        self.assertEqual([g["id"] for g in found if not g["advanced"]], ["base:Machine", "role", "base:Network", "base:Updates"])
        ids = [g["id"] for g in found]
        self.assertIn("mod:cloud", ids)                             # one group per module it runs
        self.assertIn("mod:vpn-client", ids)
        self.assertNotIn("mod:immich", ids)
        self.assertIn("base:uCore", ids)                            # its own target's group only
        self.assertNotIn("base:Debian", ids)
        self.assertIn("base:Debian", [g["id"] for g in self.groups(data, "gate")])
        names = [f["name"] for g in found for f in g["fields"]]
        self.assertIn("hostname", names)                            # scope host
        self.assertNotIn("domain", names)                           # scope defaults
        tz = self.field(found, "timezone")
        self.assertEqual((tz["value"], tz["inherited"], tz["type"], tz["secret"]), (None, "Europe/Berlin", "string", False))
        self.assertEqual(self.field(found, "network.address")["value"], "192.168.1.30/24")
        self.assertEqual(self.field(found, "locale")["inherited"], "en_US.UTF-8")            # the built-in value
        self.assertEqual([o["value"] for o in self.field(found, "node.preset")["options"]], ["gateway", "cloud", "minimal"])
        parts = self.field(found, "node.modules")
        self.assertEqual([o["value"] for o in parts["options"] if o["on"]], ["cloud", "portainer", "reverse-proxy", "vault", "vpn-client"])
        self.assertNotIn("vpn-server", [o["value"] for o in parts["options"]])              # a master's part
        self.assertEqual(self.field(found, "role")["options"][0], {"value": "bare", "label": "bare"})
        # a module's default is the preset's where the preset has one
        gw = self.groups({"defaults": {}, "hosts": {"g": {"role": "node", "node": {"preset": "gateway"}}}}, "g")
        self.assertIs(self.field(gw, "node.dns.expose_web_ui")["inherited"], True)
        # the defaults: what only they have, every target's group, no machine's own fields
        every = self.groups(data, None)
        names = [f["name"] for g in every for f in g["fields"]]
        self.assertIn("domain", names)
        self.assertNotIn("hostname", names)
        self.assertLessEqual({"base:uCore", "base:Debian", "base:RAM"}, {g["id"] for g in every})
        self.assertEqual(self.field(every, "timezone")["value"], "Europe/Berlin")
        self.assertEqual(self.field(every, "timezone")["inherited"], "UTC")
        # a secret's value is in none of it, only that there is one
        for host in ("gate", "papaya", None):
            self.assertNotIn("s3cr3t", repr(self.groups(data, host)))
        pw = self.field(self.groups(data, "gate"), "master.vpn-server.wg_password")
        self.assertEqual((pw["secret"], pw["set"], pw["value"]), (True, True, None))
        inherited = self.field(found, "admin.password")
        self.assertEqual((inherited["set"], inherited["inherited_set"], inherited["inherited"]), (False, True, None))

    def test_only_what_was_changed_is_a_change(self):
        data = yaml.safe_load(yaml.safe_dump(self.FLEET))
        found = self.groups(data, "papaya")
        shown = {f["name"]: formmod.to_text(f["type"], f["value"]) for g in found for f in g["fields"]
                 if f["type"] not in ("modules", "bool", "secret")}
        shown["node.modules"] = [o["value"] for o in self.field(found, "node.modules")["options"] if o["on"]]
        self.assertEqual(formmod.changes(found, shown), ({}, {}))                           # sent back untouched
        got, wrong = formmod.changes(found, dict(shown, **{
            "timezone": "Europe/Vienna", "updates.time": "03:30", "network.dhcp": "no", "network.address": "",
            "updates.days": "Sat  Sun", "admin.ssh_port": "2222", "updates.enabled": "no", "admin.password": "typed-secret",
            "node.cloud.shared_folders": "/mnt/a=/a\r\n/mnt/b=/b\r\n", "node.docker_dir": ""}))
        self.assertEqual(wrong, {})
        self.assertEqual(got, {("timezone",): "Europe/Vienna", ("network", "address"): None, ("updates", "days"): ["Sat", "Sun"],
                               ("admin", "ssh_port"): 2222, ("updates", "enabled"): False, ("admin", "password"): "typed-secret",
                               ("node", "cloud", "shared_folders"): {"/mnt/a": "/a", "/mnt/b": "/b"}})
        # what cannot be meant is said, with the field's name, and is no change
        got, wrong = formmod.changes(found, {"admin.ssh_port": "abc", "node.variables": "no equals sign", "target": "amiga",
                                             "keyboard": "de"})
        self.assertEqual(got, {("keyboard",): "de"})
        self.assertEqual(sorted(wrong), ["admin.ssh_port", "node.variables", "target"])
        self.assertTrue(wrong["admin.ssh_port"].startswith("SSH port: "))
        # a secret: empty keeps it, None removes one that is set
        gate = self.groups(data, "gate")
        self.assertEqual(formmod.changes(gate, {"master.vpn-server.wg_password": ""})[0], {})
        self.assertEqual(formmod.changes(gate, {"master.vpn-server.wg_password": None})[0], {("master", "vpn-server", "wg_password"): None})
        self.assertEqual(formmod.changes(found, {"admin.password": None})[0], {})
        # parts: one more is the preset's list and it; the preset's list again is no list of its own
        on = shown["node.modules"]
        got, _ = formmod.changes(found, {"node.modules": on + ["immich"]})
        self.assertEqual(got, {("node", "modules"): ["vpn-client", "reverse-proxy", "cloud", "vault", "portainer", "immich"]})
        doc = formmod.Doc(data)
        doc.apply("papaya", got)
        more = self.groups(doc, "papaya")
        self.assertIn("mod:immich", [g["id"] for g in more])
        self.assertEqual(formmod.changes(more, {"node.modules": on})[0], {("node", "modules"): None})
        # a part another one needs comes along: leaving it out alone changes nothing
        self.assertEqual(formmod.changes(found, {"node.modules": [m for m in on if m != "reverse-proxy"]})[0], {})
        # another preset: its list counts, a list of the machine's own goes
        got, _ = formmod.changes(more, {"node.preset": "minimal", "node.modules": on + ["immich", "tor"]})
        self.assertEqual(got, {("node", "preset"): "minimal", ("node", "modules"): None})
        # another role: the old role's settings are not written into the file
        got, _ = formmod.changes(found, {"role": "master", "node.docker_dir": "/srv/docker", "timezone": "Europe/Vienna"})
        self.assertEqual(got, {("role",): "master", ("timezone",): "Europe/Vienna"})

    def test_an_error_names_the_field(self):
        found = self.groups(yaml.safe_load(yaml.safe_dump(self.FLEET)), "papaya")
        self.assertEqual(formmod.plain("papaya: updates.time must be HH:MM (24h)", found, "papaya"), "“Reboot time” must be HH:MM (24h)")
        self.assertEqual(formmod.plain("papaya: node.wg_mtu must be an integer", found, "papaya"), "“WireGuard MTU” must be an integer")
        self.assertEqual(formmod.plain("gate: something else", found, "papaya"), "gate: something else")


class TestOutputInMemory(Base):
    def test_rendered_files_go_to_the_open_store_and_the_disk_keeps_the_iso(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {
            "gate": {"role": "master", "target": "debian", "master": self.master()}}})
        media = os.path.join(self.tmp, "output")
        ram = os.path.join(self.tmp, "run", "output")
        for stale in ("gate.role.sh", "gate.preseed.cfg", "README.txt", "gate.stack/x.env"):
            write(os.path.join(media, "gate", stale), "a secret an older release left here")
        write(os.path.join(media, "gate", "gate.iso"), "ISO")

        class Open:
            def exists(self):
                return True

            def is_open(self):
                return True

            def runtime(self):
                return os.path.join(os.path.dirname(ram))

            def locked(self):
                return False

            def sync(self):
                return False
        with mock.patch.object(cli.store.Store, "for_fleet", classmethod(lambda cls, path: Open())):
            self.assertEqual(cli.out_dir_for(argparse.Namespace(output_dir=None), f), ram)
            self.assertEqual(cli.main(["render", f.path, "gate", "--no-backup"]), 0)
        self.assertTrue(os.path.isfile(os.path.join(ram, "gate", "gate.role.sh")))
        self.assertEqual(sorted(os.listdir(os.path.join(media, "gate"))), ["gate.iso"])   # the plain copies are gone
        self.assertEqual(cli.out_dir_for(argparse.Namespace(output_dir=None), f), media)   # no store: on disk as before


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

            def do_DELETE(self):
                fake.calls.append(("DELETE", self.path, None))
                if not self._authed():
                    return self._send(401, b'{"error":"Not Logged In"}')
                m = re.match(r"^/api/wireguard/client/([^/]+)$", self.path)
                c = fake.by_id(m.group(1)) if m else None
                if c is None:
                    return self._send(404, b'{"error":"no such route"}')
                fake.clients.remove(c)
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
        self.assertEqual([c[1] for c in calls], [b"s3cret\n"] * 3)
        self.assertNotIn("s3cret", " ".join(c[0] for c in calls))
        self.assertIn(" unlock", calls[0][0])                # what a killed restic left must not stop this
        self.assertIn("--network none", calls[1][0])
        self.assertIn("--group-by host,tags", calls[1][0])
        self.assertIn("--keep-daily 7 --keep-weekly 4 --keep-monthly 6", calls[1][0])
        self.assertIn(" prune", calls[2][0])

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

    def test_the_server_settings_say_what_is_kept(self):
        f = self.prune_fleet()
        with open(f.path) as fh:
            data = yaml.safe_load(fh)
        data["hosts"]["store"]["node"]["backup-server"]["keep_daily"] = 3
        write(f.path, yaml.safe_dump(data))
        scripts = []

        def fake_run(tgt, script, data=None, runner=None):
            scripts.append(script)
            return self.FORGET if " forget " in script else b""
        with mock.patch.object(remote, "run", fake_run):
            TestManage.run_cli(self, "prune", f.path)
            TestManage.run_cli(self, "prune", f.path, "--keep-daily", "5")
        forget = [x for x in scripts if " forget " in x]
        self.assertIn("--keep-daily 3 --keep-weekly 4", forget[0])
        self.assertIn("--keep-daily 5", forget[1])

    def test_the_command_will_not_keep_nothing(self):
        rc, out, err = TestManage.run_cli(self, "prune", self.prune_fleet().path, "--keep-daily", "0", "--keep-weekly", "0", "--keep-monthly", "0")
        self.assertNotEqual(rc, 0)
        self.assertIn("would keep nothing", err)

    def test_the_command_needs_a_backup_server(self):
        rc, out, err = TestManage.run_cli(self, "prune", self.prune_fleet().path, "plain")
        self.assertNotEqual(rc, 0)
        self.assertIn("none of these hosts", err)

    def test_a_timer_prunes_from_here_and_can_be_switched_off(self):
        calls = []

        def systemctl(*args):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, "", "")
        fleet_path = os.path.join(self.tmp, "fleet.yaml")
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": os.path.join(self.tmp, "cfg")}):
            backup.prune_timer(fleet_path, "week", systemctl=systemctl)
            units = os.path.join(self.tmp, "cfg", "systemd", "user")
            names = sorted(os.listdir(units))
            self.assertEqual(len(names), 2)
            timer = util.read_text(os.path.join(units, [n for n in names if n.endswith(".timer")][0]))
            service = util.read_text(os.path.join(units, [n for n in names if n.endswith(".service")][0]))
            self.assertIn("OnCalendar=weekly", timer)
            self.assertIn("Persistent=true", timer)                  # a PC that was off catches up
            self.assertIn("prune %s" % fleet_path, service)
            self.assertTrue(backup.timer_on(fleet_path))
            self.assertIn(("enable", "--now", names[1]), calls)
            backup.prune_timer(fleet_path, "off", systemctl=systemctl)
            self.assertEqual(os.listdir(units), [])
            self.assertFalse(backup.timer_on(fleet_path))

    def test_status_says_when_a_node_was_last_pruned(self):
        f = self.prune_fleet()
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": os.path.join(self.tmp, "cfg")}):
            level, text = backup.prune_note(f, "store", now=100 * 86400)
            self.assertEqual(level, "warn")
            self.assertIn("never pruned from here", text)
            self.assertIn("kiwi-server prune --every week", text)
            backup.record_prune(f, ["store"], [], now=90 * 86400)
            self.assertEqual(backup.prune_note(f, "store", now=100 * 86400)[0], None)
            self.assertIn("pruned 10 days ago", backup.prune_note(f, "store", now=100 * 86400)[1])
            self.assertEqual(backup.prune_note(f, "store", now=115 * 86400)[0], "warn")      # 25 days
            backup.record_prune(f, ["store"], ["papaya.kiwi"], now=99 * 86400)
            self.assertIn("failed for papaya.kiwi", backup.prune_note(f, "store", now=100 * 86400)[1])

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

    def test_a_locked_machine_is_told_what_really_opens_it(self):
        h = self.coreos_host()                                   # its ring is at home, nothing is forwarded
        said = unlock.locked_advice(h, "fleet.yaml")
        self.assertIn("Switch one of them on and it opens by itself", said)
        self.assertIn("secrets/luks/plum.key", said)
        self.assertNotIn("kiwi-server unlock", said)             # that command could not help here
        h.cfg["unlock"]["via"] = {"house": "http://127.0.0.1:7500"}
        self.assertEqual(unlock.locked_advice(h, "fleet.yaml"), "kiwi-server unlock fleet.yaml plum")

    def test_ignition_binds_a_coreos_disk_to_the_ring(self):
        bu = coreos_t.build_butane(self.coreos_host(), "#!/bin/sh\n")
        self.assertEqual(bu["boot_device"]["luks"]["tang"], [{"url": "http://192.168.2.61:7500", "thumbprint": "THP", "advertisement": '{"payload":"x"}'}])
        self.assertEqual(bu["boot_device"]["luks"]["threshold"], 1)
        self.assertEqual(bu["kernel_arguments"]["should_exist"], ["rd.neednet=1"])

    def test_a_static_coreos_host_gets_its_address_in_the_initramfs(self):
        h = self.coreos_host({"dhcp": False, "address": "192.168.2.70/16", "gateway": "192.168.2.1", "interface": "eno1"})
        bu = coreos_t.build_butane(h, "#!/bin/sh\n")
        self.assertIn("ip=192.168.2.70::192.168.2.1:255.255.0.0::eno1:none", bu["kernel_arguments"]["should_exist"])
        h = self.coreos_host({"dhcp": False, "address": "192.168.2.70/16", "gateway": "192.168.2.1", "interface": "eno1",
                              "dns": ["9.9.9.9", "149.112.112.112", "1.1.1.1"]})
        bu = coreos_t.build_butane(h, "#!/bin/sh\n")          # that connection stays after the boot: it needs the DNS
        self.assertIn("ip=192.168.2.70::192.168.2.1:255.255.0.0::eno1:none:9.9.9.9:149.112.112.112",
                      bu["kernel_arguments"]["should_exist"])

    def test_a_static_coreos_host_need_not_name_its_card(self):
        h = self.coreos_host({"dhcp": False, "address": "192.168.2.70/16", "gateway": "192.168.2.1"})
        bu = coreos_t.build_butane(h, "#!/bin/sh\n")          # the setting is for the wired card, whichever it is
        self.assertIn("ip=192.168.2.70::192.168.2.1:255.255.0.0:::none", bu["kernel_arguments"]["should_exist"])

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
        self.assertEqual(argv[argv.index("-i") + 1], f.resolve_path("secrets/unlock_ed25519"))
        self.assertEqual(argv[argv.index("-p") + 1], "2222")
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertLess(argv.index("StrictHostKeyChecking=yes"), argv.index("BatchMode=yes"))     # the first one given counts

    def test_a_machine_is_waiting_only_when_it_shows_the_initramfs_key(self):
        f, h = self.tunnel_host()
        h.cfg["unlock"]["ssh"] = True
        path = f.resolve_path("secrets/known_hosts.initramfs")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as out:
            out.write("[lychee.home]:2222 ssh-ed25519 PINNED\n")
        seen = []

        def scan(shown):
            def runner(argv, capture_output=True):
                seen.append(argv)
                return subprocess.CompletedProcess(argv, 0, ("# a comment\n192.168.2.61 ssh-ed25519 %s\n" % shown).encode(), b"")
            return runner
        with mock.patch.object(remote, "_answers", lambda addr, port=22, timeout=2: addr == "192.168.2.61"):
            self.assertEqual(unlock.waiting_at(h, runner=scan("PINNED")), "192.168.2.61")
            self.assertIsNone(unlock.waiting_at(h, runner=scan("SOMEONE-ELSE")))
        self.assertEqual(seen[0][seen[0].index("-p") + 1], "2222")
        h.cfg["unlock"]["ssh"] = False
        self.assertIsNone(unlock.waiting_at(h, runner=scan("PINNED")))

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
        self.assertEqual(seen[0][0][-1], "unlock")
        self.assertNotIn("thediskkey", " ".join(seen[0][0]))

    def test_a_connection_that_failed_is_tried_again(self):
        f, h = self.tunnel_host()
        tries = []

        def runner(argv, input=None, capture_output=True):
            tries.append(1)
            return subprocess.CompletedProcess(argv, 255 if len(tries) < 3 else 0, b"", b"banner exchange")
        with mock.patch.object(remote, "_answers", lambda addr, port=22, timeout=2: addr == "192.168.2.61"):
            self.assertTrue(unlock.type_key(h, runner=runner, sleep=lambda s: None, clock=lambda: 0.0))
        self.assertEqual(len(tries), 3)

    def test_a_reinstalled_machine_gets_the_one_time_key_and_never_the_old_one(self):
        f, h = self.tunnel_host()
        once = f.resolve_path("secrets/luks/%s.once" % h.name)
        os.makedirs(os.path.dirname(once), exist_ok=True)
        with open(once, "w") as out:
            out.write("theonetimekey")
        seen = []

        def runner(argv, input=None, capture_output=True):
            seen.append((argv, input))
            if len(seen) == 1:      # the pinned key is the old installation's
                return subprocess.CompletedProcess(argv, 255, b"", b"@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@\nHost key verification failed.\n")
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        with mock.patch.object(remote, "_answers", lambda addr, port=22, timeout=2: addr == "192.168.2.61"):
            self.assertTrue(unlock.type_key(h, runner=runner, sleep=lambda s: None, clock=lambda: 0.0))
        self.assertEqual(len(seen), 2)
        argv, given = seen[1]
        self.assertEqual(given, b"theonetimekey")
        self.assertNotIn(f.resolve_path("secrets/known_hosts.initramfs"), " ".join(argv))   # nothing pinned is changed
        self.assertLess(argv.index("StrictHostKeyChecking=accept-new"), argv.index("BatchMode=yes"))
        os.remove(once)
        seen.clear()
        with mock.patch.object(remote, "_answers", lambda addr, port=22, timeout=2: addr == "192.168.2.61"), \
                self.assertRaisesRegex(KiwiError, "not on record and there is no one-time key"):
            unlock.type_key(h, runner=runner, sleep=lambda s: None, clock=lambda: 0.0)
        self.assertEqual(len(seen), 1)                  # the key on record went nowhere

    def test_no_ring_goes_to_an_initramfs_with_another_host_key(self):
        f, h = self.tunnel_host()
        _calls, a, b = self.fake_remote()

        def runner(argv, stdin=None, capture_output=True):
            return subprocess.CompletedProcess(argv, 255, b"", b"Host key verification failed.\n")
        with a, b, mock.patch.object(remote, "_answers", lambda addr, port=22, timeout=2: True), \
                self.assertRaisesRegex(KiwiError, "no ring goes to it.*--manual"):
            unlock.open_disk(h, self.servers(f), runner=runner, clock=lambda: 0.0, sleep=lambda s: None)

    def test_a_timeout_says_what_ssh_said(self):
        f, h = self.tunnel_host()
        now = [0.0]

        def runner(argv, input=None, capture_output=True):
            now[0] += 5
            return subprocess.CompletedProcess(argv, 255, b"", b"Connection reset by peer")
        with mock.patch.object(remote, "_answers", lambda addr, port=22, timeout=2: True), \
                self.assertRaisesRegex(KiwiError, "no initramfs answered within 20 s \\(ssh: Connection reset by peer\\)"):
            unlock.type_key(h, timeout=20, runner=runner, sleep=lambda s: None, clock=lambda: now[0])

    def test_a_refused_key_is_not_tried_again(self):
        f, h = self.tunnel_host()
        count = []

        def runner(argv, input=None, capture_output=True):
            count.append(1)
            return subprocess.CompletedProcess(argv, 1, b"", b"")
        with mock.patch.object(remote, "_answers", lambda addr, port=22, timeout=2: True), self.assertRaisesRegex(KiwiError, "counts tries"):
            unlock.type_key(h, runner=runner, sleep=lambda s: None)
        self.assertEqual(len(count), 1)

    def test_the_initramfs_key_is_its_own(self):
        f, h = self.tunnel_host()
        line = unlock.authorized_line(h)
        self.assertTrue(os.path.isfile(f.resolve_path("secrets/unlock_ed25519")))
        for admin in h.ssh_keys():
            self.assertNotIn(admin.split()[1], line)

    def test_dropbear_gets_only_what_it_knows(self):
        f, h = self.tunnel_host()
        line = unlock.authorized_line(h)
        self.assertIn("no-agent-forwarding,no-X11-forwarding,no-pty,", line)
        self.assertIn('command="case $SSH_ORIGINAL_COMMAND in unlock) cryptroot-unlock;;', line)
        self.assertNotIn("no-port-forwarding", line)    # the ring is forwarded in
        for unknown in ("from=", "permitlisten", "restrict"):
            self.assertNotIn(unknown, line)

    def test_dropbear_may_not_forward_when_no_ring_needs_it(self):
        f = self.unlock_fleet()
        h = f.hosts["lychee"]
        self.assertIn("no-port-forwarding", unlock.authorized_line(h))

    def test_openssh_is_held_to_one_port_and_one_command(self):
        h = self.coreos_host()
        h.cfg["unlock"].update(via={"house": "http://127.0.0.1:7500"}, ssh=True, ssh_from=["192.0.2.0/24", "10.9.0.0/16"])
        line = unlock.authorized_line(h)
        self.assertTrue(line.startswith('restrict,port-forwarding,permitlisten="127.0.0.1:7500",'))
        self.assertIn('command="echo kiwi-unlock-connected; sleep 900"', line)
        self.assertIn('from="192.0.2.0/24,10.9.0.0/16"', line)

    def test_openssh_without_a_forwarded_ring_forwards_nothing(self):
        line = unlock.authorized_line(self.coreos_host())
        self.assertNotIn("port-forwarding", line)
        self.assertNotIn("permitlisten", line)

    def test_the_initramfs_sshd_allows_only_a_forced_command_and_remote_forwarding(self):
        for setting in ("PermitRootLogin forced-commands-only", "AllowTcpForwarding remote", "MaxAuthTries 3", "AllowAgentForwarding no"):
            self.assertIn(setting, unlock.PREPARE_DRACUT_SSHD)

    def test_ssh_from_is_refused_on_debian(self):
        f = self.unlock_fleet()
        f.hosts["lychee"].cfg["unlock"].update(ssh=True, ssh_from=["192.0.2.0/24"])
        self.assertTrue([e for e in f.hosts["lychee"].validate(self.roles) if "Dropbear has no from=" in e])

    def media_host(self):
        f = self.unlock_fleet()
        h = f.hosts["lychee"]
        os.remove(luks.key_file(h))
        h.cfg["unlock"].update(via={"infra": "http://127.0.0.1:7500"}, ssh=True)
        return f, h

    def test_debian_media_bring_ssh_for_the_first_boot(self):
        f, h = self.media_host()
        self.assertIn("dropbear-initramfs", debian_t.preseed(h, VERSION))
        late = debian_t.late_script(h)
        self.assertIn("cp $S/dropbear-authorized_keys $D/authorized_keys", late)
        self.assertLess(late.index("kiwi-unlock"), late.index("update-initramfs -u"))
        files = debian_t.iso_files(h, "#!/bin/sh\n")
        self.assertIn("command=", files["dropbear-authorized_keys"][0])
        self.assertEqual(files["kiwi-unlock-ip"][0], "IP=192.168.2.61::192.168.2.1:255.255.0.0::IFACE:off\n")
        self.assertIn('-p 2222 -s -j -T 3', files["dropbear.conf"][0])

    def test_media_without_ssh_stay_as_they_were(self):
        f = self.unlock_fleet()
        h = f.hosts["lychee"]
        self.assertNotIn("dropbear", debian_t.preseed(h, VERSION))
        self.assertNotIn("dropbear-authorized_keys", debian_t.iso_files(h, "#!/bin/sh\n"))

    def test_the_first_boot_opens_with_the_one_time_key_and_takes_the_host_key(self):
        f, h = self.media_host()
        write(luks.once_file(h), "onetimekey\n")
        self.assertTrue(luks.at_first_boot(h))
        seen = []

        def runner(argv, input=None, capture_output=True):
            seen.append((argv, input))
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        with mock.patch.object(remote, "_answers", lambda addr, port=22, timeout=2: True):
            unlock.type_key(h, runner=runner, sleep=lambda s: None)
        self.assertEqual(seen[0][1], b"onetimekey")
        self.assertIn("StrictHostKeyChecking=accept-new", seen[0][0])

    def test_after_the_first_apply_the_host_key_has_to_match(self):
        f, h = self.media_host()
        write(luks.once_file(h), "onetimekey\n")
        write(luks.key_file(h), "thediskkey\n")
        self.assertFalse(luks.at_first_boot(h))
        self.assertIn("StrictHostKeyChecking=yes", unlock._initramfs_ssh_argv(h, "192.0.2.1"))

    def test_a_machine_waiting_for_a_forwarded_ring_does_not_reboot_by_itself(self):
        f, h = self.tunnel_host()
        calls, a, b = self.fake_remote()
        with a, b, mock.patch.object(unlock, "_initramfs_ssh"):
            unlock.bind(h, "core@lychee", self.servers(f))
        self.assertTrue([c for c in calls if "disable --now kiwi-reboot-if-required.timer" in c[1]])

    def test_a_ring_of_its_own_reach_keeps_the_reboot_window(self):
        f = self.unlock_fleet()
        h = f.hosts["lychee"]
        h.cfg["unlock"].update(rings=["infra", "operator"], via={"operator": "http://127.0.0.1:7500"})
        self.assertFalse(unlock.forwarded(h))
        h.cfg["unlock"]["threshold"] = 2
        self.assertTrue(unlock.forwarded(h))

    def test_a_machine_on_the_lan_keeps_its_reboot_window(self):
        f = self.unlock_fleet()
        calls, a, b = self.fake_remote()
        with a, b:
            unlock.bind(f.hosts["lychee"], "core@lychee", self.servers(f))
        self.assertFalse([c for c in calls if "kiwi-reboot-if-required" in c[1]])

    def test_the_initramfs_brings_up_its_loopback(self):
        self.assertIn("ip link set lo up", unlock.LOOPBACK_HOOK)
        self.assertIn("kiwi-loopback", unlock.LOOPBACK_PATH)

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
        with self.assertRaisesRegex(KiwiError, "this computer cannot open it"):
            unlock.forwarded_ring(f.hosts["lychee"])

    def fake_podman(self, keys_exist=True):
        calls = []

        def run(argv, capture_output=True):
            calls.append(argv)
            if argv[1:3] == ["exec", "kiwi-tang-operator-5555"] and "tang-show-keys" in argv:
                return subprocess.CompletedProcess(argv, 0, b"OPTHUMB\n", b"")
            if "curl" in argv:
                return subprocess.CompletedProcess(argv, 0, b'{"payload":"op"}\n', b"")
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        return calls, run

    def operator_ring(self, f):
        calls, run = self.fake_podman()
        return calls, unlock.OperatorRing(f, run=run)

    def test_the_operator_ring_makes_its_keys_once_and_stays_on_loopback(self):
        f = self.unlock_fleet()
        calls, ring = self.operator_ring(f)
        with mock.patch.object(remote, "free_port", lambda: 5555), mock.patch.object(remote, "_answers", lambda a, p=22, timeout=2: True), mock.patch.object(unlock.atexit, "register"):
            self.assertEqual(ring.start(), 5555)
            self.assertEqual(ring.start(), 5555)
        started = [c for c in calls if "-d" in c]
        self.assertEqual(len(started), 1)
        self.assertIn("127.0.0.1:5555:7500", started[0])
        self.assertIn("%s:/db" % f.resolve_path("secrets/tang/operator"), started[0])
        made = [c for c in calls if any("tangd-keygen" in x for x in c)]
        self.assertEqual(len(made), 1)
        self.assertIn("chown -R 0:0 /db", made[0][-1])       # the store has to be able to read them
        self.assertEqual(ring.thumbprint(), "OPTHUMB")
        ring.stop()
        self.assertEqual(calls[-1], ["podman", "rm", "-f", "kiwi-tang-operator-5555"])

    def test_existing_keys_are_not_made_again(self):
        f = self.unlock_fleet()
        write(os.path.join(f.resolve_path("secrets/tang/operator"), "abc.jwk"), "{}")
        calls, ring = self.operator_ring(f)
        with mock.patch.object(remote, "free_port", lambda: 5555), mock.patch.object(remote, "_answers", lambda a, p=22, timeout=2: True), mock.patch.object(unlock.atexit, "register"):
            ring.start()
        self.assertFalse([c for c in calls if any("tangd-keygen" in x for x in c)])

    def test_the_operator_ring_is_forwarded_from_its_own_loopback(self):
        f = self.unlock_fleet()
        h = f.hosts["lychee"]
        h.cfg["unlock"].update(rings=["operator"], via={"operator": "http://127.0.0.1:7500"})
        _calls, ring = self.operator_ring(f)
        with mock.patch.object(remote, "free_port", lambda: 5555), mock.patch.object(remote, "_answers", lambda a, p=22, timeout=2: True), mock.patch.object(unlock.atexit, "register"):
            self.assertEqual(unlock._forward(h, {"operator": (ring, 0)})[1], "127.0.0.1:7500:127.0.0.1:5555")

    def test_the_operator_ring_needs_a_way_in(self):
        f = self.unlock_fleet()
        h = f.hosts["lychee"]
        h.cfg["unlock"]["rings"] = ["operator"]
        _calls, ring = self.operator_ring(f)
        with self.assertRaisesRegex(KiwiError, "unlock.via needs it"):
            unlock.pins(h, {"operator": (ring, 0)})

    def test_the_threshold_has_to_fit_the_rings(self):
        f = self.unlock_fleet(threshold=2)
        self.assertTrue([e for e in f.hosts["lychee"].validate(self.roles) if "unlock.threshold must be between 1" in e])


class TestKeepWireguard(Base):
    STATE = b'{"server":{"privateKey":"K","publicKey":"P"},"clients":{}}'

    def host(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {"m": {"role": "master", "master": self.master()}}})
        h = f.hosts["m"]
        rolesmod.resolve_settings(self.roles[h.role], h, self.ms)
        return f, h

    def run_with(self, out):
        sent = []

        def runner(argv, input=None, capture_output=True):
            sent.append((argv[-1], input))
            return subprocess.CompletedProcess(argv, 0, out, b"")
        return sent, runner

    def test_a_master_is_kept_and_only_when_it_changed(self):
        f, h = self.host()
        self.assertTrue(backup.carries_wireguard(h))
        _s, runner = self.run_with(self.STATE)
        self.assertTrue(backup.keep_wireguard(h, "core@m", runner=runner))
        path = f.resolve_path("secrets/wg-easy/m.json")
        self.assertEqual(util.read_bytes(path), self.STATE)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertFalse(backup.keep_wireguard(h, "core@m", runner=runner))

    def test_what_is_not_a_server_state_is_not_kept(self):
        f, h = self.host()
        for out in (b"", b"not json", b'{"server":{}}'):
            _s, runner = self.run_with(out)
            self.assertFalse(backup.keep_wireguard(h, "core@m", runner=runner))
        self.assertFalse(os.path.exists(f.resolve_path("secrets/wg-easy/m.json")))

    def test_it_goes_back_on_stdin_and_only_if_the_machine_has_none(self):
        f, h = self.host()
        write(f.resolve_path("secrets/wg-easy/m.json"), self.STATE.decode())
        sent, runner = self.run_with(b"restored\n")
        self.assertTrue(backup.put_back_wireguard(h, "core@m", runner=runner))
        self.assertEqual(sent[0][1], self.STATE)
        self.assertNotIn("privateKey", sent[0][0])
        self.assertIn("echo present; exit 0", sent[0][0])
        self.assertLess(sent[0][0].index("echo present"), sent[0][0].index("mv "))

    def test_a_machine_that_has_its_own_stays_as_it_is(self):
        f, h = self.host()
        write(f.resolve_path("secrets/wg-easy/m.json"), self.STATE.decode())
        _s, runner = self.run_with(b"present\n")
        self.assertFalse(backup.put_back_wireguard(h, "core@m", runner=runner))

    def test_no_copy_yet_is_nothing_to_do(self):
        _f, h = self.host()
        sent, runner = self.run_with(b"")
        self.assertFalse(backup.put_back_wireguard(h, "core@m", runner=runner))
        self.assertEqual(sent, [])


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
            # CoreOS: the initramfs answers on port 22 with its own key, and that is no reinstall
            def initramfs_key(address, port, known_hosts, runner=None):
                return (address, port, known_hosts) == ("a.kiwi", 22, "/x/secrets/known_hosts.initramfs")
            with mock.patch.object(remote, "shows_key_from", initramfs_key):
                with self.assertRaisesRegex(KiwiError, "waiting for its disk key, not reinstalled"):
                    remote.run("core@a.kiwi", "true", runner=changed)
        finally:
            remote.configure("")

    def test_a_device_gets_a_tunnel_size_that_fits_everywhere(self):
        have = "[Interface]\nPrivateKey = x\nAddress = 10.8.0.5/24\nMTU = 1376\n\n[Peer]\nPublicKey = y\n"
        self.assertIn("\nMTU = 1280\n\n[Peer]", wgeasy.with_mtu(have, 1280))
        self.assertNotIn("1376", wgeasy.with_mtu(have, 1280))
        for none in ("[Interface]\nPrivateKey = x\n\n[Peer]\nPublicKey = y\n", "[Interface]\nPrivateKey = x\n[Peer]\nPublicKey = y\n"):
            out = wgeasy.with_mtu(none, 1280)
            self.assertEqual(out.count("MTU = 1280"), 1)
            self.assertLess(out.index("MTU"), out.index("[Peer]"))
        with self.assertRaises(KiwiError):
            wgeasy.with_mtu("[Interface]\nPrivateKey = x\n", 1280)

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
            phone = fh.read()
        self.assertIn("Address = 10.8.3.2/24", phone)
        # a device roams: its tunnel is the size that fits a mobile network; the node keeps the master's
        self.assertIn("MTU = 1280\n", phone)
        self.assertNotIn("MTU = 1280", conf)
        rc, out, err = self.run_cli("enroll", f.path, "laptop", "--group", "devices", "--api", api.url, "--mtu", "1300")
        self.assertEqual(rc, 0, err)
        with open(os.path.join(self.tmp, "secrets", "devices", "laptop.conf")) as fh:
            self.assertIn("MTU = 1300\n", fh.read())
        rc, out, err = self.run_cli("enroll", f.path, "tablet", "--api", api.url, "--mtu", "1500")
        self.assertIn("--mtu is between 1280 and", err)
        self.assertEqual(api.by_id("id-4")["address"], "10.8.3.3")
        rc, out, err = self.run_cli("enroll", f.path, "router", "--address", "10.8.4.10", "--api", api.url)
        self.assertEqual(rc, 0, err)
        self.assertEqual(api.by_id("id-5")["address"], "10.8.4.10")
        # a lost phone: off the master, its config gone from the fleet; a machine is not removed that way
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "secrets", "devices", "phone.conf")))
        rc, out, err = self.run_cli("enroll", f.path, "phone", "--remove", "--api", api.url)
        self.assertEqual(rc, 0, err)
        self.assertNotIn("phone", [c["name"] for c in api.clients])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "secrets", "devices", "phone.conf")))
        rc, out, err = self.run_cli("enroll", f.path, "phone", "--remove", "--api", api.url)
        self.assertIn("no device called phone", err)
        rc, out, err = self.run_cli("enroll", f.path, "sh3", "--remove", "--api", api.url)
        self.assertIn("a machine of the fleet", err)
        rc, out, err = self.run_cli("enroll", f.path, "x", "--group", "nope", "--api", api.url)
        self.assertEqual(rc, 1)
        self.assertIn("no client group 'nope'", err)
        self.assertIn("devices", err)

    def test_a_machine_leaves_for_good(self):
        api = FakeWgEasy("w")
        self.addCleanup(api.stop)
        f = self.manage_fleet()
        self.assertEqual(self.run_cli("enroll", f.path, "sh3", "--api", api.url)[0], 0)
        conf = os.path.join(self.tmp, "secrets", "sh3.kiwi.conf")
        self.assertTrue(os.path.isfile(conf))
        # the master of other machines stays; and nothing is taken for granted without a person
        rc, out, err = self.run_cli("remove", f.path, "gate", "--yes", "--api", api.url)
        self.assertIn("gate is the master of 1 other machine(s)", err)
        rc, out, err = self.run_cli("remove", f.path, "sh3", "--api", api.url)
        self.assertIn("add --yes to go ahead", err)
        self.assertTrue(os.path.isfile(conf))
        rc, out, err = self.run_cli("remove", f.path, "sh3", "--yes", "--api", api.url)
        self.assertEqual(rc, 0, err)
        self.assertEqual([c["name"] for c in api.clients], [])          # off the VPN: its key no longer works
        self.assertFalse(os.path.exists(conf))
        self.assertEqual(sorted(config.Fleet.load(f.path).hosts), ["gate"])
        self.assertEqual(self.run_cli("validate", f.path)[0], 0)
        rc, out, err = self.run_cli("remove", f.path, "sh3", "--yes", "--api", api.url)
        self.assertIn("no such machine: sh3", err)
        # the last machine stays: a network without machines is not a network
        rc, out, err = self.run_cli("remove", f.path, "gate", "--yes", "--api", api.url)
        self.assertIn("gate is the last machine of this network", err)
        self.assertEqual(sorted(config.Fleet.load(f.path).hosts), ["gate"])

    def test_a_host_is_taken_out_as_text_and_the_comments_of_the_others_stay(self):
        path = os.path.join(self.tmp, "f.yaml")
        write(path, "defaults:\n  domain: home\nhosts:\n"
                    "  # the master, rented\n  strudel:\n    role: master\n    master:\n      modules: [vpn-server]\n"
                    "  # the one with the files\n  papaya:\n    role: node\n    # its data disk\n    node:\n      preset: cloud\n\n"
                    "  # a board\n  lime:\n    role: node\n    node: { preset: minimal }\n")
        setupmod.remove_host(path, "papaya")
        text = open(path).read()
        self.assertNotIn("papaya", text)
        self.assertNotIn("its data disk", text)
        self.assertNotIn("the one with the files", text.split("lime")[0].split("strudel")[1])
        self.assertIn("  # the master, rented\n  strudel:", text)
        self.assertIn("  # a board\n  lime:", text)            # the comment above the next host is that host's
        setupmod.remove_host(path, "lime")
        setupmod.remove_host(path, "strudel")
        import yaml
        self.assertEqual(yaml.safe_load(open(path).read())["hosts"], {})
        with self.assertRaises(KiwiError):
            setupmod.remove_host(path, "strudel")

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
class StoreBase(Base):
    """A fleet whose secrets can become a store: a fake keyring, its own runtime directory."""
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


@unittest.skipUnless(have("age") and have("age-keygen"), "age not installed")
class TestStore(StoreBase):
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

    def on(self, computer):
        """Switch to another computer: its own keyring, the same fleet folder."""
        kr = os.path.join(self.tmp, "keyring-" + computer)
        os.makedirs(kr, exist_ok=True)
        os.environ["FAKE_KEYRING"] = kr

    def test_another_computer_joins_and_the_first_one_can_leave(self):
        self.on("desktop")
        self.init()
        self.on("laptop")
        rc, out, err = self.run_cli("secrets", "join", self.f.path, "laptop")
        self.assertEqual(rc, 0, err)
        key = re.search(r"secrets add \S+ laptop (age1\S+)", out).group(1)
        rc, _out, err = self.run_cli("secrets", "join", self.f.path, "laptop")
        self.assertNotEqual(rc, 0)                                      # its keyring has a key for it already
        self.on("desktop")
        rc, _out, err = self.run_cli("secrets", "add", self.f.path, "laptop", key)
        self.assertEqual(rc, 0, err)
        rc, out, err = self.run_cli("secrets", "status", self.f.path)
        self.assertIn("opens with: desktop, recovery, laptop", out)
        self.assertEqual(self.run_cli("secrets", "lock", self.f.path)[0], 0)
        self.on("laptop")
        rc, _out, err = self.run_cli("secrets", "unlock", self.f.path)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.read_secret("deep", "key"), "private")
        # the first computer leaves: the others still find their keys under the same store id
        rc, _out, err = self.run_cli("secrets", "remove", self.f.path, "desktop")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.run_cli("secrets", "lock", self.f.path)[0], 0)
        self.assertEqual(self.run_cli("secrets", "unlock", self.f.path)[0], 0)
        rc, out, _err = self.run_cli("secrets", "status", self.f.path)
        self.assertIn("opens with: recovery, laptop", out)
        self.assertNotEqual(self.run_cli("secrets", "add", self.f.path, "x", "age1nope")[0], 0)
        self.assertNotEqual(self.run_cli("secrets", "remove", self.f.path, "nobody")[0], 0)

    def test_a_copy_saved_by_another_computer_is_not_overwritten(self):
        self.init()
        with open(os.path.join(self.tmp, "secrets.age"), "ab") as fh:
            fh.write(b"\n")                                            # another computer saved it meanwhile
        write(os.path.join(self.secrets, "new"), "changed here")
        rc, _out, err = self.run_cli("secrets", "lock", self.f.path)
        self.assertNotEqual(rc, 0)
        self.assertIn("changed on disk since this computer opened it", err)
        self.assertTrue(os.path.isfile(os.path.join(self.secrets, "new")))   # what changed here is still there

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
        self.memory = 2048
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
                        "serverLiveInfo": {"state": fake.state, "uefi": False, "cpuCount": 2, "maxServerMemoryInMiB": fake.memory,
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


class TestIsoName(unittest.TestCase):
    def test_every_upload_has_a_name_of_its_own(self):
        host = mock.Mock()
        host.name = "x"
        with mock.patch.object(cli.time, "time", side_effect=[1000, 1001]):
            self.assertNotEqual(cli._iso_key(host), cli._iso_key(host))


class TestVps(Base):
    def setUp(self):
        super().setUp()
        key = mock.patch.object(cli, "_iso_key", lambda host: "kiwi-%s.iso" % host.name)
        key.start()
        self.addCleanup(key.stop)
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
        if stack == 0:      # a bare ISO says so beside itself: the rendered role lives in the store's tmpfs
            write(os.path.join(self.tmp, "output", "x", "x.iso.bare"), "bare\n")

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

    def coreos_vps(self):
        return self.fleet({"defaults": self.base_defaults(), "hosts": {
            "x": {"role": "bare", "target": "coreos", "disk": "/dev/sda", "vps": {"server": 7},
                  "unlock": {"rings": ["operator"], "via": {"operator": "http://127.0.0.1:7500"}}}}})

    def test_a_coreos_disk_is_not_encrypted_on_too_little_memory(self):
        f = self.coreos_vps()
        self.logged_in()
        self.built()
        self.api.memory = 2048
        rc, out, err = self.run_cli("vps", "install", f.path, "x", "--yes")
        self.assertNotEqual(rc, 0)
        self.assertIn("about 4 GiB", err)
        self.assertNotIn(("POST", "/users/94774/isos/kiwi-x.iso"), self.api.seen())

    def test_enough_memory_goes_ahead(self):
        f = self.coreos_vps()
        self.logged_in()
        self.built()
        self.api.memory = 8192
        rc, out, err = self.run_cli("vps", "install", f.path, "x", "--yes")
        self.assertEqual(rc, 0, err)

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

    def test_a_ram_master_boots_its_image_and_keeps_it_attached(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {
            "x": {"role": "bare", "target": "coreos-ram", "vps": {"server": 7}}}})
        self.logged_in()
        self.built()
        rc, out, err = self.run_cli("vps", "install", f.path, "x", "--yes")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.api.seen(), [
            ("POST", "/users/94774/isos/kiwi-x.iso"), ("PUT", "/s3/kiwi-x.iso"),
            ("PATCH", "/servers/7"), ("POST", "/servers/7/iso"), ("PATCH", "/servers/7")])  # off, attach, on: no detach
        self.assertIn("kiwi-server watch", out)

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
        self.assertEqual(notes[0], "kn-cloud is still starting")
        self.assertTrue(notes[1].startswith("disk /var 10% of"), notes)

    def test_held_backups_say_why(self):
        base = "now=100000\napplied=x\nos=Debian\nclock=yes\nuptime=99999\nbackup=0\n"
        state, problems, _ = facts.assess(facts.parse(base + "backup_held=backup: lychee holds snapshots of sorbet.home and this machine made none of them \n", local_now=100000))
        self.assertEqual(state, "warn")
        self.assertEqual([p["text"] for p in problems], ["backups are held: lychee holds snapshots of sorbet.home and this machine made none of them"])
        state, problems, _ = facts.assess(facts.parse(base, local_now=100000))
        self.assertEqual([p["text"] for p in problems], ["no backup has succeeded yet"])

    def test_a_certificate_that_is_not_being_renewed_is_a_problem(self):
        base = "now=1000\napplied=x\nos=Debian\nclock=yes\nuptime=9999\n"
        day = 86400

        def assessed(left):
            return facts.assess(facts.parse(base + "certificate=hub.example.org %d\n" % (1000 + left * day), local_now=1000))
        state, problems, notes = assessed(60)
        self.assertEqual((state, problems), ("ok", []))
        self.assertEqual(notes, ["certificates good for 60 days"])
        self.assertEqual(assessed(20)[0], "warn")
        self.assertIn("should have been renewed", assessed(20)[1][0]["text"])
        self.assertEqual(assessed(3)[0], "bad")
        self.assertIn("has expired", assessed(-1)[1][0]["text"])
        # a file the probe could not read says nothing rather than something wrong
        f = facts.parse(base + "certificate=hub.example.org \n", local_now=1000)
        self.assertEqual(facts.assess(f), ("ok", [], []))

    def test_a_healthy_machine_has_nothing_to_say_but_its_disks(self):
        f = facts.parse("applied=x\nos=Debian\nclock=yes\nuptime=5\nservice=a active\ncontainer=a|running|Up 1 hour|i\n"
                        "disk=/ 18874368 2621440\ndisk=/mnt/data 1000000000 4000000\nmem=1000 900\n")
        self.assertEqual(facts.assess(f), ("ok", [], ["disk / 13% of 18 GB, /mnt/data 0% of 953.7 GB"]))

    def test_each_backup_server_has_its_copy(self):
        now = 1000000
        base = "now=%d\napplied=x\nos=Debian\nclock=yes\nuptime=99999\nbackup=%d\n" % (now, now - 3 * 3600)
        f = facts.parse(base + "backup_copy=backup.lychee.home %d\nbackup_copy=backup.papaya.home %d\n"
                        % (now - 3 * 3600, now - 3 * 3600), local_now=now)
        state, problems, notes = facts.assess(f)
        self.assertEqual((state, problems), ("ok", []))
        self.assertIn("copies: backup.lychee.home 3 h ago, backup.papaya.home 3 h ago", notes)
        # one node down for two days while the other still gets every copy
        f = facts.parse(base + "backup_copy=backup.lychee.home %d\nbackup_copy=backup.papaya.home %d\n"
                        % (now - 3 * 3600, now - 50 * 3600), local_now=now)
        state, problems, _ = facts.assess(f)
        self.assertEqual(state, "warn")
        self.assertEqual([p["text"] for p in problems], ["the last copy at backup.papaya.home is 50 hours old"])

    def test_a_backup_node_says_what_it_keeps_and_how_long_it_lasts(self):
        base = "applied=x\nos=Debian\nclock=yes\nuptime=5\ndisk=/mnt/backups 104857600 52428800\n"
        store = "backup_store=/mnt/backups 52428800\n"          # 50 GB free

        def assessed(week_kib):
            return facts.assess(facts.parse(base + store + "backup_repo=papaya.home 2097152 34 %d\n"
                                            "backup_repo=sorbet.home 122880 12 0\n" % week_kib))
        state, problems, notes = assessed(1048576)           # 1 GB a week: 50 weeks
        self.assertEqual((state, problems), ("ok", []))
        self.assertIn("backups here: papaya.home 2 GB in 34 snapshots, sorbet.home 120 MB in 12 snapshots; "
                      "50 GB free, about 50 weeks at last week's rate", notes)
        state, problems, _ = assessed(10 * 1048576)          # 10 GB a week: 35 days
        self.assertEqual(state, "ok")
        state, problems, _ = assessed(15 * 1048576)          # 23 days
        self.assertEqual(state, "warn")
        self.assertIn("fill /mnt/backups in about 23 days", problems[0]["text"])
        self.assertIn("kiwi-server prune", problems[0]["text"])
        self.assertEqual(assessed(60 * 1048576)[0], "bad")   # 5 days
        _, _, notes = facts.assess(facts.parse(base + store))
        self.assertIn("backups here: none yet; 50 GB free", notes)

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

    def assessed(self, extra, uptime=86400):
        now = 2000000000
        data = "now=%d\napplied=x\nos=Debian\nuptime=%d\nclock=yes\n%s\n" % (now, uptime, extra)
        f = facts.parse(data, local_now=now)
        return f, facts.assess(f)

    def test_a_fresh_backup_is_a_note(self):
        f, (state, problems, notes) = self.assessed("backup=%d" % (2000000000 - 7200))
        self.assertEqual(f["backup"]["age"], 7200)
        self.assertEqual(state, "ok")
        self.assertIn("last backup 2 h ago", notes)

    def test_a_missed_night_is_a_warning(self):
        _f, (state, problems, _n) = self.assessed("backup=%d" % (2000000000 - 40 * 3600))
        self.assertEqual(state, "warn")
        self.assertIn("40 hours old", problems[0]["text"])

    def test_a_week_without_a_backup_is_bad(self):
        _f, (state, problems, _n) = self.assessed("backup=%d" % (2000000000 - 8 * 86400))
        self.assertEqual(state, "bad")
        self.assertIn("8 days old", problems[0]["text"])

    def test_a_backup_that_never_ran_warns_once_the_machine_has_been_up(self):
        self.assertEqual(self.assessed("backup=0")[1][0], "warn")
        state, _p, notes = self.assessed("backup=0", uptime=60)[1]
        self.assertEqual(state, "ok")
        self.assertIn("no backup yet", notes)

    def test_a_machine_without_the_module_says_nothing_about_backups(self):
        f, (_s, problems, notes) = self.assessed("")
        self.assertNotIn("backup", f)
        self.assertFalse([x for x in problems + notes if "backup" in str(x)])

    def test_the_disk_is_reported_as_it_is(self):
        self.assertIn("the disk is not encrypted", self.assessed("luks=none")[1][2])
        self.assertIn("the disk opens only with a typed key", self.assessed("luks=0")[1][2])
        f, (_s, _p, notes) = self.assessed("luks=1")
        self.assertEqual(f["encryption"], "bound")
        self.assertFalse([n for n in notes if "disk" in n])

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
        with mock.patch.object(remote, "_answers", lambda a, *p: a == "192.168.9.9"):
            self.assertEqual(remote.reach(self.host()), "core@192.168.9.9")

    @unittest.skipUnless(have("ssh-keygen"), "ssh-keygen")
    def test_an_address_never_met_gets_nothing_sent_to_it(self):
        self.on_record("10.0.0.1")
        with mock.patch.object(remote, "_answers", lambda a, *p: True):
            self.assertEqual(remote.reach(self.host()), "core@a.kiwi")

    @unittest.skipUnless(have("ssh-keygen"), "ssh-keygen")
    def test_a_moved_ssh_port_is_found_by_the_record_of_the_address(self):
        """ssh takes a key on record for the plain address on any port and adds no line
        for the port: after sshd moved, the address is still one the fleet has met."""
        self.assertFalse(remote._on_record("192.168.9.9", 41022))
        self.on_record("192.168.9.9")
        self.assertTrue(remote._on_record("192.168.9.9", 41022))
        self.assertFalse(remote._on_record("192.168.9.8", 41022))

    def test_an_address_given_wins(self):
        self.assertEqual(remote.reach(self.host(), "root@1.2.3.4"), "root@1.2.3.4")


class TestConnect(Base):
    """This computer on the network (connect.py): the config becomes a NetworkManager
    connection that carries only the network, and the browsers trust its certificates."""
    def test_the_connection_carries_only_the_network(self):
        from kiwiserver import connect as connectmod
        conf = os.path.join(self.tmp, "desk.conf")
        write(conf, "[Interface]\nPrivateKey = x\nAddress = 10.9.3.2/24\nDNS = 10.9.0.1\nMTU = 1280\n\n[Peer]\nPublicKey = y\n"
                    "AllowedIPs = 10.9.0.0/16\nEndpoint = 203.0.113.7:51820\n")
        calls, seen = [], {}

        def runner(argv, **kw):
            calls.append(argv[1:])
            if argv[1:3] == ["connection", "import"]:
                seen["file"] = os.path.basename(argv[-1])
                with open(argv[-1]) as fh:
                    seen["text"] = fh.read()
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.tmp}):
            name = connectmod.up(conf, "home", runner=runner)
        self.assertEqual((name, seen["file"]), ("kiwi-home", "kiwi-home.conf"))        # the file's name names the connection
        self.assertIn("AllowedIPs = 10.9.0.0/16", seen["text"])
        self.assertEqual(calls[0], ["connection", "delete", "kiwi-home"])               # an older one of this network goes
        self.assertEqual(calls[2], ["connection", "modify", "kiwi-home", "ipv4.dns-search", "~home", "ipv4.never-default", "yes",
                                    "ipv6.method", "disabled", "connection.autoconnect", "yes"])
        self.assertEqual(calls[3], ["connection", "up", "kiwi-home"])
        self.assertEqual([n for n in os.listdir(self.tmp) if n.startswith("kiwi-connect-")], [])   # no copy of the key is left
        self.assertEqual(connectmod.connection_name("my-long-network-name.example"), "kiwi-mylongnetw")   # 15 at most

    def test_a_refusal_of_networkmanager_is_said(self):
        from kiwiserver import connect as connectmod
        conf = os.path.join(self.tmp, "desk.conf")
        write(conf, "[Interface]\n")

        def runner(argv, **kw):
            bad = argv[1:3] == ["connection", "import"]
            return types.SimpleNamespace(returncode=1 if bad else 0, stdout="", stderr="Error: failed to import: no key\n" if bad else "")
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.tmp}), self.assertRaises(KiwiError) as e:
            connectmod.up(conf, "home", runner=runner)
        self.assertIn("failed to import", str(e.exception))

    def test_the_browsers_of_this_user_are_found_and_told(self):
        from kiwiserver import connect as connectmod
        home = os.path.join(self.tmp, "home")
        for d in (".pki/nssdb", ".mozilla/firefox/abc.default", ".var/app/org.mozilla.firefox/.mozilla/firefox/xyz.default-release"):
            write(os.path.join(home, d, "cert9.db"), "")
        write(os.path.join(home, ".mozilla/firefox/profiles.ini"), "")                  # not a profile: no store in it
        stores = connectmod.browser_stores(home)
        self.assertEqual(len(stores), 3)
        ran = []

        class Tc:
            def how(self, tool):
                return "container", ""

            def run(self, argv, mounts=(), **kw):
                ran.append((argv, list(mounts), kw))
                return types.SimpleNamespace(returncode=0)
        ca = os.path.join(self.tmp, "ca", "kiwiCA.pem")
        write(ca, "-----BEGIN CERTIFICATE-----\n")
        write(os.path.join(self.tmp, "ca", "kiwiCA.key"), "the key")
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.tmp}):
            took, why = connectmod.trust(ca, "home", Tc(), home)
        self.assertEqual((sorted(took), why), (sorted(stores), ""))
        argv, mounts, kw = ran[0]
        self.assertEqual(argv[:7], ["certutil", "-d", "sql:" + stores[0], "-A", "-t", "C,,", "-n"])
        self.assertEqual(kw.get("relabel"), False)                       # a browser's folder is not ours to relabel
        self.assertNotIn(os.path.join(self.tmp, "ca"), mounts)           # the certificate goes in alone, never its key's folder

        class NoTc:
            def how(self, tool):
                return None, "certutil is not installed and the toolchain image is missing — run: kiwi-server toolchain build"
        self.assertEqual(connectmod.trust(ca, "home", NoTc(), home), ([], NoTc().how("certutil")[1]))
        self.assertEqual(connectmod.trust(ca, "home", Tc(), os.path.join(self.tmp, "nobody")), ([], ""))   # no browser yet


class TestAddress(Base):
    """A machine's fixed address after it was installed (address.py): asked, changed by the
    machine itself, found at the new place."""
    def host(self, network):
        return types.SimpleNamespace(name="lime", cfg={"network": network}, admin={"user": "core"})

    def test_what_the_fleet_file_wants(self):
        self.assertIsNone(addressmod.wanted(self.host({"dhcp": True})))
        self.assertIsNone(addressmod.wanted(self.host({"dhcp": False})))
        self.assertEqual(addressmod.wanted(self.host({"dhcp": False, "address": "192.168.1.30", "gateway": "192.168.1.1",
                                                      "dns": ["9.9.9.9"]})), ("192.168.1.30/24", "192.168.1.1", ["9.9.9.9"]))

    def settle(self, has, answers_new=True, can=True):
        ran, said = [], []

        def run(tgt, script, **kw):
            ran.append((tgt, script))
            if script.startswith("ip -o"):
                return has.encode()
            if script == "/usr/local/bin/kiwi-stack address" and not can:
                raise KiwiError("core@192.168.1.112: kiwi-stack: the network here is not run by systemd-networkd: "
                                "this machine's address is set where it was installed")
            if script.startswith("journalctl"):
                return b"kiwi-stack: 192.168.1.30 is in use by another device: nothing changed\n"
            return b""
        h = self.host({"dhcp": False, "address": "192.168.1.30/24", "gateway": "192.168.1.1", "dns": ["9.9.9.9"]})
        known = os.path.join(self.tmp, "known_hosts")
        write(known, "192.168.1.112 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJtmZ2FrZWZha2VmYWtlZmFrZWZha2VmYWtlZmFrZWZha2Vm\n")
        remote.configure(known)
        self.addCleanup(remote.configure, "")
        with mock.patch.object(remote, "run", run), mock.patch.object(remote, "_answers", lambda a, p=22: answers_new), \
                mock.patch.object(remote, "run_ok", lambda t: True):
            got = addressmod.settle(h, "core@192.168.1.112", say=said.append, warn=said.append, wait=0.2, sleep=lambda s: time.sleep(0.05))
        return got, ran, " ".join(said), known

    def test_it_already_has_the_address(self):
        got, ran, said, _k = self.settle("192.168.1.30/24\n")
        self.assertEqual((got, len(ran), said), ("core@192.168.1.112", 1, ""))

    @unittest.skipUnless(have("ssh-keygen"), "ssh-keygen")
    def test_a_board_on_the_routers_address_takes_the_fixed_one(self):
        got, ran, said, known = self.settle("192.168.1.112/24\n")
        self.assertEqual(got, "core@192.168.1.30")
        self.assertIn("systemd-run --collect --quiet --unit=kiwi-address /usr/local/bin/kiwi-stack address "
                      "192.168.1.30/24 192.168.1.1 9.9.9.9", ran[-1][1])
        self.assertIn("it answers at 192.168.1.30 now", said)
        with open(known) as fh:          # the same machine, the same key: on record for the new address
            self.assertIn("192.168.1.30 ssh-ed25519 AAAAC3", fh.read())

    def test_a_machine_that_cannot_change_it_says_so(self):
        got, ran, said, _k = self.settle("192.168.1.112/24\n", can=False)
        self.assertEqual(got, "core@192.168.1.112")
        self.assertIn("lime has 192.168.1.112/24 and the fleet file says 192.168.1.30/24", said)
        self.assertIn("set where it was installed", said)
        self.assertFalse(any("systemd-run" in s for _t, s in ran))

    def test_an_address_that_does_not_work_is_reported_with_the_machines_reason(self):
        got, ran, said, _k = self.settle("192.168.1.112/24\n", answers_new=False)
        self.assertEqual(got, "core@192.168.1.112")
        self.assertIn("lime did not take 192.168.1.30/24: 192.168.1.30 is in use by another device", said)


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
        self.assertEqual([m["name"] for m in master["module_schemas"]], ["vpn-client", "vpn-server", "dns", "tor", "reverse-proxy", "fail2ban"])
        # the fields every machine has, for a front end that builds its form from this
        self.assertEqual(data["base"], json.loads(json.dumps(formmod.BASE_GROUPS)))
        self.assertEqual(data["base"][0][1][0]["path"], ["hostname"])
        rc, out, _ = self.run_cli("modules", "--porcelain")
        self.assertEqual(len(json.loads(out)["modules"]), 23)
        rc, out, _ = self.run_cli("targets", "--porcelain")
        self.assertEqual(set(json.loads(out)["targets"]), {"coreos", "ucore", "debian", "armbian", "coreos-ram"})

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
        rc, out, err = self.run_cli("init", dest, "--example")
        self.assertEqual(rc, 0, err)
        self.assertTrue(os.path.isfile(dest))
        rc, _, _ = self.run_cli("init", dest, "--example")
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



@unittest.skipUnless(have("age") and have("age-keygen"), "age not installed")
class TestLogins(Base):
    """`kiwi-server logins`: what a person types to get into each service of each machine."""
    run_cli = TestCli.run_cli

    def network(self, **hosts):
        d = {"gate": {"role": "master", "target": "debian", "master": self.master()},
             "m1": {"role": "node", "node": self.node_gw(preset="gateway")},
             # nobody wrote Nextcloud's password down: it comes from the seed
             "sh3": {"role": "node", "node": self.node_cloud(preset="cloud", cloud={})},
             "lab": {}}
        d.update(hosts)
        return self.fleet({"defaults": self.base_defaults(), "hosts": d})

    def settings(self, path, name):
        """What the machine's modules are set to, generated secrets among them."""
        h = config.Fleet.load(path).hosts[name]
        rolesmod.resolve_settings(self.roles[h.role], h, self.ms)
        return h.module_settings

    def logins(self, *args):
        rc, out, err = self.run_cli("logins", *args, "--porcelain")
        self.assertEqual(rc, 0, err)
        return {h["name"]: h for h in json.loads(out)["hosts"]}, err

    def test_a_gateway_with_a_fixed_address_says_how_the_home_uses_it(self):
        f = self.network(m1={"role": "node", "node": self.node_gw(preset="gateway"),
                             "network": {"dhcp": False, "address": "192.168.1.30/24", "gateway": "192.168.1.1"}})
        hosts, _err = self.logins(f.path)
        last = hosts["m1"]["logins"][-1]
        self.assertEqual((last["label"], last["address"], last["password"]), ("Your home through this machine", "", ""))
        self.assertIn("uses 192.168.1.30 as its DNS server and as its gateway", last["note"])
        self.assertNotIn("Your home through this machine", [x["label"] for x in hosts["gate"]["logins"]])   # not a master

    def test_every_machine_lists_its_services_with_user_name_and_password(self):
        f = self.network()
        hosts, _err = self.logins(f.path)
        self.assertEqual(list(hosts), ["gate", "m1", "sh3", "lab"])
        self.assertEqual(hosts["sh3"]["hostname"], "sh3.kiwi")
        self.assertEqual(hosts["lab"]["logins"], [])                     # a bare machine: nothing to sign in to
        rows = {n: [(x["module"], x["label"], x["address"], x["user"]) for x in h["logins"]] for n, h in hosts.items()}
        self.assertEqual(rows["gate"], [("vpn-server", "VPN devices (wg-easy)", "https://wg.gate.kiwi", ""),
                                        ("dns", "Ad blocker (Pi-hole)", "https://pihole.gate.kiwi/admin", "")])
        self.assertEqual(rows["m1"], [("dns", "Ad blocker (Pi-hole)", "https://pihole.m1.kiwi/admin", ""),
                                      ("downloader", "Downloads (Transmission)", "https://dl.m1.kiwi", "user"),
                                      ("downloader", "Downloads (JDownloader)", "https://jd.m1.kiwi", "user"),
                                      ("portainer", "Containers (Portainer)", "https://portainer.m1.kiwi", "admin"),
                                      ("sftp", "File transfer (SFTP)", "sftp://m1.kiwi:2224", "user")])
        self.assertEqual(rows["sh3"], [("cloud", "Files (Nextcloud)", "https://cloud.sh3.kiwi", "admin"),
                                       ("vault", "Passwords (Vaultwarden)", "https://vault.sh3.kiwi", ""),
                                       ("portainer", "Containers (Portainer)", "https://portainer.sh3.kiwi", "admin")])
        # the passwords are the ones the machines' settings resolve to, written down or from the seed
        want = {n: self.settings(f.path, n) for n in ("gate", "m1", "sh3")}
        got = {n: {x["label"]: x["password"] for x in h["logins"]} for n, h in hosts.items()}
        self.assertEqual(got["gate"], {"VPN devices (wg-easy)": want["gate"]["vpn-server"]["wg_password"],
                                       "Ad blocker (Pi-hole)": want["gate"]["dns"]["pihole_password"]})
        self.assertEqual(got["m1"], {"Ad blocker (Pi-hole)": want["m1"]["dns"]["pihole_password"],
                                     "Downloads (Transmission)": want["m1"]["downloader"]["transmission_password"],
                                     "Downloads (JDownloader)": want["m1"]["downloader"]["transmission_password"],
                                     "Containers (Portainer)": want["m1"]["portainer"]["admin_password"],
                                     "File transfer (SFTP)": want["m1"]["sftp"]["sftp_password"]})
        self.assertEqual(got["sh3"], {"Files (Nextcloud)": want["sh3"]["cloud"]["admin_password"], "Passwords (Vaultwarden)": "",
                                      "Containers (Portainer)": want["sh3"]["portainer"]["admin_password"]})
        self.assertEqual(len(got["sh3"]["Files (Nextcloud)"]), 32)       # generated
        self.assertNotEqual(got["sh3"]["Containers (Portainer)"], got["m1"]["Containers (Portainer)"])
        for h in hosts.values():
            for x in h["logins"]:
                self.assertEqual(sorted(x), ["address", "has_password", "label", "module", "note", "password", "user"])
                self.assertEqual(x["has_password"], bool(x["password"]))
                self.assertTrue(x["password"] or x["note"], x["label"])   # no password: a word on how to get in
        # one machine only
        hosts, _err = self.logins(f.path, "sh3")
        self.assertEqual(list(hosts), ["sh3"])
        rc, _out, err = self.run_cli("logins", f.path, "nobody")
        self.assertEqual(rc, 1)
        self.assertIn("no such host", err)

    def test_what_is_off_is_not_listed(self):
        f = self.network(
            m1={"role": "node", "node": self.node_gw(preset="gateway", downloader={
                "download_dir": "/mnt/dl", "jdownloader": False, "transmission_user": "anna"})},
            sh3={"role": "node", "node": self.node_cloud(preset="cloud", modules=["vpn-client", "reverse-proxy", "cloud"])})
        hosts, _err = self.logins(f.path)
        labels = {n: [x["label"] for x in h["logins"]] for n, h in hosts.items()}
        self.assertEqual(labels["sh3"], ["Files (Nextcloud)"])           # no vault, no portainer on it
        self.assertNotIn("Downloads (JDownloader)", labels["m1"])
        self.assertEqual([x["user"] for x in hosts["m1"]["logins"] if x["label"] == "Downloads (Transmission)"], ["anna"])
        # JDownloader with a login of its own
        f = self.network(m1={"role": "node", "node": self.node_gw(preset="gateway", downloader={
            "download_dir": "/mnt/dl", "jd_web_user": "ben", "jd_web_password": "jd-own"})})
        jd = next(x for x in self.logins(f.path, "m1")[0]["m1"]["logins"] if x["label"] == "Downloads (JDownloader)")
        self.assertEqual((jd["user"], jd["password"]), ("ben", "jd-own"))

    def test_the_list_a_person_reads(self):
        f = self.network()
        rc, out, err = self.run_cli("logins", f.path)
        self.assertEqual(rc, 0, err)
        lines = out.splitlines()
        self.assertEqual([x for x in lines if x and not x.startswith(" ")], ["gate", "m1", "sh3"])     # lab has nothing
        at = lines.index("m1")
        head, row = lines[at + 1], next(x for x in lines[at:] if "Downloads (Transmission)" in x)
        self.assertEqual(head.split(), ["SERVICE", "ADDRESS", "USER", "NAME", "PASSWORD"])
        self.assertEqual(row.split(), ["Downloads", "(Transmission)", "https://dl.m1.kiwi", "user", "t"])
        self.assertEqual((row.index("https://"), row.index(" user ") + 1, row.rindex("t")),
                         (head.index("ADDRESS"), head.index("USER NAME"), head.index("PASSWORD")))          # in columns
        pihole = next(i for i, x in enumerate(lines[at:]) if "Ad blocker (Pi-hole)" in x) + at
        self.assertEqual(lines[pihole].split()[-2:], ["-", "p"])                                          # no user name
        self.assertEqual(lines[pihole + 1].strip(), "Pi-hole asks for the password only.")
        vault = next(x for x in lines if "Passwords (Vaultwarden)" in x)
        self.assertEqual(vault.split()[-2:], ["-", "-"])
        self.assertIn(self.settings(f.path, "sh3")["cloud"]["admin_password"], out)
        rc, out, _err = self.run_cli("logins", f.path, "lab")
        self.assertEqual((rc, out.strip()), (0, ":: nothing to sign in to: lab runs no service with a login"))

    def test_a_machine_that_is_not_on_the_vpn_yet_is_left_out(self):
        f = self.network(new={"role": "node", "node": {"preset": "cloud", "vpn-client": {"wireguard_config": "secrets/new.kiwi.conf"}}})
        hosts, err = self.logins(f.path)
        self.assertEqual(hosts["new"]["logins"], [])
        self.assertIn("not enrolled yet", hosts["new"]["error"])
        self.assertIn("warning: left out: new: not enrolled yet", err)
        self.assertEqual(len(hosts["sh3"]["logins"]), 3)                 # the others are still listed
        rc, out, err = self.run_cli("logins", f.path, "new")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("not enrolled yet", err)

    def test_list_and_the_schema_carry_them_without_a_password(self):
        f = self.network()
        secrets = self.settings(f.path, "sh3")
        rc, out, err = self.run_cli("list", f.path, "--porcelain")
        self.assertEqual(rc, 0, err)
        hosts = {h["name"]: h for h in json.loads(out)["hosts"]}
        self.assertEqual([(x["label"], x["address"], x["user"], x["has_password"]) for x in hosts["sh3"]["logins"]], [
            ("Files (Nextcloud)", "https://cloud.sh3.kiwi", "admin", True),
            ("Passwords (Vaultwarden)", "https://vault.sh3.kiwi", "", False),
            ("Containers (Portainer)", "https://portainer.sh3.kiwi", "admin", True)])
        self.assertEqual(hosts["lab"]["logins"], [])
        for h in hosts.values():
            for x in h["logins"]:
                self.assertNotIn("password", x)
            # a login's site is one of the machine's sites
            for x in h["logins"]:
                if x["address"].startswith("https://"):
                    self.assertIn(urllib.parse.urlsplit(x["address"]).hostname, h["sites"])
        for generated in (secrets["cloud"]["admin_password"], secrets["portainer"]["admin_password"]):
            self.assertNotIn(generated, out)
        rc, out, _err = self.run_cli("roles", "--porcelain")
        modules = {m["name"]: m["logins"] for m in json.loads(out)["modules"]}
        self.assertEqual(modules["cloud"], [{"label": "Files (Nextcloud)", "site": "", "path": "", "address": "", "user": "admin",
                                             "password": ["admin_password"], "note": modules["cloud"][0]["note"], "when": ""}])
        self.assertEqual(modules["dns"][0]["path"], "/admin")
        self.assertEqual([x["site"] for x in modules["downloader"]], ["dl", "jd"])
        self.assertEqual(modules["tor"], [])
        self.assertEqual(sorted(n for n, x in modules.items() if x), [
            "cloud", "control", "dns", "downloader", "immich", "opencloud", "paperless", "portainer", "sftp", "vault", "vpn-server"])


class TestControl(StoreBase):
    """The control node's login is the store's key: no password hash, no key file on disk."""
    PW = "a long enough password"

    def control(self, clock=None):
        fake = [sys.executable, "-c", "import sys; print('ran', *sys.argv[1:]); sys.exit(3 if 'bad' in sys.argv else 0)"]
        return controlmod.Control(self.f.path, kiwi_cmd=fake, clock=clock or time.time,
                                  run_dir=os.path.join(self.tmp, "ram"))

    def test_the_password_is_a_way_into_the_store_and_nothing_else_is_kept(self):
        c = self.control()
        with self.assertRaisesRegex(KiwiError, "secrets init"):
            c.set_password(self.PW)                              # plain secrets/: the node never gets those
        self.init()
        with self.assertRaisesRegex(KiwiError, "12 characters"):
            c.set_password("short")
        c.set_password(self.PW)
        st = store.Store.for_fleet(self.f.path)
        self.assertEqual([n for n, _k in st.ways()], ["desktop", "recovery", "control"])
        on_disk = "".join(util.read_text(os.path.join(c.dir, f)) for f in os.listdir(c.dir))
        self.assertRegex(on_disk.strip(), r"^[0-9a-f]{32}$")     # the salt, and only the salt
        c.set_password(self.PW + " changed")                     # again: the old way goes, the new one comes
        self.assertEqual([n for n, _k in st.ways()].count("control"), 1)

    def test_a_new_password_replaces_the_old_one(self):
        self.init()
        c = self.control()
        c.set_password(self.PW)
        store.Store.for_fleet(self.f.path).lock()
        self.assertTrue(c.login(self.PW))
        with self.assertRaisesRegex(KiwiError, "current password"):
            c.change_password("not the password!", "another long password")
        with self.assertRaisesRegex(KiwiError, "12 characters"):
            c.change_password(self.PW, "short")
        c.change_password(self.PW, "another long password")
        self.assertIn("changed the control password", str(c.audit_tail(5)))
        c.lock_store()
        self.assertIsNone(c.login(self.PW, "1.1.1.1"))           # the old one opens nothing
        self.assertTrue(c.login("another long password", "1.1.1.2"))
        self.assertFalse(c.locked())
        self.assertEqual(c.rotation(), {"pending": [], "last": None, "seed_pending": [], "seed_last": None})
        write(os.path.join(self.tmp, "secrets", "rotation.json"),
              json.dumps({"ssh": {"done": ["a"]}, "seed": {"done": []}, "last": {"seed": "2026-11-13T10:00:00"}}))
        with mock.patch.object(c, "fleet_info", lambda **_k: {"hosts": [{"name": "a"}, {"name": "b"}]}):
            self.assertEqual(c.rotation(), {"pending": ["b"], "last": None, "seed_pending": ["a", "b"],
                                            "seed_last": "2026-11-13T10:00:00"})
        self.assertEqual(c.argv("rotate-seed", ["b"])[-5:], ["rotate", self.f.path, "seed", "--waive", "b"])
        self.assertEqual(c.argv("rotate", ["b"])[-5:], ["rotate", self.f.path, "ssh", "--waive", "b"])   # the page's Leave out
        self.assertEqual(c.argv("rotate")[-3:], ["rotate", self.f.path, "ssh"])
        with mock.patch.object(c, "fleet_info", lambda **_k: {"hosts": [{"name": "a"}]}):
            self.assertEqual(c.argv("update")[-4:], ["apply", self.f.path, "--update", "a"])    # newer images into use

    def test_settings_are_saved_only_when_the_check_says_so(self):
        self.init()
        c = self.control()
        c.set_password(self.PW)
        before = util.read_text(self.f.path)
        with self.assertRaises(KiwiError):                      # this check answers nothing that can be read: not saved
            c.save_settings("a", {("timezone",): "Europe/Vienna"})
        self.assertEqual(util.read_text(self.f.path), before)
        with self.assertRaisesRegex(KiwiError, "no such machine"):
            c.save_settings("nobody", {("timezone",): "UTC"})
        self.assertEqual(c.save_settings("a", {("timezone",): "Europe/Berlin"}), [])      # what it inherits anyway
        self.assertEqual(util.read_text(self.f.path), before)                              # nothing to write: not rewritten
        with self.assertRaisesRegex(KiwiError, "could not be read"):
            c.settings("a")
        c.kiwi = [sys.executable, "-c", "import sys; sys.path.insert(0, %r); from kiwiserver import cli; "
                  "sys.exit(cli.main(sys.argv[1:]))" % os.path.join(ROOT, "lib")]
        self.assertEqual(c.save_settings("a", {("timezone",): "Europe/Vienna", "admin.password": "a-new-s3cr3t"}, "anna"),
                         ["timezone", "admin.password"])
        self.assertEqual(config.Fleet.load(self.f.path).hosts["a"].cfg["timezone"], "Europe/Vienna")
        last = c.audit_tail(1)[0]
        self.assertEqual((last["user"], last["what"], last["hosts"], last["keys"]),
                         ("anna", "settings changed", ["a"], ["timezone", "admin.password"]))
        self.assertNotIn("s3cr3t", util.read_text(os.path.join(c.dir, "audit.jsonl")))     # which setting, never its value
        saved = util.read_text(self.f.path)
        with self.assertRaisesRegex(KiwiError, "Reboot time.*HH:MM"):
            c.save_settings("a", {("updates", "time"): "25:00"})
        self.assertEqual(util.read_text(self.f.path), saved)
        # a mistake that was there before is not this change's: the file can be put right step by step
        write(self.f.path, saved + "  b:\n    updates:\n      time: '26:00'\n")
        self.assertEqual(c.save_settings("a", {("keyboard",): "de"}), ["keyboard"])
        with self.assertRaisesRegex(KiwiError, "Reboot days"):
            c.save_settings("a", {("updates", "days"): ["Funday"]})
        self.assertEqual(c.save_settings("b", {("updates", "time"): None}), ["updates.time"])
        self.assertEqual([f["name"] for f in c.settings()["groups"][0]["fields"]][:2], ["target", "role"])
        c.lock_store()
        with self.assertRaisesRegex(KiwiError, "locked"):
            c.save_settings("a", {("keyboard",): "us"})
        with self.assertRaisesRegex(KiwiError, "locked"):
            c.settings("a")

    def test_login_opens_the_locked_store_and_lock_closes_it(self):
        self.init()
        now = [1000.0]
        c = self.control(lambda: now[0])
        c.set_password(self.PW)
        st = store.Store.for_fleet(self.f.path)
        st.lock()                                                # as after a reboot of the node
        self.assertTrue(c.locked())
        self.assertFalse(os.path.exists(os.path.join(self.secrets, "seed")))
        with self.assertRaisesRegex(KiwiError, "locked"):
            c.start("apply", ["a"])
        self.assertIsNone(c.refresh_status())
        self.assertIsNone(c.login("not the password!", "10.9.3.2"))
        self.assertTrue(c.locked())
        token = c.login(self.PW, "10.9.3.2")
        self.assertFalse(c.locked())
        self.assertEqual(util.read_text(os.path.join(self.secrets, "seed")), "S" * 32)
        self.assertEqual(c.session(token), "admin")
        self.assertIsNone(c.session(token.replace("admin", "root")))          # tampered
        self.assertTrue(c.login(self.PW, "10.9.3.2"))                         # open already: the key is compared
        self.assertIsNone(c.login("not the password!", "10.9.3.2"))
        c.lock_store()
        self.assertTrue(c.locked())
        self.assertIsNone(c.session(token))                                   # a locked fleet has no sessions
        token = c.login(self.PW, "10.9.3.2")
        now[0] += 13 * 3600
        self.assertIsNone(c.session(token))                                   # expired
        for _ in range(5):
            self.assertIsNone(c.login("wrong wrong wrong", "10.9.3.9"))
        self.assertIsNone(c.login(self.PW, "10.9.3.9"))                       # five wrong: it waits
        now[0] += 601
        self.assertTrue(c.login(self.PW, "10.9.3.9"))
        self.assertIn("unlocked", [e["what"] for e in c.audit_tail(30)])
        other = self.control()                                                # another process: its sessions are its own
        self.assertIsNone(other.session(token))

    def test_jobs(self):
        self.init()
        c = self.control()
        jid = c.start("apply", ["fig"], wait=True)
        j = c.job(jid)
        self.assertEqual((j["state"], j["rc"], j["hosts"]), ("ok", 0, ["fig"]))
        text, off = c.log(jid)
        self.assertIn("ran apply %s fig" % c.fleet_path, text)
        self.assertEqual(c.log(jid, off), ("", off))
        self.assertTrue(c.jobs_dir.startswith(os.path.join(self.tmp, "ram")))  # logs in RAM, not in the fleet directory
        self.assertEqual(c.audit_tail(1)[0]["what"], "apply")                 # a change says who
        self.assertEqual(c.job(c.start("plan", ["bad"], wait=True))["state"], "failed")
        self.assertIn("--once", c.argv("watch"))
        self.assertEqual(c.argv("save", ["papaya"])[-4:], ["backups", c.fleet_path, "papaya", "--now"])
        with self.assertRaises(KiwiError):
            c.argv("save", [])
        with self.assertRaisesRegex(KiwiError, "no machines"):
            c.argv("update")
        util.write_text(os.path.join(c.run, "fleet.json"), json.dumps({"hosts": [{"name": "a"}, {"name": "b"}]}))
        self.assertEqual(c.argv("update")[-5:], ["apply", c.fleet_path, "--update", "a", "b"])
        with mock.patch.dict(os.environ, {"KIWI_CONTROL_HOST": "a"}):
            self.assertEqual(c.argv("update")[-2:], ["b", "a"])               # the control node itself comes last
        with self.assertRaises(KiwiError):
            c.start("apply", ["fig; rm -rf /"])
        with self.assertRaises(KiwiError):
            c.start("reboot")
        self.assertIsNone(c.job("../../etc/passwd"))
        self.assertFalse(c.has_ram_masters())

    def test_a_ring_on_another_machine_unlocks_it_after_a_restart(self):
        self.init()
        c = self.control()
        c.set_password(self.PW)
        # clevis stands in: what it "encrypts" only comes back while the ring answers
        tool = os.path.join(self.tmp, "bin", "clevis")
        write(tool, '#!/bin/sh\ncase $1 in\n  encrypt) printf JWE:; cat ;;\n'
                    '  decrypt) [ -e "$FAKE_KEYRING/ring-down" ] && exit 1; sed s/^JWE:// ;;\nesac\n')
        os.chmod(tool, 0o755)
        pin = {"ring": "house", "server": "lychee", "url": "http://192.168.2.61:7500", "adv": {"payload": "x"}}
        c.kiwi = [sys.executable, "-c", "print(%r)" % json.dumps(pin)]
        self.assertFalse(c.try_auto())                           # nothing armed, and it is open anyway
        c.login(self.PW)
        self.assertEqual(c.arm_auto(), "house")
        self.assertEqual(c.auto_state(), {"ring": "house", "server": "lychee"})
        self.assertEqual(oct(os.stat(c.auto_file).st_mode & 0o777), "0o600")
        c.lock_store()                                           # a restart, a power cut
        fresh = self.control()                                   # the process that starts afterwards
        write(os.path.join(self.keyring, "ring-down"), "")
        self.assertFalse(fresh.try_auto())                       # the ring's machine is off: locked, the password works
        self.assertTrue(fresh.locked())
        os.remove(os.path.join(self.keyring, "ring-down"))
        self.assertTrue(fresh.try_auto())
        self.assertFalse(fresh.locked())
        self.assertEqual(util.read_text(os.path.join(self.secrets, "seed")), "S" * 32)
        self.assertIn("unlocked by the ring house", [e["what"] for e in fresh.audit_tail(5)])
        # the owner takes unlock.control away again: the next login removes what the ring could open
        fresh.kiwi = [sys.executable, "-c", "print('{}')"]
        fresh.login(self.PW)
        self.assertIsNone(fresh.arm_auto())
        self.assertFalse(os.path.exists(fresh.auto_file))
        self.assertIsNone(fresh.auto_state())

    def test_a_pushed_store_is_taken_and_a_changed_password_locks(self):
        self.init()
        c = self.control()
        c.set_password(self.PW)
        c.login(self.PW)
        st = store.Store.for_fleet(self.f.path)
        self.assertIsNone(c.reload_if_pushed())
        # the operator's computer saved a new store over the node's (control push)
        tree = os.path.join(self.tmp, "elsewhere")
        store.extract(store.snapshot(self.secrets), tree)
        write(os.path.join(tree, "new.conf"), "pushed")
        subprocess.run([store._tool("age"), "-e", "-R", st.recipients, "-o", st.file],
                       input=store.snapshot(tree), check=True)
        self.assertEqual(c.reload_if_pushed(), "reloaded")
        self.assertEqual(util.read_text(os.path.join(self.secrets, "new.conf")), "pushed")
        # ... and one whose control password is another: the old key no longer opens it
        keys = [k for n, k in st.ways() if n != "control"]
        r2 = os.path.join(self.tmp, "r2")
        write(r2, "\n".join(keys) + "\n")
        subprocess.run([store._tool("age"), "-e", "-R", r2, "-o", st.file], input=store.snapshot(tree), check=True)
        self.assertEqual(c.reload_if_pushed(), "locked")
        self.assertTrue(c.locked())

    def test_a_copy_comes_back_on_a_node_with_a_new_password_and_no_keyring_way(self):
        recovery = self.init()                      # a network made on a computer: desktop, recovery
        write(os.path.join(self.tmp, "federation.yaml"), "# no partners yet\n{}\n")
        write(os.path.join(self.tmp, "notes", "router.txt"), "192.168.1.1\n")
        f = self.fleet({"defaults": self.base_defaults(backup={"extra": ["notes"]}), "hosts": {"a": {}}})
        self.control().set_password(self.PW)        # and the page of a node that is gone: control
        copy = backup.create_copy(f)
        with open(f.path, "rb") as fh:
            fleet_file = fh.read()
        store.Store.for_fleet(f.path).lock()
        folder = os.path.join(self.tmp, "node")
        fake = [sys.executable, "-c", "print('ran')"]
        c = controlmod.Control(os.path.join(folder, "fleet.yaml"), kiwi_cmd=fake, run_dir=os.path.join(self.tmp, "ram2"))
        new = "the new node's password"

        def nothing_there():
            self.assertTrue(c.fresh())
            self.assertEqual([n for n in os.listdir(folder) if n != "control"], [])
            self.assertFalse(os.path.exists(c.salt_file))
            self.assertFalse(os.path.exists(os.path.join(c.run, "restore-run")))
            self.assertEqual([d for d in os.listdir(self.run_dir) if d.startswith("kiwi-")], [])    # nothing open in memory
        for why, args in (("not the setup code", ("0000-0000", new, copy, recovery)),
                          ("12 characters", (c.setup_code(), "short", copy, recovery)),
                          ("starts with AGE-SECRET-KEY-", (c.setup_code(), new, copy, "hello")),
                          ("does not open this copy", (c.setup_code(), new, copy, store.keygen()[0])),
                          ("not a copy of a network", (c.setup_code(), new, b"hello", recovery)),
                          ("not a copy of a network", (c.setup_code(), new, b"", recovery)),
                          ("kiwi-server restore", (c.setup_code(), new, backup.encrypt(b"x", "pw"), recovery))):
            with self.assertRaisesRegex(KiwiError, why):
                c.restore_run(*args, address=why)
            nothing_there()
        self.assertTrue(c.restore_run(c.setup_code(), new, copy, recovery, address="192.168.1.9"))
        self.assertFalse(c.fresh())
        self.assertEqual(sorted(n for n in os.listdir(folder) if not os.path.islink(os.path.join(folder, n))),
                         ["control", "federation.yaml.age", "fleet.yaml.age", "secrets.age", "secrets.recipients"])
        self.assertEqual(sorted(os.listdir(c.dir)), ["audit.jsonl", "salt"])
        # the keyring's way and the old page's password are gone from the store; the recovery key stays
        self.assertEqual([n for n, _k in c.store.ways()], ["recovery", "control"])
        with open(os.path.join(folder, "fleet.yaml"), "rb") as fh:
            self.assertEqual(fh.read(), fleet_file)
        with open(os.path.join(folder, "secrets", "deep", "key")) as fh:
            self.assertEqual(fh.read(), "private")
        said = json.dumps(c.audit_tail(20))
        self.assertIn("brought back from a copy", said)
        self.assertIn("1 of its files have no place on a control node", said)       # notes/router.txt
        self.assertIn("the recovery key does not open it", said)
        for secret in (recovery, new, "short", "hello"):
            self.assertNotIn(secret, said)
        with self.assertRaisesRegex(KiwiError, "has a network already"):
            c.restore_run(c.setup_code(), new, copy, recovery)
        c.lock_store()
        self.assertIsNone(c.login(self.PW, "1.1.1.1"))
        self.assertTrue(c.login(new, "1.1.1.2"))
        # the copy this node gives is the same network again
        name, again = c.network_copy()
        self.assertRegex(name, r"^fleet-fleet-\d{8}T\d{6}Z\.tar$")
        fleet_name, plain, _kept = backup.open_copy(again, recovery)
        self.assertEqual((fleet_name, sorted(plain)), ("fleet.yaml", ["federation.yaml", "fleet.yaml"]))
        self.assertEqual(plain["fleet.yaml"], fleet_file)
        c.lock_store()
        with self.assertRaisesRegex(KiwiError, "locked"):
            c.network_copy()

    def test_the_networks_copy_goes_where_the_fleet_says_or_to_every_machine_set_up(self):
        self.init()
        c = self.control()
        c.set_password(self.PW)
        self.assertTrue(c.login(self.PW))
        fleet = self.f.path
        util.write_text(os.path.join(c.run, "fleet.json"), json.dumps({"hosts": [
            {"name": "sorbet", "phase": "applied"}, {"name": "papaya", "phase": "install"}, {"name": "lychee", "phase": "applied"}]}))
        self.assertEqual(c.argv("backup")[-4:], ["backup", fleet, "sorbet", "lychee"])
        util.write_text(os.path.join(c.run, "fleet.json"), json.dumps({"hosts": [{"name": "papaya", "phase": "install"}]}))
        with self.assertRaisesRegex(KiwiError, "no machine is set up yet"):
            c.argv("backup")
        self.fleet({"defaults": self.base_defaults(backup={"hosts": ["a"]}), "hosts": {"a": {}}})
        self.assertEqual(c.argv("backup")[-2:], ["backup", fleet])      # backup.hosts: the command knows where


@unittest.skipUnless(have("age") and have("age-keygen"), "age not installed")
class TestControlSync(StoreBase):
    """push and pull against a 'node' that is a directory here: the scripts run in a local shell."""
    PW = "a long enough password"

    def setUp(self):
        super().setUp()
        self.node = os.path.join(self.tmp, "node", "kn-control")
        self.h = types.SimpleNamespace(name="papaya", role_settings={"docker_dir": os.path.dirname(self.node)})

        def run(tgt, script, data=None, **kw):
            r = subprocess.run(["sh", "-c", script], input=data, capture_output=True)
            if r.returncode:
                raise KiwiError(r.stderr.decode())
            return r.stdout
        p = mock.patch.object(remote, "run", run)
        p.start()
        self.addCleanup(p.stop)

    def test_nothing_travels_or_rests_in_the_clear(self):
        with self.assertRaisesRegex(KiwiError, "secrets init"):
            controlsync.push(self.f, self.h, "t")
        self.init()
        with self.assertRaisesRegex(KiwiError, "--set-password"):
            controlsync.push(self.f, self.h, "t")
        controlmod.Control(self.f.path, run_dir=os.path.join(self.tmp, "ram")).set_password(self.PW)
        write(os.path.join(self.node, "fleet.yaml"), "an older layout left this\n")
        self.assertEqual(controlsync.push(self.f, self.h, "t", force=True), 4)
        self.assertEqual(sorted(os.listdir(self.node)), ["control", "fleet.yaml.age", "secrets.age", "secrets.recipients"])
        self.assertEqual(sorted(os.listdir(os.path.join(self.node, "control"))), ["salt", "synced"])
        for root, _d, files in os.walk(self.node):
            for f in files:
                with open(os.path.join(root, f), "rb") as fh:
                    data = fh.read()
                self.assertNotIn(b"S" * 32, data)                # the seed
                self.assertNotIn(b"Europe/Berlin", data)         # the fleet file
        there, stamp = controlsync.remote_state("t", self.node)
        self.assertEqual(there, stamp)                           # python's digest and the shell's agree
        controlsync.push(self.f, self.h, "t")                    # nothing changed there: again is fine

    def test_the_node_opens_it_into_ram_and_saves_what_a_job_changed(self):
        self.init()
        controlmod.Control(self.f.path, run_dir=os.path.join(self.tmp, "ram")).set_password(self.PW)
        controlsync.push(self.f, self.h, "t")
        store.Store.for_fleet(self.f.path).lock()                # (one runtime directory here: the node's turn)
        fleet = os.path.join(self.node, "fleet.yaml")
        n = controlmod.Control(fleet, kiwi_cmd=["true"], run_dir=os.path.join(self.tmp, "ram2"))
        self.assertTrue(n.ready() and n.locked())
        self.assertFalse(os.path.lexists(fleet))
        self.assertIsNone(n.login("not the password!"))
        self.assertTrue(n.login(self.PW))
        self.assertTrue(os.path.islink(fleet) and os.path.realpath(fleet).startswith(self.run_dir))
        self.assertIn("Europe/Berlin", util.read_text(fleet))
        self.assertIsNone(n.reload_if_pushed())
        before = util.sha256_file(fleet + ".age")
        with open(fleet, "a") as fh:
            fh.write("# a job changed it\n")
        n.save_fleet()
        self.assertNotEqual(util.sha256_file(fleet + ".age"), before)
        self.assertIsNone(n.reload_if_pushed())                  # its own save is not a push
        n.lock_store()
        self.assertTrue(n.locked())
        self.assertFalse(os.path.lexists(fleet))
        self.assertTrue(n.login(self.PW))
        self.assertIn("# a job changed it", util.read_text(fleet))
        n.lock_store()
        # the operator's computer sees the node changed, and gets it with pull
        store.Store.for_fleet(self.f.path).unlock(util.read_text(os.path.join(self.keyring, "item")).strip())
        with self.assertRaisesRegex(KiwiError, "control pull first"):
            controlsync.push(self.f, self.h, "t")
        self.assertGreater(controlsync.pull(self.f, self.h, "t"), 0)
        self.assertIn("# a job changed it", util.read_text(self.f.path))
        self.assertEqual(controlsync.pull(self.f, self.h, "t"), 0)   # in step: nothing to fetch
        controlsync.push(self.f, self.h, "t")

    def test_who_changed_decides(self):
        self.init()
        controlmod.Control(self.f.path, run_dir=os.path.join(self.tmp, "ram")).set_password(self.PW)
        controlsync.push(self.f, self.h, "t")
        st = store.Store.for_fleet(self.f.path)
        # the node enrolled something: its store is another now
        tree = os.path.join(self.tmp, "nodes-ram")
        store.extract(store.snapshot(self.secrets), tree)
        write(os.path.join(tree, "new.conf"), "from the node")
        subprocess.run([store._tool("age"), "-e", "-R", os.path.join(self.node, "secrets.recipients"),
                        "-o", os.path.join(self.node, "secrets.age")], input=store.snapshot(tree), check=True)
        with self.assertRaisesRegex(KiwiError, "control pull first"):
            controlsync.push(self.f, self.h, "t")
        self.assertGreater(controlsync.pull(self.f, self.h, "t"), 0)
        self.assertTrue(st.is_open())                            # opened again with the keyring's key
        self.assertEqual(util.read_text(os.path.join(self.secrets, "new.conf")), "from the node")
        controlsync.push(self.f, self.h, "t")                    # in step again
        write(os.path.join(self.secrets, "local.conf"), "x")     # changed here, not saved yet: pull saves and sees it
        with self.assertRaisesRegex(KiwiError, "control push first"):
            controlsync.pull(self.f, self.h, "t")
        write(os.path.join(self.node, "control", "audit.jsonl"), "{}\n")
        controlsync.push(self.f, self.h, "t")
        self.assertTrue(os.path.isfile(os.path.join(self.node, "control", "audit.jsonl")))    # the node's own files stay

    def test_which_control_node(self):
        with self.assertRaisesRegex(KiwiError, "no host has the control module"):
            controlsync.control_host(self.f)



def _have_web():
    try:
        import fastapi, uvicorn, multipart  # noqa: F401,E401
        return True
    except ImportError:
        try:
            import fastapi, uvicorn, python_multipart  # noqa: F401,E401
            return True
        except ImportError:
            return False


@unittest.skipUnless(have("age") and have("age-keygen") and _have_web(), "age, fastapi or uvicorn not installed")
class TestWeb(StoreBase):
    """The pages over a real socket, asked the way a browser asks: a form posted under the
    proxy's no-referrer policy carries `Origin: null`."""
    PW = "a long enough password"

    def setUp(self):
        super().setUp()
        import uvicorn
        from kiwiserver import web
        self.init()
        fake = [sys.executable, "-c", "import sys; print('ran', *sys.argv[1:])"]
        self.c = controlmod.Control(self.f.path, kiwi_cmd=fake, run_dir=os.path.join(self.tmp, "ram"))
        self.c.set_password(self.PW)
        store.Store.for_fleet(self.f.path).lock()                # as on a node that just started
        port = remote.free_port()
        self.server = uvicorn.Server(uvicorn.Config(web.make_app(self.c), host="127.0.0.1", port=port, log_level="error"))
        t = threading.Thread(target=self.server.run, daemon=True)
        t.start()
        self.addCleanup(lambda: (setattr(self.server, "should_exit", True), t.join(5)))
        self.url = "http://127.0.0.1:%d" % port
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.05)
        self.cookie = ""

    def ask(self, path, form=None, origin="null"):
        """(status, Location or body). A form is posted with the Origin a browser sends."""
        import urllib.error
        import urllib.request

        class Stay(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **kw):
                return None
        headers = {"Cookie": self.cookie} if self.cookie else {}
        data = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Origin"] = origin
        req = urllib.request.Request(self.url + path, data=data, headers=headers)
        try:
            with urllib.request.build_opener(Stay).open(req) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            set_cookie = e.headers.get("set-cookie") or ""
            if set_cookie.startswith("kiwi_session=") and '""' not in set_cookie.split(";")[0]:
                self.cookie = set_cookie.split(";")[0]
            with e:
                return e.code, e.headers.get("location") or e.read().decode()

    def test_the_page_changes_its_password_and_replaces_the_keys(self):
        self.assertEqual(self.ask("/login", {"password": self.PW})[0], 303)
        code, body = self.ask("/controllers")
        self.assertIn("Never replaced", body)
        tok = re.search(r'name="csrf" value="([0-9a-f]{64})"', body).group(1)
        new = "another long password"
        self.assertIn("not the same", self.ask("/controllers/password", {"old": self.PW, "new": new, "again": new + "x", "csrf": tok})[1])
        self.assertIn("current password", self.ask("/controllers/password", {"old": "wrong wrong wrong", "new": new, "again": new, "csrf": tok})[1])
        self.assertIn("The password is changed", self.ask("/controllers/password", {"old": self.PW, "new": new, "again": new, "csrf": tok})[1])
        self.assertEqual(self.ask("/controllers")[0], 200)                        # still logged in
        code, where = self.ask("/run", {"action": "rotate", "csrf": tok})
        self.assertEqual(code, 303)
        for _ in range(100):
            js = self.c.jobs()
            if js and js[0]["state"] in ("ok", "failed"):
                break
            time.sleep(0.05)
        self.assertIn("ran rotate %s ssh" % self.f.path, self.ask("/api" + where + "/log")[1])
        self.assertIn("Replacing the SSH keys", self.ask("/activity")[1])
        code, where = self.ask("/run", {"action": "rotate-seed", "csrf": tok})
        self.assertEqual(code, 303)
        for _ in range(100):
            js = self.c.jobs()
            if js and js[0]["action"] == "rotate-seed" and js[0]["state"] in ("ok", "failed"):
                break
            time.sleep(0.05)
        self.assertIn("ran rotate %s seed" % self.f.path, self.ask("/api" + where + "/log")[1])

    def test_a_browser_logs_in_unlocks_runs_and_locks(self):
        self.assertIn('"locked":true', self.ask("/healthz")[1].replace(" ", ""))
        self.assertEqual(self.ask("/")[0], 303)                                   # nobody yet
        code, body = self.ask("/login", {"password": "not the password!"})
        self.assertEqual(code, 200)
        self.assertIn("did not work", body)
        self.assertIn("keys are locked", body)                                    # said in plain words
        self.assertTrue(self.c.locked())
        self.assertEqual(self.ask("/login", {"password": self.PW + " "})[0], 303)   # Origin: null, a trailing space
        self.assertFalse(self.c.locked())
        code, body = self.ask("/")
        self.assertEqual(code, 200)
        tok = re.search(r'name="csrf" value="([0-9a-f]{64})"', body).group(1)
        # a form another site makes the browser post: the cookie may come along, the token cannot
        self.assertEqual(self.ask("/run", {"action": "apply", "host": "a"}), (303, "/login"))
        self.assertEqual(self.ask("/run", {"action": "apply", "host": "a", "csrf": "0" * 64}), (303, "/login"))
        self.assertEqual(self.c.jobs(), [])
        code, where = self.ask("/run", {"action": "apply", "host": "a", "csrf": tok})
        self.assertEqual(code, 303)
        self.assertRegex(where, r"^/jobs/\d{8}-\d{6}-[0-9a-f]{6}$")
        for _ in range(100):
            js = self.c.jobs()          # (a listing can miss a file that is being replaced just then)
            if js and js[0]["state"] in ("ok", "failed"):
                break
            time.sleep(0.05)
        code, body = self.ask("/api" + where + "/log")
        self.assertIn("ran apply", body)
        code, body = self.ask(where)
        self.assertEqual(code, 200)
        self.assertIn("Applying to a", body)                                      # in words, not a command name
        for path, has in (("/devices", "Add a phone or laptop"), ("/backups", "The network itself"),
                          ("/help", "The words"), ("/activity", "Applying to"), ("/controllers", "Add a computer")):
            code, body = self.ask(path)
            self.assertEqual(code, 200, path)
            self.assertIn(has, body)
            self.assertIn('name="csrf" value="%s"' % tok, body)                   # every page's forms carry the token
        self.assertEqual(self.ask("/hosts/nosuch"), (303, "/"))
        self.assertEqual(self.ask("/lock", {})[0], 303)                           # no token: nothing happens
        self.assertFalse(self.c.locked())
        self.assertEqual(self.ask("/lock", {"csrf": tok})[0], 303)
        self.assertTrue(self.c.locked())
        self.assertEqual(self.ask("/")[0], 303)                                   # the session went with it
        self.assertEqual(self.ask("/api/status")[0], 401)



    def test_home_and_a_machine_in_plain_words(self):
        self.ask("/login", {"password": self.PW})
        self.assertIn("Checking your machines", self.ask("/")[1])                 # the look the login started
        for _ in range(200):
            if not self.c.looking:
                break
            time.sleep(0.05)
        util.write_text(os.path.join(self.c.run, "fleet.json"), json.dumps({"groups": {"devices": "10.9.3.0/24"}, "hosts": [
            {"name": "papaya", "hostname": "papaya.home", "role": "node", "modules": ["cloud", "backup"],
             "sites": ["cloud.papaya.home", "vault.papaya.home"], "vpn_ip": "10.9.0.2", "next": ""},
            {"name": "sorbet", "hostname": "sorbet.home", "role": "master", "modules": [], "sites": [], "next": "apply it"}]}))
        util.write_text(os.path.join(self.c.run, "status.json"), json.dumps({"time": time.time(), "hosts": [
            {"host": "papaya", "state": "warn", "output": "raw words", "notes": ["disk 14%"],
             "problems": [{"level": "warn", "text": "an update is waiting for a reboot"}],
             "facts": {"backup": {"last": 1, "age": 7200, "copies": [{"server": "backup.lychee.home"}]}, "reboot": True}}]}))
        code, body = self.ask("/")
        self.assertIn("1 machine wants a look", body)
        self.assertIn("Update everything", body)
        self.assertIn("restarts by itself in its update window", body)            # what the problem means
        self.assertIn("Files (Nextcloud)", body)
        self.assertIn('href="https://vault.papaya.home"', body)
        self.assertIn("not checked yet", body)                                    # sorbet said nothing so far
        code, body = self.ask("/hosts/papaya")
        self.assertIn("Home server (node)", body)
        self.assertIn("Changes nothing.", body)                                   # each button says what it does
        self.assertIn("2 hours ago", body)
        self.assertIn("raw words", body)
        self.assertIn("has not answered yet", self.ask("/hosts/sorbet")[1])
        self.assertIn("saved 2 hours ago", self.ask("/backups")[1])
        self.assertIn('<option value="devices" selected>', self.ask("/devices")[1])

    def test_a_device_gets_a_qr_code(self):
        self.ask("/login", {"password": self.PW})
        tok = re.search(r'name="csrf" value="([0-9a-f]{64})"', self.ask("/")[1]).group(1)
        conf = "[Interface]\nPrivateKey = abc\n"
        # the fake kiwi-server "enrolls": it writes the device's config into the open store
        self.c.kiwi = [sys.executable, "-c", "import sys,os; d=os.path.join(os.path.dirname(sys.argv[2]),'secrets','devices'); "
                       "os.makedirs(d,exist_ok=True); open(os.path.join(d,sys.argv[3]+'.conf'),'w').write(%r)" % conf]
        self.assertEqual(self.ask("/devices/add", {"name": "annas-phone"})[0], 303)       # no token: nothing
        code, body = self.ask("/devices/add", {"name": "Annas Phone!", "csrf": tok})
        self.assertIn("lowercase letters", body)
        code, body = self.ask("/devices/add", {"name": "annas-phone", "group": "devices", "csrf": tok})
        self.assertIn("annas-phone is ready", body)
        self.assertIn("PrivateKey = abc", body)                                   # for the download
        if shutil.which("qrencode"):
            self.assertIn("<svg", body)
        self.assertIn("exists already", self.ask("/devices/add", {"name": "annas-phone", "csrf": tok})[1])
        self.assertIn("annas-phone is ready", self.ask("/devices/show", {"name": "annas-phone", "csrf": tok})[1])
        self.assertIn("no device called", self.ask("/devices/show", {"name": "nobody", "csrf": tok})[1])
        self.assertIn("added the device annas-phone", [e["what"] for e in self.c.audit_tail(5)])
        # a lost phone: the fake kiwi-server "removes" it when asked with --remove
        self.c.kiwi = [sys.executable, "-c", "import sys,os; assert sys.argv[4]=='--remove'; "
                       "os.remove(os.path.join(os.path.dirname(sys.argv[2]),'secrets','devices',sys.argv[3]+'.conf'))"]
        self.assertEqual(self.ask("/devices/remove", {"name": "annas-phone"})[0], 303)    # no token: it stays
        self.assertIn("annas-phone", self.c.devices())
        self.assertIn("annas-phone is off the VPN", self.ask("/devices/remove", {"name": "annas-phone", "csrf": tok})[1])
        self.assertEqual(self.c.devices(), [])
        self.assertIn("no device called", self.ask("/devices/remove", {"name": "annas-phone", "csrf": tok})[1])

    def test_the_computers_page(self):
        self.ask("/login", {"password": self.PW})
        tok = re.search(r'name="csrf" value="([0-9a-f]{64})"', self.ask("/")[1]).group(1)
        self.assertEqual(self.ask("/controllers/invite", {})[0], 303)             # no token: no invite
        code, body = self.ask("/controllers/invite", {"csrf": tok})
        self.assertEqual(code, 200)
        link = re.search(r'<pre id="inv">(http://127\.0\.0\.1:\d+/#join=[a-z0-9]+\.[0-9a-f]{8})</pre>', body).group(1)
        self.assertEqual(self.c.join(controlsync.parse_invite(link)[1], "laptop", store.keygen()[1], "10.9.3.2"), "laptop")
        self.assertIn("<strong>laptop</strong>", self.ask("/controllers")[1])
        self.ask("/controllers/remove", {"name": "laptop"})                       # forged: nothing
        self.assertIn("laptop", [n for n, _k in self.c.controllers()])
        self.assertEqual(self.ask("/controllers/remove", {"name": "laptop", "csrf": tok}), (303, "/controllers"))
        self.assertNotIn("laptop", [n for n, _k in self.c.controllers()])
        code, body = self.ask("/controllers/remove", {"name": "control", "csrf": tok})
        self.assertIn("own key", body)                                            # said on the page, not a crash
        self.assertEqual(self.ask("/api/fleet")[0], 401)                          # the sync API wants a controller


@unittest.skipUnless(have("age") and have("age-keygen") and _have_web(), "age, fastapi or uvicorn not installed")
class TestControllers(StoreBase):
    """A second computer (its own process, keyring and runtime directory) joins through the
    control node, is accepted, syncs both ways, and is removed. design/controllers.md, step 1."""
    PW = "a long enough password"

    def setUp(self):
        super().setUp()
        import uvicorn
        from kiwiserver import web
        write(os.path.join(self.secrets, "ca", "kiwiCA.pem"), "-----BEGIN CERTIFICATE-----\nthe fleet's\n-----END CERTIFICATE-----\n")
        self.init()
        controlmod.Control(self.f.path, run_dir=os.path.join(self.tmp, "ram0")).set_password(self.PW)
        node = os.path.join(self.tmp, "node", "kn-control")

        def run(tgt, script, data=None, **kw):
            return subprocess.run(["sh", "-c", script], input=data, capture_output=True, check=True).stdout
        with mock.patch.object(remote, "run", run):
            controlsync.push(self.f, types.SimpleNamespace(name="papaya", role_settings={"docker_dir": os.path.dirname(node)}), "t")
        store.Store.for_fleet(self.f.path).lock()                # the operator's computer steps aside
        self.n = controlmod.Control(os.path.join(node, "fleet.yaml"), kiwi_cmd=["true"], run_dir=os.path.join(self.tmp, "ram"))
        self.assertTrue(self.n.login(self.PW))
        port = remote.free_port()
        server = uvicorn.Server(uvicorn.Config(web.make_app(self.n), host="127.0.0.1", port=port, log_level="error"))
        t = threading.Thread(target=server.run, daemon=True)
        t.start()
        self.addCleanup(lambda: (setattr(server, "should_exit", True), t.join(5)))
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        self.url = "http://127.0.0.1:%d" % port
        self.laptop = os.path.join(self.tmp, "laptop", "fleet.yaml")
        self.env = dict(os.environ, XDG_RUNTIME_DIR=os.path.join(self.tmp, "laptop-run"),
                        FAKE_KEYRING=os.path.join(self.tmp, "laptop-keyring"),
                        PYTHONPATH=os.path.join(ROOT, "lib"), PYTHONDONTWRITEBYTECODE="1")
        for d in ("laptop", "laptop-run", "laptop-keyring"):
            os.makedirs(os.path.join(self.tmp, d))

    def laptop_does(self, *args, stdin=""):
        r = subprocess.run([sys.executable, "-m", "kiwiserver", "control", args[0], self.laptop] + list(args[1:]),
                           input=stdin, capture_output=True, text=True, env=self.env)
        return r.returncode, r.stdout + r.stderr

    def test_join_sync_remove(self):
        link = self.n.new_invite(self.url)
        url, code = controlsync.parse_invite(link)
        self.assertEqual(url, self.url)
        rc, out = self.laptop_does("join", self.url + "/#join=aaaaaaaaaaaaaaaaaaaa.00000000")
        self.assertNotEqual(rc, 0)
        self.assertIn("not the one this invite is from", out)     # another fleet's invite: nothing sent
        rc, out = self.laptop_does("join", "--name", "desktop", stdin=link + "\n")       # the link on stdin
        self.assertEqual(rc, 0, out)
        self.assertIn("joined as desktop-2", out)                                         # the name was taken
        self.assertIn("desktop-2", [n for n, _k in self.n.controllers()])
        self.assertIsNone(self.n.join(code, "other", store.keygen()[1]))                  # the invite worked once
        # joining fetched the fleet at once, opened with its own key, into its RAM
        self.assertIn("Europe/Berlin", util.read_text(self.laptop))
        seed = os.path.join(os.path.dirname(self.laptop), "secrets", "seed")
        self.assertEqual(util.read_text(seed), "S" * 32)
        self.assertTrue(os.path.realpath(seed).startswith(self.env["XDG_RUNTIME_DIR"]))
        self.assertIn("in step", self.laptop_does("pull")[1])
        rc, out = self.laptop_does("join", self.n.new_invite(self.url))
        self.assertNotEqual(rc, 0)
        self.assertIn("holds a fleet already", out)                                       # not twice into one folder
        # the laptop changes the fleet and pushes: the open node takes it
        with open(self.laptop, "a") as fh:
            fh.write("# from the laptop\n")
        rc, out = self.laptop_does("push")
        self.assertEqual(rc, 0, out)
        self.assertIn("# from the laptop", util.read_text(self.n.fleet_path))
        self.assertIn("pushed the fleet", [e["what"] for e in self.n.audit_tail(5)])
        # the node learned a host key meanwhile, the laptop changed the fleet file: one copy from both
        write(os.path.join(os.path.dirname(self.n.fleet_path), "secrets", "known_hosts"), "m1 a-key-the-node-saw\n")
        self.n.store.sync()
        with open(self.laptop, "a") as fh:
            fh.write("# and once more\n")
        rc, out = self.laptop_does("push")
        self.assertEqual(rc, 0, out)
        self.assertIn("a-key-the-node-saw", util.read_text(os.path.join(os.path.dirname(self.laptop), "secrets", "known_hosts")))
        self.assertIn("# and once more", util.read_text(self.n.fleet_path))
        self.assertIn("a-key-the-node-saw", util.read_text(os.path.join(os.path.dirname(self.n.fleet_path), "secrets", "known_hosts")))
        # the node changes the fleet file too (a job), and so does the laptop: that is for a person
        with open(self.n.fleet_path, "a") as fh:
            fh.write("# from the node\n")
        self.n.save_fleet()
        with open(self.laptop, "a") as fh:
            fh.write("# again from the laptop\n")
        rc, out = self.laptop_does("push")
        self.assertNotEqual(rc, 0)
        self.assertIn("changed on both sides since the last sync: fleet.yaml", out)
        rc, out = self.laptop_does("pull")
        self.assertIn("Which copy counts?", out)                                          # both changed it: the owner decides
        self.assertEqual(self.laptop_does("pull", "--force")[0], 0)
        self.assertIn("# from the node", util.read_text(self.laptop))
        # removed: it gets nothing any more, and the store is no longer encrypted to it
        self.n.remove("desktop-2")
        rc, out = self.laptop_does("pull", "--force")
        self.assertNotEqual(rc, 0)
        self.assertNotIn("desktop-2", [n for n, _k in self.n.controllers()])
        with self.assertRaisesRegex(KiwiError, "own key"):
            self.n.remove("control")

    def test_a_second_fleet_folder_on_one_computer_keeps_its_key(self):
        write(os.path.join(self.env["FAKE_KEYRING"], "item"), "AGE-SECRET-KEY-1THEKEYTHISCOMPUTERHASALREADY")
        rc, out = self.laptop_does("join", self.n.new_invite(self.url))
        self.assertNotEqual(rc, 0)
        self.assertIn("already has a key", out)
        self.assertIn("THEKEYTHISCOMPUTERHASALREADY", util.read_text(os.path.join(self.env["FAKE_KEYRING"], "item")))
        self.assertEqual([n for n, _k in self.n.controllers()], ["desktop", "recovery", "control"])

    def test_an_invite_is_short_lived_and_guess_proof(self):
        key = store.keygen()[1]
        self.assertIsNone(self.n.join("anything", "x", key))                              # no invite out
        code = controlsync.parse_invite(self.n.new_invite(self.url))[1]
        for _ in range(3):
            self.assertIsNone(self.n.join("a" * 20 + "." + code.rsplit(".", 1)[1], "x", key))
        self.assertIsNone(self.n.join(code, "x", key))                                    # three wrong guesses voided it
        code = controlsync.parse_invite(self.n.new_invite(self.url))[1]
        self.assertIsNone(self.n.join(code, "Not A Name", key))
        self.assertEqual(self.n.join(code, "x", key), "x")
        self.assertIsNone(self.n.challenge("nobody"))
        self.assertIsNone(self.n.controller("no such token"))
        self.n.lock_store()
        with self.assertRaisesRegex(KiwiError, "locked"):
            self.n.new_invite(self.url)
        with self.assertRaises(KiwiError):
            controlsync.parse_invite("https://example.org/no-invite")



class TestQr(unittest.TestCase):
    """The QR encoder against qrencode, module for module, and by itself."""

    def test_shapes(self):
        from kiwiserver import qr
        self.assertEqual(len(qr.matrix("x")), 21)                       # version 1
        self.assertEqual(qr.version_for(17), 1)
        self.assertEqual(qr.version_for(18), 2)
        self.assertEqual(qr.version_for(271), 10)
        self.assertEqual(qr.version_for(272), 11)
        with self.assertRaises(KiwiError):
            qr.version_for(900)
        m = qr.matrix("[Interface]\nPrivateKey = abc\n" * 9)
        size = len(m)
        self.assertTrue(all(len(row) == size for row in m))
        self.assertEqual(m[size - 8][8], 1)                              # the dark module
        for r0, c0 in ((0, 0), (0, size - 7), (size - 7, 0)):            # the three finders
            self.assertEqual([m[r0][c0 + i] for i in range(7)], [1] * 7)
            self.assertEqual(m[r0 + 3][c0 + 3], 1)
        # the format information reads back: level L, a mask, and its error correction agrees
        info = sum(m[8][size - 1 - i] << i for i in range(8)) | sum(m[size - 15 + i][8] << i for i in range(8, 15))
        data = (info ^ 0x5412) >> 10
        self.assertEqual(data >> 3, 0b01)
        self.assertEqual(qr._bch(data, 5, 0x537, 11) ^ 0x5412, info)
        self.assertTrue(qr.png("hello").startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn("<svg", qr.svg("hello"))
        self.assertEqual(len(qr.text_blocks("hello").splitlines()), (21 + 4 + 1) // 2)

    @unittest.skipUnless(have("qrencode"), "qrencode not installed")
    def test_every_module_is_qrencodes(self):
        import random
        from kiwiserver import qr
        rnd = random.Random(7)
        for n in (1, 17, 18, 100, 154, 271, 272, 330, 458, 520, 644, 858):
            text = "".join(rnd.choice("abcdefghijklmnopqrstuvwxyzABCDEFGH0123456789=/+ \n[]") for _ in range(n))
            out = subprocess.run(["qrencode", "-l", "L", "-8", "-m", "0", "-t", "ASCII", "-o", "-"],
                                 input=text.encode(), capture_output=True, check=True).stdout.decode()
            theirs = [[1 if line[i:i + 2] == "##" else 0 for i in range(0, len(line), 2)] for line in out.splitlines() if line]
            size = len(theirs)
            info = sum(theirs[8][size - 1 - i] << i for i in range(8)) | sum(theirs[size - 15 + i][8] << i for i in range(8, 15))
            mask = ((info ^ 0x5412) >> 10) & 7                           # qrencode's choice of mask: the same one here
            self.assertEqual(qr.matrix(text, mask), theirs, "%d bytes" % n)



@unittest.skipUnless(have("age") and have("age-keygen"), "age not installed")
class TestFolderSync(StoreBase):
    """Two computers and a folder something else syncs (design/controllers.md, step 2): the
    first pushes, the second asks to join, the first accepts, both stay in step."""

    def setUp(self):
        super().setUp()
        self.init()
        self.folder = os.path.join(self.tmp, "Nextcloud", "kiwi")
        self.laptop = os.path.join(self.tmp, "laptop", "fleet.yaml")
        self.env = dict(os.environ, XDG_RUNTIME_DIR=os.path.join(self.tmp, "laptop-run"),
                        FAKE_KEYRING=os.path.join(self.tmp, "laptop-keyring"),
                        PYTHONPATH=os.path.join(ROOT, "lib"), PYTHONDONTWRITEBYTECODE="1")
        for d in ("laptop", "laptop-run", "laptop-keyring"):
            os.makedirs(os.path.join(self.tmp, d))

    def laptop_does(self, *args):
        r = subprocess.run([sys.executable, "-m", "kiwiserver", "control", args[0], self.laptop] + list(args[1:]),
                           capture_output=True, text=True, env=self.env, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout + r.stderr

    def test_push_join_accept_and_stay_in_step(self):
        rc, out, err = self.run_cli("control", "requests", self.f.path)
        self.assertIn("keeps no synced folder", err)
        rc, out, err = self.run_cli("control", "push", self.f.path, "--dir", self.folder)
        self.assertEqual(rc, 0, err)
        self.assertEqual(sorted(os.listdir(self.folder)),
                         ["control", "fleet.yaml.age", "kiwi-fleet.json", "secrets.age", "secrets.recipients"])
        for root, _d, files in os.walk(self.folder):
            for f in files:
                with open(os.path.join(root, f), "rb") as fh:
                    data = fh.read()
                self.assertNotIn(b"S" * 32, data)                # the seed
                self.assertNotIn(b"Europe/Berlin", data)         # the fleet file
        rc, out, err = self.run_cli("control", "push", self.f.path)          # remembered: no --dir needed
        self.assertEqual(rc, 0, err)
        # the laptop asks; until it is accepted its key opens nothing
        rc, out = self.laptop_does("pull", "--dir", self.folder)
        self.assertIn("no key for the fleet", out)
        rc, out = self.laptop_does("join", "--dir", self.folder, "--name", "laptop")
        self.assertEqual(rc, 0, out)
        self.assertIn("asks to join as laptop", out)
        rc, out = self.laptop_does("join", "--dir", self.folder, "--name", "laptop")
        self.assertNotEqual(rc, 0)
        self.assertIn("not accepted yet", out)
        rc, out, err = self.run_cli("control", "requests", self.f.path, "--porcelain")
        self.assertEqual([r["name"] for r in json.loads(out)["requests"]], ["laptop"])
        rc, out, err = self.run_cli("control", "accept", self.f.path, "nobody")
        self.assertIn("nobody called nobody", err)
        rc, out, err = self.run_cli("control", "accept", self.f.path, "laptop")
        self.assertEqual(rc, 0, err)
        self.assertEqual(os.listdir(os.path.join(self.folder, "requests")), [])
        self.assertIn("laptop", [n for n, _k in store.Store.for_fleet(self.f.path).ways()])
        rc, out = self.laptop_does("join", "--dir", self.folder, "--name", "laptop")
        self.assertEqual(rc, 0, out)
        self.assertIn("Europe/Berlin", util.read_text(self.laptop))
        seed = os.path.join(os.path.dirname(self.laptop), "secrets", "seed")
        self.assertEqual(util.read_text(seed), "S" * 32)
        self.assertTrue(os.path.realpath(seed).startswith(self.env["XDG_RUNTIME_DIR"]))    # opened into its own RAM
        self.assertIn("in step", self.laptop_does("pull")[1])
        # a change travels both ways; two changes are not overwritten
        with open(self.laptop, "a") as fh:
            fh.write("# from the laptop\n")
        self.assertEqual(self.laptop_does("push")[0], 0)
        with open(self.f.path, "a") as fh:
            fh.write("# from the desktop\n")
        rc, out, err = self.run_cli("control", "push", self.f.path)
        self.assertIn("changed on both sides since the last sync: fleet.yaml", err)
        rc, out, err = self.run_cli("control", "pull", self.f.path)
        self.assertIn("Which copy counts?", err)                 # the same file on both sides: the owner decides
        rc, out, err = self.run_cli("control", "pull", self.f.path, "--force")
        self.assertEqual(rc, 0, err)
        self.assertIn("# from the laptop", util.read_text(self.f.path))
        # a second request, denied: nothing is added
        os.makedirs(os.path.join(self.tmp, "other"))
        env2 = dict(self.env, FAKE_KEYRING=os.path.join(self.tmp, "other"), XDG_RUNTIME_DIR=os.path.join(self.tmp, "other"))
        subprocess.run([sys.executable, "-m", "kiwiserver", "control", "join", os.path.join(self.tmp, "other", "fleet.yaml"),
                        "--dir", self.folder, "--name", "stranger"], capture_output=True, env=env2, check=True)
        rc, out, err = self.run_cli("control", "push", self.f.path)
        self.assertIn("stranger", out)                           # said when this computer syncs
        rc, out, err = self.run_cli("control", "deny", self.f.path, "stranger")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("stranger", [n for n, _k in store.Store.for_fleet(self.f.path).ways()])
        self.assertEqual(controlsync.requests(self.folder), [])

    def both_joined(self):
        self.assertEqual(self.run_cli("control", "push", self.f.path, "--dir", self.folder)[0], 0)
        self.assertEqual(self.laptop_does("join", "--dir", self.folder, "--name", "laptop")[0], 0)
        self.assertEqual(self.run_cli("control", "accept", self.f.path, "laptop")[0], 0)
        rc, out = self.laptop_does("join", "--dir", self.folder, "--name", "laptop")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.run_cli("control", "pull", self.f.path)[0], 0)
        return os.path.join(os.path.dirname(self.laptop), "secrets")

    def test_changes_to_different_files_on_both_sides_become_one_copy(self):
        there = self.both_joined()
        write(os.path.join(there, "devices", "phone.conf"), "made on the laptop")
        write(os.path.join(there, "known_hosts"), "sh3 key-the-laptop-saw\n")
        rc, out = self.laptop_does("push")
        self.assertEqual(rc, 0, out)
        write(os.path.join(self.secrets, "known_hosts"), "m1 key-the-desktop-saw\n")
        with open(self.f.path, "a") as fh:
            fh.write("# from the desktop\n")
        rc, out, err = self.run_cli("control", "push", self.f.path)            # both changed, not the same files
        self.assertEqual(rc, 0, err)
        self.assertEqual(util.read_text(os.path.join(self.secrets, "devices", "phone.conf")), "made on the laptop")
        self.assertEqual(util.read_text(os.path.join(self.secrets, "known_hosts")),
                         "m1 key-the-desktop-saw\nsh3 key-the-laptop-saw\n")       # host keys of both
        rc, out = self.laptop_does("pull")
        self.assertEqual(rc, 0, out)
        self.assertIn("# from the desktop", util.read_text(self.laptop))
        self.assertIn("m1 key-the-desktop-saw", util.read_text(os.path.join(there, "known_hosts")))
        self.assertIn("in step", self.run_cli("control", "pull", self.f.path)[1] + self.run_cli("control", "pull", self.f.path)[2])
        # and a pull does it as well, when it is this side that looks first
        write(os.path.join(there, "note"), "laptop again")
        self.assertEqual(self.laptop_does("push")[0], 0)
        write(os.path.join(self.secrets, "other"), "desktop again")
        rc, out, err = self.run_cli("control", "pull", self.f.path)
        self.assertEqual(rc, 0, err)
        self.assertEqual(util.read_text(os.path.join(self.secrets, "note")), "laptop again")
        self.assertEqual(self.laptop_does("pull")[0], 0)
        self.assertEqual(util.read_text(os.path.join(there, "other")), "desktop again")

    def test_a_folder_caught_mid_sync_is_refused(self):
        self.run_cli("control", "push", self.f.path, "--dir", self.folder)
        with open(os.path.join(self.folder, "secrets.age"), "ab") as fh:
            fh.write(b"half of the next version")
        with self.assertRaisesRegex(KiwiError, "not complete yet"):
            controlsync.folder_state(self.folder)
        rc, out, err = self.run_cli("control", "push", self.f.path)
        self.assertIn("not complete yet", err)
        os.remove(os.path.join(self.folder, "kiwi-fleet.json"))
        self.assertEqual(controlsync.folder_state(self.folder), "")       # no list: no fleet there (yet)
        write(os.path.join(self.folder, "requests", "x.json"), '{"name": "y", "key": "age1bad"}')
        self.assertEqual(controlsync.requests(self.folder), [])           # a request that is not one is not shown


class TestRotate(Base):
    """New SSH keys. A machine is a directory here; ssh is a function that runs the script
    with that directory as the admin's home."""
    def setUp(self):
        super().setUp()
        import getpass
        from kiwiserver import rotate
        self.rotate = rotate
        self.user = getpass.getuser()
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        write(os.path.join(self.bin, "getent"), '#!/bin/sh\necho "u:x:0:0::$FAKE_HOME:/bin/sh"\n')
        os.chmod(os.path.join(self.bin, "getent"), 0o755)
        self.key = os.path.join(self.tmp, "secrets", "admin_ed25519")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "the fleet", "-f", self.key], check=True)
        self.old = util.read_text(self.key + ".pub").split()[1]
        self.f = self.fleet({"defaults": self.base_defaults(admin={"user": self.user, "ssh_keys": ["secrets/admin_ed25519.pub", KEY]}),
                             "hosts": {"a": {}, "b": {}}})
        for n in ("a", "b"):            # as Ignition leaves it, and without a newline at the end
            write(os.path.join(self.home(n), ".ssh", "authorized_keys.d", "ignition"),
                  KEY + "\n" + util.read_text(self.key + ".pub").strip())
        self.down, self.refuses = set(), set()

    def home(self, name):
        return os.path.join(self.tmp, "home-" + name)

    def keys_on(self, name):
        d = os.path.join(self.home(name), ".ssh")
        files = [os.path.join(d, "authorized_keys")] + [os.path.join(d, "authorized_keys.d", f)
                                                         for f in os.listdir(os.path.join(d, "authorized_keys.d"))]
        return "".join(util.read_text(f) for f in files if os.path.isfile(f))

    def reach(self, h):
        if h.name in self.down:
            raise KiwiError("%s: unreachable" % h.name)
        return "%s@%s" % (self.user, h.name)

    def runner(self, argv, input=None, capture_output=True, stdin=None):
        name = argv[-2].split("@")[1]
        if argv[-1] == "true":          # a login with one key alone
            blob = util.read_text(argv[argv.index("-i") + 1] + ".pub").split()[1]
            ok = blob in self.keys_on(name) and name not in self.refuses
            return subprocess.CompletedProcess(argv, 0 if ok else 255, b"", b"")
        script = shlex.split(argv[-1])[-1]
        env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ["PATH"], FAKE_HOME=self.home(name))
        return subprocess.run(["sh", "-c", script], capture_output=True, env=env)

    def go(self, *names, **kw):
        said = []
        left = self.rotate.ssh(self.f, names, reach=self.reach, say=said.append, runner=self.runner, **kw)
        return left, said

    def new(self):
        return util.read_text(self.key + ".pub").split()[1]

    def test_every_machine_takes_the_new_key_and_the_old_one_goes(self):
        self.assertIn("never replaced", str(self.rotate.plan(self.f)))
        left, said = self.go()
        self.assertEqual(left, [])
        self.assertNotEqual(self.new(), self.old)
        for n in ("a", "b"):
            lines = self.keys_on(n).splitlines()
            self.assertEqual(lines, [KEY, " ".join(util.read_text(self.key + ".pub").split())])     # the operator's own key stays
        self.assertFalse(os.path.exists(self.key + ".old"))
        self.assertEqual(self.rotate.pending(self.f), [])
        self.assertIn("replaced 20", str(self.rotate.plan(self.f)))
        self.assertIn("outside the fleet", said[-1])             # a desktop that got the key by hand is not reached
        self.assertIn("secrets/admin_ed25519.pub", said[-1])
        self.assertIn("machines outside the fleet", str(self.rotate.plan(self.f)))
        self.assertIn("a key of yours", str(self.rotate.plan(self.f)))
        self.assertEqual(oct(os.stat(self.key).st_mode & 0o777), "0o600")

    def test_a_machine_that_is_off_keeps_the_old_key_until_the_next_run(self):
        self.down = {"b"}
        left, said = self.go()
        self.assertEqual(left, ["b"])
        self.assertIn("still on the old keys: b", said[-1])
        self.assertIn(self.old, self.keys_on("b"))
        self.assertNotIn(self.old, self.keys_on("a"))
        self.assertEqual(cli.fleet_identities(self.f), [self.key, self.key + ".old"])       # ssh tries both meanwhile
        new = self.new()
        self.down = set()
        left, said = self.go()                                  # no second new key: the same rotation goes on
        self.assertEqual((left, self.new()), ([], new))
        self.assertEqual([x for x in said if x.startswith("a:")], [])
        self.assertNotIn(self.old, self.keys_on("b"))
        self.assertEqual(cli.fleet_identities(self.f), [self.key])

    def test_the_old_key_stays_where_the_new_one_does_not_open(self):
        self.refuses = {"a"}
        left, said = self.go("a")
        self.assertEqual(left, ["a", "b"])
        self.assertIn("the old one stays", said[0])
        self.assertIn(self.old, self.keys_on("a"))

    def test_a_machine_left_out_does_not_hold_the_rotation_open(self):
        self.down = {"b"}
        left, _said = self.go(waive=["b"])
        self.assertEqual(left, [])
        self.assertFalse(os.path.exists(self.key + ".old"))
        with self.assertRaisesRegex(KiwiError, "no such machine"):
            self.go(waive=["nope"])

    def test_a_machine_without_the_old_key_gets_the_new_one(self):
        os.remove(os.path.join(self.home("a"), ".ssh", "authorized_keys.d", "ignition"))
        self.assertEqual(self.go("a")[0], ["b"])
        path = os.path.join(self.home("a"), ".ssh", "authorized_keys")
        self.assertIn(self.new(), util.read_text(path))
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")

    def test_the_ssh_ca_changes_with_it(self):
        sshca.init(self.f)
        was = sshca.public(self.f).split()[1]
        known = os.path.join(self.tmp, "secrets", "known_hosts")
        signed = []
        with mock.patch.object(sshca, "install", lambda fleet, h, tgt: signed.append((h.name, sshca.public(fleet).split()[1]))):
            self.down = {"b"}
            self.go()
            both = [x for x in util.read_text(known).splitlines() if x.startswith("@cert-authority ")]
            self.assertEqual(len(both), 2)                      # a machine with the old certificate is still known
            self.down = set()
            self.go()
        now = sshca.public(self.f).split()[1]
        self.assertNotEqual(now, was)
        self.assertEqual(signed, [("a", now), ("b", now)])
        lines = [x for x in util.read_text(known).splitlines() if x.startswith("@cert-authority ")]
        self.assertEqual(len(lines), 1)
        self.assertIn(now, lines[0])
        self.assertFalse(os.path.exists(sshca.path(self.f) + ".old"))

    def test_the_command_tells_the_other_computers_before_and_after(self):
        order = []
        with mock.patch.object(cli, "share", lambda _p, action: order.append(action) or True), \
                mock.patch.object(self.rotate, "ssh", lambda *_a, **_k: order.append("machines") or []), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["rotate", self.f.path, "ssh"])
            cli.main(["rotate", self.f.path])                    # the list asks nobody
        self.assertEqual(order, ["pull", "push", "machines", "push"])
        self.assertTrue(os.path.isfile(self.key + ".old"))       # made before the first push

    def seed_fleet(self):
        write(os.path.join(self.tmp, "secrets", "seed"), "S" * 32)
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {
            "sh3": {"role": "node-cloud", "node-cloud": self.node_cloud(cloud={})},
            "sh4": {"role": "node-cloud", "node-cloud": self.node_cloud(cloud={}, vpn_ip="10.8.0.26")}}})
        for h in f.hosts.values():
            self.assertEqual(h.validate(self.roles), [])
            rolesmod.resolve_settings(self.roles[h.role], h, self.ms)
        return f

    def secrets_of(self, f, name):
        h = config.Fleet.load(f.path).hosts[name]
        rolesmod.resolve_settings(self.roles[h.role], h, self.ms)
        return h.module_settings["cloud"]

    def test_a_new_seed_changes_what_a_restart_can_change_and_pins_the_rest(self):
        f = self.seed_fleet()
        before = self.secrets_of(f, "sh3")
        self.assertIn("never replaced", str(self.rotate.plan(f, (), self.ms)))
        applied, said = [], []
        left = self.rotate.seed(f, self.ms, lambda n: applied.append(n) or (1 if n == "sh4" else 0), say=said.append)
        self.assertEqual((applied, left), (["sh3", "sh4"], ["sh4"]))         # sh4's apply did not work
        after = self.secrets_of(f, "sh3")
        self.assertNotEqual(after["redis_password"], before["redis_password"])       # read at every start
        self.assertEqual(after["database_password"], before["database_password"])    # lives in the database
        self.assertEqual(after["admin_password"], before["admin_password"])
        self.assertEqual(self.secrets_of(f, "sh4")["database_password"], f.derive_secret("sh4.kiwi", "cloud", "database_password"))
        pins = json.loads(util.read_text(os.path.join(self.tmp, "secrets", "pins.json")))
        self.assertIn("sh3.kiwi/cloud/database_password", pins)
        self.assertNotIn("sh3.kiwi/cloud/redis_password", pins)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "secrets", "seed.old")))       # a way back until all are done
        was = config.Fleet.load(f.path).seed_from()
        self.assertRegex(was, r"^[0-9a-f]{12}$")
        self.assertNotEqual(was, config.Fleet.load(f.path).seed_id())
        self.assertIn("under way: sh4", str(self.rotate.plan(f, (), self.ms)))
        seed = util.read_bytes(os.path.join(self.tmp, "secrets", "seed"))
        applied.clear()
        left = self.rotate.seed(f, self.ms, lambda n: applied.append(n) or 0, say=said.append)      # the same rotation goes on
        self.assertEqual((applied, left), (["sh4"], []))
        self.assertEqual(util.read_bytes(os.path.join(self.tmp, "secrets", "seed")), seed)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "secrets", "seed.old")))
        self.assertEqual(config.Fleet.load(f.path).seed_from(), was)          # a late machine is still taken along
        self.assertIn("replaced 20", str(self.rotate.plan(f, (), self.ms)))
        rows = str(self.rotate.plan(f, (), self.ms))
        self.assertIn("kept as they are", rows)
        self.assertIn("Admin logins of the services", rows)          # the owner's to change, said so
        self.assertIn("change the password in the service itself", rows)

    def test_a_database_password_is_changed_where_it_lives_before_its_files_follow(self):
        write(os.path.join(self.tmp, "secrets", "seed"), "S" * 32)
        node = {"modules": ["vpn-client", "immich", "paperless"], "vpn_ip": "10.8.0.25",
                "vpn-client": {"wireguard_config": "secrets/wg.conf"}, "paperless": {"db_password": "written by hand"}}
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {"sh3": {"role": "node", "node": node}}})
        h = f.hosts["sh3"]
        self.assertEqual(h.validate(self.roles), [])
        rolesmod.resolve_settings(self.roles[h.role], h, self.ms)
        old = h.module_settings["immich"]["db_password"]
        ran, said, fails = [], [], [True]

        def run(tgt, script, data=None):
            ran.append((tgt, script, data))
            if fails[0]:
                raise KiwiError("the container is not running")

        def before(n):
            self.rotate.in_place(f, f.hosts[n], "core@sh3", self.roles, self.ms, said.append, run)
        self.rotate.seed(f, self.ms, lambda n: 0, say=said.append, before=before)
        pins = json.loads(util.read_text(os.path.join(self.tmp, "secrets", "pins.json")))
        self.assertEqual(pins["sh3.kiwi/immich/db_password"], old)             # it did not work: the old one stays
        self.assertIn("keeps its value", "".join(said))
        self.assertEqual(len(ran), 1)                                          # the one written by hand is not ours
        fails[0] = False
        self.rotate.seed(f, self.ms, lambda n: 0, say=said.append, before=before)      # the next rotation tries again
        tgt, script, data = ran[-1]
        new = data.decode().strip()
        self.assertRegex(new, r"^[A-Za-z0-9]{32}$")
        self.assertNotEqual(new, old)
        self.assertNotIn(new, script)                                          # on stdin, not on a command line
        self.assertIn("podman exec -e NEW kn-immich-db sh -c", script)
        self.assertIn("ALTER USER immich", script)
        pins = json.loads(util.read_text(os.path.join(self.tmp, "secrets", "pins.json")))
        self.assertNotIn("sh3.kiwi/immich/db_password", pins)
        h = config.Fleet.load(f.path).hosts["sh3"]
        rolesmod.resolve_settings(self.roles[h.role], h, self.ms)
        self.assertEqual(h.module_settings["immich"]["db_password"], new)      # and the files follow
        self.assertEqual(h.module_settings["paperless"]["db_password"], "written by hand")

    def test_a_secret_with_several_steps_runs_them_in_order_in_their_containers(self):
        f = self.seed_fleet()
        ran = []
        self.rotate.seed(f, self.ms, lambda n: 0, say=lambda _x: None,
                         before=lambda n: self.rotate.in_place(f, f.hosts[n], "core@" + n, self.roles, self.ms, lambda _x: None,
                                                               lambda tgt, script, data=None: ran.append((tgt, script))))
        mine = [script for tgt, script in ran if tgt == "core@sh3"]
        self.assertEqual(len(mine), 2)                                         # Nextcloud's database password: two places
        self.assertIn("exec -e NEW nextcloud-aio-nextcloud sh -c", mine[0])   # a container with a name of its own
        self.assertIn("config:system:set dbpassword", mine[0])
        self.assertIn("exec -e NEW nextcloud-aio-database sh -c", mine[1])
        self.assertIn("ALTER USER oc_nextcloud", mine[1])
        self.assertNotIn("sh3.kiwi/cloud/database_password", config.Fleet.load(f.path).pins())

    def stack_vars(self, f, name):
        from kiwimodules import manifest
        h = f.hosts[name]
        rolesmod.resolve_settings(self.roles[h.role], h, self.ms)
        spec = rolesmod.stack_spec(h, self.roles[h.role], [("10.8.0.25", "sh3.kiwi")], master_ip="10.8.0.1")
        return "\n".join(manifest.stack_vars(h, modmod.Renderer(self.ms).render(spec)))

    def test_a_pin_goes_once_its_secret_can_follow_the_seed(self):
        f = self.seed_fleet()
        was = f.derive_secret("sh3.kiwi", "cloud", "redis_password")
        write(os.path.join(self.tmp, "secrets", "pins.json"),
              json.dumps({"sh3.kiwi/cloud/redis_password": was, "sh3.kiwi/cloud/database_password": "kept"}))
        self.rotate.seed(f, self.ms, lambda n: 0, say=lambda _x: None)
        pins = config.Fleet.load(f.path).pins()
        self.assertNotIn("sh3.kiwi/cloud/redis_password", pins)        # an older release pinned it; now it follows
        self.assertEqual(pins["sh3.kiwi/cloud/database_password"], "kept")
        self.assertNotEqual(self.secrets_of(f, "sh3")["redis_password"], was)

    def test_the_role_script_names_the_seed_it_follows(self):
        f = self.seed_fleet()
        self.assertNotIn("KS_SEED_FROM", self.stack_vars(f, "sh3"))
        self.rotate.seed(f, self.ms, lambda n: 0, say=lambda _x: None)
        f = config.Fleet.load(f.path)
        sh = self.stack_vars(f, "sh3")
        self.assertIn("KS_SEED_FROM='%s'" % f.seed_from(), sh)
        self.assertIn("KS_SEED_ID='%s'" % f.seed_id(), sh)

    def test_the_seed_command_tells_the_others_and_applies_each_machine(self):
        f = self.seed_fleet()
        order = []
        real_main = cli.main

        def fake_main(argv):
            if argv[0] == "apply":
                order.append("apply " + argv[2])
                return 0
            return real_main(argv)
        with mock.patch.object(cli, "share", lambda _p, action: order.append(action) or True), \
                mock.patch.object(cli, "main", fake_main), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            real_main(["rotate", f.path, "seed", "--why", "a test"])
        self.assertEqual(order, ["pull", "push", "apply sh3", "apply sh4", "push"])

    def test_a_fleet_without_keys_of_its_own_says_so(self):
        f = self.fleet({"defaults": self.base_defaults(), "hosts": {"a": {}}})
        with self.assertRaisesRegex(KiwiError, "by hand"):
            self.rotate.begin(f)


class TestMerge(unittest.TestCase):
    """Two copies that both changed, made one: by file, against what both held last."""
    def merge(self, base, here, there):
        from kiwiserver import merge
        enc = lambda d: {k: v.encode() for k, v in d.items()}
        merged, theirs, both = merge.merge(enc(base), enc(here), enc(there))
        return {k: v.decode() for k, v in merged.items()}, theirs, both

    def test_each_side_keeps_what_only_it_changed(self):
        base = {"fleet.yaml": "v1", "secrets/seed": "s", "secrets/a.conf": "a", "secrets/gone": "x"}
        here = {"fleet.yaml": "v2", "secrets/seed": "s", "secrets/a.conf": "a", "secrets/new-here": "h"}
        there = {"fleet.yaml": "v1", "secrets/seed": "s", "secrets/a.conf": "A", "secrets/gone": "x", "secrets/new-there": "t"}
        merged, theirs, both = self.merge(base, here, there)
        self.assertEqual(merged, {"fleet.yaml": "v2", "secrets/seed": "s", "secrets/a.conf": "A",
                                  "secrets/new-here": "h", "secrets/new-there": "t"})      # `gone` was removed here
        self.assertEqual((theirs, both), (["secrets/a.conf", "secrets/new-there"], []))

    def test_host_keys_of_both_sides_are_kept(self):
        base = {"secrets/known_hosts": "a k1\n"}
        merged, theirs, both = self.merge(base, {"secrets/known_hosts": "a k1\nb k2\n"}, {"secrets/known_hosts": "a k1\nc k3\n"})
        self.assertEqual(merged["secrets/known_hosts"], "a k1\nb k2\nc k3\n")
        self.assertEqual(both, [])

    def test_a_file_both_changed_is_named_and_stays_as_it_is_here(self):
        merged, theirs, both = self.merge({"fleet.yaml": "v1"}, {"fleet.yaml": "mine"}, {"fleet.yaml": "theirs"})
        self.assertEqual((merged, both), ({"fleet.yaml": "mine"}, ["fleet.yaml"]))
        merged, theirs, both = self.merge({"fleet.yaml": "v1"}, {"fleet.yaml": "same"}, {"fleet.yaml": "same"})
        self.assertEqual((merged, both), ({"fleet.yaml": "same"}, []))             # the same change on both sides


@unittest.skipUnless(have("age") and have("age-keygen"), "age not installed")
class TestNetworkCopy(StoreBase):
    """The copy of a fleet with a store: the store plus the fleet file, opened by the recovery key."""
    def setUp(self):
        super().setUp()
        write(os.path.join(self.tmp, "notes", "router.txt"), "the router's page: 192.168.1.1\n")
        write(os.path.join(self.tmp, "federation.yaml"), "# no partners yet\n{}\n")
        self.f = self.fleet({"defaults": self.base_defaults(backup={"extra": ["notes"]}), "hosts": {"a": {}}})
        self.out = os.path.join(self.tmp, "stick")
        os.makedirs(self.out)

    def copy(self):
        """A copy made through the CLI: (its path, its bytes)."""
        before = set(os.listdir(self.out))
        rc, out, err = self.run_cli("backup", self.f.path, "--local", self.out)
        self.assertEqual(rc, 0, err)
        name = (set(os.listdir(self.out)) - before).pop()
        with open(os.path.join(self.out, name), "rb") as fh:
            return os.path.join(self.out, name), fh.read()

    def another_computer(self):
        """The first one is gone: nothing of the store in memory, an empty keyring."""
        self.assertEqual(self.run_cli("secrets", "lock", self.f.path)[0], 0)
        kr = os.path.join(self.tmp, "keyring-new")
        os.makedirs(kr, exist_ok=True)
        os.environ["FAKE_KEYRING"] = kr
        return kr

    def keyfile(self, key):
        path = os.path.join(self.tmp, "recovery.key")
        write(path, "# the recovery key\n%s\n" % key)
        return path

    def test_the_copy_is_the_store_and_the_fleet_file_for_the_stores_keys(self):
        self.init()
        write(os.path.join(self.secrets, "later"), "made after the store was opened")     # in memory only so far
        path, data = self.copy()
        self.assertRegex(os.path.basename(path), r"^fleet-fleet-\d{8}T\d{6}Z\.tar$")
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        self.assertEqual(backup.kind(data), "store")
        with tarfile.open(path) as tar:
            names = tar.getnames()
            manifest = json.load(tar.extractfile("MANIFEST.json"))
            members = {n: tar.extractfile(n).read() for n in names}
        self.assertEqual(names, ["MANIFEST.json", "secrets.age", "secrets.recipients", "fleet.yaml.age",
                                 "federation.yaml.age", "notes/router.txt.age"])
        self.assertEqual((manifest["format"], manifest["kiwi-server"], manifest["fleet"]), (2, VERSION, "fleet.yaml"))
        self.assertRegex(manifest["time"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual([f["name"] for f in manifest["files"]], names[1:])
        for f in manifest["files"]:
            self.assertEqual(f["sha256"], __import__("hashlib").sha256(members[f["name"]]).hexdigest())
            self.assertEqual(f["size"], len(members[f["name"]]))
        for plain in (b"Europe/Berlin", b"192.168.1.1", b"private", b"made after", b"AGE-SECRET-KEY"):
            self.assertNotIn(plain, data)                               # nothing in it is readable
        with open(os.path.join(self.tmp, "secrets.age"), "rb") as fh:
            self.assertEqual(members["secrets.age"], fh.read())         # the store as it rests, saved first
        # no passphrase is asked for, and one that is given is not needed
        pf = os.path.join(self.tmp, "pass")
        write(pf, "correct horse\n")
        rc, out, err = self.run_cli("backup", self.f.path, "--local", self.out, "--passphrase-file", pf)
        self.assertEqual(rc, 0, err)
        self.assertIn("the passphrase is not used", out)
        self.assertEqual([n for n in os.listdir(self.out) if n.endswith(".enc")], [])

    def test_the_recovery_key_brings_it_back_on_another_computer(self):
        recovery = self.init()
        write(os.path.join(self.secrets, "later"), "made after the store was opened")
        path, _data = self.copy()
        with open(self.f.path, "rb") as fh:
            fleet_file = fh.read()
        kr = self.another_computer()
        into = os.path.join(self.tmp, "back")
        # another network's key: said in plain words, nothing written
        rc, out, err = self.run_cli("restore", path, "--into", into, "--recovery-file", self.keyfile(store.keygen()[0]))
        self.assertEqual(rc, 1)
        self.assertIn("that key does not open this copy", err)
        self.assertFalse(os.path.exists(into))
        self.assertEqual(os.listdir(kr), [])
        rc, out, err = self.run_cli("restore", path, "--into", into, "--recovery-file", self.keyfile("not a key at all"))
        self.assertEqual(rc, 1)
        self.assertIn("holds no recovery key", err)
        self.assertFalse(os.path.exists(into))
        # the right one, as a person types it
        with mock.patch.dict(os.environ, {"KIWI_RECOVERY_KEY": " %s \n" % recovery.lower()}):
            rc, out, err = self.run_cli("restore", path, "--into", into)
        self.assertEqual(rc, 0, err)
        self.assertNotIn(recovery, out + err)
        self.assertEqual(len([ln for ln in out.splitlines() if ln.startswith((":: next: ", ":: then: "))]), 2)
        with open(os.path.join(into, "fleet.yaml"), "rb") as fh:
            self.assertEqual(fh.read(), fleet_file)
        with open(os.path.join(into, "notes", "router.txt")) as fh:
            self.assertEqual(fh.read(), "the router's page: 192.168.1.1\n")
        for name in ("fleet.yaml", "federation.yaml", "notes/router.txt", "secrets.age"):
            self.assertEqual(oct(os.stat(os.path.join(into, name)).st_mode & 0o777), "0o600", name)
        self.assertEqual(oct(os.stat(into).st_mode & 0o777), "0o700")
        st = store.Store(into)
        self.assertTrue(st.is_open())                                   # left open, in memory
        self.assertTrue(os.path.islink(os.path.join(into, "secrets")))
        for name, text in (("deep/key", "private"), ("later", "made after the store was opened"), ("seed", "S" * 32)):
            with open(os.path.join(into, "secrets", name)) as fh:
                self.assertEqual(fh.read(), text)
        # this computer has a key of its own now, in its keyring: no recovery key the next time
        self.assertEqual(len(st.ways()), 3)
        self.assertEqual([n for n, _k in st.ways()][:2], ["desktop", "recovery"])
        self.assertEqual(self.run_cli("secrets", "lock", os.path.join(into, "fleet.yaml"))[0], 0)
        rc, out, err = self.run_cli("secrets", "unlock", os.path.join(into, "fleet.yaml"))
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.run_cli("validate", os.path.join(into, "fleet.yaml"))[0], 0)
        # a folder that holds a network is not written over
        self.assertEqual(self.run_cli("secrets", "lock", os.path.join(into, "fleet.yaml"))[0], 0)
        rc, out, err = self.run_cli("restore", path, "--into", into, "--recovery-file", self.keyfile(recovery))
        self.assertEqual(rc, 1)
        self.assertIn("holds a network already", err)

    def test_without_a_keyring_the_recovery_key_stays_the_way_in(self):
        recovery = self.init()
        path, _data = self.copy()
        kr = self.another_computer()
        write(os.path.join(kr, "broken"), "")
        into = os.path.join(self.tmp, "back")
        rc, out, err = self.run_cli("restore", path, "--into", into, "--recovery-file", self.keyfile(recovery))
        self.assertEqual(rc, 0, err)
        self.assertIn("unlocking will ask for the recovery key", err)
        self.assertTrue(store.Store(into).is_open())
        self.assertEqual([n for n, _k in store.Store(into).ways()], ["desktop", "recovery"])
        # the app's dialog has one box, which reaches the command as a passphrase file: the key in it is taken
        store.Store(into).lock()
        again = os.path.join(self.tmp, "again")
        rc, out, err = self.run_cli("restore", path, "--into", again, "--passphrase-file", self.keyfile(recovery))
        self.assertEqual(rc, 0, err)
        pf = os.path.join(self.tmp, "pass")
        write(pf, "correct horse\n")
        rc, out, err = self.run_cli("restore", path, "--into", os.path.join(self.tmp, "third"), "--passphrase-file", pf)
        self.assertEqual(rc, 1)
        self.assertIn("recovery key (AGE-SECRET-KEY-…), not with a passphrase", err)

    def test_a_copy_that_was_changed_or_made_up_is_refused(self):
        recovery = self.init()
        _path, data = self.copy()
        manifest, files = backup.read_copy(data)
        into = os.path.join(self.tmp, "back")

        def refused(why, changed, fleet_name="fleet.yaml"):
            with self.assertRaisesRegex(KiwiError, why):
                backup.restore_copy(backup.pack(fleet_name, changed), recovery, into)
            self.assertFalse(os.path.exists(into))
        # a damaged file: the list knows its sha256
        cut = bytearray(data)
        at = data.index(files["fleet.yaml.age"][:40]) + 60
        cut[at] ^= 1
        with self.assertRaisesRegex(KiwiError, "damaged: fleet.yaml.age"):
            backup.restore_copy(bytes(cut), recovery, into)
        with self.assertRaisesRegex(KiwiError, "not a copy of a network"):
            backup.restore_copy(data[:3000], recovery, into)
        self.assertFalse(os.path.exists(into))
        # names that would leave the folder, or land on the store
        refused("not allowed", dict(files, **{"../x.age": files["fleet.yaml.age"]}))
        refused("not allowed", dict(files, **{"/etc/x.age": files["fleet.yaml.age"]}))
        refused("not allowed", dict(files, **{"secrets/seed.age": files["fleet.yaml.age"]}))
        refused("not allowed", dict(files, **{"secrets.age.age": files["fleet.yaml.age"]}))
        refused("not allowed", dict(files, **{"plain.txt": b"not encrypted"}))
        refused("missing", files, fleet_name="other.yaml")
        refused("missing", {k: v for k, v in files.items() if k != "secrets.age"})
        # the store's id names a directory in memory: only what a store writes
        for bad in (b"# store: ../../x\n" + files["secrets.recipients"], b"rm -rf /\n" + files["secrets.recipients"],
                    b"# nobody\n"):
            refused("list of keys", dict(files, **{"secrets.recipients": bad}))
        # a file the store's key does not open (encrypted for somebody else)
        other = os.path.join(self.tmp, "other.recipients")
        write(other, store.keygen()[1] + "\n")
        refused("opens its store but not notes/router.txt.age",
                dict(files, **{"notes/router.txt.age": store.encrypt_bytes(other, b"x")}))
        with tarfile.open(os.path.join(self.tmp, "x.tar"), "w") as tar:
            tar.add(self.f.path, arcname="fleet.yaml")
        with open(os.path.join(self.tmp, "x.tar"), "rb") as fh:
            self.assertIsNone(backup.kind(fh.read()))                   # a tar, but not a copy
        self.assertIsNone(backup.kind(b"hello"))
        rc, out, err = self.run_cli("restore", os.path.join(self.tmp, "x.tar"), "--into", into)
        self.assertEqual(rc, 1)
        self.assertIn("not a copy of a network", err)

    def test_a_store_cannot_write_outside_itself_through_a_link(self):
        # whoever can replace a copy can encrypt a store of their own making for the same public keys
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            link = tarfile.TarInfo("ca")
            link.type, link.linkname = tarfile.SYMTYPE, os.path.join(self.tmp, "elsewhere")
            tar.addfile(link)
            inside = tarfile.TarInfo("ca/planted")
            inside.size = 1
            tar.addfile(inside, io.BytesIO(b"x"))
        os.makedirs(os.path.join(self.tmp, "elsewhere"))
        with self.assertRaisesRegex(KiwiError, "through a link"):
            store.extract(buf.getvalue(), os.path.join(self.tmp, "tree"))
        self.assertEqual(os.listdir(os.path.join(self.tmp, "elsewhere")), [])
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            link = tarfile.TarInfo("seed")
            link.type, link.linkname = tarfile.SYMTYPE, os.path.join(self.tmp, "elsewhere", "file")
            tar.addfile(link)
            over = tarfile.TarInfo("seed")
            over.size = 1
            tar.addfile(over, io.BytesIO(b"x"))
        with self.assertRaisesRegex(KiwiError, "through a link"):
            store.extract(buf.getvalue(), os.path.join(self.tmp, "tree2"))
        self.assertEqual(os.listdir(os.path.join(self.tmp, "elsewhere")), [])

    def test_a_fleet_without_a_store_keeps_the_passphrase(self):
        pf = os.path.join(self.tmp, "pass")
        write(pf, "correct horse\n")
        rc, out, err = self.run_cli("backup", self.f.path, "--local", self.out, "--passphrase-file", pf)
        self.assertEqual(rc, 0, err)
        name = os.listdir(self.out)[0]
        self.assertRegex(name, r"^fleet-fleet-\d{8}T\d{6}Z\.tar\.enc$")
        with open(os.path.join(self.out, name), "rb") as fh:
            self.assertEqual(backup.kind(fh.read()), "passphrase")
        into = os.path.join(self.tmp, "back")
        # the older kind opens with its passphrase as before; a recovery key is not asked for
        rc, out, err = self.run_cli("restore", os.path.join(self.out, name), "--into", into, "--passphrase-file", pf,
                                    "--recovery-file", self.keyfile(store.keygen()[0]))
        self.assertEqual(rc, 0, err)
        self.assertIn("a passphrase opens it", out)
        with open(os.path.join(into, "secrets", "deep", "key")) as fh:
            self.assertEqual(fh.read(), "private")
        self.assertFalse(os.path.exists(os.path.join(into, "secrets.age")))
        write(pf, "another horse\n")
        rc, out, err = self.run_cli("restore", os.path.join(self.out, name), "--into", os.path.join(self.tmp, "b2"),
                                    "--passphrase-file", pf)
        self.assertEqual(rc, 1)
        self.assertIn("passphrase does not open", err)

    def test_the_machines_keep_list_and_prune_both_kinds(self):
        d = os.path.join(self.tmp, "on-the-node")
        for i, name in enumerate(("fleet-x-1.tar.enc", "fleet-x-2.tar", "fleet-x-3.tar.enc", "notes.txt")):
            write(os.path.join(d, name), "old %d" % i)
            os.utime(os.path.join(d, name), (1000 + i, 1000 + i))

        def node(argv, input=None, capture_output=True):       # the script as the node runs it, without ssh and sudo
            self.assertTrue(argv[-1].startswith("sudo sh -c "))
            return subprocess.run(shlex.split(argv[-1])[1:], input=input, capture_output=True)
        with mock.patch.object(backup, "REMOTE_DIR", d):
            kept = backup.store_remote(b"NEW", "core@x", "fleet-x-4.tar", keep=3, runner=node)
            self.assertEqual(sorted(kept), ["fleet-x-2.tar", "fleet-x-3.tar.enc", "fleet-x-4.tar", "notes.txt"])
            self.assertEqual(backup.list_remote("core@x", runner=node), ["fleet-x-4.tar", "fleet-x-3.tar.enc", "fleet-x-2.tar"])
            self.assertEqual(backup.fetch_remote("core@x", runner=node), ("fleet-x-4.tar", b"NEW"))
            self.assertEqual(backup.fetch_remote("core@x", "fleet-x-3.tar.enc", runner=node), ("fleet-x-3.tar.enc", b"old 2"))
            for bad in ("../fleet-x-4.tar", "fleet-x-4.tar.gz", "notes.txt", "x/fleet-1.tar"):
                with self.assertRaises(KiwiError):
                    backup.fetch_remote("core@x", bad, runner=node)
            # a node that keeps one kind only
            os.remove(os.path.join(d, "fleet-x-3.tar.enc"))
            self.assertEqual(backup.list_remote("core@x", runner=node), ["fleet-x-4.tar", "fleet-x-2.tar"])
            kept = backup.store_remote(b"NEWER", "core@x", "fleet-x-5.tar", keep=1, runner=node)
            self.assertEqual(sorted(kept), ["fleet-x-5.tar", "notes.txt"])
            os.remove(os.path.join(d, "fleet-x-5.tar"))
            self.assertEqual(backup.list_remote("core@x", runner=node), [])
        self.init()
        self.assertTrue(backup.archive_name(self.f, 0).endswith("Z.tar"))

    def test_a_render_sends_the_copy_without_a_passphrase(self):
        self.init()
        f = self.fleet({"defaults": self.base_defaults(backup={"hosts": ["a"]}), "hosts": {"a": {}}})
        sent = []
        with mock.patch.object(backup, "store_remote", lambda data, tgt, name, keep=10, **kw: sent.append((name, data)) or []):
            cli.auto_backup(f)
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0][0].endswith(".tar"))
        self.assertEqual(backup.kind(sent[0][1]), "store")


@unittest.skipUnless(have("age") and have("age-keygen") and _have_web(), "age, fastapi or uvicorn not installed")
class TestFirstRunPage(StoreBase):
    """A machine with nothing on it: its page asks the questions of `init`, behind a setup code."""
    PW = "a long enough password"
    FORM = {"master_where": "vps", "master_address": "203.0.113.7", "exit": "direct", "lan_subnet": "192.168.1.0/24",
            "lan_gateway": "192.168.1.1", "nodes.0.preset": "cloud", "nodes.0.address": "192.168.1.30"}

    def setUp(self):
        super().setUp()
        import uvicorn
        from kiwiserver import web
        self.dir = os.path.join(self.tmp, "node")
        os.makedirs(self.dir)
        self.c = controlmod.Control(os.path.join(self.dir, "fleet.yaml"), run_dir=os.path.join(self.tmp, "ram"),
                                    kiwi_cmd=[sys.executable, "-c", "print('ran')"])
        port = remote.free_port()
        self.server = uvicorn.Server(uvicorn.Config(web.make_app(self.c), host="127.0.0.1", port=port, log_level="error"))
        t = threading.Thread(target=self.server.run, daemon=True)
        t.start()
        self.addCleanup(lambda: (setattr(self.server, "should_exit", True), t.join(5)))
        self.url = "http://127.0.0.1:%d" % port
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.05)

    def ask(self, path, form=None, cookie=""):
        """(status, body or Location, the session cookie it set)."""
        import urllib.error
        import urllib.request

        class Stay(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **kw):
                return None
        req = urllib.request.Request(self.url + path, data=urllib.parse.urlencode(form).encode() if form is not None else None,
                                     headers={"Cookie": cookie} if cookie else {})
        try:
            with urllib.request.build_opener(Stay).open(req) as r:
                return r.status, r.read().decode(), (r.headers.get("set-cookie") or "").split(";")[0]
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.headers.get("location") or e.read().decode(), ""

    def test_the_questions_make_a_network_that_rests_encrypted(self):
        self.assertTrue(self.c.fresh())
        self.assertEqual(self.ask("/")[:2], (303, "/setup"))
        self.assertEqual(self.ask("/login")[:2], (303, "/setup"))
        status, body, _ = self.ask("/setup")
        self.assertEqual(status, 200)
        self.assertIn("Its address on the Internet", body)
        self.assertNotIn("Key to your machines", body)          # one answer: not asked
        # what is missing is said where it is asked; nothing is made
        status, body, _ = self.ask("/setup", dict(self.FORM, master_address="", password=self.PW, again=self.PW))
        self.assertIn("The master&#39;s address is needed", body)
        # without the code only the person who started the page can read: nothing
        status, body, _ = self.ask("/setup", dict(self.FORM, password=self.PW, again=self.PW, code="0000-0000"))
        self.assertIn("That is not the setup code", body)
        self.assertTrue(self.c.fresh())
        status, body, _ = self.ask("/setup", dict(self.FORM, password=self.PW, again="another one, long too", code=self.c.setup_code()))
        self.assertIn("The two passwords are not the same", body)
        status, body, cookie = self.ask("/setup", dict(self.FORM, password=self.PW, again=self.PW, code=self.c.setup_code()))
        self.assertEqual(status, 200)
        self.assertIn("AGE-SECRET-KEY-", body)                  # the recovery key, once
        self.assertTrue(cookie.startswith("kiwi_session="))
        self.assertFalse(self.c.fresh())
        # at rest: the encrypted files only; the fleet file is a link into RAM
        on_disk = sorted(n for n in os.listdir(self.dir) if not os.path.islink(os.path.join(self.dir, n)))
        self.assertEqual(on_disk, ["control", "fleet.yaml.age", "secrets.age", "secrets.recipients"])
        self.assertTrue(os.path.islink(os.path.join(self.dir, "fleet.yaml")))
        fleet = config.Fleet.load(os.path.join(self.dir, "fleet.yaml"))
        self.assertEqual(sorted(h.role for h in fleet.hosts.values()), ["master", "node"])
        self.assertEqual([n for n, _k in self.c.store.ways()], ["recovery", "control"])
        # the page is the network's now: the questions are gone, the password logs in
        self.assertEqual(self.ask("/setup")[:2], (303, "/login"))
        self.assertEqual(self.ask("/", cookie=cookie)[0], 200)
        self.c.lock_store()
        self.assertTrue(self.c.login(self.PW))

    def made(self):
        """A network made on the page: (cookie, csrf token)."""
        _s, _b, cookie = self.ask("/setup", dict(self.FORM, password=self.PW, again=self.PW, code=self.c.setup_code()))
        self.c.kiwi = [sys.executable, "-c", "import sys; sys.path.insert(0, %r); from kiwiserver import cli; "
                       "sys.exit(cli.main(sys.argv[1:]))" % os.path.join(ROOT, "lib")]
        self.c.fleet_info(fresh=True)
        return cookie, re.search(r'name="csrf" value="([^"]+)"', self.ask("/", cookie=cookie)[1]).group(1)

    def test_the_machines_are_set_up_from_the_page(self):
        cookie, csrf = self.made()
        body = self.ask("/", cookie=cookie)[1]
        self.assertIn("Still to set up", body)
        self.assertIn("Make its install media", body)                   # the master first
        self.assertIn("Its turn comes when the master is set up", body)
        self.assertNotIn("Checking your machines", body)                # nothing to check yet
        # one more machine at home; the fleet file stays a link into memory
        status, where, _ = self.ask("/machines/add", {"csrf": csrf, "preset": "gateway", "address": "192.168.1.31", "name": "fig"},
                                    cookie=cookie)
        self.assertEqual((status, where), (303, "/hosts/fig"))
        self.assertTrue(os.path.islink(os.path.join(self.dir, "fleet.yaml")))
        self.assertNotIn("fleet.yaml.tmp", os.listdir(self.dir))
        fig = config.Fleet.load(os.path.join(self.dir, "fleet.yaml")).hosts["fig"]
        self.assertEqual((fig.cfg["network"]["address"], fig.cfg["network"]["gateway"], fig.cfg["disk"]),
                         ("192.168.1.31/24", "192.168.1.1", "auto"))
        self.c.lock_store()                                             # and it rests encrypted with the new machine
        self.assertTrue(self.c.login(self.PW))
        self.assertIn("fig", config.Fleet.load(os.path.join(self.dir, "fleet.yaml")).hosts)
        self.assertIn("An+address+looks+like", self.ask("/machines/add", {"csrf": csrf, "preset": "cloud", "address": "x"},
                                                        cookie=cookie)[1])
        # install media: made in memory, fetched by someone who is logged in, removed
        self.assertTrue(self.c.media_dir().startswith(os.path.join(self.tmp, "ram")))
        self.assertEqual(self.c.argv("build", ["fig"])[-3:], ["build", os.path.join(self.dir, "fleet.yaml"), "fig"])
        with self.assertRaises(KiwiError):
            self.c.argv("build", [])                                    # never every machine at once
        iso = os.path.join(self.c.media_dir(), "fig", "fig.iso")
        write(iso, "an image")
        self.assertEqual(self.ask("/hosts/fig/media")[:2], (303, "/login"))
        self.assertEqual(self.ask("/hosts/fig/media", cookie=cookie)[:2], (200, "an image"))
        self.assertEqual(self.ask("/hosts/fig/media/drop", {"csrf": csrf}, cookie=cookie)[0], 303)
        self.assertFalse(os.path.exists(iso))
        # the next step is `next` for that machine; one the router gave an address is set up at the address typed
        self.assertEqual(self.c.argv("setup", ["fig"])[-4:], ["next", os.path.join(self.dir, "fleet.yaml"), "fig", "--yes"])
        self.assertEqual(self.c.argv("setup", ["fig"], "192.168.1.77")[-5:],
                         ["apply", os.path.join(self.dir, "fleet.yaml"), "fig", "--ssh", "core@192.168.1.77", "--new-host-key"][-5:])
        with self.assertRaises(KiwiError):
            self.c.argv("setup", ["fig"], "not an address; rm -rf")

    # ---- settings ----
    def data(self):
        return yaml.safe_load(util.read_text(os.path.join(self.dir, "fleet.yaml")))

    def node(self):
        return next(n for n, h in self.data()["hosts"].items() if h["role"] == "node")

    @staticmethod
    def shown(body):
        """What a browser sends back for the settings form of a page, nothing touched: [(name, value)]."""
        from html.parser import HTMLParser
        out = []

        class Form(HTMLParser):
            on, select, area = False, None, None

            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                if tag == "form":
                    self.on = (a.get("action") or "").endswith("settings")
                if not self.on:
                    return
                if tag == "input" and a.get("type") == "checkbox":
                    if "checked" in a:
                        out.append((a["name"], a.get("value", "on")))
                elif tag == "input":
                    out.append((a["name"], a.get("value", "")))
                elif tag == "select":
                    self.select = [a["name"], None, None]
                elif tag == "option" and self.select:
                    if self.select[2] is None:
                        self.select[2] = a.get("value", "")
                    if "selected" in a:
                        self.select[1] = a.get("value", "")
                elif tag == "textarea":
                    self.area = [a["name"], ""]

            def handle_data(self, data):
                if self.area:
                    self.area[1] += data

            def handle_endtag(self, tag):
                if tag == "select" and self.select:
                    out.append((self.select[0], self.select[2] if self.select[1] is None else self.select[1]))
                    self.select = None
                elif tag == "textarea" and self.area:
                    out.append(tuple(self.area))
                    self.area = None
                elif tag == "form":
                    self.on = False
        Form().feed(body)
        return out

    def change(self, path, cookie, changes):
        """The page's whole form sent back, as a browser does, with these fields changed (a list: the boxes ticked)."""
        pairs = [(n, v) for n, v in self.shown(self.ask(path, cookie=cookie)[1]) if n not in changes]
        self.assertIn("csrf", dict(pairs))
        for name, value in changes.items():
            pairs += [(name, v) for v in (value if isinstance(value, list) else [value])]
        return self.ask(path, pairs, cookie)

    def at_rest(self):
        """Nothing readable beside the link, after whatever the page just did."""
        self.assertTrue(os.path.islink(os.path.join(self.dir, "fleet.yaml")))
        self.assertEqual([n for n in os.listdir(self.dir) if ".tmp" in n], [])
        on_disk = sorted(n for n in os.listdir(self.dir) if not os.path.islink(os.path.join(self.dir, n)))
        self.assertEqual(on_disk, ["control", "fleet.yaml.age", "secrets.age", "secrets.recipients"])

    def test_a_machines_time_zone_and_update_time_are_changed_on_its_page(self):
        cookie, csrf = self.made()
        node, page = self.node(), "/hosts/%s/settings" % self.node()
        self.assertIn('href="%s"' % page, self.ask("/hosts/" + node, cookie=cookie)[1])
        self.assertEqual(self.ask(page)[:2], (303, "/login"))                              # not without a login
        self.assertEqual(self.ask(page, {"f:timezone": "Europe/Vienna"})[:2], (303, "/login"))
        self.assertEqual(self.ask(page, {"f:timezone": "Europe/Vienna", "csrf": "x"}, cookie)[:2], (303, "/login"))
        self.assertEqual(self.ask("/hosts/nobody/settings", cookie=cookie)[:2], (303, "/"))
        before, age = self.data(), util.sha256_file(os.path.join(self.dir, "fleet.yaml.age"))
        self.assertNotIn("timezone", before["hosts"][node])
        status, body, _ = self.ask(page, cookie=cookie)
        self.assertEqual(status, 200)
        for said in ("Settings of " + node, "<h2 style=\"margin-top:0\">Machine</h2>", "<summary>Nextcloud</summary>", ">Save</button>"):
            self.assertIn(said, body)
        status, where, _ = self.change(page, cookie, {"f:timezone": "Europe/Vienna", "f:updates.time": "04:15"})
        self.assertEqual((status, where), (303, page + "?saved=1"))
        before["hosts"][node].update(timezone="Europe/Vienna", updates={"time": "04:15"})
        self.assertEqual(self.data(), before)                                               # these two, and nothing else
        host = config.Fleet.load(os.path.join(self.dir, "fleet.yaml")).hosts[node]
        self.assertEqual((host.cfg["timezone"], host.cfg["updates"]["time"], host.cfg["updates"]["days"]), ("Europe/Vienna", "04:15", ["Sun"]))
        self.at_rest()
        self.assertNotEqual(util.sha256_file(os.path.join(self.dir, "fleet.yaml.age")), age)   # and it rests encrypted, changed
        last = self.c.audit_tail(1)[0]
        self.assertEqual((last["what"], last["hosts"], last["keys"]), ("settings changed", [node], ["timezone", "updates.time"]))
        # after the save: a machine that is set up gets it with Apply; this one is not set up yet
        body = self.ask(where, cookie=cookie)[1]
        self.assertIn("Saved. The machine gets it when it is set up.", body)
        self.assertNotIn('name="action" value="apply"', body)
        info = self.c.fleet_info()
        for h in info["hosts"]:
            h["phase"] = "applied"
        with mock.patch.object(self.c, "fleet_info", lambda **_k: info):
            body = self.ask(where, cookie=cookie)[1]
        self.assertIn("Saved. Apply brings it to the machine.", body)
        self.assertIn('<input type="hidden" name="action" value="apply"><input type="hidden" name="host" value="%s">' % node, body)
        # the whole form sent back untouched changes nothing
        text = util.read_text(os.path.join(self.dir, "fleet.yaml"))
        self.assertEqual(self.change(page, cookie, {})[:2], (303, page + "?saved=0"))
        self.assertIn("Nothing was changed.", self.ask(page + "?saved=0", cookie=cookie)[1])
        self.assertEqual(util.read_text(os.path.join(self.dir, "fleet.yaml")), text)
        self.c.lock_store()                                                                 # a restart: it is what was saved
        self.assertTrue(self.c.login(self.PW))
        self.assertEqual(self.data(), before)
        for t in threading.enumerate():         # the login looks for a ring in the background: before the folder goes
            if "arm_auto" in t.name:
                t.join(20)

    def test_an_override_is_set_and_taken_away_again(self):
        cookie, _csrf = self.made()
        node, page = self.node(), "/hosts/%s/settings" % self.node()
        usual = self.data()["defaults"]["keyboard"]
        body = self.ask(page, cookie=cookie)[1]
        self.assertIn('name="f:keyboard" value="" placeholder="%s"' % usual, body)         # empty: what it inherits, in grey
        self.assertRegex(body, r'name="f:updates.enabled"><option value="">as inherited \(yes\)</option>')
        self.assertEqual(self.change(page, cookie, {"f:keyboard": "fr", "f:updates.enabled": "no", "f:updates.days": "Sat Sun"})[0], 303)
        own = self.data()["hosts"][node]
        self.assertEqual((own["keyboard"], own["updates"]), ("fr", {"enabled": False, "days": ["Sat", "Sun"]}))
        body = self.ask(page, cookie=cookie)[1]
        self.assertIn('name="f:keyboard" value="fr" placeholder="%s"' % usual, body)
        self.assertIn('<option value="no" selected>no</option>', body)
        self.assertRegex(body, r'name="f:updates.days" rows="2" placeholder="Sun">Sat Sun</textarea>')
        # empty again, or the inherited value itself: the override goes, the machine follows the defaults
        self.assertEqual(self.change(page, cookie, {"f:keyboard": "", "f:updates.enabled": "yes", "f:updates.days": "Sun"})[:2],
                         (303, page + "?saved=1"))
        own = self.data()["hosts"][node]
        self.assertNotIn("keyboard", own)
        self.assertNotIn("updates", own)
        self.assertIn('name="f:keyboard" value="" placeholder="%s"' % usual, self.ask(page, cookie=cookie)[1])
        self.at_rest()

    def test_a_node_gets_one_more_part_and_the_part_a_setting(self):
        cookie, _csrf = self.made()
        node, page = self.node(), "/hosts/%s/settings" % self.node()
        body = self.ask(page, cookie=cookie)[1]
        on = [v for n, v in self.shown(body) if n == "f:node.modules"]
        self.assertEqual(sorted(on), ["cloud", "portainer", "reverse-proxy", "vault", "vpn-client"])    # the preset's
        self.assertIn('name="f:node.modules" value="immich">', body)                       # offered, not ticked
        self.assertNotIn("f:node.immich.library_dir", body)
        self.assertIn('<option value="cloud" selected>Cloud (cloud)</option>', body)
        self.assertEqual(self.change(page, cookie, {"f:node.modules": on + ["immich"]})[:2], (303, page + "?saved=1"))
        self.assertEqual(self.data()["hosts"][node]["node"]["modules"], ["vpn-client", "reverse-proxy", "cloud", "vault", "portainer", "immich"])
        body = self.ask(page, cookie=cookie)[1]
        self.assertIn('name="f:node.modules" value="immich" checked>', body)
        self.assertIn("<summary>Immich</summary>", body)
        self.assertIn('name="f:node.immich.library_dir"', body)
        self.assertEqual(self.change(page, cookie, {"f:node.immich.library_dir": "/mnt/photos", "f:node.immich.machine_learning": "no"})[0], 303)
        own = self.data()["hosts"][node]["node"]
        self.assertEqual(own["immich"], {"library_dir": "/mnt/photos", "machine_learning": False})
        self.assertEqual(own["preset"], "cloud")
        self.assertIn("immich", next(h for h in self.c.fleet_info()["hosts"] if h["name"] == node)["modules"])
        # the preset's parts again: no list of its own any more
        self.assertEqual(self.change(page, cookie, {"f:node.modules": on})[0], 303)
        self.assertNotIn("modules", self.data()["hosts"][node]["node"])
        self.at_rest()

    def test_a_value_that_cannot_be_is_refused_and_nothing_is_written(self):
        cookie, _csrf = self.made()
        node, page = self.node(), "/hosts/%s/settings" % self.node()
        path = os.path.realpath(os.path.join(self.dir, "fleet.yaml"))
        text, age = util.read_text(path), util.sha256_file(os.path.join(self.dir, "fleet.yaml.age"))
        # the fleet's own check says no
        status, body, _ = self.change(page, cookie, {"f:updates.time": "25:00", "f:keyboard": "fr", "f:admin.password": "typed-s3cr3t"})
        self.assertEqual(status, 200)
        self.assertIn('<p class="banner bad">Not saved: “Reboot time” must be HH:MM (24h)</p>', body)
        self.assertIn('name="f:updates.time" value="25:00"', body)                         # what was typed stays
        self.assertIn('name="f:keyboard" value="fr"', body)
        self.assertNotIn("typed-s3cr3t", body)                                              # but never a secret
        self.assertNotIn("Saved", body)
        # the form says no before the check is asked
        status, body, _ = self.change(page, cookie, {"f:admin.ssh_port": "abc", "f:keyboard": "fr"})
        self.assertEqual(status, 200)
        self.assertIn("Not saved: SSH port: a whole number is needed", body)
        self.assertIn('name="f:admin.ssh_port" value="abc"', body)
        self.assertRegex(body, r'<details class="card" open><summary>Admin user</summary>')      # where the mistake is
        self.assertEqual(util.read_text(path), text)                                        # the file, to the letter
        self.assertEqual(util.sha256_file(os.path.join(self.dir, "fleet.yaml.age")), age)
        self.assertNotIn("settings changed", str(self.c.audit_tail(5)))
        self.at_rest()

    def test_a_secret_is_set_and_never_shown(self):
        cookie, _csrf = self.made()
        node, page = self.node(), "/hosts/%s/settings" % self.node()
        self.assertEqual(self.change(page, cookie, {"f:node.cloud.admin_password": "nextcloud-s3cr3t"})[:2], (303, page + "?saved=1"))
        self.assertEqual(self.data()["hosts"][node]["node"]["cloud"], {"admin_password": "nextcloud-s3cr3t"})
        body = self.ask(page, cookie=cookie)[1]
        self.assertNotIn("s3cr3t", body)
        self.assertRegex(body, r'type="password" name="f:node.cloud.admin_password" autocomplete="new-password"\s+'
                               r'placeholder="set; leave empty to keep it">')
        self.assertNotIn("s3cr3t", util.read_text(os.path.join(self.c.dir, "audit.jsonl")))
        self.assertEqual(self.c.audit_tail(1)[0]["keys"], ["node.cloud.admin_password"])
        for name in os.listdir(self.dir):                                                   # nor readable on the disk
            if os.path.isfile(os.path.join(self.dir, name)) and not os.path.islink(os.path.join(self.dir, name)):
                self.assertNotIn(b"s3cr3t", util.read_bytes(os.path.join(self.dir, name)))
        # left empty it stays; ticked away it goes
        self.assertEqual(self.change(page, cookie, {"f:keyboard": "fr"})[0], 303)
        self.assertEqual(self.data()["hosts"][node]["node"]["cloud"], {"admin_password": "nextcloud-s3cr3t"})
        self.assertEqual(self.change(page, cookie, {"clear:node.cloud.admin_password": "1"})[0], 303)
        self.assertNotIn("cloud", self.data()["hosts"][node]["node"])
        self.at_rest()

    def test_the_settings_for_every_machine(self):
        cookie, _csrf = self.made()
        node, page = self.node(), "/hosts/%s/settings" % self.node()
        self.assertEqual(self.ask("/settings")[:2], (303, "/login"))
        self.assertIn('<a class="tile" href="/settings"><b>Settings for every machine</b>', self.ask("/", cookie=cookie)[1])
        body = self.ask("/settings", cookie=cookie)[1]
        self.assertIn("Settings for every machine", body)
        self.assertIn('name="f:domain"', body)                                             # what only the defaults have
        self.assertNotIn('name="f:hostname"', body)                                        # what only a machine has
        self.assertIn("the usual (yes)", body)
        before = self.data()
        self.assertEqual(self.change("/settings", cookie, {"f:keyboard": "fr", "f:updates.days": "Sat"})[:2], (303, "/settings?saved=1"))
        before["defaults"]["keyboard"] = "fr"
        before["defaults"]["updates"]["days"] = ["Sat"]
        self.assertEqual(self.data(), before)
        fleet = config.Fleet.load(os.path.join(self.dir, "fleet.yaml"))
        self.assertEqual({h.cfg["keyboard"] for h in fleet.hosts.values()}, {"fr"})        # every machine inherits it
        self.assertEqual([h.cfg["updates"]["days"] for h in fleet.hosts.values()], [["Sat"], ["Sat"]])
        self.assertIn('name="f:keyboard" value="" placeholder="fr"', self.ask(page, cookie=cookie)[1])
        self.assertEqual(self.c.audit_tail(1)[0]["hosts"], [])
        body = self.ask("/settings?saved=1", cookie=cookie)[1]
        self.assertIn("Saved. Every machine gets it when it is set up.", body)
        info = self.c.fleet_info()
        for h in info["hosts"]:
            h["phase"] = "applied"
        with mock.patch.object(self.c, "fleet_info", lambda **_k: info):
            body = self.ask("/settings?saved=1", cookie=cookie)[1]
        self.assertIn("Saved. Update everything brings it to every machine.", body)
        self.assertIn('<input type="hidden" name="action" value="update">', body)
        # a domain that cannot be is refused here as well
        text = util.read_text(os.path.join(self.dir, "fleet.yaml"))
        status, body, _ = self.change("/settings", cookie, {"f:domain": "local"})
        self.assertEqual(status, 200)
        self.assertIn("Not saved: domain", body)
        self.assertEqual(util.read_text(os.path.join(self.dir, "fleet.yaml")), text)
        self.assertEqual(self.ask("/machines/add", {"csrf": _csrf, "preset": "cloud", "name": "fig"}, cookie=cookie)[:2],
                         (303, "/hosts/fig"))                                               # a machine can still be added
        self.at_rest()

    def test_a_machines_logins_are_listed_and_a_password_is_fetched_when_asked(self):
        cookie, _csrf = self.made()
        master = next(n for n, h in self.data()["hosts"].items() if h["role"] == "master")
        host = config.Fleet.load(os.path.join(self.dir, "fleet.yaml")).hosts[master]
        rolesmod.resolve_settings(self.roles[host.role], host, self.ms)
        wg, pihole = host.module_settings["vpn-server"]["wg_password"], host.module_settings["dns"]["pihole_password"]
        self.assertEqual((len(wg), len(pihole)), (32, 32))                      # from the seed: nobody typed them
        # the machine's page: a row per service, the label a link, and no password in it
        status, body, _ = self.ask("/hosts/" + master, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn('<h2 id="logins" style="scroll-margin-top:64px">Logins</h2>', body)
        self.assertIn('<a href="https://wg.%s" target="_blank" rel="noopener">VPN devices (wg-easy)</a>' % host.hostname, body)
        self.assertIn('<a href="https://pihole.%s/admin" target="_blank" rel="noopener">Ad blocker (Pi-hole)</a>' % host.hostname, body)
        self.assertEqual(body.count(">Show password</button>"), 2)
        self.assertIn("/logins", body)                                          # where the button asks
        for secret in (wg, pihole):
            self.assertNotIn(secret, body)
        self.assertNotIn(wg, util.read_text(os.path.join(self.c.run, "fleet.json")))    # nor in what the page keeps
        # a machine that is not on the VPN yet has no card
        self.assertNotIn('id="logins"', self.ask("/hosts/" + self.node(), cookie=cookie)[1])
        # asked for: only with a login
        api = "/api/hosts/%s/logins" % master
        status, said, _ = self.ask(api)
        self.assertEqual((status, json.loads(said)), (401, {"error": "log in"}))
        self.assertEqual(self.ask(api, cookie="kiwi_session=not-a-session")[0], 401)
        self.assertNotIn("logins shown", str(self.c.audit_tail(20)))
        status, said, _ = self.ask(api, cookie=cookie)
        self.assertEqual(status, 200)
        answer = json.loads(said)
        self.assertEqual(answer["host"], master)
        self.assertEqual([(x["label"], x["address"], x["user"], x["password"]) for x in answer["logins"]], [
            ("VPN devices (wg-easy)", "https://wg." + host.hostname, "", wg),
            ("Ad blocker (Pi-hole)", "https://pihole.%s/admin" % host.hostname, "", pihole)])
        # the audit says that they were shown, and of which machine: nothing else
        last = self.c.audit_tail(1)[0]
        self.assertEqual(sorted(last), ["hosts", "time", "user", "what"])
        self.assertEqual((last["what"], last["hosts"], last["user"]), ("logins shown", [master], "admin"))
        log = util.read_text(os.path.join(self.c.dir, "audit.jsonl"))
        for secret in (wg, pihole):
            self.assertNotIn(secret, log)
        # nothing of it was kept: no file here holds a password
        for top in (self.dir, self.c.run):
            for dp, _dn, names in os.walk(top):
                for n in names:
                    p = os.path.join(dp, n)
                    if os.path.isfile(p):
                        self.assertNotIn(wg.encode(), util.read_bytes(p), p)
        # a machine there is not, and one whose settings are not complete yet: an answer, no audit line
        before = len(self.c.audit_tail(50))
        status, said, _ = self.ask("/api/hosts/nobody/logins", cookie=cookie)
        self.assertEqual(status, 400)
        self.assertIn("no such host", json.loads(said)["error"])
        status, said, _ = self.ask("/api/hosts/%s/logins" % self.node(), cookie=cookie)
        self.assertEqual(status, 400)
        self.assertIn("not enrolled yet", json.loads(said)["error"])
        with self.assertRaises(KiwiError):
            self.c.logins("no; such")
        self.assertEqual(len(self.c.audit_tail(50)), before)
        # Home: a service's tile leads to its machine's logins
        info = self.c.fleet_info()
        for h in info["hosts"]:
            h["phase"] = "applied"
        with mock.patch.object(self.c, "fleet_info", lambda **_k: info):
            body = self.ask("/", cookie=cookie)[1]
        self.assertIn("Open a service", body)
        self.assertEqual(body.count('href="/hosts/%s#logins">login</a>' % master), 2)
        self.assertNotIn(wg, body)
        # the keys locked: nothing is handed out
        self.c.lock_store()
        self.assertEqual(self.ask(api, cookie=cookie)[0], 401)
        with self.assertRaisesRegex(KiwiError, "locked"):
            self.c.logins(master)

    def test_a_part_is_brought_back_from_a_snapshot(self):
        cookie, csrf = self.made()
        fleet = os.path.join(self.dir, "fleet.yaml")
        said = {"servers": [
            {"server": "backup.lychee.home", "ok": True, "snapshots": [
                {"module": "vault", "time": "2026-11-13T02:10:05Z"}, {"module": "vault", "time": "2026-11-12T02:10:01Z"},
                {"module": "cloud", "time": "2026-11-13T02:11:40Z"}]},
            {"server": "backup.fig.home", "ok": False, "snapshots": []}]}
        self.c.kiwi = [sys.executable, "-c", "print(%r)" % json.dumps(said)]
        snaps = self.c.snapshots("papaya")
        self.assertEqual(list(snaps["parts"]), ["cloud", "vault"])
        self.assertEqual(snaps["parts"]["vault"][0], ("2026-11-13T02:10:05Z", ["lychee"]))        # newest first
        self.assertEqual(snaps["missed"], ["backup.fig.home"])
        body = self.ask("/backups/papaya", cookie=cookie)[1]
        self.assertIn("2026-11-13 02:10", body)
        self.assertIn("Nothing found on backup.fig.home", body)
        self.assertIn("What it has now is replaced", body)
        self.assertEqual(self.c.argv("restore", ["papaya"], "2026-11-12T02:10:01Z", "vault")[-6:],
                         ["backups", fleet, "papaya", "vault", "--restore", "--at", "2026-11-12T02:10:01Z"][-6:])
        self.assertEqual(self.c.argv("restore", ["papaya"])[-3:], [fleet, "papaya", "--restore"])
        for at, module in (("yesterday; rm -rf /", "vault"), ("2026-11-12T02:10:01Z", "--ssh")):
            with self.assertRaises(KiwiError):
                self.c.argv("restore", ["papaya"], at, module)
        self.assertEqual(self.c.argv("unlock", ["papaya"])[-3:], ["unlock", fleet, "papaya"])
        self.assertEqual(self.c.argv("remove", ["papaya"])[-4:], ["remove", fleet, "papaya", "--yes"])
        with self.assertRaises(KiwiError):
            self.c.argv("remove", [])
        with mock.patch.dict(os.environ, {"KIWI_CONTROL_HOST": "papaya"}), self.assertRaises(KiwiError):
            self.c.argv("remove", ["papaya"])                   # the page's own machine
        self.assertIn("Remove this machine", self.ask("/hosts/papaya", cookie=cookie)[1])
        # a machine that keeps no answer: the page says so and still opens
        self.c.kiwi = [sys.executable, "-c", "import sys; print('error: papaya does not back up', file=sys.stderr); sys.exit(1)"]
        self.assertIn("papaya does not back up", self.ask("/backups/papaya", cookie=cookie)[1])
        self.assertEqual(self.ask("/backups/papaya")[:2], (303, "/login"))

    def test_the_recovery_key_sets_a_new_password(self):
        _s, body, _c = self.ask("/setup", dict(self.FORM, password=self.PW, again=self.PW, code=self.c.setup_code()))
        recovery = re.search(r"AGE-SECRET-KEY-[A-Z0-9]+", body).group(0)
        self.c.lock_store()
        new = "another password, long too"
        status, body, _ = self.ask("/recover", {"recovery": "AGE-SECRET-KEY-1NOTTHEONE", "password": new, "again": new})
        self.assertIn("not this network", body)
        status, where, _ = self.ask("/recover", {"recovery": recovery.lower(), "password": new, "again": new})
        self.assertEqual((status, where), (303, "/"))
        self.assertFalse(self.c.locked())
        self.c.lock_store()
        self.assertIsNone(self.c.login(self.PW))                       # the old password opens nothing
        self.assertTrue(self.c.login(new))
        self.assertTrue(os.path.islink(os.path.join(self.dir, "fleet.yaml")))

    def test_five_wrong_codes_and_the_page_waits(self):
        for _ in range(5):
            self.assertFalse(self.c.setup_code_ok("0000-0000", "10.0.0.9"))
        self.assertFalse(self.c.setup_code_ok(self.c.setup_code(), "10.0.0.9"))
        self.assertTrue(self.c.setup_code_ok(self.c.setup_code(), "10.0.0.8"))

    # ---- the network's own copy: fetched from the page, brought back on a new machine ----
    def fetch(self, path, cookie=""):
        """A GET that may be a download: (status, headers, bytes)."""
        import urllib.error
        import urllib.request

        class Stay(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **kw):
                return None
        req = urllib.request.Request(self.url + path, headers={"Cookie": cookie} if cookie else {})
        try:
            with urllib.request.build_opener(Stay).open(req) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.headers, e.read()

    def send(self, path, fields, files, cookie=""):
        """A form with a file in it, as a browser sends one (multipart/form-data):
        (status, body or Location, the session cookie it set)."""
        import urllib.error
        import urllib.request

        class Stay(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **kw):
                return None
        mark = "----kiwi" + os.urandom(8).hex()
        body = b""
        for name, value in fields.items():
            body += ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (mark, name, value)).encode()
        for name, (filename, data) in files.items():
            body += ('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
                     'Content-Type: application/octet-stream\r\n\r\n' % (mark, name, filename)).encode() + data + b"\r\n"
        body += ("--%s--\r\n" % mark).encode()
        headers = {"Content-Type": "multipart/form-data; boundary=" + mark}
        if cookie:
            headers["Cookie"] = cookie
        req = urllib.request.Request(self.url + path, data=body, headers=headers)
        try:
            with urllib.request.build_opener(Stay).open(req) as r:
                return r.status, r.read().decode(), (r.headers.get("set-cookie") or "").split(";")[0]
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.headers.get("location") or e.read().decode(), (e.headers.get("set-cookie") or "").split(";")[0]

    def another_machine(self):
        """A second machine with nothing on it, and its page: (its Control, its folder, its address)."""
        import uvicorn
        from kiwiserver import web
        folder = os.path.join(self.tmp, "node2")
        os.makedirs(folder)
        c = controlmod.Control(os.path.join(folder, "fleet.yaml"), run_dir=os.path.join(self.tmp, "ram2"),
                               kiwi_cmd=[sys.executable, "-c", "print('ran')"])
        port = remote.free_port()
        server = uvicorn.Server(uvicorn.Config(web.make_app(c), host="127.0.0.1", port=port, log_level="error"))
        t = threading.Thread(target=server.run, daemon=True)
        t.start()
        self.addCleanup(lambda: (setattr(server, "should_exit", True), t.join(5)))
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        return c, folder, "http://127.0.0.1:%d" % port

    def disk_of(self, folder):
        """What a machine's disk holds: every file that is not a link into memory, with its bytes."""
        found = {}
        for root, _dirs, files in os.walk(folder):
            for f in files:
                path = os.path.join(root, f)
                if not os.path.islink(path):
                    with open(path, "rb") as fh:
                        found[os.path.relpath(path, folder)] = fh.read()
        return found

    def test_a_copy_is_fetched_and_brings_the_network_back_on_a_new_machine(self):
        from kiwiserver import web
        _s, body, cookie = self.ask("/setup", dict(self.FORM, password=self.PW, again=self.PW, code=self.c.setup_code()))
        recovery = re.search(r"AGE-SECRET-KEY-[A-Z0-9]+", body).group(0)
        machines = sorted(config.Fleet.load(os.path.join(self.dir, "fleet.yaml")).hosts)
        self.assertEqual(len(machines), 2)
        # the copy: only for someone who is logged in, made in memory, nothing of it kept here
        self.assertEqual(self.ask("/backups/network.tar")[:2], (303, "/login"))
        self.assertIn("Fetch a copy", self.ask("/backups", cookie=cookie)[1])
        self.assertIn("A copy and your recovery key are the whole network", self.ask("/backups", cookie=cookie)[1])
        status, headers, data = self.fetch("/backups/network.tar", cookie)
        self.assertEqual(status, 200)
        self.assertRegex(headers["content-disposition"], r'^attachment; filename="fleet-fleet-\d{8}T\d{6}Z\.tar"$')
        self.assertEqual(backup.kind(data), "store")
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            self.assertEqual(tar.getnames(), ["MANIFEST.json", "secrets.age", "secrets.recipients", "fleet.yaml.age"])
        self.assertNotIn(b"203.0.113.7", data)
        self.assertEqual([f for _r, _d, files in os.walk(self.tmp) for f in files if f.endswith(".tar")], [])
        self.assertIn("fetched a copy of the network", str(self.c.audit_tail(5)))
        self.c.lock_store()                                 # that machine is lost; from here on, a new one's page
        c2, folder, self.url = self.another_machine()
        new = "the new machine's password"
        form = {"recovery": recovery, "code": c2.setup_code(), "password": new, "again": new}
        copy = {"copy": ("fleet-fleet.tar", data)}
        self.assertIn("I already have a network: bring it back from a copy", self.ask("/setup")[1])
        # what is wrong is said on the page, and nothing is brought back
        for wrong, files, says in ((dict(form, code="0000-0000"), copy, "That is not the setup code"),
                                   (dict(form, recovery=store.keygen()[0]), copy, "That recovery key does not open this copy."),
                                   (dict(form, again="another one, long too"), copy, "The two passwords are not the same."),
                                   (dict(form, password="short", again="short"), copy, "12 characters at least"),
                                   (form, {}, "Choose the copy")):
            status, body, got = self.send("/setup/restore", wrong, files)
            self.assertEqual(status, 200)
            self.assertIn(says, body)
            self.assertIn("<details class=\"card\" open>", body)       # the card is open, with what went wrong
            self.assertEqual(got, "")
            self.assertNotIn(recovery, body)
            self.assertTrue(c2.fresh())
            self.assertEqual(sorted(n for n in self.disk_of(folder) if n != "control/audit.jsonl"), [])
        with mock.patch.object(web, "COPY_MAX", 1000):      # a file that cannot be a copy: refused, whatever it holds
            for files in (copy, {"copy": ("big.iso", b"\0" * 200000)}):
                status, body, _c = self.send("/setup/restore", form, files)
                self.assertEqual(status, 413)
                self.assertIn("too large to be a copy of a network", body)
        status, body, _c = self.ask("/setup/restore", form)             # a form without a file part
        self.assertEqual(status, 200)
        self.assertIn("did not arrive whole", body)
        self.assertTrue(c2.fresh())
        self.assertEqual(sorted(n for n in self.disk_of(folder) if n != "control/audit.jsonl"), [])
        # the copy, the recovery key (as a person types it) and a new password
        status, where, cookie = self.send("/setup/restore", dict(form, recovery=" %s " % recovery.lower()), copy)
        self.assertEqual((status, where), (303, "/"))
        self.assertTrue(cookie.startswith("kiwi_session="))
        self.assertFalse(c2.fresh())
        self.assertFalse(c2.locked())
        # at rest: the encrypted files only, as after a first run
        rest = self.disk_of(folder)
        self.assertEqual(sorted(rest), ["control/audit.jsonl", "control/salt", "fleet.yaml.age", "secrets.age", "secrets.recipients"])
        for name, blob in rest.items():
            for plain in (b"203.0.113.7", b"AGE-SECRET-KEY", recovery.lower().encode(), new.encode(), self.PW.encode()):
                self.assertNotIn(plain, blob, name)
        self.assertTrue(os.path.islink(os.path.join(folder, "fleet.yaml")))
        self.assertFalse(os.path.exists(os.path.join(c2.run, "restore-run")))
        self.assertEqual(sorted(config.Fleet.load(os.path.join(folder, "fleet.yaml")).hosts), machines)
        self.assertEqual([n for n, _k in c2.store.ways()], ["recovery", "control"])
        self.assertIn("brought back from a copy", str(c2.audit_tail(5)))
        self.assertEqual(self.ask("/setup")[:2], (303, "/login"))
        self.assertEqual(self.send("/setup/restore", form, copy)[:2], (303, "/login"))
        # the password of the page that was lost opens nothing here; the new one does
        c2.lock_store()
        self.assertIsNone(c2.login(self.PW, "10.0.0.1"))
        self.assertTrue(c2.locked())
        self.assertEqual(self.ask("/login", {"password": new})[:2], (303, "/"))
        self.assertFalse(c2.locked())
        # its page is the network's now, and shows the machines to compare
        c2.kiwi = [sys.executable, "-c", "import sys; sys.path.insert(0, %r); from kiwiserver import cli; "
                   "sys.exit(cli.main(sys.argv[1:]))" % os.path.join(ROOT, "lib")]
        c2.fleet_info(fresh=True)
        body = self.ask("/", cookie=cookie)[1]
        for name in machines:
            self.assertIn(name, body)

    def test_the_setup_code_reaches_a_log_at_once(self):
        # a container's log is a pipe, not a terminal: what is printed must not wait in a buffer,
        # or `podman logs` shows no code for the page to ask for
        import select
        folder = os.path.join(self.tmp, "node3")
        os.makedirs(folder)
        env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
        env["PYTHONPATH"] = os.path.join(ROOT, "lib")
        p = subprocess.Popen([sys.executable, "-m", "kiwiserver", "serve", os.path.join(folder, "fleet.yaml"), "--wait",
                              "--listen", "127.0.0.1:%d" % remote.free_port()], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, env=env)
        try:
            said = b""
            while b"serving" not in said and select.select([p.stdout], [], [], 30)[0]:
                more = os.read(p.stdout.fileno(), 4096)
                if not more:
                    break
                said += more
            self.assertIsNone(p.poll())                                 # it serves; nothing was flushed by an exit
            self.assertRegex(said.decode(), r"setup code: [0-9a-f]{4}-[0-9a-f]{4}\n")
        finally:
            p.terminate()
            p.wait(10)
            p.stdout.close()

    def test_save_it_now_needs_no_passphrase_on_a_network_made_here(self):
        _s, body, cookie = self.ask("/setup", dict(self.FORM, password=self.PW, again=self.PW, code=self.c.setup_code()))
        recovery = re.search(r"AGE-SECRET-KEY-[A-Z0-9]+", body).group(0)
        self.c.kiwi = [sys.executable, "-c", "import sys; sys.path.insert(0, %r); from kiwiserver import cli; "
                       "sys.exit(cli.main(sys.argv[1:]))" % os.path.join(ROOT, "lib")]
        self.c.fleet_info(fresh=True)
        csrf = re.search(r'name="csrf" value="([^"]+)"', self.ask("/", cookie=cookie)[1]).group(1)
        fleet = os.path.join(self.dir, "fleet.yaml")
        # no machine is set up yet: said on the Backups page, with the other way to a copy
        status, body, _c = self.ask("/run", {"action": "backup", "csrf": csrf}, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn("Nothing was saved: no machine is set up yet", body)
        self.assertIn("Fetch a copy", body)
        # once machines are set up, the copy goes to them, by name
        info = self.c.fleet_info()
        names = [h["name"] for h in info["hosts"]]
        info["hosts"][0]["phase"] = "applied"
        util.write_text(os.path.join(self.c.run, "fleet.json"), json.dumps(info))
        self.assertEqual(self.c.argv("backup")[-3:], ["backup", fleet, names[0]])
        # the command itself, on a network whose fleet file is a link into memory: a copy, no passphrase
        out = os.path.join(self.tmp, "stick")
        os.makedirs(out)
        r = subprocess.run(self.c.kiwi + ["backup", fleet, "--local", out], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("passphrase", r.stdout + r.stderr)
        name = os.listdir(out)[0]
        self.assertTrue(name.endswith(".tar"))
        with open(os.path.join(out, name), "rb") as fh:
            fleet_name, plain, _kept = backup.open_copy(fh.read(), recovery)
        self.assertEqual((fleet_name, sorted(plain)), ("fleet.yaml", ["fleet.yaml"]))
        self.assertEqual(sorted(yaml.safe_load(plain["fleet.yaml"])["hosts"]), sorted(names))


if __name__ == "__main__":
    unittest.main()
