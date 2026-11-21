"""The Machines table's own logic (lib/kiwiserver/machtable.py): states, the filter, sites."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
from kiwiserver import machtable as mt  # noqa: E402


def m(name, state="ok", site="home", **kw):
    d = {"name": name, "state": state, "site": site, "role": "node", "kind": "Home server (node)", "modules": [], "phase": "applied"}
    d.update(kw)
    return d


class TestMachineTable(unittest.TestCase):
    def test_a_machine_has_one_state(self):
        self.assertEqual(mt.state(m("a")), "ok")
        self.assertEqual(mt.state(m("a", "down")), "down")
        self.assertEqual(mt.state(m("a", "down", locked=True)), "unlock")          # never "not reachable"
        self.assertEqual(mt.state(m("a", "down", stage="waits")), "unlock")
        self.assertEqual(mt.state(m("a", "down", stage="installing")), "installing")
        self.assertEqual(mt.state(m("a", "asleep")), "asleep")
        self.assertEqual(mt.state(m("a", "unknown", phase="build")), "new")
        self.assertEqual(mt.state(m("a", "ok", phase="install")), "ok")            # it answers: that counts
        self.assertEqual(mt.state(m("a", "something new")), "unknown")

    def test_the_filter_has_a_text_form(self):
        f = mt.parse("site:Main state:down state:unlock part:dns lychee")
        self.assertEqual(f, {"state": ["down", "unlock"], "site": ["main"], "part": ["dns"], "role": [], "text": ["lychee"]})
        self.assertEqual(mt.parse("")["text"], [])
        self.assertEqual(mt.parse("state:")["state"], [])                           # half typed: no filter yet

    def test_what_a_filter_lets_through(self):
        ms = [m("lychee", "down", "Main building", modules=["dns", "cloud"]), m("lime", "down", "Main building", locked=True),
              m("fig", "ok", "CEO", role="master", kind="Master")]
        t = lambda q: [x["name"] for g in mt.table(ms, q, {"dns": "Ad blocker (Pi-hole)", "cloud": "Files (Nextcloud)"})["groups"]
                       for x in g["machines"]]
        self.assertEqual(t(""), ["fig", "lychee", "lime"])                          # the master, then by site, the worst first
        self.assertEqual(t("state:down"), ["lychee"])                               # lime waits for its key
        self.assertEqual(t("state:unlock"), ["lime"])
        self.assertEqual(t("state:fine"), ["fig"])                                  # by its word too
        self.assertEqual(t("state:down state:fine"), ["fig", "lychee"])             # the same key twice: either
        self.assertEqual(t("site:main"), ["lychee", "lime"])
        self.assertEqual(t("site:main-building li"), ["lime"])                      # and a part of a name
        self.assertEqual(t("part:dns"), ["lychee"])
        self.assertEqual(t("part:pi-hole"), ["lychee"])                             # by the part's title
        self.assertEqual(t("role:master"), ["fig"])
        self.assertEqual(t("site:nowhere"), [])

    def test_counts_and_sites(self):
        ms = [m("a", "ok"), m("b", "warn"), m("c", "warn"), m("d", "down", "CEO"), m("e", "ok", "CEO")]
        t = mt.table(ms)
        self.assertEqual([(c["key"], c["n"], c["words"]) for c in t["counts"]],
                         [("down", 1, "not reachable"), ("warn", 2, "want a look"), ("ok", 2, "fine")])
        self.assertEqual([(g["site"], g["level"], g["fine"]) for g in t["groups"]], [("CEO", "bad", False), ("home", "warn", False)])
        self.assertEqual((t["all"], t["shown"], t["filtered"]), (5, 5, False))
        self.assertTrue(mt.table([m("a")])["groups"][0]["fine"])

    def test_masters_are_a_group_of_their_own(self):
        ms = [m("lychee"), m("sorbet", role="master", kind="Master"), m("sorbet-b", role="master", standby_of="sorbet"),
              m("far", "down", "Abroad", role="master")]
        t = mt.table(ms)
        self.assertEqual([(g["site"], g["masters"], [x["name"] for x in g["machines"]]) for g in t["groups"]],
                         [("Masters", True, ["far", "sorbet", "sorbet-b"]), ("home", False, ["lychee"])])
        self.assertEqual([x["name"] for g in mt.table(ms, "site:abroad")["groups"] for x in g["machines"]], ["far"])   # its site still filters

    def test_a_count_toggles_its_filter(self):
        self.assertEqual(mt.toggled("", "state", "down"), "state:down")
        self.assertEqual(mt.toggled("site:ceo state:warn", "state", "down"), "site:ceo state:down")
        self.assertEqual(mt.toggled("site:ceo state:down", "state", "down"), "site:ceo")

    def test_the_devices_table(self):
        rows = [{"name": "annas-phone", "address": "10.9.3.2", "group": "family", "split": False},
                {"name": "laptop", "address": "10.9.3.3", "group": "family", "split": True},
                {"name": "desk-07", "address": "10.9.8.9", "group": "staff", "split": True},
                {"name": "old", "address": "", "group": "", "split": False}]
        t = lambda q: [d["name"] for d in mt.device_table(rows, q)["devices"]]
        self.assertEqual(t(""), ["annas-phone", "laptop", "desk-07", "old"])        # by group, then name; no group last
        self.assertEqual(t("group:staff"), ["desk-07"])
        self.assertEqual(t("group:staff group:family"), ["annas-phone", "laptop", "desk-07"])
        self.assertEqual(t("lap"), ["laptop"])
        self.assertEqual(t("10.9.8"), ["desk-07"])                                  # the start of an address
        self.assertEqual(t("group:family phone"), ["annas-phone"])
        full = mt.device_table(rows, "group:family")
        self.assertEqual(full["groups"], [{"group": "family", "n": 2}, {"group": "staff", "n": 1}])
        self.assertEqual((full["all"], full["shown"], full["filtered"]), (4, 2, True))
        self.assertEqual(full["site_names"], [])                                    # one network, no sites known: one table

    def test_the_devices_by_the_site_of_their_master(self):
        rows = [{"name": "annas-phone", "group": "family", "site": "Main building", "master": "gate"},
                {"name": "desk-07", "group": "staff", "site": "HQ abroad", "master": "gate2"},
                {"name": "desk-08", "group": "staff", "site": "HQ abroad", "master": "gate2"},
                {"name": "old", "group": "", "site": "", "master": ""}]
        t = mt.device_table(rows)
        self.assertEqual(t["site_names"], ["HQ abroad", "Main building"])
        self.assertEqual([(g["site"], [d["name"] for d in g["devices"]]) for g in t["sites"]],
                         [("HQ abroad", ["desk-07", "desk-08"]), ("Main building", ["annas-phone"]), ("", ["old"])])
        self.assertEqual([d["name"] for d in mt.device_table(rows, "site:hq-abroad")["devices"]], ["desk-07", "desk-08"])
        self.assertEqual([d["name"] for d in mt.device_table(rows, "site:main-building group:staff")["devices"]], [])

    def test_many_machines_stay_fast(self):
        import time
        ms = [m("node%03d" % i, "ok", "site%d" % (i % 5), modules=["dns"]) for i in range(1000)]
        t0 = time.time()
        t = mt.table(ms, "part:dns site:site3")
        self.assertEqual(t["shown"], 200)
        self.assertLess(time.time() - t0, 0.5)


if __name__ == "__main__":
    unittest.main()
