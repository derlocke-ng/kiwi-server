"""The coreos-ram target: a master whose system runs from RAM (docs/design/ram-master.md)."""
import json
import os
import subprocess
import unittest

import yaml

from test_kiwiserver import KEY, Base, have, write  # noqa: F401  (KEY: the fleet's admin key)
from kiwiserver import cli, config, util
from kiwiserver.targets import ram
from kiwiserver.util import KiwiError


class TestRam(Base):
    def ram_fleet(self, **over):
        d = self.base_defaults(target="coreos-ram", role="master", disk="/dev/vda")
        d.update(over)
        return self.fleet({"defaults": d, "hosts": {"trifle": {"master": self.master()}}})

    def butane(self, f, name="trifle"):
        h = f.hosts[name]
        self.prepare(h)
        return h, yaml.safe_load(ram.render(h))

    def test_the_image_holds_no_role(self):
        h, bu = self.butane(self.ram_fleet())
        files = {fl["path"]: fl for fl in bu["storage"]["files"]}
        self.assertNotIn("/var/lib/kiwi-server/role.sh", files)
        self.assertNotIn("/etc/zincati/config.d/55-updates-strategy.toml", files)
        self.assertEqual(files[ram.STATE_SCRIPT]["mode"], 0o700)
        self.assertEqual(bu["passwd"]["users"][0]["ssh_authorized_keys"], [KEY])
        # nothing of the master's stack in it: no password, no key, no provider config
        text = ram.render(h)
        for secret in ("pihole_password", "wireguard_private_key", "vpn.example.org"):
            self.assertNotIn(secret, text)

    def test_units_order_the_state_before_the_role(self):
        _h, bu = self.butane(self.ram_fleet())
        units = {u["name"]: u for u in bu["systemd"]["units"]}
        state, role = units["kiwi-state.service"]["contents"], units["kiwi-role.service"]["contents"]
        self.assertIn("ExecStart=/usr/bin/bash %s mount" % ram.STATE_SCRIPT, state)
        self.assertIn("Before=sshd-keygen@rsa.service sshd-keygen@ecdsa.service sshd-keygen@ed25519.service "
                      "sshd.service kiwi-role.service", state)
        self.assertIn("After=network-online.target kiwi-state.service", role)
        self.assertIn("ConditionPathExists=/var/lib/kiwi-server/role.sh", role)
        self.assertIn("ConditionPathExists=!/var/lib/kiwi-server/role.done", role)
        self.assertTrue(units["kiwi-state.service"]["enabled"] and units["kiwi-role.service"]["enabled"])

    def test_defaults_logs_in_ram_images_on_disk(self):
        h, bu = self.butane(self.ram_fleet())
        files = {fl["path"]: fl["contents"]["inline"] for fl in bu["storage"]["files"]}
        self.assertIn("Storage=volatile", files["/etc/systemd/journald.conf.d/50-kiwi-ram.conf"])
        script = files[ram.STATE_SCRIPT]
        self.assertIn("IMAGE_CACHE=1\n", script)
        self.assertIn("KEEP_LOGS=0\n", script)
        self.assertIn("LABEL=kiwi-state\n", script)
        self.assertNotIn("__", script)
        self.assertEqual(ram.live_kargs(h), [])

    def test_settings_change_the_image(self):
        f = self.ram_fleet(ram={"copy_to_ram": True, "image_cache": False, "keep_logs": True},
                           coreos={"console": "ttyS0,115200n8", "kernel_arguments": ["mitigations=auto"]})
        h, bu = self.butane(f)
        files = {fl["path"]: fl["contents"]["inline"] for fl in bu["storage"]["files"]}
        self.assertIn("Storage=persistent", files["/etc/systemd/journald.conf.d/50-kiwi-ram.conf"])
        self.assertIn("IMAGE_CACHE=0\n", files[ram.STATE_SCRIPT])
        self.assertIn("KEEP_LOGS=1\n", files[ram.STATE_SCRIPT])
        self.assertEqual(ram.live_kargs(h), [ram.FROMRAM, "mitigations=auto", "console=ttyS0,115200n8"])

    def test_iso_is_the_live_system_not_an_installer(self):
        f = self.ram_fleet(ram={"copy_to_ram": True})
        h = f.hosts["trifle"]
        base = os.path.join(self.tmp, "fcos.iso")
        write(base, "")
        calls = []

        class FakeTC:
            def run(self, argv, **kw):
                calls.append(list(argv))
                return type("R", (), {"stdout": base + "\n", "stderr": ""})()
        ign = os.path.join(self.tmp, "o", "trifle.ign")
        write(ign, "{}")
        out = os.path.join(self.tmp, "o", "trifle.iso")
        ram.iso(FakeTC(), h, ign, out, os.path.join(self.tmp, "cache"))
        argv = calls[-1]
        self.assertEqual(argv[:3], ["coreos-installer", "iso", "customize"])
        self.assertEqual(argv[argv.index("--live-ignition") + 1], ign)
        self.assertEqual(argv[argv.index("--live-karg-append") + 1], ram.FROMRAM)
        for installer in ("--dest-ignition", "--dest-device", "--post-install"):
            self.assertNotIn(installer, argv)
        self.assertEqual(argv[-3:], ["-o", out, base])

    def test_validation(self):
        f = self.ram_fleet()
        self.assertEqual(f.hosts["trifle"].validate(self.roles), [])
        bad = self.fleet({"defaults": self.base_defaults(target="coreos-ram", role="master"),
                          "hosts": {"a": {"master": self.master(), "unlock": {"rings": ["infra"]}},
                                    "b": {"master": self.master(), "coreos": {"luks": {"device": "/dev/vda"}}},
                                    "c": {"master": self.master(), "ram": {"keep_logs": "yes"}},
                                    "d": {"master": self.master(), "ram": {"keep_log": True}},
                                    "n": {"role": "node-cloud", "node-cloud": self.node_cloud()}}})
        errs = {n: " ".join(h.validate(self.roles)) for n, h in bad.hosts.items()}
        self.assertIn("no encrypted disk to unlock", errs["a"])
        self.assertIn("coreos.luks", errs["b"])
        self.assertIn("ram.keep_logs must be true or false", errs["c"])
        self.assertIn("did you mean keep_logs", errs["d"])
        self.assertIn("does not support target coreos-ram", errs["n"])

    def test_without_disk_it_keeps_nothing(self):
        f = self.ram_fleet(disk=None)
        h = f.hosts["trifle"]
        self.assertEqual(h.validate(self.roles), [])
        self.assertTrue(any("keeps nothing between boots" in w for w in h.warnings))
        with self.assertRaisesRegex(KiwiError, "disk:"):
            ram.prepare(h, "core@x", runner=None)

    def test_prepare_makes_the_partition_for_the_stack_dir(self):
        f = self.fleet({"defaults": self.base_defaults(target="coreos-ram", role="master", disk="/dev/vda"),
                        "hosts": {"trifle": {"master": self.master(docker_dir="/var/srv/stack")}}})
        h = f.hosts["trifle"]
        self.prepare(h)
        seen = []

        def runner(argv, **kw):
            seen.append(argv)
            return subprocess.CompletedProcess(argv, 0, b"kiwi-state: /dev/vda1 made and in place\n", b"")
        self.assertEqual(ram.prepare(h, "core@trifle", runner=runner), "kiwi-state: /dev/vda1 made and in place")
        self.assertTrue(seen[0][-1].endswith("bash /etc/kiwi-ram/state.sh init /dev/vda /var/srv/stack'"), seen[0][-1])

    def test_ssh_ca_files_are_kept(self):
        f = self.ram_fleet()
        h = f.hosts["trifle"]
        script = ram.state_script(h)
        self.assertIn('SSH_KEEP="/etc/ssh/kiwi-user-ca.pub /etc/ssh/sshd_config.d/40-kiwi-ca.conf"', script)
        seen = []

        def runner(argv, **kw):
            seen.append(argv[-1])
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        ram.save(h, "core@trifle", runner=runner)
        self.assertIn("bash /etc/kiwi-ram/state.sh save", seen[0])

        def old_image(argv, **kw):
            return subprocess.CompletedProcess(argv, 1, b"", b"kiwi-state: usage: state.sh mount | init DISK STACK_DIR | status")
        ram.save(h, "core@trifle", runner=old_image)     # a warning, not a failed apply

    def test_render_and_offline(self):
        f = self.ram_fleet()
        out = os.path.join(self.tmp, "out")
        self.assertEqual(cli.main(["render", f.path, "trifle", "--no-backup", "-o", out]), 0)
        bu = yaml.safe_load(util.read_text(os.path.join(out, "trifle", "trifle.bu")))
        self.assertNotIn("/var/lib/kiwi-server/role.sh", [fl["path"] for fl in bu["storage"]["files"]])
        self.assertTrue(os.path.isfile(os.path.join(out, "trifle", "trifle.role.sh")))
        self.assertIn("state partition", util.read_text(os.path.join(out, "trifle", "README.txt")))
        with self.assertRaisesRegex(KiwiError, "never carries the role"):
            cli._render_or_build(type("A", (), {"fleet": f.path, "hosts": ["trifle"], "offline": True, "bare": False,
                                                "toolchain": "auto", "output_dir": out, "no_backup": True})(), True)

    def test_targets_lists_it(self):
        self.assertIn("coreos-ram", config.TARGETS)
        self.assertIn("coreos-ram", self.roles["master"].targets)
        self.assertNotIn("coreos-ram", self.roles["node"].targets)

    def test_state_script_parses(self):
        f = self.ram_fleet()
        p = os.path.join(self.tmp, "state.sh")
        write(p, ram.state_script(f.hosts["trifle"]))
        r = subprocess.run(["bash", "-n", p], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        if have("shellcheck"):
            r = subprocess.run(["shellcheck", "-S", "warning", p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout)

    @unittest.skipUnless(have("butane"), "butane not installed")
    def test_butane_strict(self):
        f = self.ram_fleet(ram={"copy_to_ram": True, "image_cache": True, "keep_logs": True})
        h = f.hosts["trifle"]
        self.prepare(h)
        bu = os.path.join(self.tmp, "trifle.bu")
        write(bu, ram.render(h))
        r = subprocess.run(["butane", "--strict", "--pretty", bu], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["ignition"]["version"], "3.5.0")


if __name__ == "__main__":
    unittest.main()
