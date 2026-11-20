"""A standby for a part (lib/kiwiserver/partstandby.py): what the sender lets a key do, what the
fetcher does, what an apply installs. The scripts run here, with stand-ins for rsync and podman."""
import os
import subprocess
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
from kiwiserver import partstandby as ps  # noqa: E402

BACKUP_SH = '''restic() { podman run --rm --network host --env-file x; }
FREEZE[vault]="kn-vault"
FROZEN[vault]="/home/user/docker/kn-vault:/source/home/user/docker/kn-vault"
BULK[vault]=""
FREEZE[cloud]="kn-db"
FROZEN[cloud]="/srv/db:/source/srv/db"
BULK[cloud]="/srv/files:/source/srv/files"
FREEZE[paste]=""
FROZEN[paste]=""
BULK[paste]="privatebin_data:/volume/privatebin_data"
'''


def bundle(text=BACKUP_SH):
    return types.SimpleNamespace(files={"kiwi/backup.sh": (text, 0o755)})


class TestStandby(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_a_parts_folders_come_from_the_backup_script(self):
        self.assertEqual(ps.folders(bundle(), "vault"), {"frozen": ["/home/user/docker/kn-vault"], "bulk": [], "volumes": [],
                                                         "containers": ["kn-vault"], "runtime": "podman"})
        cloud = ps.folders(bundle(), "cloud")
        self.assertEqual((cloud["frozen"], cloud["bulk"], cloud["containers"]), (["/srv/db"], ["/srv/files"], ["kn-db"]))
        self.assertEqual(ps.folders(bundle(), "paste")["volumes"], ["privatebin_data"])     # a volume: not followed yet
        self.assertIsNone(ps.folders(bundle(), "nothing"))
        self.assertIsNone(ps.folders(types.SimpleNamespace(files={}), "vault"))              # no backup part
        conf = ps.send_conf({"vault": ps.folders(bundle(), "vault"), "cloud": cloud})
        self.assertEqual(conf, "runtime podman\n/srv/db frozen kn-db\n/srv/files bulk\n/home/user/docker/kn-vault frozen kn-vault\n")

    def test_the_key_can_only_read_the_folders_kept_ready(self):
        line = ps.authorized_line("ssh-ed25519 AAAAkey kiwi-standby-medlar")
        self.assertEqual(line, 'command="/usr/local/sbin/kiwi-standby-send",restrict ssh-ed25519 AAAAkey kiwi-standby')
        conf = os.path.join(self.tmp, "send.conf")
        with open(conf, "w") as fh:
            fh.write("runtime podman\n/srv/db frozen kn-db kn-db2\n/srv/files bulk\n")
        log = os.path.join(self.tmp, "log")
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir)
        for name in ("rsync", "podman"):
            with open(os.path.join(bindir, name), "w") as fh:
                fh.write("#!/bin/bash\necho \"%s $*\" >> %s\n" % (name, log))
            os.chmod(os.path.join(bindir, name), 0o755)
        script = ps.SEND.replace("/etc/kiwi-server/standby/send.conf", conf)

        def run(cmd):
            if os.path.exists(log):
                os.remove(log)
            r = subprocess.run(["bash", "-c", script], env={"PATH": bindir + ":/usr/bin:/bin", "SSH_ORIGINAL_COMMAND": cmd},
                               capture_output=True, text=True)
            said = open(log).read().splitlines() if os.path.exists(log) else []
            return r.returncode, said, r.stderr
        ok = "rsync --server --sender -vlogDtpre.iLsfxCIvu --numeric-ids . /srv/db/"
        rc, said, _err = run(ok)
        self.assertEqual(rc, 0)
        self.assertEqual(said, ["podman pause kn-db", "podman pause kn-db2", ok, "podman unpause kn-db", "podman unpause kn-db2"])
        rc, said, _err = run("rsync --server --sender -vlogDtpre.iLsfxCIvu . /srv/files/")
        self.assertEqual((rc, said), (0, ["rsync --server --sender -vlogDtpre.iLsfxCIvu . /srv/files/"]))      # no database: nothing paused
        for bad in ("rsync --server -vlogDtpre.iLsfxCIvu . /srv/db/",                         # writing here
                    "rsync --server --sender -vlogDtpre.iLsfxCIvu --remove-source-files . /srv/db/",
                    "rsync --server --sender -vlogDtpre.iLsfxCIvu . /etc/",                    # another folder
                    "rsync --server --sender -vlogDtpre.iLsfxCIvu . /srv/db/../../etc/",
                    "rsync --server --sender -vlogDtpre.iLsfxCIvu . /srv/db/ /etc/",
                    "cat /etc/shadow", "", "rsync --server --sender -e.x . /srv/db/; id"):
            rc, said, err = run(bad)
            self.assertEqual((rc != 0, said), (True, []), bad)
            self.assertIn("kiwi-standby:", err)

    def test_the_fetcher_marks_a_part_only_when_all_of_it_came(self):
        d = os.path.join(self.tmp, "standby")
        os.makedirs(d)
        with open(os.path.join(d, "pull.conf"), "w") as fh:
            fh.write("vault quince.home %s/a\ncloud papaya.home %s/b\ncloud papaya.home %s/c\n" % (self.tmp, self.tmp, self.tmp))
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir)
        with open(os.path.join(bindir, "rsync"), "w") as fh:        # the folder .../c does not come
            fh.write("#!/bin/bash\necho \"$*\" >> %s/rsync.log\ncase \"$*\" in */c/) echo 'rsync: connection refused' >&2; exit 12;; esac\n" % self.tmp)
        with open(os.path.join(bindir, "logger"), "w") as fh:
            fh.write("#!/bin/bash\necho \"$*\" >> %s/logger.log\n" % self.tmp)
        with open(os.path.join(bindir, "flock"), "w") as fh:
            fh.write("#!/bin/bash\nexit 0\n")
        for n in ("rsync", "logger", "flock"):
            os.chmod(os.path.join(bindir, n), 0o755)
        script = ps.PULL.replace("d=/etc/kiwi-server/standby", "d=" + d).replace("exec 9>/run/kiwi-standby.lock", "exec 9>%s/lock" % self.tmp)
        for _ in range(2):
            subprocess.run(["bash", "-c", script], env={"PATH": bindir + ":/usr/bin:/bin"}, check=True)
        self.assertTrue(os.path.isfile(os.path.join(d, "state", "vault.ok")))
        self.assertFalse(os.path.exists(os.path.join(d, "state", "cloud.ok")))                 # one of its folders did not come
        calls = open(os.path.join(self.tmp, "rsync.log")).read()
        self.assertIn("root@quince.home:%s/a/ %s/a/" % (self.tmp, self.tmp), calls)
        self.assertIn("--delete --numeric-ids", calls)
        said = open(os.path.join(self.tmp, "logger.log")).read().splitlines()
        self.assertEqual(len(said), 1)                                                         # the same reason is said once
        self.assertIn("cloud from papaya.home: rsync: connection refused", said[0])

    def test_ages(self):
        self.assertEqual(ps.ages("vault 12\ncloud 4000\nnoise\n"), {"vault": 12, "cloud": 4000})


if __name__ == "__main__":
    unittest.main()
