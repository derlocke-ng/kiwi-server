"""Where the lean AI put a model it loaded (ai.py `placed`): on the graphics card, partly, or on the
processor, read from llama.cpp's log on the machine. A model that does not fit beside what
ai.card_free_mb leaves goes to the processor without an error (28 Nov 2026, lemonaide)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

from kiwiserver import ai, remote  # noqa: E402
from kiwiserver.util import KiwiError  # noqa: E402


class Host:
    name = "lemonaide"


class TestPlaced(unittest.TestCase):
    def setUp(self):
        self._reach = remote.reach
        remote.reach = lambda host, ssh=None: "core@lemonaide"
        self.addCleanup(setattr, remote, "reach", self._reach)

    def test_the_log_says_where_the_model_went(self):
        asked = []

        def run(tgt, script):
            asked.append((tgt, script))
            return b"offloaded 0/37 layers to GPU\n"
        self.assertEqual(ai.placed(Host, 95, run), (0, 37))
        self.assertIn("--since 95s", asked[0][1])
        self.assertIn("k[mn]-ai", asked[0][1])
        said = ai.placement_said(Host, "unsloth/Qwen3-4B-GGUF", (0, 37))
        self.assertIn("runs on the processor", said)
        self.assertIn("ai.card_free_mb", said)
        self.assertIn("on the graphics card (all 37 layers)", ai.placement_said(Host, "m", (37, 37)))
        self.assertIn("partly on the card (20 of 37 layers)", ai.placement_said(Host, "m", (20, 37)))

    def test_nothing_is_said_when_the_log_says_nothing(self):
        self.assertIsNone(ai.placed(Host, 60, lambda tgt, script: b""))         # no card: llama.cpp says nothing
        self.assertIsNone(ai.placement_said(Host, "m", None))

        def gone(tgt, script):
            raise KiwiError("lemonaide does not answer")
        self.assertIsNone(ai.placed(Host, 60, gone))                           # a load that worked is no failure


if __name__ == "__main__":
    unittest.main()
