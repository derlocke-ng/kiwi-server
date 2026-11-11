"""The coreos-ram target: a master in RAM only, its role pushed from home (docs/design/ram-master.md)."""
import argparse
import json
import os
import subprocess
import unittest
from unittest import mock

import yaml

from test_kiwiserver import KEY, Base, have, write
from kiwiserver import backup, cli, config, sshca, util
from kiwiserver.targets import ram
from kiwiserver.util import KiwiError


def done(out=b"", rc=0):
    return lambda argv, **kw: subprocess.CompletedProcess(argv, rc, out, b"")


class TestRam(Base):
    def ram_fleet(self, **over):
        d = self.base_defaults(target="coreos-ram", role="master", disk=None)
        d.update(over)
        return self.fleet({"defaults": d, "hosts": {"trifle": {"master": self.master()}}})

    def butane(self, f, name="trifle"):
        h = f.hosts[name]
        self.prepare(h)
        return h, yaml.safe_load(ram.render(h))

    def files(self, bu):
        return {fl["path"]: fl for fl in bu["storage"]["files"]}

    def test_the_image_holds_the_host_key_and_no_role(self):
        h, bu = self.butane(self.ram_fleet())
        files = self.files(bu)
        self.assertNotIn("/var/lib/kiwi-server/role.sh", files)
        self.assertEqual(files[ram.HOST_KEY]["mode"], 0o600)
        self.assertIn("OPENSSH PRIVATE KEY", files[ram.HOST_KEY]["contents"]["inline"])
        self.assertEqual(files[ram.SSHD_DROP_IN]["contents"]["inline"], "HostKey %s\n" % ram.HOST_KEY)
        self.assertIn("Storage=volatile", files["/etc/systemd/journald.conf.d/50-kiwi-ram.conf"]["contents"]["inline"])
        self.assertIn("/etc/issue.d/50-kiwi-ram.issue", files)
        self.assertEqual(bu["passwd"]["users"][0]["ssh_authorized_keys"], [KEY])
        self.assertEqual(bu["systemd"]["units"], [])         # nothing to run at boot: home pushes
        text = ram.render(h)
        for secret in ("pihole_password", "wireguard_private_key", "vpn.example.org", "wg_password"):
            self.assertNotIn(secret, text)

    def test_the_host_key_stays_and_is_on_record(self):
        f = self.ram_fleet()
        h, bu = self.butane(f)
        key = self.files(bu)[ram.HOST_KEY]["contents"]["inline"]
        _h, again = self.butane(config.Fleet.load(f.path))
        self.assertEqual(self.files(again)[ram.HOST_KEY]["contents"]["inline"], key)
        pub = util.read_text(os.path.join(ram.key_dir(h), "ssh_host_ed25519_key.pub")).split()
        known = util.read_text(f.resolve_path("secrets/known_hosts"))
        self.assertIn("trifle.kiwi,vpn.example.org %s %s" % (pub[0], pub[1]), known)

    def test_pin_replaces_an_earlier_install_once(self):
        f = self.ram_fleet()
        h = f.hosts["trifle"]
        self.prepare(h)
        known = f.resolve_path("secrets/known_hosts")
        write(known, "vpn.example.org ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOldOldOldOldOldOldOldOldOldOldOldOldOldOldOld\n"
                     "other.kiwi ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOtherOtherOtherOtherOtherOtherOtherOtherOth\n")
        pub = ram.host_key(h)[1]
        self.assertTrue(ram.pin(h, pub, known))
        self.assertFalse(ram.pin(h, pub, known))
        text = util.read_text(known)
        self.assertNotIn("OldOld", text)
        self.assertIn("other.kiwi", text)

    def test_with_the_ssh_ca_the_image_shows_a_certificate(self):
        f = self.ram_fleet()
        sshca.init(f)
        _h, bu = self.butane(config.Fleet.load(f.path))
        files = self.files(bu)
        self.assertIn("ssh-ed25519-cert-v01@openssh.com", files[sshca.HOST_CERT]["contents"]["inline"])
        self.assertIn("TrustedUserCAKeys %s" % sshca.USER_CA, files[sshca.DROP_IN]["contents"]["inline"])

    def test_kernel_arguments(self):
        h, _bu = self.butane(self.ram_fleet())
        self.assertEqual(ram.live_kargs(h), [])
        h, _bu = self.butane(self.ram_fleet(ram={"copy_to_ram": True},
                                            coreos={"console": "ttyS0,115200n8", "kernel_arguments": ["mitigations=auto"]}))
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
        h = f.hosts["trifle"]
        self.assertEqual(h.validate(self.roles), [])
        self.assertFalse(any("disk" in w for w in h.warnings))      # it touches no disk
        bad = self.fleet({"defaults": self.base_defaults(target="coreos-ram", role="master"),
                          "hosts": {"a": {"master": self.master(), "unlock": {"rings": ["infra"]}},
                                    "b": {"master": self.master(), "coreos": {"luks": {"device": "/dev/vda"}}},
                                    "c": {"master": self.master(), "ram": {"copy_to_ram": "yes"}},
                                    "d": {"master": self.master(), "ram": {"keep_logs": True}},
                                    "n": {"role": "node-cloud", "node-cloud": self.node_cloud()}}})
        errs = {n: " ".join(h.validate(self.roles)) for n, h in bad.hosts.items()}
        self.assertIn("no disk to unlock", errs["a"])
        self.assertIn("coreos.luks", errs["b"])
        self.assertIn("ram.copy_to_ram must be true or false", errs["c"])
        self.assertIn("unknown key 'keep_logs'", errs["d"])
        self.assertIn("does not support target coreos-ram", errs["n"])

    def test_keep_and_put_back(self):
        f = self.ram_fleet()
        h = f.hosts["trifle"]
        self.prepare(h)
        seen = []

        def runner(argv, **kw):
            seen.append(argv[-1])
            return subprocess.CompletedProcess(argv, 0, b"TAR", b"")
        self.assertFalse(ram.put_back(h, "core@trifle", runner=runner))      # nothing kept yet
        self.assertTrue(ram.keep(h, "core@trifle", runner=runner))
        self.assertIn("km-nginx/letsencrypt km-nginx/acme", seen[-1])
        self.assertFalse(ram.keep(h, "core@trifle", runner=runner))          # unchanged
        self.assertFalse(ram.keep(h, "core@trifle", runner=done(b"")))       # nothing there: the copy stays
        self.assertEqual(util.read_bytes(os.path.join(ram.key_dir(h), "kept.tar")), b"TAR")
        self.assertTrue(ram.put_back(h, "core@trifle", runner=done(b"restored\n")))
        self.assertFalse(ram.put_back(h, "core@trifle", runner=done(b"present\n")))

    def test_state(self):
        self.assertEqual(ram.state("x", runner=done(b"blank\n")), "blank")
        self.assertEqual(ram.state("x", runner=done(b"ready\n")), "ready")
        self.assertEqual(ram.state("x", runner=done(b"", rc=255)), "unreachable")
        # the probe does not find itself (its paths moved where nothing else runs)
        probe = ram.STATE_PROBE.replace("var/lib/kiwi-server", self.tmp.lstrip("/") + "/kiwi")
        self.assertIn("[/]%s/kiwi/role.sh" % self.tmp.lstrip("/"), probe)
        r = subprocess.run(["sh", "-c", probe], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "blank")
        write(os.path.join(self.tmp, "kiwi", "role.done"), "")
        r = subprocess.run(["sh", "-c", probe], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "ready")

    def watch_args(self, f, **over):
        a = argparse.Namespace(fleet=f.path, hosts=[], ssh=None, once=True, interval=60, timer=None,
                               toolchain="auto", output_dir=None, cache=None, verbose=False)
        a.__dict__.update(over)
        return a

    def test_watch_sends_the_role_to_a_blank_master(self):
        f = self.ram_fleet()
        with mock.patch.object(ram, "state", return_value="blank"), mock.patch.object(cli, "cmd_apply") as apply:
            cli.cmd_watch(self.watch_args(f))
        self.assertEqual(apply.call_args[0][0].hosts, ["trifle"])
        self.assertFalse(apply.call_args[0][0].no_run)

    def test_watch_keeps_the_copies_of_a_running_master_hourly(self):
        f = self.ram_fleet()
        with mock.patch.object(ram, "state", return_value="ready"), mock.patch.object(cli, "cmd_apply") as apply, \
                mock.patch.object(backup, "keep_wireguard") as wg, mock.patch.object(ram, "keep") as kept:
            cli.cmd_watch(self.watch_args(f))
            cli.cmd_watch(self.watch_args(f))
        apply.assert_not_called()
        self.assertEqual((wg.call_count, kept.call_count), (1, 1))
        self.assertIn("trifle", json.loads(util.read_text(f.resolve_path("output/watch.json"))))

    def test_watch_timer(self):
        calls = []

        def systemctl(*a):
            calls.append(a)
            return subprocess.CompletedProcess(a, 0, "", "")
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp}):
            ram.watch_timer("/f/fleet.yaml", True, systemctl=systemctl)
            unit = os.path.join(self.tmp, "systemd", "user", ram._unit("/f/fleet.yaml"))
            self.assertIn("watch /f/fleet.yaml --once", util.read_text(unit + ".service"))
            self.assertIn("OnUnitInactiveSec=1min", util.read_text(unit + ".timer"))
            ram.watch_timer("/f/fleet.yaml", False, systemctl=systemctl)
            self.assertFalse(os.path.exists(unit + ".timer"))
        self.assertIn(("enable", "--now", ram._unit("/f/fleet.yaml") + ".timer"), calls)

    def test_render_and_offline(self):
        f = self.ram_fleet()
        out = os.path.join(self.tmp, "out")
        self.assertEqual(cli.main(["render", f.path, "trifle", "--no-backup", "-o", out]), 0)
        bu = yaml.safe_load(util.read_text(os.path.join(out, "trifle", "trifle.bu")))
        paths = [fl["path"] for fl in bu["storage"]["files"]]
        self.assertNotIn("/var/lib/kiwi-server/role.sh", paths)
        self.assertIn(ram.HOST_KEY, paths)
        self.assertTrue(os.path.isfile(os.path.join(out, "trifle", "trifle.role.sh")))
        readme = util.read_text(os.path.join(out, "trifle", "README.txt"))
        self.assertIn("kiwi-server watch", readme)
        self.assertNotIn("install disk", readme)
        with self.assertRaisesRegex(KiwiError, "never carries the role"):
            cli._render_or_build(type("A", (), {"fleet": f.path, "hosts": ["trifle"], "offline": True, "bare": False,
                                                "toolchain": "auto", "output_dir": out, "no_backup": True})(), True)

    def test_targets_lists_it(self):
        self.assertIn("coreos-ram", config.TARGETS)
        self.assertIn("coreos-ram", self.roles["master"].targets)
        self.assertIn("coreos-ram", self.roles["bare"].targets)
        self.assertNotIn("coreos-ram", self.roles["node"].targets)

    @unittest.skipUnless(have("butane"), "butane not installed")
    def test_butane_strict(self):
        h, _bu = self.butane(self.ram_fleet())
        bu = os.path.join(self.tmp, "trifle.bu")
        write(bu, ram.render(h))
        r = subprocess.run(["butane", "--strict", "--pretty", bu], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
