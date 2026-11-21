"""The table per part (policy.py, docs/design/authority.md step 3): for every part, in one place, what
happens to it when its machine stops, and its sentence. Nothing moves by itself."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
from kiwiserver import lease, policy  # noqa: E402
from kiwiserver.util import KiwiError  # noqa: E402


def host(name, modules, site="home", role="node", **cfg):
    return types.SimpleNamespace(name=name, role=role, modules=modules, cfg=dict({"network": {"site": site}}, **cfg),
                                 hostname=name + ".home", standby_of=None)


class TestPolicy(unittest.TestCase):
    def fleet(self, parts=None, recovery=None):
        hosts = [host("medlar", ["vault", "dns", "games"], keep_ready={"vault": "quince"}), host("quince", ["gateway"]),
                 host("lime", ["tang"]), host("sorbet", ["vpn-server"], site="netcup", role="master")]
        return types.SimpleNamespace(hosts={h.name: h for h in hosts},
                                     defaults={"parts": parts or {}, "recovery": recovery or {}})

    roles = {"node": types.SimpleNamespace(node_type="node"), "master": types.SimpleNamespace(node_type="master")}

    def rows(self, f):
        return {(r["machine"], r["part"]): r for r in policy.rows(f, self.roles)}

    def test_the_rows_and_their_sentences(self):
        r = self.rows(self.fleet())
        vault = r[("medlar", "vault")]
        self.assertEqual((vault["copies"], vault["standby"], vault["need"]), ("standby", "quince", "normal"))
        self.assertEqual(policy.sentence(vault), "vault: away while medlar is; a copy is kept ready on quince, to move it "
                                                 "there while medlar runs.")
        self.assertEqual((r[("medlar", "dns")]["where"], r[("medlar", "dns")]["need"]), ("here", "must"))
        self.assertEqual(policy.sentence(r[("medlar", "games")]),
                         "games: away while medlar is; back from its backups if medlar is installed anew. It can wait.")
        self.assertEqual(policy.sentence(r[("quince", "gateway")]),
                         "gateway: runs on quince only, away while quince is. The network needs it.")
        mine = [x for x in r.values() if x["machine"] == "medlar"]
        self.assertEqual(policy.summary("medlar", mine), "When medlar stops: dns (the network needs it) and 2 more are "
                                                         "away until it is back.")
        # nothing takes a part over by itself (the owner, 22 Nov 2026): no permissions are set up
        self.assertEqual(lease.links(self.fleet(), self.roles), {})

    def test_the_fleet_file_changes_a_part(self):
        # lost: an older release's switch by itself, left in a fleet file: it does nothing now
        f = self.fleet(parts={"vault": {"lost": "ask", "need": "must"}, "games": {"need": "normal"}})
        r = self.rows(f)
        self.assertEqual(policy.table(f)["vault"], {"need": "must"})
        self.assertIn("The network needs it", policy.sentence(r[("medlar", "vault")]))
        self.assertNotIn("It can wait", policy.sentence(r[("medlar", "games")]))

    def test_what_the_fleet_file_may_say(self):
        for bad in ({"vault": {"need": "very"}}, {"vault": {"where": "here"}}, {"vault": "ask"}, ["vault"]):
            with self.assertRaises(KiwiError):
                policy.table(types.SimpleNamespace(defaults={"parts": bad}))
        self.assertEqual(policy.table(types.SimpleNamespace(defaults={})), {})


if __name__ == "__main__":
    unittest.main()
