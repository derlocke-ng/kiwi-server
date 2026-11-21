"""The table per part (policy.py, docs/design/authority.md step 3): for every part, in one place, what
happens to it when its machine stops, and its sentence."""
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
        self.assertEqual((vault["copies"], vault["lost"], vault["alone"], vault["standby"], vault["witness"]),
                         ("standby", "switch", "stop", "quince", "lime"))
        self.assertEqual(policy.sentence(vault), "vault: quince takes it over about four minutes after medlar stops (witness: lime).")
        self.assertEqual((r[("medlar", "dns")]["where"], r[("medlar", "dns")]["need"]), ("here", "must"))
        self.assertEqual(policy.sentence(r[("medlar", "games")]), "games: away while medlar is (no standby). It can wait.")
        self.assertEqual(policy.sentence(r[("quince", "gateway")]),
                         "gateway: runs on quince only, away while quince is. The network needs it.")
        mine = [x for x in r.values() if x["machine"] == "medlar"]
        self.assertEqual(policy.summary("medlar", mine), "When medlar stops: vault goes to quince after about four minutes; "
                                                         "dns (the network needs it) and 1 more are away until it is back.")

    def test_the_fleet_file_changes_a_part(self):
        f = self.fleet(parts={"vault": {"lost": "ask", "need": "must"}, "games": {"need": "normal"}})
        r = self.rows(f)
        self.assertEqual((r[("medlar", "vault")]["lost"], r[("medlar", "vault")]["alone"]), ("ask", "keep"))
        self.assertIn("a person switches it", policy.sentence(r[("medlar", "vault")]))
        self.assertIn("The network needs it", policy.sentence(r[("medlar", "vault")]))
        self.assertNotIn("It can wait", policy.sentence(r[("medlar", "games")]))
        # a part that a person switches needs no permission: nobody takes it by itself
        self.assertEqual(lease.links(f, self.roles), {})
        self.assertEqual(lease.links(self.fleet(), self.roles), {("medlar", "vault"): ("quince", "lime")})

    def test_the_switch_by_itself_off_for_every_part(self):
        f = self.fleet(recovery={"switch": {"do": "tell"}})
        self.assertEqual(self.rows(f)[("medlar", "vault")]["lost"], "ask")
        self.assertEqual(lease.links(f, self.roles), {})
        on = self.fleet(parts={"vault": {"lost": "switch"}}, recovery={"switch": {"do": "tell"}})
        self.assertEqual(lease.links(on, self.roles), {("medlar", "vault"): ("quince", "lime")})     # this one part, by itself

    def test_what_the_fleet_file_may_say(self):
        for bad in ({"vault": {"lost": "maybe"}}, {"vault": {"need": "very"}}, {"vault": {"where": "here"}}, {"vault": "ask"}, ["vault"]):
            with self.assertRaises(KiwiError):
                policy.table(types.SimpleNamespace(defaults={"parts": bad}))
        self.assertEqual(policy.table(types.SimpleNamespace(defaults={})), {})


if __name__ == "__main__":
    unittest.main()
