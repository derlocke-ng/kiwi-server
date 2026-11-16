"""The sign-up page's rules, with a stand-in for Kanidm."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
from kiwiserver import signup as signupmod      # noqa: E402
from kiwiserver.util import KiwiError           # noqa: E402


class FakeKanidm:
    def __init__(self):
        self.people, self.calls = {}, []

    def __call__(self, method, path, body):
        self.calls.append((method, path))
        if method == "POST" and path == "/v1/person":
            name = body["attrs"]["name"][0]
            if name in self.people:
                raise KiwiError("that name is taken")
            self.people[name] = body
            return None
        if method == "GET" and "/_credential/_update_intent/" in path:
            return {"token": "tok-" + path.split("/")[3]}
        raise AssertionError(path)


class TestSignup(unittest.TestCase):
    def setUp(self):
        self.k = FakeKanidm()
        self.t = [1_800_000_000.0]
        self.s = signupmod.Signup(tempfile.mkdtemp(), id_host="id.example.org", opener=self.k, clock=lambda: self.t[0])

    def test_an_invite_makes_an_account_once(self):
        code = self.s.invite(uses=1, days=7, note="for anna")
        made = self.s.join(code, "anna", "Anna")
        self.assertEqual(made, {"link": "https://id.example.org/ui/reset?token=tok-anna"})
        with self.assertRaisesRegex(KiwiError, "not good"):
            self.s.join(code, "bert")                               # used up
        self.assertEqual([i["used"] for i in self.s.invites()], [1])
        self.assertNotIn(code, open(self.s.path).read())            # only its digest is kept
        late = self.s.invite(days=1)
        self.t[0] += 2 * 86400
        with self.assertRaisesRegex(KiwiError, "not good"):
            self.s.join(late, "carl")                               # expired
        with self.assertRaisesRegex(KiwiError, "kept for the network"):
            self.s.join(self.s.invite(), "admin")
        with self.assertRaisesRegex(KiwiError, "lowercase"):
            self.s.join(self.s.invite(), "Bad Name")

    def test_a_request_waits_for_an_answer_and_then_shows_its_link_for_a_day(self):
        code = self.s.ask("dora", "Dora from the climbing club, for the shared files", "203.0.113.5")
        self.assertEqual(self.s.status(code), ("waiting", {}))
        hint = self.s.requests()[0]["hint"]
        self.assertEqual(self.s.answer(hint, True), "accepted")
        self.assertEqual(self.s.status(code), ("accepted", {"link": "https://id.example.org/ui/reset?token=tok-dora"}))
        self.assertIn("dora", self.k.people)
        self.t[0] += 2 * 86400
        self.assertEqual(self.s.status(code), ("expired", {}))
        self.assertEqual(self.s.status("ask-0000-0000-0000"), ("unknown", {}))
        other = self.s.ask("emil", "Emil, a friend of Dora's", "203.0.113.6")
        self.s.answer(self.s.requests(waiting_only=True)[0]["hint"], False)
        self.assertEqual(self.s.status(other), ("denied", {}))
        self.assertNotIn("emil", self.k.people)

    def test_who_comes_over_tor_gets_a_link_on_tor(self):
        s = signupmod.Signup(tempfile.mkdtemp(), id_host="id.example.org", opener=self.k, clock=lambda: self.t[0],
                             id_onion="abcdefghijklmnopqrstuvwxyz234567abcdefghijklmnopqrstuvwx.onion")
        made = s.join(s.invite(uses=2), "tora", page="abcdefghijklmnopqrstuvwxyz234567abcdefghijklmnopqrstuvwx.onion")
        self.assertEqual(made["link"], "http://abcdefghijklmnopqrstuvwxyz234567abcdefghijklmnopqrstuvwx.onion/ui/reset?token=tok-tora")
        made = s.join(s.invite(), "webby", page="id.example.org")
        self.assertEqual(made["link"], "https://id.example.org/ui/reset?token=tok-webby")
        code = s.ask("toro", "asked over Tor, for the club", "203.0.113.4", page="abcdefghijklmnopqrstuvwxyz234567abcdefghijklmnopqrstuvwx.onion")
        s.answer(s.requests(waiting_only=True)[0]["hint"], True)
        self.assertTrue(s.status(code)[1]["link"].startswith("http://abcdefghijklmnopqrstuvwxyz234567"))

    def test_requests_are_limited(self):
        with self.assertRaisesRegex(KiwiError, "few words"):
            self.s.ask("fritz", "hi", "203.0.113.7")
        for n in range(signupmod.ASK_PER_HOUR):
            self.s.ask("fritz%d" % n, "a person who would like an account", "203.0.113.7")
        with self.assertRaisesRegex(KiwiError, "Too many requests from here"):
            self.s.ask("fritzx", "a person who would like an account", "203.0.113.7")
        self.t[0] += 3601
        self.s.ask("fritzy", "a person who would like an account", "203.0.113.7")
        with self.assertRaisesRegex(KiwiError, "asked for that name"):
            self.s.ask("fritzy", "the same name again, please", "203.0.113.8")


class FakeEjabberd:
    def __init__(self):
        self.users, self.calls = {}, []

    def __call__(self, method, path, body):
        self.calls.append(path.rsplit("/", 1)[1])
        if path.endswith("/check_account"):
            return 0 if (body["user"], body["host"]) in self.users else 1
        if path.endswith("/register"):
            self.users[(body["user"], body["host"])] = body["password"]
            return "registered"
        raise AssertionError(path)


class TestJabberSignup(unittest.TestCase):
    def setUp(self):
        self.e = FakeEjabberd()
        self.s = signupmod.Signup(tempfile.mkdtemp(), opener=self.e, ejabberd={
            "jabber.medlar.home": {"api": "http://kn-xmpp:5281/api", "host": "medlar.home"},
            "xmpp.example.org": {"api": "http://kn-xmpp-public:5281/api", "host": "xmpp.example.org"}})

    def test_an_invite_makes_a_jabber_address_with_the_persons_password(self):
        made = self.s.join(self.s.invite(uses=2), "anna", page="xmpp.example.org", password="a long password")
        self.assertEqual(made, {"address": "anna@xmpp.example.org", "password": ""})
        self.assertEqual(self.e.users[("anna", "xmpp.example.org")], "a long password")
        with self.assertRaisesRegex(KiwiError, "10 characters"):
            self.s.join(self.s.invite(), "bert", page="xmpp.example.org", password="short")
        with self.assertRaisesRegex(KiwiError, "taken"):
            self.s.join(self.s.invite(), "anna", page="xmpp.example.org", password="another long one")

    def test_an_accepted_request_shows_its_made_password_once(self):
        code = self.s.ask("carl", "Carl, a neighbour, for chatting", "203.0.113.9", page="jabber.medlar.home")
        self.s.answer(self.s.requests()[0]["hint"], True)
        state, made = self.s.status(code)
        self.assertEqual((state, made["address"]), ("accepted", "carl@medlar.home"))
        self.assertEqual(self.e.users[("carl", "medlar.home")], made["password"])
        self.assertEqual(self.s.status(code)[1]["password"], "")                # shown once
        self.assertNotIn(made["password"], open(self.s.path).read())            # and forgotten


if __name__ == "__main__":
    unittest.main()
