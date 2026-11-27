"""kiwi-server apply --careful (lib/kiwiserver/careful.py): the order, and asking a machine's sites."""
import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "lib"))

from kiwiserver import careful  # noqa: E402
from kiwiserver.util import KiwiError  # noqa: E402


def host(name, modules=(), role="node", standby_of=None, vip=None):
    raw = {"modules": list(modules)}
    if vip:
        raw["vip"] = {"address": vip}
    return types.SimpleNamespace(name=name, hostname=name + ".home", domain="home", role=role, modules=list(modules),
                                 standby_of=standby_of, role_settings_raw=raw)


class TestCareful(unittest.TestCase):
    roles = {"master": types.SimpleNamespace(node_type="master"), "node": types.SimpleNamespace(node_type="node")}

    def test_the_least_important_first_and_the_holder_of_a_moving_address_after_the_one_that_waits(self):
        hs = [host("sorbet", ["vpn-server", "dns"], role="master"), host("compote", ["vpn-server"], role="master", standby_of="sorbet"),
              host("lime", ["dns", "gateway", "vip"], vip="192.168.2.69/16"), host("rockpi", ["dns", "gateway", "vip"], vip="192.168.2.69/16"),
              host("lychee", ["tang", "backup-server"]), host("medlar", ["reverse-proxy", "git"]),
              host("papaya", ["control", "cloud", "dns"]), host("apple", ["reverse-proxy"])]
        pair = [(hs[2], "192.168.2.64"), (hs[3], "192.168.2.67")]
        renderer = types.SimpleNamespace(vip_pair=lambda h: pair if h.name in ("lime", "rockpi") else [])
        asked = []

        def holds(h, address):
            asked.append((h.name, address))
            return h.name == "lime"
        got = [h.name for h in careful.order(None, self.roles, hs, renderer, holds=holds, me="papaya")]
        self.assertEqual(got, ["apple", "medlar",               # nothing leans on them
                               "lychee", "rockpi", "lime",      # rings and names; rockpi waits, lime holds the address
                               "compote", "sorbet",             # the standby, then the master
                               "papaya"])                       # the machine this runs on, last
        self.assertEqual(sorted(asked), [("lime", "192.168.2.69"), ("rockpi", "192.168.2.69")])
        # a machine that does not say whether it holds the address counts as waiting: by name then
        got = [h.name for h in careful.order(None, self.roles, hs[2:4], renderer, holds=lambda h, a: None)]
        self.assertEqual(got, ["lime", "rockpi"])

    def test_a_machines_sites_are_asked_until_they_answer(self):
        h = host("medlar")
        outs = iter([b"bad git.medlar.home:502 paste.medlar.home:000\n", b"bad paste.medlar.home:502\n", b"bad\n"])
        now, slept, sent = [0.0], [], []

        def run(tgt, script):
            sent.append(script)
            return next(outs)
        bad = careful.answers(h, "core@medlar", ["git.medlar.home", "paste.medlar.home"], run=run, wait=120,
                              sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)), clock=lambda: now[0])
        self.assertEqual((bad, slept), ([], [5, 5]))
        self.assertIn("NAMES='git.medlar.home paste.medlar.home'", sent[0])
        # one that keeps failing is named when the time is up; a machine that stops answering too
        bad = careful.answers(h, "core@medlar", ["git.medlar.home"], run=lambda t, s: b"bad git.medlar.home:502\n", wait=12,
                              sleep=lambda s: now.__setitem__(0, now[0] + s), clock=lambda: now[0])
        self.assertEqual(bad, ["git.medlar.home:502"])

        def gone(tgt, script):
            raise KiwiError("medlar: ssh: connect to host medlar.home port 22: Connection refused")
        bad = careful.answers(h, "core@medlar", ["git.medlar.home"], run=gone, wait=0, sleep=lambda s: None, clock=lambda: now[0])
        self.assertIn("Connection refused", bad[0])
        self.assertEqual(careful.answers(h, "core@medlar", [], run=gone), [])       # no site: nothing to ask

    def test_only_the_machines_own_names_under_the_fleets_domain_are_asked(self):
        r = types.SimpleNamespace(scan=lambda: None, aliases={"cloud.home"},
                                  _names={"medlar.home": ["medlar.home", "git.medlar.home", "*.medlar.home", "paste.dlhost.eu",
                                                          "cloud.home", "paste.medlar.home"]})
        self.assertEqual(careful.names_of(r, host("medlar")), ["git.medlar.home", "paste.medlar.home"])

    def test_the_script_counts_a_login_or_a_redirect_as_an_answer(self):
        import subprocess
        import tempfile
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "curl"), "w") as f:
            f.write('#!/bin/sh\nfor a in "$@"; do u=$a; done\ncase $u in *ok*) printf 200 ;; *login*) printf 401 ;; *moved*) printf 302 ;; '
                    '*down*) printf 502 ;; *) exit 7 ;; esac\n')
        os.chmod(os.path.join(d, "curl"), 0o755)
        r = subprocess.run(["sh", "-c", "NAMES='ok.m.home login.m.home moved.m.home down.m.home gone.m.home'\n" + careful.VERIFY_SCRIPT],
                           capture_output=True, text=True, env=dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"]))
        self.assertEqual(r.stdout.strip(), "bad down.m.home:502 gone.m.home:000")


if __name__ == "__main__":
    unittest.main()
