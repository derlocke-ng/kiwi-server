"""Permissions that run out (lease.py, docs/design/authority.md step 2), with the real scripts: three
machines are three folders, `ssh` runs the other machine's forced command in its folder (or fails
when that machine is "away"), `date` says the time the test sets, `kiwi-stack` writes down what it
was told. Nothing runs as root and nothing leaves the test's folder."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
from kiwiserver import lease  # noqa: E402

SSH = r'''#!/bin/bash
# ssh -i KEY -o ... root@ADDRESS COMMAND...: ADDRESS is a machine's folder name under $NET
for a in "$@"; do case $a in root@*) to=${a#root@}; shift; break ;; esac; shift; done
cmd="$*"
[[ -e $NET/away-$to || -e $NET/cut-$ME-$to ]] && { echo "ssh: connect to host $to port 22: Connection timed out" >&2; exit 255; }
case $cmd in
    grant\ *) SSH_ORIGINAL_COMMAND=$cmd KIWI_LEASE_DIR=$NET/$to/lease KIWI_LEASE_RUN=$NET/$to/run ME=$to exec "$BIN/kiwi-lease" grant ;;
    ask\ *)   SSH_ORIGINAL_COMMAND=$cmd KIWI_LEASE_DIR=$NET/$to/vote ME=$to exec "$BIN/kiwi-vote" ask ;;
esac
echo "no such forced command" >&2; exit 1
'''
DATE = '#!/bin/bash\n[[ ${1:-} == +%s ]] && { cat "$NET/now"; exit 0; }\nexec /usr/bin/date "$@"\n'
STACK = '#!/bin/bash\necho "$ME $*" >> "$NET/stack.log"\n[[ -e $NET/stack-fails-$ME ]] && exit 1\nexit 0\n'
LOGGER = '#!/bin/bash\nshift 2\necho "$ME: $*" >> "$NET/log"\n'


@unittest.skipUnless(shutil.which("bash") and shutil.which("flock"), "bash and flock needed")
class TestLease(unittest.TestCase):
    """lime runs vault; quince keeps it ready (the standby); medlar is the witness."""

    def setUp(self):
        self.net = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.net, True)
        self.bin = os.path.join(self.net, "bin")
        lease.write_scripts(self.bin)
        for name, text in (("ssh", SSH), ("date", DATE), ("kiwi-stack", STACK), ("logger", LOGGER)):
            with open(os.path.join(self.bin, name), "w") as fh:
                fh.write(text)
            os.chmod(os.path.join(self.bin, name), 0o755)
        self.now = 1_000_000
        self.clock(0)
        # the holder
        self.put("lime/lease/parts", lease.holder_conf({"vault": ["vault", "vault-db"]}))
        self.put("lime/lease/voters", lease.voters_conf({"vault": ["quince", "medlar"]}))
        self.put("lime/lease/length", "180")
        # the voters
        for me in ("quince", "medlar"):
            self.put("%s/vote/me" % me, me)
            self.put("%s/vote/length" % me, "180")
            self.put("%s/vote/margin" % me, "60")
        self.put("quince/vote/vote.conf", lease.vote_conf([{"part": "vault", "machine": "lime", "address": "lime", "role": "standby",
                                                            "switch": True, "witness": "medlar", "containers": ["vault", "vault-db"]}]))
        self.put("medlar/vote/vote.conf", lease.vote_conf([{"part": "vault", "machine": "lime", "address": "lime", "role": "witness"}]))
        self.copy_fresh()

    def put(self, rel, text):
        p = os.path.join(self.net, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text if text.endswith("\n") else text + "\n")

    def read(self, rel, default=None):
        try:
            with open(os.path.join(self.net, rel)) as fh:
                return fh.read()
        except OSError:
            return default

    def clock(self, seconds):
        self.now += seconds
        self.put("now", str(self.now))

    def copy_fresh(self):
        self.put("quince/standby/state/vault.ok", str(self.now))

    def env(self, me):
        return dict(os.environ, PATH=self.bin + ":" + os.environ["PATH"], NET=self.net, BIN=self.bin, ME=me,
                    KIWI_LEASE_DIR=os.path.join(self.net, me, "vote" if me != "lime" else "lease"),
                    KIWI_LEASE_RUN=os.path.join(self.net, me, "run"),
                    KIWI_STANDBY_DIR=os.path.join(self.net, me, "standby"))

    def tick(self):
        subprocess.run([os.path.join(self.bin, "kiwi-lease"), "tick"], env=self.env("lime"), check=True)

    def vote(self, *who):
        for me in who or ("quince", "medlar"):
            subprocess.run([os.path.join(self.bin, "kiwi-vote")], env=self.env(me), check=True)

    def may_run(self):
        return subprocess.run([os.path.join(self.bin, "kiwi-lease"), "check", "vault"], env=self.env("lime")).returncode == 0

    def stack(self):
        return (self.read("stack.log") or "").splitlines()

    def minutes(self, n, away=()):
        """n minutes of the machines' timers: the votes every minute, the tick three times a minute."""
        for _ in range(n):
            self.vote(*[m for m in ("quince", "medlar") if m not in away])
            for _t in range(3):
                if "lime" not in away:
                    self.tick()
                self.clock(20)

    def test_the_voters_give_it_and_the_part_runs(self):
        self.minutes(2)
        self.assertTrue(self.may_run())
        self.assertEqual(self.stack(), ["lime lease start vault vault-db"])        # started once, not every tick
        left = lease.parse_show(subprocess.run([os.path.join(self.bin, "kiwi-lease"), "show"], env=self.env("lime"),
                                               capture_output=True, text=True).stdout)
        self.assertEqual(left["vault"][1], "running")
        self.assertGreater(left["vault"][0], 60)

    def test_a_machine_cut_off_stops_the_part_and_the_standby_takes_it(self):
        self.minutes(2)
        self.put("away-lime", "")       # its network is cut: it still runs, and reaches nobody
        for _ in range(3):
            self.clock(60)
            self.tick_alone()
        self.assertFalse(self.may_run())
        self.assertIn("lime lease stop vault vault-db", self.stack())             # stopped by itself, within 3 min
        stopped_at = self.now
        self.minutes(2, away=("lime",))
        self.assertIn("quince ready start vault vault-db", self.stack())          # the standby took it
        self.assertGreater(self.now, stopped_at)                                  # after the machine had stopped it
        self.assertIsNotNone(self.read("quince/vote/state/vault.took"))
        self.assertIsNotNone(self.read("medlar/vote/state/vault@lime.refused"))
        # it comes back: nobody gives it the permission again, so the part stays stopped there
        os.remove(os.path.join(self.net, "away-lime"))
        self.minutes(3)
        self.assertFalse(self.may_run())
        self.assertEqual([x for x in self.stack() if x.startswith("lime lease start")], ["lime lease start vault vault-db"])

    def tick_alone(self):
        for _t in range(3):
            self.tick()
            self.clock(20)

    def test_only_the_standby_lost_its_way_there_nothing_moves(self):
        self.minutes(2)
        self.put("cut-quince-lime", "")         # the standby does not reach the machine; the witness does
        self.minutes(8)
        self.assertTrue(self.may_run())
        self.assertNotIn("quince ready start vault vault-db", self.stack())
        self.assertIn("its witness says here", self.read("log"))

    def test_without_the_witness_nothing_is_taken(self):
        self.minutes(2)
        self.put("away-lime", "")
        self.put("away-medlar", "")
        self.minutes(10, away=("lime", "medlar"))
        self.assertNotIn("quince ready start vault vault-db", self.stack())

    def test_a_witness_away_alone_changes_nothing(self):
        self.minutes(2)
        self.put("away-medlar", "")
        self.minutes(10, away=("medlar",))
        self.assertTrue(self.may_run())
        self.assertEqual([x for x in self.stack() if "stop" in x or "ready" in x], [])

    def test_an_old_copy_is_not_taken(self):
        self.minutes(2)
        self.put("away-lime", "")
        self.clock(-0)
        self.put("quince/standby/state/vault.ok", str(self.now - 3600))
        self.minutes(10, away=("lime",))
        self.assertNotIn("quince ready start vault vault-db", self.stack())
        self.assertIn("minutes old: not taken", self.read("log"))

    def test_without_the_switch_by_itself_it_waits_for_a_person(self):
        self.put("quince/vote/vote.conf", lease.vote_conf([{"part": "vault", "machine": "lime", "address": "lime", "role": "standby",
                                                            "switch": False, "witness": "medlar", "containers": ["vault"]}]))
        self.minutes(2)
        self.put("away-lime", "")
        self.minutes(10, away=("lime",))
        self.assertNotIn("quince ready start vault", self.stack())

    def test_a_part_installed_a_moment_ago_runs_until_the_first_vote(self):
        self.put("lime/lease/vault.fresh", str(self.now))
        self.tick()
        self.assertTrue(self.may_run())
        self.clock(200)
        self.tick()
        self.assertFalse(self.may_run())            # nobody voted within LENGTH: stopped
        self.assertIn("lime lease stop vault vault-db", self.stack())

    def test_a_stranger_gets_no_say(self):
        r = subprocess.run([os.path.join(self.bin, "kiwi-lease"), "grant"], capture_output=True, text=True,
                           env=dict(self.env("lime"), SSH_ORIGINAL_COMMAND="grant vault papaya"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("gives no permission", r.stderr)
        for bad in ("grant vault quince; rm -rf /", "grant ../vault quince", "rm -rf /", "grant vault quince extra"):
            r = subprocess.run([os.path.join(self.bin, "kiwi-lease"), "grant"], capture_output=True, text=True,
                               env=dict(self.env("lime"), SSH_ORIGINAL_COMMAND=bad))
            self.assertNotEqual(r.returncode, 0, bad)
        self.assertFalse(self.may_run())
        r = subprocess.run([os.path.join(self.bin, "kiwi-vote"), "ask"], capture_output=True, text=True,
                           env=dict(self.env("medlar"), SSH_ORIGINAL_COMMAND="ask gitea lime"))
        self.assertNotEqual(r.returncode, 0)        # it is the witness of vault only

    def test_witness_and_conf(self):
        import types

        def h(name, site="home"):
            return types.SimpleNamespace(name=name, cfg={"network": {"site": site}})
        fleet = types.SimpleNamespace(hosts={n.name: n for n in (h("lime"), h("quince"), h("medlar"), h("papaya"),
                                                                 h("sorbet", "netcup"), h("crumble", "netcup"))})
        master = (lambda x: x.name == "sorbet")
        self.assertEqual(lease.witness(fleet, "lime", "quince", master), "medlar")
        self.assertEqual(lease.witness(fleet, "crumble", "lime", master), "sorbet")      # alone at its site: the master
        fleet.hosts.pop("sorbet")
        self.assertIsNone(lease.witness(fleet, "crumble", "lime", master))
        self.assertIn('command="/usr/local/sbin/kiwi-lease grant",no-port-forwarding', lease.authorized_line("ssh-ed25519 AAAA x", "/usr/local/sbin/kiwi-lease grant"))


if __name__ == "__main__":
    unittest.main()
