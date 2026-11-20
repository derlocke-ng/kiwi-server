"""A standby for a part (lib/kiwiserver/partstandby.py): what the sender lets a key do, what the
fetcher does, what an apply installs. The scripts run here, with stand-ins for rsync and podman."""
import os
import subprocess
import sys
import tempfile
import time
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
                                                         "frozen_volumes": [], "bulk_volumes": [],
                                                         "containers": ["kn-vault"], "runtime": "podman"})
        cloud = ps.folders(bundle(), "cloud")
        self.assertEqual((cloud["frozen"], cloud["bulk"], cloud["containers"]), (["/srv/db"], ["/srv/files"], ["kn-db"]))
        paste = ps.folders(bundle(), "paste")
        self.assertEqual((paste["volumes"], paste["bulk_volumes"], paste["bulk"]), (["privatebin_data"], ["privatebin_data"], []))
        self.assertEqual(ps.send_conf({"paste": paste}), "runtime podman\nvol:privatebin_data bulk\n")      # a volume: looked up on the machine
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
        with open(os.path.join(bindir, "logger"), "w") as fh:        # (what it refuses goes to the journal)
            fh.write("#!/bin/bash\nexit 0\n")
        os.chmod(os.path.join(bindir, "logger"), 0o755)
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
        timed = "rsync --server --sender -logDtpre.iLsfxCIvu --timeout=120 --numeric-ids . /srv/db/"     # as rsync 3.5 asks
        self.assertEqual(run(timed)[:2], (0, ["podman pause kn-db", "podman pause kn-db2", timed, "podman unpause kn-db", "podman unpause kn-db2"]))
        rc, said, _err = run("rsync --server --sender -vlogDtpre.iLsfxCIvu . /srv/files/")
        self.assertEqual((rc, said), (0, ["rsync --server --sender -vlogDtpre.iLsfxCIvu . /srv/files/"]))      # no database: nothing paused
        for bad in ("rsync --server -vlogDtpre.iLsfxCIvu . /srv/db/",                         # writing here
                    "rsync --server --sender -vlogDtpre.iLsfxCIvu --remove-source-files . /srv/db/",
                    "rsync --server --sender -vlogDtpre.iLsfxCIvu . /etc/",                    # another folder
                    "rsync --server --sender -vlogDtpre.iLsfxCIvu . /srv/db/../../etc/",
                    "rsync --server --sender -vlogDtpre.iLsfxCIvu --timeout=1 --files-from=/etc/shadow . /srv/db/",
                    "rsync --server --sender -vlogDtpre.iLsfxCIvu --timeout=x . /srv/db/",
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
        # a folder that holds data at the first fetch (the machine ran the part before a switch): kept beside it first
        e = os.path.join(self.tmp, "e")
        os.makedirs(e)
        with open(os.path.join(e, "db.sqlite3"), "w") as fh:
            fh.write("written after the copy\n")
        os.utime(e, (time.time() - 40 * 86400,) * 2)                                         # a folder made long ago
        with open(os.path.join(d, "pull.conf"), "w") as fh:
            fh.write("paste lychee.home %s\n" % e)
        for _ in range(2):
            subprocess.run(["bash", "-c", script], env={"PATH": bindir + ":/usr/bin:/bin"}, check=True)
        kept = [x for x in os.listdir(self.tmp) if x.startswith("e.kept-")]
        self.assertEqual(len(kept), 1)                                                         # once, not at every fetch
        self.assertEqual(open(os.path.join(self.tmp, kept[0], "db.sqlite3")).read(), "written after the copy\n")
        self.assertIn("paste: what %s held is kept in" % e, open(os.path.join(self.tmp, "logger.log")).read())
        self.assertFalse(os.path.exists(os.path.join(d, "state", "paste.keeping")))           # gone with the whole fetch
        # a later first fetch (a switch cleared the mark): kept again beside the first, which stays
        os.remove(os.path.join(d, "state", "paste.ok"))
        time.sleep(1.1)
        subprocess.run(["bash", "-c", script], env={"PATH": bindir + ":/usr/bin:/bin"}, check=True)
        self.assertEqual(len([x for x in os.listdir(self.tmp) if x.startswith("e.kept-")]), 2)
        # a folder that cannot be kept beside itself is not fetched over
        os.remove(os.path.join(d, "state", "paste.ok"))
        with open(os.path.join(bindir, "cp"), "w") as fh:
            fh.write("#!/bin/bash\nexit 1\n")
        os.chmod(os.path.join(bindir, "cp"), 0o755)
        before = open(os.path.join(self.tmp, "rsync.log")).read().count(" %s/" % e)
        subprocess.run(["bash", "-c", script], env={"PATH": bindir + ":/usr/bin:/bin"}, check=True)
        self.assertEqual(open(os.path.join(self.tmp, "rsync.log")).read().count(" %s/" % e), before)
        self.assertFalse(os.path.exists(os.path.join(d, "state", "paste.ok")))
        self.assertIn("could not be kept beside it", open(os.path.join(self.tmp, "logger.log")).read())

    def test_an_apply_sets_up_both_sides_with_a_volume(self):
        """The machine that runs the part says where its volume lies; the standby asks for that folder
        and puts it into its own volume of the same name."""
        import json
        fleet = types.SimpleNamespace(resolve_path=lambda p: os.path.join(self.tmp, p))
        os.makedirs(os.path.join(self.tmp, "secrets", "standby"))
        for n in ("lychee",):                           # the standby's key, made before (ssh-keygen need not be here)
            with open(os.path.join(self.tmp, "secrets", "standby", n + "_ed25519"), "w") as fh:
                fh.write("PRIVATE\n")
            with open(os.path.join(self.tmp, "secrets", "standby", n + "_ed25519.pub"), "w") as fh:
                fh.write("ssh-ed25519 AAAApub kiwi-standby-%s\n" % n)
        medlar = types.SimpleNamespace(name="medlar", hostname="medlar.home", cfg={"keep_ready": {"paste": "lychee"}})
        lychee = types.SimpleNamespace(name="lychee", hostname="lychee.home", cfg={})
        fleet.hosts = {"medlar": medlar, "lychee": lychee}
        ran = []

        def runner(tgt, script, data=None):
            ran.append((tgt, script, data))
            if "send.conf" in script and "authorized_keys" in script:
                return b"kiwi-volume privatebin_data /var/lib/containers/storage/volumes/privatebin_data/_data\nssh-ed25519 AAAAhost root@medlar\n"
            return b""
        render = lambda h: ("", bundle())
        ps.after_apply(fleet, medlar, "core@medlar", {}, None, render, runner=runner)
        send = next(x for x in ran if "send.conf" in x[1] and "authorized_keys" in x[1])
        self.assertIn("vol:privatebin_data bulk", send[1])                       # looked up on the machine itself
        self.assertIn('command="/usr/local/sbin/kiwi-standby-send",restrict ssh-ed25519 AAAApub kiwi-standby', send[2].decode())
        self.assertEqual(json.load(open(os.path.join(self.tmp, "secrets", "standby", "medlar.volumes"))),
                         {"privatebin_data": "/var/lib/containers/storage/volumes/privatebin_data/_data"})
        del ran[:]
        ps.after_apply(fleet, lychee, "core@lychee", {}, None, render, runner=runner)
        pull = next(x for x in ran if "pull.conf" in x[1] and "kiwi-standby.timer" in x[1])
        self.assertIn("paste medlar.home /var/lib/containers/storage/volumes/privatebin_data/_data vol:privatebin_data", pull[1])
        self.assertIn("medlar.home ssh-ed25519 AAAAhost", pull[1])               # whom it trusts
        self.assertEqual(pull[2].decode(), "PRIVATE\n")                          # its key on standard input, not in the script
        self.assertNotIn("PRIVATE", pull[1])

    def test_a_switch_leaves_the_two_a_pair(self):
        """After a switch the machine the part left keeps it ready: a failover back is possible as soon
        as that machine is back. A move (not a switch) ends the standby."""
        from kiwiserver import partmove
        raw = {"hosts": {"quince": {"role": "node", "keep_ready": {"vault": "medlar"}, "node": {"modules": ["vault"]}},
                         "medlar": {"role": "node", "node": {"modules": ["git"]}}}}
        q = types.SimpleNamespace(name="quince", role="node", role_settings_raw={"modules": ["vault"]})
        m = types.SimpleNamespace(name="medlar", role="node", role_settings_raw={"modules": ["git"]})
        after = partmove.changed_data(raw, "vault", q, m, swap_ready=True)
        self.assertNotIn("keep_ready", after["hosts"]["quince"])
        self.assertEqual(after["hosts"]["medlar"]["keep_ready"], {"vault": "quince"})
        self.assertEqual(after["hosts"]["medlar"]["node"]["modules"], ["git", "vault"])
        moved = partmove.changed_data(raw, "vault", q, m)
        self.assertNotIn("keep_ready", moved["hosts"]["medlar"])

    def test_ages(self):
        self.assertEqual(ps.ages("vault 12\ncloud 4000\nnoise\n"), {"vault": 12, "cloud": 4000})


if __name__ == "__main__":
    unittest.main()
