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
        self.assertEqual(runbook.sentence("disk-full", medlar["disk-full"]), "When its disk fills up: images no container uses are removed at 90 %.")
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


SYSTEMCTL = r'''#!/bin/bash
# systemctl cat|is-failed|is-active|restart UNIT: a unit is a file $NET/units/NAME holding its state
for a in "$@"; do case $a in -q) ;; *) set -- "$@" "$a" ;; esac; shift; done
what=$1; u=${2%.service}; f=$NET/units/$u
case $what in
    cat) [ -e "$f" ] ;;
    is-failed) [ "$(cat "$f" 2>/dev/null)" = failed ] ;;
    is-active) [ "$(cat "$f" 2>/dev/null)" = active ] || [ "$(cat "$f" 2>/dev/null)" = unhealthy ] ;;
    restart) echo "restart $u" >> "$NET/did"; echo active > "$f" ;;
esac
'''
PODMAN = '#!/bin/bash\n[ "$(cat "$NET/units/${@: -1}")" = unhealthy ] && echo unhealthy || echo healthy\n'
DATE = '#!/bin/bash\n[[ ${1:-} == +%s ]] && { cat "$NET/now"; exit 0; }\nexec /usr/bin/date "$@"\n'


class MachineBase(unittest.TestCase):
    """The machine's own runbook script in a folder, with a fake systemd and podman."""

    def setUp(self):
        import shutil
        import tempfile
        self.net = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.net, True)
        self.bin = os.path.join(self.net, "bin")
        os.makedirs(os.path.join(self.net, "units"))
        os.makedirs(self.bin)
        for name, text in (("kiwi-runbook", runbook.SCRIPT), ("systemctl", SYSTEMCTL), ("podman", PODMAN), ("date", DATE),
                           ("logger", "#!/bin/bash\nexit 0\n"), ("docker", "#!/bin/bash\nexit 1\n")):
            with open(os.path.join(self.bin, name), "w") as fh:
                fh.write(text)
            os.chmod(os.path.join(self.bin, name), 0o755)
        for unit, state in (("kn-git", "failed"), ("kn-vault", "unhealthy"), ("kn-paste", "inactive"), ("kn-games", "inactive")):
            self.put("units/" + unit, state)
        self.put("conf/conf", runbook.conf([("kn-git", "part-down", "restart", 2, 2), ("kn-vault", "part-down", "restart", 0, 3),
                                            ("kn-paste", "part-down", "restart", 0, 3), ("kn-games", "part-down", "restart", 0, 3)]))
        self.put("stopped/kn-paste", "")
        self.now = 1_000_000

    def put(self, rel, text):
        path = os.path.join(self.net, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)

    def tick(self, minutes=0):
        import subprocess
        self.now += minutes * 60
        self.put("now", str(self.now))
        env = dict(os.environ, PATH=self.bin + ":" + os.environ["PATH"], NET=self.net, KIWI_RUNBOOK_DIR=os.path.join(self.net, "conf"),
                   KIWI_RUNBOOK_RUN=os.path.join(self.net, "run"), KIWI_RUNBOOK_KEEP=os.path.join(self.net, "keep"),
                   KIWI_STOPPED_DIR=os.path.join(self.net, "stopped"))
        subprocess.run([os.path.join(self.bin, "kiwi-runbook")], env=env, check=True, timeout=30)

    def did(self):
        try:
            with open(os.path.join(self.net, "did")) as fh:
                return fh.read().split("\n")[:-1]
        except OSError:
            return []

    def log(self):
        with open(os.path.join(self.net, "keep", "log")) as fh:
            return fh.read()



@unittest.skipUnless(__import__("shutil").which("bash") and __import__("shutil").which("flock"), "bash and flock needed")
class TestOnTheMachine(MachineBase):
    """kn-git fails, kn-vault is unhealthy, kn-paste was stopped by a person, kn-games stopped
    cleanly (asleep)."""

    def test_down_restarted_after_its_minutes_a_person_stop_and_a_clean_stop_left(self):
        self.tick()
        self.assertEqual(self.did(), ["restart kn-vault"])              # unhealthy, after 0 minutes
        self.tick(1)
        self.assertEqual(self.did(), ["restart kn-vault"])              # kn-git: 2 minutes not yet
        self.tick(1)
        self.assertEqual(self.did(), ["restart kn-vault", "restart kn-git"])
        self.assertIn("kn-git was down for 120 s: restarted (try 1 of 2 today)", self.log())
        self.assertNotIn("kn-paste", self.log())                        # a person stopped it
        self.assertNotIn("kn-games", self.log())                        # stopped cleanly: not down
        self.tick(1)
        self.assertIn("kn-git runs again", self.log())

    def test_tries_then_left_to_a_person_and_a_new_day(self):
        for _ in range(4):
            self.put("units/kn-git", "failed")
            self.tick(2)
        self.assertEqual(self.did().count("restart kn-git"), 2)
        self.assertEqual(self.log().count("kn-git is down: restarted 2 times today, left to a person"), 1)
        self.tick(24 * 60)                                              # a day later it may try again
        self.assertEqual(self.did().count("restart kn-git"), 3)

    def test_a_person_stop_ends_when_it_runs_again(self):
        self.put("units/kn-paste", "active")                            # started again (an apply)
        self.tick()
        self.assertFalse(os.path.exists(os.path.join(self.net, "stopped", "kn-paste")))
        self.put("units/kn-paste", "failed")
        self.tick()
        self.assertIn("restart kn-paste", self.did())

    def test_the_lines_for_the_machine(self):
        f = fleet([{"case": "part-down", "for": "part:git", "do": "restart", "after": 2},
                   {"case": "part-down", "for": "all", "do": "tell"}])
        rows = runbook.machine_lines(f.hosts["medlar"], {"kn-git": "git", "kn-games": "games"})
        self.assertEqual(rows, [("kn-git", "part-down", "restart", 2, 3)])     # tell: nothing to do there
        self.assertEqual(runbook.machine_lines(f.hosts["sorbet"], {"km-wg": "vpn-server"}), [])


class TestReported(unittest.TestCase):
    def test_what_the_runbook_did_is_in_the_facts_and_a_note(self):
        from kiwiserver import facts
        f = facts.parse("now=2000\nrunbook=1900 kn-git was down for 120 s: restarted (try 1 of 3 today)\nrunbook=x bad\n",
                        local_now=2000)
        self.assertEqual(f["runbook"], [{"at": 1900, "what": "kn-git was down for 120 s: restarted (try 1 of 3 today)"}])
        notes = facts.assess(f, now=2000)
        flat = " ".join(str(x) for x in (notes if isinstance(notes, (list, tuple)) else [notes]))
        self.assertIn("the runbook, ", flat)
        self.assertIn("kn-git was down for 120 s: restarted", flat)
        self.assertNotIn("runbook", facts.parse("now=1\n", local_now=1))


class TestApply(unittest.TestCase):
    def test_installed_with_lines_taken_away_without(self):
        bundle = types.SimpleNamespace(service_modules={"kn-git": "git"})
        render = lambda h: ("", bundle)                                               # noqa: E731
        ran = []
        run = lambda tgt, script, data=None: ran.append(script) or b""                # noqa: E731
        bare = fleet()
        self.assertEqual(runbook.after_apply(bare, bare.hosts["medlar"], "t", render, False, run), [])
        self.assertEqual(ran, [])                                   # nothing before, nothing now: not asked at all
        self.assertEqual(runbook.after_apply(bare, bare.hosts["medlar"], "t", render, True, run), [])
        self.assertEqual(ran, [runbook.REMOVE])                     # had some: taken away
        f = fleet([{"case": "part-down", "for": "all", "do": "restart", "after": 1}])
        ran[:] = []
        self.assertEqual(runbook.after_apply(f, f.hosts["medlar"], "t", render, False, run), [("kn-git", "part-down", "restart", 1, 3)])
        self.assertIn("kn-git part-down restart 1 3\n", ran[0])
        self.assertIn("systemctl enable -q --now kiwi-runbook.timer", ran[0])


@unittest.skipUnless(__import__("shutil").which("bash") and __import__("shutil").which("flock"), "bash and flock needed")
class TestDiskFull(MachineBase):
    """disk-full on the machine: `df` says how full, `podman image prune` is written down."""

    def setUp(self):
        super().setUp()
        self.put("conf/conf", runbook.conf([("-", "disk-full", "prune-images", 90, 0)]))
        for name, text in (("df", '#!/bin/bash\necho " Use%%"; echo " $(cat "$NET/pct")%%"\n'),
                           ("podman", '#!/bin/bash\necho "podman $*" >> "$NET/did"; [ "$1" = image ] && echo 70 > "$NET/pct"; exit 0\n')):
            with open(os.path.join(self.bin, name), "w") as fh:
                fh.write(text.replace("%%", "%"))
            os.chmod(os.path.join(self.bin, name), 0o755)

    def test_pruned_at_its_mark_once_an_hour(self):
        self.put("pct", "80")
        self.tick()
        self.assertEqual(self.did(), [])
        self.put("pct", "93")
        self.tick()
        self.assertEqual(self.did(), ["podman image prune -af --filter until=24h"])
        self.assertIn("the disk was at 93 %: images no container uses removed, now at 70 %", self.log())
        self.put("pct", "95")
        self.tick(10)
        self.assertEqual(len(self.did()), 1)                    # once an hour at most
        self.tick(60)
        self.assertEqual(len(self.did()), 2)

    def test_the_line_for_the_machine(self):
        f = fleet([{"case": "disk-full", "for": "all", "do": "prune-images", "at": 85}])
        self.assertEqual(runbook.machine_lines(f.hosts["medlar"], {"kn-git": "git"}), [("-", "disk-full", "prune-images", 85, 0)])


class TestCrashLoop(unittest.TestCase):
    def test_a_container_restarted_again_and_again_is_told(self):
        from kiwiserver import facts
        f = facts.parse("now=1\nrestarts=kn-git 5\nrestarts=kn-vault 1\nrestarts=kn-x nope\n", local_now=1)
        self.assertEqual(f["restarts"], {"kn-git": 5, "kn-vault": 1})
        problems = facts.assess(f, now=1)
        flat = " ".join(str(x) for x in (problems if isinstance(problems, (list, tuple)) else [problems]))
        self.assertIn("kn-git (5 times): restarted again and again in the last ten minutes", flat)
        self.assertNotIn("kn-vault (1", flat)                                   # once is no loop
        self.assertIn("crash-loop", flat)
        self.assertNotIn("restarts", facts.parse("now=1\n", local_now=1))

    def test_the_probe_counts_restarts_over_ten_minutes(self):
        import shutil
        import subprocess
        import tempfile
        from kiwiserver import facts
        if not shutil.which("bash"):
            self.skipTest("bash needed")
        part = facts.SCRIPT.split("    rs=/run/kiwi-server/restarts")[1].split("    # what this machine's runbook did")[0]
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        for name, text in (("systemctl", '#!/bin/bash\ncat "$D/n"\n'), ("date", '#!/bin/bash\ncat "$D/now"\n')):
            with open(os.path.join(d, name), "w") as fh:
                fh.write(text)
            os.chmod(os.path.join(d, name), 0o755)
        script = 'kv() { echo "$1=$2"; }\nrt=podman\nSTACK_SERVICES="kn-git"\nrs=%s/rs%s' % (d, part)
        def run(now, n):
            for k, v in (("now", now), ("n", n)):
                with open(os.path.join(d, k), "w") as fh:
                    fh.write(str(v))
            return subprocess.run(["bash", "-c", script], env=dict(os.environ, PATH=d + ":" + os.environ["PATH"], D=d),
                                  capture_output=True, text=True).stdout
        self.assertEqual(run(1000, 2), "")                          # the first count: nothing to say yet
        self.assertEqual(run(1060, 4), "restarts=kn-git 2\n")
        self.assertEqual(run(1120, 7), "restarts=kn-git 5\n")
        self.assertEqual(run(1900, 7), "")                          # ten minutes without one: quiet again


class TestMovingAddress(unittest.TestCase):
    def test_who_holds_it_is_in_the_facts_and_a_note(self):
        from kiwiserver import facts
        f = facts.parse("now=1\nvip=192.168.2.70 holds\n", local_now=1)
        self.assertEqual(f["vip"], {"address": "192.168.2.70", "holds": True})
        flat = " ".join(str(x) for x in facts.assess(f, now=1))
        self.assertIn("holds the moving address 192.168.2.70", flat)
        f = facts.parse("now=1\nvip=192.168.2.70 waits\n", local_now=1)
        self.assertIn("waits for the moving address", " ".join(str(x) for x in facts.assess(f, now=1)))
        self.assertNotIn("vip", facts.parse("now=1\nvip=x maybe\n", local_now=1))

    def test_the_machines_table_names_the_holder(self):
        try:
            from kiwiserver import web
        except ImportError:
            self.skipTest("the page's libraries are not here")
        self.assertEqual(web.tags(["gateway", "vip"], {"vip": {"address": "192.168.2.70", "holds": True}}),
                         ["gateway", "holds 192.168.2.70"])
        self.assertEqual(web.tags(["gateway", "vip"], {"vip": {"address": "192.168.2.70", "holds": False}}), ["gateway"])


class TestControlNodeCases(unittest.TestCase):
    def test_a_machine_gone_follows_its_runbook_line_then_recovery(self):
        from kiwiserver import watchdog
        f = fleet([{"case": "machine-gone", "for": "site:netcup", "do": "restart", "after": 5}],
                  medlar=[{"case": "machine-gone", "for": "medlar", "do": "tell"}])
        f.defaults["recovery"] = {"master": {"after": 15, "do": "tell"}, "node": {"after": 60, "do": "restart"}}
        self.assertEqual(watchdog.policy(f, "master", f.hosts["sorbet"]), (5.0, "restart"))     # its line
        self.assertEqual(watchdog.policy(f, "node", f.hosts["medlar"]), (60.0, "tell"))        # its own line: tell
        self.assertEqual(watchdog.policy(f, "master"), (15.0, "tell"))                         # no machine: recovery
        bare = fleet()
        bare.defaults["recovery"] = {"node": {"after": 30, "do": "restart"}}
        self.assertEqual(watchdog.policy(bare, "node", bare.hosts["medlar"]), (30.0, "restart"))  # no line: recovery


class TestSshReuse(unittest.TestCase):
    def test_plain_calls_share_a_connection_probes_and_forwards_do_not(self):
        from kiwiserver import remote
        seen = []
        fake = lambda argv, **k: seen.append(argv) or types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")  # noqa: E731
        remote.run("core@lime", "true", runner=fake)
        remote.run("core@lime", "true", runner=fake, options=["-R", "7500:localhost:7500"])
        on = " ".join(seen[0])
        self.assertIn("ControlMaster=auto", on)
        self.assertIn("ControlPersist=120", on)
        self.assertNotIn("ControlMaster=no", on)                      # ssh keeps the first value: replaced
        self.assertIn("ControlMaster=no", " ".join(seen[1]))          # a forward: its own connection
        os.environ["KIWI_SSH_REUSE"] = "0"
        try:
            remote.run("core@lime", "true", runner=fake)
        finally:
            del os.environ["KIWI_SSH_REUSE"]
        self.assertIn("ControlMaster=no", " ".join(seen[2]))
        self.assertEqual(remote.ssh_opts().count("ControlMaster=no"), 1)  # probes and the others as before
