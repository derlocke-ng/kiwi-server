"""Golden output of every preset: the stack directory a render produces for a
fixed fleet (fixed seed, fixed secrets, fixed CA) is compared file by file
against tests/golden/<host>/. The split of kiwi-modules and kiwi-stack out of
this tree must not change a byte of it; a deliberate change regenerates the
files with UPDATE_GOLDEN=1 and shows up in the diff of the commit.
Run: python3 -m unittest tests.test_golden
"""
import base64
import difflib
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))
os.environ["KIWI_SERVER_HOME"] = ROOT

import yaml  # noqa: E402

from kiwiserver import cli, config, modules as modmod, roles as rolesmod  # noqa: E402
from kiwiserver.toolchain import Toolchain  # noqa: E402

GOLDEN = os.path.join(HERE, "golden")
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEh+golden+kiwi+server+key+00000000000000000000 golden@kiwi"
SEED = b"kiwi-golden-seed-0123456789abcdef"      # 32 bytes, never used for a real fleet
CA_PEM = "-----BEGIN CERTIFICATE-----\nR09MREVOLUNBLU5PVC1BLVJFQUwtQ0VSVA==\n-----END CERTIFICATE-----\n"
WG = "[Interface]\nPrivateKey = Z29sZGVuLXByaXZhdGUta2V5LW5vdC1yZWFsLTAwMDAwMDAwMDA=\nAddress = %s/24\nDNS = 10.8.0.1\n\n[Peer]\nPublicKey = Z29sZGVuLXNlcnZlci1wdWJsaWMta2V5LW5vdC1yZWFsLTAwMDA=\nAllowedIPs = 0.0.0.0/0\nEndpoint = vpn.example.org:51820\n"

TLS = {"tls_fullchain": "secrets/golden-fullchain.pem", "tls_privkey": "secrets/golden-privkey.pem"}
FLEET = {
    "defaults": {"target": "ucore", "role": "bare", "domain": "home", "timezone": "Europe/Berlin", "disk": "/dev/sda",
                 "tls": {"auto": False},
                 "admin": {"user": "core", "password": "golden-admin-pw", "ssh_keys": [KEY]}},
    "hosts": {
        "gate": {"target": "debian", "role": "master", "master": {
            "vpn-client": {"vpn_provider": "mullvad", "wireguard_private_key": "golden-mullvad-key",
                           "wireguard_addresses": "10.66.1.2/32", "server_countries": "Germany"},
            "vpn-server": {"wg_host": "vpn.example.org", "wg_password": "golden-wg-password"},
            "dns": {"pihole_password": "golden-pihole-master"}, "reverse-proxy": dict(TLS)}},
        "m1": {"role": "node", "node": {
            "preset": "gateway", "pub_iface": "eth0",
            "dns": {"pihole_password": "golden-pihole-m1"},
            "downloader": {"download_dir": "/mnt/dl", "transmission_password": "golden-transmission"},
            "sftp": {"sftp_password": "golden-sftp"}, "reverse-proxy": dict(TLS)}},
        "sh3": {"role": "node", "node": {
            "preset": "cloud",
            "cloud": {"admin_password": "golden-nc-admin", "collabora": True, "talk": True}, "reverse-proxy": dict(TLS)}},
        "min": {"target": "coreos", "role": "node", "node": {"preset": "minimal", "reverse-proxy": dict(TLS)}},
    },
}
ADDRESSES = {"m1": "10.8.0.6", "sh3": "10.8.0.25", "min": "10.8.0.9"}


def as_text(content):
    if isinstance(content, bytes):
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return "base64:" + base64.b64encode(content).decode()
    return content


class TestGolden(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kiwi-golden-")
        os.makedirs(os.path.join(self.tmp, "secrets"), mode=0o700)
        with open(os.path.join(self.tmp, "secrets", "seed"), "wb") as f:
            f.write(SEED)
        with open(os.path.join(self.tmp, "secrets", "golden-fullchain.pem"), "w") as f:
            f.write(CA_PEM)
        with open(os.path.join(self.tmp, "secrets", "golden-privkey.pem"), "w") as f:
            f.write("-----BEGIN PRIVATE KEY-----\nR09MREVOLUtFWS1OT1QtUkVBTA==\n-----END PRIVATE KEY-----\n")
        for name, addr in ADDRESSES.items():
            with open(os.path.join(self.tmp, "secrets", "%s.home.conf" % name), "w") as f:
                f.write(WG % addr)
        path = os.path.join(self.tmp, "fleet.yaml")
        with open(path, "w") as f:
            yaml.safe_dump(FLEET, f, sort_keys=False)
        self.fleet = config.Fleet.load(path)
        self.roles = rolesmod.discover()
        self.ms = modmod.discover()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def render_all(self):
        r = cli.Renderer(self.fleet, self.roles, os.path.join(self.tmp, "out"), Toolchain(mode="native"), self.ms)
        out = {}
        for name, host in self.fleet.hosts.items():
            self.assertEqual(host.validate(self.roles), [], name)
            role = r.prepare(host)
            records = r.dns_records()
            spec = rolesmod.stack_spec(host, role, records, master_ip=r._master_ip, ca_cert=CA_PEM)
            b = modmod.Renderer(self.ms).render(spec)
            files = {rel: as_text(c) for rel, (c, _m) in b.files.items()}
            files.update({"host-units/%s" % n: t for n, t in b.units.items()})
            files.update({"quadlets/%s" % n: t for n, t in b.quadlets.items()})
            files["stack.sh"] = rolesmod.stack_manifest(host, role, "VERSION", b)
            files["meta.txt"] = "\n".join([
                "modes: " + " ".join("%s:%04o" % (rel, m) for rel, (_c, m) in sorted(b.files.items())),
                "dirs: " + " ".join(b.dirs), "ports: " + " ".join(b.ports), "services: " + " ".join(b.services),
                "images: " + " ".join(b.images), "urls: " + " | ".join(b.urls),
                "hosts: " + " ".join("%s=%s" % (ip, n) for ip, n in b.hosts),
                "vpn_dependents: " + " ".join(b.vpn_dependents), "mesh_via: %s %s" % (b.mesh_via, b.mesh_subnet),
                "dns_ip: %s" % b.dns_ip, "sysctl: " + " ".join("%s=%s" % kv for kv in sorted(b.sysctl.items())),
                "kernel_modules: " + " ".join(b.kernel_modules), "auto_update: %s" % b.auto_update, ""])
            out[name] = files
        return out

    def test_every_preset_matches_its_golden_output(self):
        rendered = self.render_all()
        if os.environ.get("UPDATE_GOLDEN"):
            shutil.rmtree(GOLDEN, ignore_errors=True)
            for name, files in rendered.items():
                for rel, text in files.items():
                    p = os.path.join(GOLDEN, name, rel)
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    with open(p, "w") as f:
                        f.write(text)
            self.skipTest("golden files regenerated under tests/golden/")
        self.assertTrue(os.path.isdir(GOLDEN), "no tests/golden/ — run with UPDATE_GOLDEN=1 once")
        problems = []
        for name, files in rendered.items():
            gdir = os.path.join(GOLDEN, name)
            expected = {}
            for dp, _dn, fns in os.walk(gdir):
                for fn in fns:
                    p = os.path.join(dp, fn)
                    with open(p) as f:
                        expected[os.path.relpath(p, gdir)] = f.read()
            for rel in sorted(set(expected) | set(files)):
                if rel not in expected:
                    problems.append("%s/%s: new file, not in the golden set" % (name, rel))
                elif rel not in files:
                    problems.append("%s/%s: no longer rendered" % (name, rel))
                elif expected[rel] != files[rel]:
                    diff = difflib.unified_diff(expected[rel].splitlines(), files[rel].splitlines(),
                                                "golden/%s/%s" % (name, rel), "rendered", lineterm="", n=2)
                    problems.append("\n".join(list(diff)[:60]))
        self.assertEqual(problems, [], "\n\n".join(problems) +
                         "\n\nA deliberate change: UPDATE_GOLDEN=1 python3 -m unittest tests.test_golden, then commit tests/golden/")


if __name__ == "__main__":
    unittest.main()
