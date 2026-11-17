"""Riseup as a VPN client's exit: the certificate fetched, kept and renewed, a gateway that answers."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

from kiwiserver import riseup  # noqa: E402
from kiwiserver.util import KiwiError  # noqa: E402

SERVICE = {"gateways": [
    {"host": "vpn02-par.riseup.net", "ip_address": "192.0.2.2", "location": "paris",
     "capabilities": {"transport": [{"type": "openvpn", "protocols": ["tcp", "udp"], "ports": ["53", "80", "1194"]}]}},
    {"host": "vpn04-ams.riseup.net", "ip_address": "192.0.2.4", "location": "amsterdam",
     "capabilities": {"transport": [{"type": "openvpn", "protocols": ["tcp"], "ports": ["80"]}]}},
]}


def cert_pem(days):
    """A key and a certificate valid for `days` (openssl)."""
    d = tempfile.mkdtemp()
    try:
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=test", "-days", str(days),
                        "-keyout", os.path.join(d, "k"), "-out", os.path.join(d, "c")], check=True, capture_output=True)
        return open(os.path.join(d, "k")).read(), open(os.path.join(d, "c")).read()
    finally:
        shutil.rmtree(d)


@unittest.skipUnless(shutil.which("openssl"), "needs openssl to make a certificate")
class RiseupTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        riseup._done.clear()
        self.calls = []

    def api(self, days=35, down=False):
        key, cert = cert_pem(days)

        def get(url, accept):
            self.calls.append(url)
            if down:
                raise OSError("no route")
            if url.endswith("/cert"):
                return key + cert
            if url.endswith("ca.crt"):
                return cert
            return json.dumps(SERVICE)
        return get

    def test_a_gateway_that_does_not_answer_is_skipped(self):
        g = riseup.gateway(SERVICE, answers=lambda ip, port: ip != "192.0.2.2")
        self.assertEqual(g, ("192.0.2.4", "80", "vpn04-ams.riseup.net", "amsterdam"))
        self.assertEqual(riseup.gateway(SERVICE, "paris", answers=lambda ip, port: True)[:2], ("192.0.2.2", "1194"))
        self.assertIsNone(riseup.gateway(SERVICE, "paris", answers=lambda ip, port: False))

    def test_fetched_kept_and_renewed(self):
        full = os.path.join(self.d, "secrets", "riseup", "trifle.ovpn")
        riseup.ensure(full, get=self.api(35), answers=lambda ip, port: True)
        text = open(full).read()
        self.assertEqual(oct(os.stat(full).st_mode & 0o777), "0o600")
        for line in ("client", "proto tcp", "remote 192.0.2.2 1194", "<key>", "<cert>", "<ca>"):
            self.assertIn(line, text)
        self.assertGreater(riseup.days_left(text), 30)
        # good for long enough: kept, Riseup not asked
        riseup._done.clear(); self.calls.clear()
        riseup.ensure(full, get=self.api(35), answers=lambda ip, port: True)
        self.assertEqual(self.calls, [])
        # running out: renewed
        key, cert = cert_pem(5)                                                   # five days left now
        open(full, "w").write(riseup.config_text(key, cert, cert, ("192.0.2.2", "1194")))
        riseup._done.clear(); self.calls.clear()
        riseup.ensure(full, get=self.api(35), answers=lambda ip, port: True)
        self.assertTrue(self.calls)
        self.assertGreater(riseup.days_left(open(full).read()), 30)

    def test_riseup_down_keeps_a_good_one_and_says_so_without(self):
        full = os.path.join(self.d, "r.ovpn")
        with self.assertRaisesRegex(KiwiError, "its API does not answer"):
            riseup.ensure(full, get=self.api(down=True))
        riseup.ensure(full, get=self.api(5), answers=lambda ip, port: True)
        riseup._done.clear()
        riseup.ensure(full, get=self.api(down=True))           # 5 days left: it stays, with a warning
        self.assertGreater(riseup.days_left(open(full).read()), 4)

    def test_a_machine_gets_it_as_a_custom_openvpn_config(self):
        full = os.path.join(self.d, "secrets", "riseup", "trifle.ovpn")
        riseup.ensure(full, get=self.api(35), answers=lambda ip, port: True)
        host = types.SimpleNamespace(name="trifle", role_settings_raw={
            "vpn-client": {"vpn_provider": "riseup", "server_cities": "paris"}, "vpn-server": {"wg_host": "x"}},
            secrets=types.SimpleNamespace(resolve_path=lambda p: os.path.join(self.d, p)))
        riseup.settle(host)
        self.assertEqual(host.role_settings_raw["vpn-client"],
                         {"vpn_provider": "custom", "vpn_type": "openvpn", "openvpn_config": "secrets/riseup/trifle.ovpn"})
        self.assertEqual(host.role_settings_raw["vpn-server"], {"wg_host": "x"})
        other = types.SimpleNamespace(name="x", role_settings_raw={"vpn-client": {"vpn_provider": "mullvad"}})
        riseup.settle(other)
        self.assertEqual(other.role_settings_raw["vpn-client"], {"vpn_provider": "mullvad"})

    def test_the_control_node_renews_what_runs_out(self):
        from kiwiserver import control
        os.makedirs(os.path.join(self.d, "secrets", "riseup"))
        with open(os.path.join(self.d, "fleet.yaml"), "w") as f:
            f.write("defaults: {}\nhosts:\n"
                    "  trifle: {role: master, master: {vpn-client: {vpn_provider: riseup}}}\n"
                    "  tart: {role: master, master: {vpn-client: {vpn_provider: riseup}}}\n"
                    "  kiwano: {role: master, master: {vpn-client: {vpn_provider: mullvad}}}\n"
                    "  papaya: {role: node}\n")
        for name, days in (("trifle", 35), ("tart", 5)):
            key, cert = cert_pem(days)
            open(os.path.join(self.d, "secrets", "riseup", name + ".ovpn"), "w").write(
                riseup.config_text(key, cert, cert, ("192.0.2.2", "1194")))
        c = types.SimpleNamespace(fleet_path=os.path.join(self.d, "fleet.yaml"), fleet_dir=self.d)
        self.assertEqual(control.Control.riseup_due(c), ["tart"])
        os.remove(os.path.join(self.d, "secrets", "riseup", "trifle.ovpn"))
        self.assertEqual(control.Control.riseup_due(c), ["trifle", "tart"])


if __name__ == "__main__":
    unittest.main()
