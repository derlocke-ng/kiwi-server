"""A job on the control node, followed from a computer (controlsync.run_api): what happens when
the control page goes away while the job runs. An apply to its own machine restarts the page,
and its jobs live in memory (the owner, 20 Nov 2026: "the control node lost job … (502)")."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
from kiwiserver import controlsync as cs  # noqa: E402
from kiwiserver.util import KiwiError  # noqa: E402


class Page:
    """A stand-in for the control node's API: `answers` are given one after the other to the
    job-log questions; a KiwiError in the list is raised (no answer at all)."""
    def __init__(self, answers):
        self.answers, self.jobs, self.asked = list(answers), 0, []

    def call(self, method, path, body=None):
        self.asked.append((method, path.split("?")[0]))
        if method == "POST" and path == "/api/jobs":
            self.jobs += 1
            return 200, {"job": "job%d" % self.jobs}
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


class TestControlRun(unittest.TestCase):
    def setUp(self):
        self.said, self.keep = [], (cs.push_api, cs.pull_api, cs.api_for, cs.util.say, cs.time.sleep)
        cs.push_api = cs.pull_api = lambda *a, **k: None
        cs.util.say = self.said.append
        cs.time.sleep = lambda s: None

    def tearDown(self):
        cs.push_api, cs.pull_api, cs.api_for, cs.util.say, cs.time.sleep = self.keep

    def run_with(self, answers, action="apply"):
        page, out = Page(answers), []
        cs.api_for = lambda fleet_dir, key=None: (page, "key")
        ok = cs.run_api("/nowhere", "fleet.yaml", action, ["papaya"], write=out.append)
        return ok, "".join(out), page

    def test_a_job_that_ends_well(self):
        ok, text, page = self.run_with([(200, {"text": "one\n", "offset": 4, "state": "running"}),
                                        (200, {"text": "", "offset": 4, "state": "ok"})])
        self.assertTrue(ok)
        self.assertEqual((text, page.jobs), ("one\n", 1))

    def test_the_page_is_away_for_a_moment_and_the_job_is_still_there(self):
        ok, text, page = self.run_with([(200, {"text": "one\n", "offset": 4, "state": "running"}), (502, {}),
                                        KiwiError("does not answer"), (503, {}),
                                        (200, {"text": "two\n", "offset": 8, "state": "running"}),
                                        (200, {"text": "", "offset": 8, "state": "ok"})])
        self.assertTrue(ok)
        self.assertEqual((text, page.jobs), ("one\ntwo\n", 1))                      # nothing lost, nothing run twice
        self.assertEqual(sum("is away" in s for s in self.said), 1)                 # said once

    def test_an_apply_whose_page_restarted_runs_once_more(self):
        ok, text, page = self.run_with([(200, {"text": "sending the role script\n", "offset": 24, "state": "running"}),
                                        (502, {}), (401, {}), (404, {}),            # back, logged in again, the job is gone
                                        (200, {"text": "nothing to do\n", "offset": 14, "state": "running"}),
                                        (200, {"text": "", "offset": 14, "state": "ok"})])
        self.assertTrue(ok)
        self.assertEqual(page.jobs, 2)
        self.assertIn("nothing to do", text)
        self.assertTrue(any("Running it once more" in s for s in self.said))

    def test_it_runs_once_more_only_once(self):
        with self.assertRaises(KiwiError) as e:
            self.run_with([(502, {}), (404, {}), (502, {}), (404, {})])
        self.assertIn("Run it again", str(e.exception))

    def test_another_action_is_not_run_twice(self):
        with self.assertRaises(KiwiError) as e:
            self.run_with([(502, {}), (404, {})], action="remove")
        self.assertIn("restarted while job job1 ran", str(e.exception))

    def test_a_page_that_stays_away(self):
        clock = iter(range(0, 100000, 100))
        keep = cs.time.monotonic
        cs.time.monotonic = lambda: next(clock)
        try:
            with self.assertRaises(KiwiError) as e:
                self.run_with([(502, {})] * 10)
        finally:
            cs.time.monotonic = keep
        self.assertIn("did not come back within 5 minutes", str(e.exception))

    def test_a_job_the_page_never_had(self):
        with self.assertRaises(KiwiError) as e:                                     # 404 without a restart: as before
            self.run_with([(404, {})])
        self.assertIn("lost job", str(e.exception))


if __name__ == "__main__":
    unittest.main()
