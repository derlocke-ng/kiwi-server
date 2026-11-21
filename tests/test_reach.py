"""What one machine's render reads of the others (reach.py, docs/design/scale.md)."""
import copy
import json
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
from kiwiserver import reach  # noqa: E402

PARTS = {"cloud", "vault", "dns"}


def host(name, role="node"):
    h = types.SimpleNamespace(name=name, hostname=name + ".home", role=role, target="", standby_of=None, standby_endpoint="",
                              modules=["cloud", "dns"], role_settings={"vpn_ip": "10.8.0.2"},
                              cfg={"network": {"site": "home"}, role: {"vpn_ip": "10.8.0.2", "cloud": {"quota": 5}, "dns": {"ntp": True}}},
                              role_settings_raw={"vpn_ip": "10.8.0.2", "cloud": {"quota": 5}},
                              module_settings={"cloud": {"quota": 5}, "dns": {"ntp": True}},
                              module_files={"cloud/cert": b"PEM", "dns/list": b"x"})
    reach.watch(h, PARTS)
    return h


class TestReach(unittest.TestCase):
    def test_reads_of_another_machine_are_written_down_its_own_not(self):
        a, b = host("medlar"), host("papaya")
        with reach.reading("medlar", set()) as seen:
            a.module_settings["cloud"]                          # its own
            b.module_settings.get("dns")
            b.cfg["node"]["vpn_ip"]                             # the shared view: not a part
            b.cfg["node"].get("cloud")
            "vault" in b.role_settings_raw
            b.module_files.get("cloud/cert")
        self.assertEqual(seen, {("papaya", "dns"), ("papaya", "cloud"), ("papaya", "vault")})
        b.module_settings.get("cloud")                          # outside a render: nothing
        self.assertEqual(len(seen), 3)

    def test_a_look_at_all_of_it_is_all_of_it(self):
        b = host("papaya")
        for look in (lambda: list(b.module_settings), lambda: copy.deepcopy(b.module_settings), lambda: json.dumps(b.module_settings),
                     lambda: dict(b.module_files), lambda: copy.deepcopy(b.cfg), lambda: b.module_settings.items()):
            with reach.reading("medlar", set()) as seen:
                look()
            self.assertIn(("papaya", "*"), seen, look)

    def test_what_resolves_a_machine_counts_as_its_own(self):
        b = host("papaya")
        with reach.reading("medlar", set()) as seen:
            with reach.own("papaya"):
                b.module_settings.get("cloud")
        self.assertEqual(seen, set())

    def test_the_shared_view_leaves_the_parts_out_and_part_data_has_them(self):
        b = host("papaya")
        view = reach.shared_view(b, PARTS)
        self.assertEqual(view["cfg"]["node"], {"vpn_ip": "10.8.0.2"})
        self.assertNotIn("cloud", json.dumps(view["role_settings"]))
        d = reach.part_data(b, "cloud")
        self.assertEqual((d["settings"], d["raw"], sorted(d["files"])), ({"quota": 5}, {"quota": 5}, ["cloud/cert"]))
        self.assertEqual(sorted(reach.part_data(b, "*")), ["cloud", "dns"])
        b.module_files["cloud/cert"] = b"PEM2"                              # the same size, another file
        self.assertNotEqual(reach.part_data(b, "cloud")["files"], d["files"])


if __name__ == "__main__":
    unittest.main()
