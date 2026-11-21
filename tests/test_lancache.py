"""Install images from the LAN's cache first (iso.through_cache, the cache module's files): the
owner, 22 Nov 2026, "less traffic usage, faster updates/installs"."""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
from kiwiserver import iso  # noqa: E402


class FakeTc:
    """curl as the toolchain runs it: a URL gives its bytes, the LAN's cache only what it holds."""
    def __init__(self, upstream, cached, cache_answers=True):
        self.upstream, self.cached, self.answers, self.calls = upstream, cached, cache_answers, []

    def run(self, argv, mounts=(), capture=False, check=True):
        self.calls.append(argv)
        url = argv[-1]
        out = argv[argv.index("-o") + 1] if "-o" in argv else None
        if "/files/" in url:
            data = self.cached.get(url.split("/files/", 1)[1]) if self.answers else None
        else:
            data = self.upstream.get(url)
        if data is None:
            if check:
                raise RuntimeError("curl failed: %s" % url)
            return types.SimpleNamespace(returncode=22, stdout="", stderr="")
        if out:
            with open(out, "wb") as fh:
                fh.write(data)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return types.SimpleNamespace(returncode=0, stdout=data.decode(), stderr="")


class TestLanCache(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.addCleanup(setattr, iso, "LAN_CACHE", None)
        iso.LAN_CACHE = {"name": "cache.papaya.home", "address": "192.168.2.30", "ca": os.path.join(self.dir, "ca.pem")}
        self.image = b"debian netinst"
        base = iso.DEBIAN_BASE % "amd64"
        self.sums = ("%s  debian-13.2.0-amd64-netinst.iso\n" % hashlib.sha256(self.image).hexdigest()).encode()
        self.up = {base + "SHA256SUMS": self.sums, base + "debian-13.2.0-amd64-netinst.iso": self.image}
        self.held = {"cdimage.debian.org/debian-cd/current/amd64/iso-cd/debian-13.2.0-amd64-netinst.iso": self.image}

    def test_the_netinst_comes_from_the_cache_and_is_checked_against_debians_own_sums(self):
        tc = FakeTc(self.up, self.held)
        path = iso.debian_base_iso(tc, {}, self.dir)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), self.image)
        fetched = [c[-1] for c in tc.calls]
        self.assertIn("https://cache.papaya.home/files/cdimage.debian.org/debian-cd/current/amd64/iso-cd/debian-13.2.0-amd64-netinst.iso", fetched)
        self.assertNotIn(iso.DEBIAN_BASE % "amd64" + "debian-13.2.0-amd64-netinst.iso", fetched)    # not from the Internet
        self.assertIn(iso.DEBIAN_BASE % "amd64" + "SHA256SUMS", fetched)                             # the sums: Debian's own
        cache_call = next(c for c in tc.calls if "/files/" in c[-1])
        self.assertIn("cache.papaya.home:443:192.168.2.30", cache_call)

    def test_a_cache_that_is_away_or_holds_a_wrong_copy_means_the_server_itself(self):
        tc = FakeTc(self.up, self.held, cache_answers=False)
        path = iso.debian_base_iso(tc, {}, self.dir)
        self.assertIn(iso.DEBIAN_BASE % "amd64" + "debian-13.2.0-amd64-netinst.iso", [c[-1] for c in tc.calls])
        os.unlink(path)
        bad = {k: b"something else" for k in self.held}
        tc = FakeTc(self.up, bad)
        with open(iso.debian_base_iso(tc, {}, self.dir), "rb") as fh:
            self.assertEqual(fh.read(), self.image)             # the wrong copy was not taken
        iso.LAN_CACHE = None
        os.unlink(path)
        tc = FakeTc(self.up, self.held)
        iso.debian_base_iso(tc, {}, self.dir)
        self.assertFalse([c for c in tc.calls if "/files/" in c[-1]])     # no cache: not asked

    def test_coreos_gets_its_image_and_signature_from_the_cache_for_the_installer_to_check(self):
        stream = {"architectures": {"x86_64": {"artifacts": {"metal": {"formats": {"iso": {"disk": {
            "location": "https://builds.coreos.fedoraproject.org/prod/streams/stable/builds/43/x86_64/fcos-43-live-iso.x86_64.iso",
            "signature": "https://builds.coreos.fedoraproject.org/prod/streams/stable/builds/43/x86_64/fcos-43-live-iso.x86_64.iso.sig"}}}}}}}}
        up = {"https://builds.coreos.fedoraproject.org/streams/stable.json": json.dumps(stream).encode()}
        held = {"builds.coreos.fedoraproject.org/prod/streams/stable/builds/43/x86_64/fcos-43-live-iso.x86_64.iso": b"iso",
                "builds.coreos.fedoraproject.org/prod/streams/stable/builds/43/x86_64/fcos-43-live-iso.x86_64.iso.sig": b"sig"}
        tc = FakeTc(up, held)
        iso._fcos_from_cache(tc, "stable", self.dir)
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "fcos-43-live-iso.x86_64.iso")))
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "fcos-43-live-iso.x86_64.iso.sig")))


if __name__ == "__main__":
    unittest.main()
