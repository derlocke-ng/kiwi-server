"""Orders for the machines' agents (docs/design/scale.md, "Takes orders"): the agent's script run for
real against stand-ins of curl, systemctl and systemd-run; the signature both sides make; what it
refuses (a signature that is not the control node's, an order done before, too old, a service
outside the stack, anything but the four kinds), and what it tells with the report after."""
import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

from kiwiserver import machineagent as agent  # noqa: E402

KEY = "a-machine-key-of-the-test"


class TestAgentOrders(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        for d in ("agent", "state", "bin", "etc", "run"):
            os.makedirs(os.path.join(self.t, d))
        def w(p, text, mode=0o644):
            with open(os.path.join(self.t, p), "w") as f:
                f.write(text)
            os.chmod(os.path.join(self.t, p), mode)
        w("agent/conf", "URL=https://control.test\nNAME=quince\n")
        w("agent/key", KEY, 0o600)
        w("agent/ca.pem", "")
        w("agent/facts.sh", "echo facts of quince\n")
        w("etc/stack.env", 'STACK_SERVICES="kn-nginx kn-vault"\n')
        stubs = {
            # curl: keeps what was sent, answers what the test put into `answer`, says 200
            "curl": 'o=; b=; while [ $# -gt 0 ]; do case $1 in -o) o=$2; shift ;; --data-binary) b=${2#@}; shift ;; esac; shift; done\n'
                    'n=$(ls %(t)s/run | grep -c sent); cp "$b" %(t)s/run/sent.$n; cp %(t)s/answer "$o" 2>/dev/null || : > "$o"; printf 200\n',
            "systemctl": 'echo "systemctl $*" >> %(t)s/said\n',
            "systemd-run": 'echo "systemd-run $*" >> %(t)s/said\n',
            "logger": 'echo "logger $*" >> %(t)s/logged\n',
            "flock": 'exit 0\n',
        }
        for name, body in stubs.items():
            w("bin/" + name, "#!/bin/bash\n" + body % {"t": self.t}, 0o755)
        self.script = (agent.SCRIPT.replace("/etc/kiwi-server/agent", self.t + "/agent")
                       .replace("/var/lib/kiwi-server/agent", self.t + "/state")
                       .replace("/etc/kiwi-server/stack.env", self.t + "/etc/stack.env")
                       .replace("/run/kiwi-agent", self.t + "/run/kiwi-agent"))

    def order(self, oid, what, target, at=None, key=KEY):
        at = int(at or time.time())
        return "order %s %s %s %d %s\n" % (oid, what, target, at, agent.order_signature(key, "quince", oid, what, target, at))

    def run_agent(self):
        r = subprocess.run(["bash", "-c", self.script], env=dict(os.environ, PATH=self.t + "/bin:" + os.environ["PATH"]),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)

    def said(self):
        p = os.path.join(self.t, "said")
        if not os.path.exists(p):
            return []
        with open(p) as f:
            out = f.read().splitlines()
        os.remove(p)
        return out

    def sent(self, n):
        with open(os.path.join(self.t, "run", "sent.%d" % n), "rb") as f:
            return f.read()

    def test_the_agent_runs_only_what_the_control_node_signed_and_says_what_came_of_it(self):
        with open(os.path.join(self.t, "answer"), "w") as f:
            f.write(self.order("o1", "restart", "kn-vault")
                    + self.order("o2", "restart", "kn-nginx", key="not-the-machines-key")      # not the control node's
                    + self.order("o3", "restart", "sshd")                                    # not a service of the stack
                    + self.order("o4", "update", "-")
                    + self.order("o5", "stop", "kn-nginx", at=time.time() - 2 * agent.ORDER_AGE)   # too old
                    + self.order("o6", "rm", "kn-vault")                                     # not an order it takes
                    + "order o7 restart kn-vault 1 forged\n")                                # no signature at all
        self.run_agent()
        said = self.said()
        self.assertEqual([x for x in said if x.startswith("systemctl")], ["systemctl restart kn-vault.service"])
        self.assertEqual(len([x for x in said if x.startswith("systemd-run")]), 1)
        self.assertIn("--unit=kiwi-order-o4", [x for x in said if x.startswith("systemd-run")][0])
        self.assertIn(b"facts of quince", self.sent(0))
        # the next report says what came of them; the same answer again runs nothing twice
        self.run_agent()
        self.assertEqual([x for x in self.said() if x.startswith("systemctl")], [])
        done = {oid: (rc, text) for oid, rc, text in agent.done_lines(self.sent(1))}
        self.assertEqual(sorted(done), ["o1", "o3", "o4", "o5", "o6"])
        self.assertEqual(done["o1"][0], 0)
        self.assertEqual(done["o3"], (2, "no such service here: sshd"))
        self.assertEqual(done["o5"], (2, "too old"))
        self.assertEqual(done["o6"][0], 2)
        self.assertNotIn("o2", done)                      # never heard of: refused without a word to the sender
        with open(os.path.join(self.t, "logged")) as f:
            self.assertIn("o2 is not signed by the control node", f.read())
        # told once: the report after has none of them again
        os.remove(os.path.join(self.t, "answer"))
        self.run_agent()
        self.assertEqual(agent.done_lines(self.sent(2)), [])

    def test_an_order_and_a_report_cannot_pass_for_each_other(self):
        self.assertNotEqual(agent.order_signature(KEY, "quince", "1", "2", "3", "4"),
                            agent.signature(KEY, "quince", "1", "2", b"3"))
        self.assertNotIn("%(", agent.SCRIPT)              # the script goes into %-formatted installs as a value only


if __name__ == "__main__":
    unittest.main()
