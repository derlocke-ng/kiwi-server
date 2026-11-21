"""The runbook (runbook.py, docs/design/runbook.md): the table in the fleet file, checked, and what
it says per machine. Nothing acts by it yet."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
from kiwiserver import runbook  # noqa: E402
from kiwiserver.util import KiwiError  # noqa: E402


def fleet(defaults=None, **own):
    f = types.SimpleNamespace(defaults={"runbook": defaults or []}, hosts={})
    for name, role, site, modules in (("medlar", "node", "home", ["git", "games"]), ("sorbet", "master", "netcup", ["vpn-server"])):
        f.hosts[name] = types.SimpleNamespace(name=name, role=role, modules=modules, fleet=f,
                                              cfg={"network": {"site": site}}, overrides={"runbook": own[name]} if name in own else {})
    return f


class TestRunbook(unittest.TestCase):
    def test_the_first_line_that_fits_wins_its_own_first(self):
        f = fleet([{"case": "part-down", "for": "role:node", "do": "restart", "after": 2, "tries": 3},
                   {"case": "part-down", "for": "all", "do": "tell"},
                   {"case": "machine-gone", "for": "site:netcup", "do": "restart", "after": 15},
                   {"case": "disk-full", "for": "part:git", "do": "prune-images", "at": 90}],
                  sorbet=[{"case": "part-down", "for": "sorbet", "do": "restart"}])
        medlar, sorbet = runbook.for_machine(f.hosts["medlar"]), runbook.for_machine(f.hosts["sorbet"])
        self.assertEqual((medlar["part-down"]["do"], medlar["machine-gone"], medlar["disk-full"]["do"]), ("restart", None, "prune-images"))
        self.assertEqual((sorbet["part-down"]["after"] if "after" in sorbet["part-down"] else None, sorbet["machine-gone"]["do"]),
                         (None, "restart"))
        self.assertEqual(runbook.sentence("part-down", medlar["part-down"]),
                         "When a part is down: restarted after 2 minutes, at most 3 times, then you are told.")
        self.assertEqual(runbook.sentence("disk-full", medlar["disk-full"]), "When its disk fills up: old images are removed at 90 %.")
        self.assertEqual(runbook.sentence("machine-gone", None), "When it does not answer: you are told; nothing is done.")
        self.assertEqual(runbook.errors(f), [])

    def test_what_a_line_may_say(self):
        for bad in ({"case": "fire", "do": "tell"}, {"case": "part-down", "do": "reinstall"}, {"case": "part-down", "do": "tell", "when": 1},
                    {"case": "part-down", "do": "tell", "for": "group:x"}, {"case": "part-down", "do": "tell", "after": "soon"},
                    {"case": "part-down", "do": "tell", "at": 90}, {"case": "disk-full", "do": "tell", "at": 120}, "tell"):
            with self.assertRaises(KiwiError, msg=bad):
                runbook.lines({"runbook": [bad]})
        self.assertEqual(len(runbook.errors(fleet([{"case": "x"}], medlar=["y"]))), 2)
        with self.assertRaises(KiwiError):
            runbook.lines({"runbook": {"case": "part-down"}})


if __name__ == "__main__":
    unittest.main()
