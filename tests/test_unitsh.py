"""The shell lines kiwi-server's own install steps share (unitsh.py): a unit is written only when
it differs, systemd reads its units again only then, a unit is enabled only when it is not yet."""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

from kiwiserver import asleep, machineagent, opener, runbook, unitsh, unlock  # noqa: E402


class TestUnitSh(unittest.TestCase):
    def test_a_unit_is_written_reloaded_and_enabled_only_when_needed(self):
        d = tempfile.mkdtemp()
        os.makedirs(d + "/bin")
        with open(d + "/bin/systemctl", "w") as f:
            f.write('#!/bin/sh\ncase "$1 $2" in\n'
                    '  "is-enabled -q") [ -e %(d)s/enabled-$3 ] ;;\n  "is-active -q") [ -e %(d)s/active-$3 ] ;;\n'
                    '  "enable -q") touch %(d)s/enabled-$3; echo "enable $3" >> %(d)s/said ;;\n'
                    '  "start "*) touch %(d)s/active-$2; echo "start $2" >> %(d)s/said ;;\n'
                    '  "daemon-reload ") echo reload >> %(d)s/said ;;\nesac\n' % {"d": d})
        os.chmod(d + "/bin/systemctl", 0o755)
        once = "kiwi_unit a.timer <<'E'\n%s\nE\nprintf 'drop\\n' | kiwi_unit a.service.d/x.conf\nkiwi_reload; kiwi_enable --now a.timer\necho --- >> " + d + "/said\n"
        script = unitsh.PRELUDE.replace("/etc/systemd/system", d + "/units") + once % "one" + once % "one" + once % "two"
        r = subprocess.run(["sh", "-eu", "-c", script], env=dict(os.environ, PATH=d + "/bin:" + os.environ["PATH"]),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(d + "/said") as f:
            said = f.read().split("---\n")
        self.assertEqual(said[0].split("\n")[:-1], ["reload", "enable a.timer", "start a.timer"])    # new: all of it
        self.assertEqual(said[1], "")                                                             # as it was: nothing
        self.assertEqual(said[2], "reload\n")                                                     # changed: read again
        with open(d + "/units/a.service.d/x.conf") as f:
            self.assertEqual(f.read(), "drop\n")

    def test_the_install_steps_carry_the_lines_and_no_bare_reload(self):
        self.assertNotIn("%", unitsh.PRELUDE)             # they go into %-formatted scripts
        for name, script in (("agent", machineagent.INSTALL), ("asleep", asleep.INSTALL), ("opener", opener.INSTALL),
                             ("runbook", runbook.INSTALL), ("rings", unlock.INSTALL_RINGS_ANSWER), ("leash", unlock.INSTALL_LEASH)):
            self.assertIn("kiwi_unit() {", script, name)
            self.assertNotIn("\nsystemctl daemon-reload", script, name)
            self.assertNotRegex(script, r"\nsystemctl enable", name)
        # nothing of it on the machine: the remove steps end before `systemctl disable` (it reloads every time)
        self.assertTrue(asleep.REMOVE.startswith("[ -e "))
        self.assertTrue(opener.REMOVE.startswith("[ -e "))


if __name__ == "__main__":
    unittest.main()
