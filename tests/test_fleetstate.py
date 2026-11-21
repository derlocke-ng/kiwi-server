"""What the network remembers has one keeper (fleetstate.py, docs/design/authority.md step 1): a
computer sends its changes as steps and takes the control node's state back. Before, each copy
kept its own, and the app showed machines the control node had applied as "changes not applied
yet" (21 Nov 2026)."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
from kiwiserver import controlsync as cs  # noqa: E402
from kiwiserver import fleetstate as fs  # noqa: E402
from kiwiserver.util import KiwiError  # noqa: E402


class Node:
    """A stand-in for the control node's /api/v1/state: it keeps a state and takes steps."""
    def __init__(self, state=None, down=False, old=False):
        self.dir = tempfile.mkdtemp()
        self.down, self.old, self.sent = down, old, []
        if state is not None:
            fs._write(self.dir, state)

    def call(self, method, path, body=None):
        if self.down:
            raise KiwiError("the control node does not answer")
        if self.old:
            return 404, {"error": "not here"}
        self.sent.append(body["steps"])
        return 200, {"state": fs.receive(self.dir, body["steps"])}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("KIWI_CONTROL_HOST", None)

    def joined(self):
        os.makedirs(os.path.join(self.dir, "control"), exist_ok=True)
        with open(os.path.join(self.dir, fs.REMOTE), "w") as fh:
            json.dump({"url": "https://control.home"}, fh)


class TestSteps(Base):
    def test_diff_then_apply_gives_the_after(self):
        before = {"applied": {"lime": 1, "sorbet": 2}, "off": {"quince": 5}, "standby_links": ["a"]}
        after = {"applied": {"lime": 3, "medlar": 4}, "standby_links": ["a", "b"], "hold": {"lime": ["x"]}}
        steps = fs.diff(before, after)
        self.assertEqual(fs.apply(json.loads(json.dumps(before)), steps), after)

    def test_changes_to_other_machines_meet_without_loss(self):
        # two computers change different lines: the keeper ends with both
        keeper = {"applied": {"lime": 1, "sorbet": 1}}
        a = fs.diff(keeper, {"applied": {"lime": 9, "sorbet": 1}})
        b = fs.diff(keeper, {"applied": {"lime": 1, "sorbet": 7}})
        self.assertEqual(fs.apply(fs.apply(dict(keeper, applied=dict(keeper["applied"])), a), b),
                         {"applied": {"lime": 9, "sorbet": 7}})

    def test_a_map_gone_here_goes_line_by_line(self):
        # power on clears the last mark and drops the map: the keeper keeps the others' marks
        steps = fs.diff({"off": {"sorbet": 1}}, {})
        self.assertEqual(steps, [["del", ["off", "sorbet"]]])
        self.assertEqual(fs.apply({"off": {"sorbet": 1, "lime": 2}}, steps), {"off": {"lime": 2}})

    def test_a_list_of_names_changes_item_by_item(self):
        keeper = {"standby_links": ["lime", "quince"]}
        here = fs.diff({"standby_links": ["lime", "quince"]}, {"standby_links": ["lime", "quince", "medlar"]})
        there = fs.diff({"standby_links": ["lime", "quince"]}, {"standby_links": ["quince"]})
        self.assertEqual(here, [["add", ["standby_links"], "medlar"]])
        self.assertEqual(fs.apply(fs.apply(keeper, there), here), {"standby_links": ["medlar", "quince"]})

    def test_a_step_that_does_not_fit_is_left_out(self):
        state = {"applied": 5}
        fs.apply(state, [["set", ["applied", "lime"], 1], ["del", ["nothing", "here"]], ["boom"], "x"])
        self.assertEqual(state, {"applied": 5})


class TestChange(Base):
    def test_without_a_control_node_nothing_waits(self):
        fs.change(self.dir, lambda s: s.setdefault("applied", {}).__setitem__("lime", 1))
        self.assertEqual(fs.read(self.dir), {"applied": {"lime": 1}})
        self.assertEqual(fs.outbox(self.dir), [])

    def test_with_a_control_node_the_steps_wait_for_the_sync(self):
        self.joined()
        fs.change(self.dir, lambda s: s.setdefault("applied", {}).__setitem__("lime", 1))
        fs.change(self.dir, lambda s: s.setdefault("off", {}).__setitem__("sorbet", 2))
        self.assertEqual(fs.outbox(self.dir), [["set", ["applied", "lime"], 1], ["set", ["off", "sorbet"], 2]])

    def test_on_the_control_node_itself_it_keeps_its_own(self):
        self.joined()
        os.environ["KIWI_CONTROL_HOST"] = "papaya"
        fs.change(self.dir, lambda s: s.setdefault("applied", {}).__setitem__("lime", 1))
        self.assertEqual(fs.outbox(self.dir), [])

    def test_a_change_that_changes_nothing_sends_nothing(self):
        self.joined()
        fs.change(self.dir, lambda s: s.get("hold", {}).pop("lime", None))
        self.assertEqual(fs.outbox(self.dir), [])
        self.assertFalse(os.path.exists(fs.path(self.dir)))


class TestTrade(Base):
    def test_the_control_nodes_state_becomes_this_copys(self):
        # the case of 21 Nov: the control node applied lime with what it has now; this copy
        # remembered an older apply of its own and called lime changed
        self.joined()
        fs._write(self.dir, {"applied": {"lime": 100}, "rendered": {"lime": "v2:old"}})
        node = Node({"applied": {"lime": 200}, "rendered": {"lime": "v2:new"}})
        self.assertTrue(cs.trade_state(self.dir, node))
        self.assertEqual(fs.read(self.dir), {"applied": {"lime": 200}, "rendered": {"lime": "v2:new"}})

    def test_newer_applies_from_here_are_handed_over_once(self):
        self.joined()
        fs._write(self.dir, {"applied": {"lime": 300, "sorbet": 50}, "rendered": {"lime": "v2:here", "sorbet": "v2:s"},
                             "copy": {"hosts": {"medlar": 90}}})
        node = Node({"applied": {"lime": 200, "sorbet": 60}, "rendered": {"lime": "v2:there", "sorbet": "v2:t"}})
        cs.trade_state(self.dir, node)
        got = fs.read(node.dir)
        self.assertEqual(got["applied"], {"lime": 300, "sorbet": 60})
        self.assertEqual(got["rendered"], {"lime": "v2:here", "sorbet": "v2:t"})
        self.assertEqual(got["copy"], {"hosts": {"medlar": 90}})
        self.assertEqual(fs.read(self.dir), got)
        node.sent.clear()
        cs.trade_state(self.dir, node)          # handed over once: the next exchange sends only new changes
        self.assertEqual(node.sent, [[]])

    def test_changes_made_here_go_and_stay_until_taken(self):
        self.joined()
        fs.mark_handed(self.dir)
        fs.change(self.dir, lambda s: s.setdefault("applied", {}).__setitem__("lime", 5))
        self.assertFalse(cs.trade_state(self.dir, Node(down=True)))
        self.assertEqual(len(fs.outbox(self.dir)), 1)          # not lost while the node is away
        node = Node({"applied": {"sorbet": 1}})
        self.assertTrue(cs.trade_state(self.dir, node))
        self.assertEqual(fs.read(self.dir), {"applied": {"sorbet": 1, "lime": 5}})
        self.assertEqual(fs.outbox(self.dir), [])

    def test_an_older_control_node_leaves_this_copy_as_it_is(self):
        self.joined()
        fs._write(self.dir, {"applied": {"lime": 1}})
        self.assertFalse(cs.trade_state(self.dir, Node(old=True)))
        self.assertEqual(fs.read(self.dir), {"applied": {"lime": 1}})
        self.assertFalse(fs.handed(self.dir))

    def test_a_change_made_while_the_answer_travels_is_kept(self):
        # take() replays what the outbox holds beyond what the node took
        self.joined()
        fs.mark_handed(self.dir)
        fs.change(self.dir, lambda s: s.setdefault("applied", {}).__setitem__("lime", 5))
        sent = len(fs.outbox(self.dir))
        fs.change(self.dir, lambda s: s.setdefault("off", {}).__setitem__("quince", 6))
        fs.take(self.dir, {"applied": {"lime": 5}}, sent)
        self.assertEqual(fs.read(self.dir), {"applied": {"lime": 5}, "off": {"quince": 6}})
        self.assertEqual(fs.outbox(self.dir), [["set", ["off", "quince"], 6]])


class TestOneSync(Base):
    def test_temp_files_are_private_to_each_sync(self):
        # 21 Nov 2026: two syncs at once shared control/.pull.age
        import threading
        from kiwiserver import util
        names = set()

        def name():
            names.add(util.tmp_name(os.path.join(self.dir, "control", ".pull.age")))
        ts = [threading.Thread(target=name) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(names), 4)

    def test_one_sync_at_a_time_and_again_inside(self):
        with cs.one_sync(self.dir):
            with cs.one_sync(self.dir):     # a pull that becomes a push
                pass


if __name__ == "__main__":
    unittest.main()
