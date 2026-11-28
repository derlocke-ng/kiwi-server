"""People's computers as clients (docs/design/clients.md): the client agent's script run for real
against a stand-in curl, its facts, its signature both sides make, and what the install script
carries (never the key)."""
import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

from kiwiserver import clientagent, machineagent  # noqa: E402
from kiwiserver.util import KiwiError  # noqa: E402

KEY = "a-client-key-of-the-test"


class FakeFleet:
    def __init__(self, clients, hosts=()):
        self.raw = {"clients": clients}
        self.hosts = {h: object() for h in hosts}

    def derive_secret(self, *parts, length=32):
        return ("|".join(parts) + "x" * length)[:length]


class TestClientAgent(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        for d in ("agent", "bin", "run"):
            os.makedirs(os.path.join(self.t, d))

        def w(p, text, mode=0o644):
            with open(os.path.join(self.t, p), "w") as f:
                f.write(text)
            os.chmod(os.path.join(self.t, p), mode)
        w("agent/conf", "URL=https://control.test\nNAME=pentest-1\n")
        w("agent/key", KEY, 0o600)
        w("agent/salt", "the-salt\n")
        w("agent/ca.pem", "")
        w("agent/facts.sh", clientagent.FACTS % {"version": clientagent.VERSION})
        # curl: keeps the headers and the body it was given, says 200
        w("bin/curl", "#!/bin/bash\nb=; : > %(t)s/run/head\nwhile [ $# -gt 0 ]; do case $1 in -H) echo \"$2\" >> %(t)s/run/head; shift ;;"
                      " --data-binary) b=${2#@}; shift ;; esac; shift; done\ncp \"$b\" %(t)s/run/sent; printf 200\n" % {"t": self.t}, 0o755)
        w("bin/logger", "#!/bin/bash\necho \"$*\" >> %s/run/logged\n" % self.t, 0o755)
        w("bin/flock", "#!/bin/bash\nexit 0\n", 0o755)
        self.script = (clientagent.SCRIPT % {"dir": self.t + "/agent"}).replace("/run/kiwi-client-agent", self.t + "/run/kiwi-client-agent")

    def run_agent(self):
        path = os.path.join(self.t, "bin", "kiwi-client-agent")
        with open(path, "w") as f:
            f.write(self.script)
        os.chmod(path, 0o755)
        r = subprocess.run([path], env=dict(os.environ, PATH=self.t + "/bin:" + os.environ["PATH"]),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(self.t, "run", "head")) as f:
            head = dict(line.rstrip("\n").split(": ", 1) for line in f if ": " in line)
        with open(os.path.join(self.t, "run", "sent"), "rb") as f:
            return head, f.read()

    def test_it_reports_signed_with_its_own_key(self):
        head, body = self.run_agent()
        self.assertEqual(head["X-Kiwi-Client"], "pentest-1")
        self.assertIsNone(clientagent.check(KEY, "pentest-1", head["X-Kiwi-Time"], head["X-Kiwi-Sign"], body))
        self.assertEqual(clientagent.check("another key", "pentest-1", head["X-Kiwi-Time"], head["X-Kiwi-Sign"], body),
                         "the signature is not this client's")
        with open(os.path.join(self.t, "run", "logged")) as f:
            self.assertIn("report to https://control.test: 200", f.read())    # said once, when it changes
        facts = clientagent.parse(body.decode())
        self.assertEqual(facts["agent"], str(clientagent.VERSION))
        self.assertTrue(facts.get("os") and facts.get("kernel"))
        self.assertIsInstance(facts.get("uptime"), int)
        self.assertIn("crypt", facts)
        self.assertNotIn(KEY, body.decode())

    def test_a_clients_report_never_passes_for_a_machines(self):
        """The same key, the same body: a client's signature is not a machine's, nor the other way round."""
        when, body = str(int(time.time())), b"facts\n"
        machine = machineagent.signature(KEY, "pentest-1", when, "", body)
        self.assertNotEqual(machine, clientagent.signature(KEY, "pentest-1", when, body))
        self.assertEqual(clientagent.check(KEY, "pentest-1", when, machine, body), "the signature is not this client's")
        self.assertIn("apart", clientagent.check(KEY, "pentest-1", str(int(time.time()) - 3600),
                                                 clientagent.signature(KEY, "pentest-1", str(int(time.time()) - 3600), body), body))

    def test_the_box_is_a_salted_sum_of_the_boards_id(self):
        """The same board gives the same sum in this network and another in the next: the sum alone
        tells nothing about the hardware outside the network."""
        uuid = "4c4c4544-0042-3510-8051-b4c04f4e3232"

        def box(salt):
            r = subprocess.run(["bash", "-c", "printf '%s\\n%s' \"$1\" \"$2\" | sha256sum | cut -c1-16", "-", salt, uuid],
                               capture_output=True, text=True)
            return r.stdout.strip()
        self.assertEqual(len(box("a")), 16)
        self.assertNotEqual(box("a"), box("b"))
        self.assertIn('box $(printf', clientagent.FACTS % {"version": 1})

    def test_the_install_script_carries_everything_but_the_key(self):
        f = FakeFleet({"pentest-1": {}})
        s = clientagent.install_script(f, "pentest-1", "https://control.papaya.home", "-----BEGIN CERTIFICATE-----\nX\n-----END CERTIFICATE-----")
        self.assertIn("URL=https://control.papaya.home\nNAME=pentest-1\n", s)
        self.assertIn(clientagent.salt(f), s)
        self.assertIn("-----END CERTIFICATE-----\nKIWI_EOF", s)
        self.assertNotIn(clientagent.key(f, "pentest-1"), s)           # the key comes on stdin
        self.assertIn("key=$(cat)", s)
        for text in (s, clientagent.SCRIPT % {"dir": "/etc/kiwi-client"}, clientagent.FACTS % {"version": 1}):
            r = subprocess.run(["bash", "-n"], input=text, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        with self.assertRaisesRegex(KiwiError, "no control node"):
            clientagent.install_script(f, "pentest-1", None, "ca")

    def test_the_fleet_names_its_clients(self):
        self.assertEqual(clientagent.names(FakeFleet({"pentest-1": None, "desk": {"group": "admins"}})),
                         {"pentest-1": {}, "desk": {"group": "admins"}})
        with self.assertRaisesRegex(KiwiError, "not a plain name"):
            clientagent.names(FakeFleet({"Pentest_1": {}}))
        with self.assertRaisesRegex(KiwiError, "a machine of the fleet"):
            clientagent.names(FakeFleet({"lime": {}}, hosts=["lime"]))
        self.assertEqual(clientagent.names(FakeFleet(None)), {})

    def test_rows_say_whether_a_client_is_on_and_on_which_box(self):
        folder = tempfile.mkdtemp()
        clientagent.keep(folder, "desk", b"box abc\nos Bluefin\nvpn wg0 10.9.0.40/32\n")
        old = os.path.join(folder, "clients", "desk.txt")
        rows = clientagent.rows(folder, ["desk", "laptop"], boxes={"abc": "lemonaide"}, now=os.path.getmtime(old) + 10)
        self.assertEqual([(r["name"], r["state"], r["box_of"]) for r in rows], [("desk", "on", "lemonaide"), ("laptop", "never", None)])
        self.assertEqual(rows[0]["facts"]["vpn"], [{"link": "wg0", "address": "10.9.0.40/32"}])
        later = clientagent.rows(folder, ["desk"], now=os.path.getmtime(old) + clientagent.FRESH + 1)
        self.assertEqual(later[0]["state"], "away")
        self.assertEqual(oct(os.stat(old).st_mode & 0o777), "0o600")


if __name__ == "__main__":
    unittest.main()
