"""Whether the disk's key opens a disk, asked of the slow key derivation only when the key or the
disk's LUKS header changed (luks.HEADSUM, known_opens, keep_opens; unlock.LIST_SCRIPT and
ADD_KEY_SCRIPT). The scripts run for real against stand-ins of cryptsetup, blkid and clevis."""
import os
import subprocess
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

from kiwiserver import luks, unlock  # noqa: E402


class TestOpens(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.makedirs(self.d + "/bin")
        os.makedirs(self.d + "/dev")
        os.makedirs(self.d + "/fleet/secrets/luks")
        for dev in ("a", "b"):
            open(self.d + "/dev/" + dev, "w").close()
        with open(self.d + "/header-b", "w") as f:
            f.write("LUKS header information\nVersion: 2\nEpoch: 7\n")
        stubs = {
            # the key derivation: each question counted; the key "right" opens b only
            "cryptsetup": 'case "$1" in\n  luksDump) [ "$2" = %(d)s/dev/b ] && cat %(d)s/header-b || echo "header of $2" ;;\n'
                          '  open) echo x >> %(d)s/asked; k=; for a; do case $a in --key-file=*) k=${a#--key-file=} ;; esac; done\n'
                          '        [ "$(cat "$k")" = right ] && [ "${*: -1}" = %(d)s/dev/b ] ;;\n  *) exit 1 ;;\nesac\n',
            "blkid": 'printf "%(d)s/dev/a\\n%(d)s/dev/b\\n"\n',
            "clevis": 'echo "1: sss \'{\\"t\\":1,\\"pins\\":{\\"tang\\":[{\\"url\\":\\"http://ring:7500\\"}]}}\'"\n',
        }
        for name, body in stubs.items():
            with open(self.d + "/bin/" + name, "w") as f:
                f.write("#!/bin/bash\n" + body % {"d": self.d})
            os.chmod(self.d + "/bin/" + name, 0o755)
        fleet = types.SimpleNamespace(resolve_path=lambda p: os.path.join(self.d, "fleet", p))
        self.host = types.SimpleNamespace(name="quince", fleet=fleet)

    def run_script(self, script, key):
        script = script.replace("/run/kiwi-luks", self.d + "/kiwi-luks").replace("[ -b ", "[ -e ")
        data = "%s\n%s\n" % (key, luks.known_opens(self.host, key))
        r = subprocess.run(["bash", "-c", script], input=data, text=True, capture_output=True,
                           env=dict(os.environ, PATH=self.d + "/bin:" + os.environ["PATH"]))
        luks.keep_opens(self.host, key, r.stdout)
        return r.stdout

    def asked(self):
        p = self.d + "/asked"
        if not os.path.exists(p):
            return 0
        with open(p) as f:
            n = len(f.readlines())
        os.remove(p)
        return n

    def test_the_key_is_asked_again_only_when_key_or_header_changed(self):
        out = self.run_script(unlock.LIST_SCRIPT, "right")
        self.assertTrue(out.startswith("device %s/dev/b\nheader " % self.d), out)
        self.assertIn("1: sss", out)
        self.assertEqual(self.asked(), 2)                           # a, then b
        out = self.run_script(unlock.LIST_SCRIPT, "right")
        self.assertTrue(out.startswith("device %s/dev/b\n" % self.d), out)
        self.assertEqual(self.asked(), 0)                           # the same key, the same header: not asked
        self.assertIn("1: sss", out)                                # and what is bound still read
        out = self.run_script(unlock.ADD_KEY_SCRIPT, "right")       # on CoreOS before the listing: the same answer
        self.assertTrue(out.startswith("already %s/dev/b" % self.d), out)
        self.assertEqual(self.asked(), 0)
        with open(self.d + "/header-b", "a") as f:                  # a binding or a key changed: the header too
            f.write("Epoch: 8\n")
        self.run_script(unlock.LIST_SCRIPT, "right")
        self.assertEqual(self.asked(), 2)
        self.run_script(unlock.LIST_SCRIPT, "right")
        self.assertEqual(self.asked(), 0)
        out = self.run_script(unlock.LIST_SCRIPT, "wrong")          # another key: asked, and it opens nothing
        self.assertEqual((out.strip(), self.asked()), ("none", 2))
        self.assertEqual(luks.known_opens(self.host, "wrong"), "")
        self.assertTrue(luks.known_opens(self.host, "right").startswith(self.d + "/dev/b "))   # the right one's answer stays
        with open(luks.opens_path(self.host)) as f:
            self.assertNotIn("right", f.read())                     # a sum of the key, never the key


if __name__ == "__main__":
    unittest.main()
