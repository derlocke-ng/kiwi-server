"""People's computers as clients (docs/design/clients.md): the client agent's script run for real
against a stand-in curl, its facts, its signature both sides make, and what the install script
carries (never the key)."""
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

from kiwiserver import clientagent, machineagent  # noqa: E402
from kiwiserver.util import KiwiError  # noqa: E402

KEY = "a-client-key-of-the-test"


class FakeFleet:
    def __init__(self, clients, hosts=()):
        self.raw = {"clients": clients}
        self.hosts = {h: object() for h in hosts}

    def derive_secret(self, *parts, length=32):
        return ("".join(parts).replace("-", "") + "x" * length)[:length]


class TestClientProfileAndOrders(unittest.TestCase):
    """The agent takes its profile (the CA into the system's trust, the VPN as a NetworkManager
    connection) and its orders, all signed with its key, and refuses what is not."""

    def setUp(self):
        import base64
        self.t = tempfile.mkdtemp()
        for d in ("agent", "bin", "run", "state", "anchors"):
            os.makedirs(os.path.join(self.t, d))

        def w(p, text, mode=0o644):
            with open(os.path.join(self.t, p), "w") as f:
                f.write(text)
            os.chmod(os.path.join(self.t, p), mode)
        self.w = w
        w("agent/conf", "URL=https://control.test\nNAME=desk\n")
        w("agent/key", KEY, 0o600)
        w("agent/salt", "s\n")
        w("agent/ca.pem", "")
        w("agent/facts.sh", "echo os Test\n")
        self.conf = "[Interface]\nPrivateKey = x\nAddress = 10.9.0.40/32\n[Peer]\nPublicKey = y\nAllowedIPs = 10.9.0.0/16\nEndpoint = vpn.test:51820\n"
        self.ca = "-----BEGIN CERTIFICATE-----\nTEST\n-----END CERTIFICATE-----"
        self.profile = ("profile 1\nnetwork home\nconnection kiwi-home\nca %s\nvpn %s\nvpn_mode split\nsearch ~home\n"
                        % (base64.b64encode(self.ca.encode()).decode(), base64.b64encode(self.conf.encode()).decode())).encode()
        w("profile", self.profile.decode())
        w("profile.sign", clientagent.profile_signature(KEY, "desk", self.profile))
        stubs = {
            # curl: the profile (GET .../profile, its signature in the headers) or the answer to a report
            "curl": 'o=; h=; b=; u=; while [ $# -gt 0 ]; do case $1 in -o) o=$2; shift ;; -D) h=$2; shift ;; --data-binary) b=${2#@}; shift ;;'
                    ' https://*) u=$1 ;; esac; shift; done\n'
                    'case $u in */api/client/profile) cp %(t)s/profile "$o"; printf "HTTP/2 200\\r\\nX-Kiwi-Sign: %%s\\r\\n" "$(cat %(t)s/profile.sign)" > "$h"; printf 200 ;;\n'
                    '  *) n=$(ls %(t)s/run | grep -c sent); cp "$b" %(t)s/run/sent.$n; cp %(t)s/answer "$o" 2>/dev/null || : > "$o"; printf 200 ;; esac\n',
            "nmcli": 'echo "nmcli $*" >> %(t)s/nm\ncase "$*" in "-t -f NAME connection show") cat %(t)s/conns 2>/dev/null ;;'
                     ' "connection import type wireguard file "*) cp "${@: -1}" %(t)s/imported; echo kiwi-home >> %(t)s/conns ;; esac\n',
            "update-ca-trust": 'echo updated >> %(t)s/trust\n',
            "systemd-run": 'echo "systemd-run $*" >> %(t)s/said\n',
            "rpm-ostree": 'exit 0\n',
            "logger": 'shift 2; echo "$*" >> %(t)s/logged\n',
            "flock": 'exit 0\n',
        }
        for name, body in stubs.items():
            w("bin/" + name, "#!/bin/bash\n" + body % {"t": self.t}, 0o755)
        self.w("bin/lsblk", "exit 0\n", 0o755)          # no disk of this computer is ever looked at
        # every path of the system the script touches lies in the test's folder
        self.script = ((clientagent.SCRIPT % {"dir": self.t + "/agent"})
                       .replace("/var/lib/kiwi-client", self.t + "/state")
                       .replace("/etc/pki/ca-trust/source/anchors", self.t + "/anchors")
                       .replace("/usr/local/share/ca-certificates", self.t + "/no-certificates")
                       .replace("/etc/default/grub.d", self.t + "/grub.d")
                       .replace("/etc/systemd/system", self.t + "/units")
                       .replace("/run/kiwi-client", self.t + "/run/kiwi-client"))
        for path in ("/etc/", "/usr/local/share", "/var/lib/"):
            self.assertNotIn(path, self.script.replace(self.t, ""), "a system path left in the script under test")

    def answer(self, *lines):
        self.w("answer", "".join(line + "\n" for line in lines))

    def run_agent(self):
        path = os.path.join(self.t, "bin", "kiwi-client-agent")
        with open(path, "w") as f:
            f.write(self.script)
        os.chmod(path, 0o755)
        r = subprocess.run([path], env=dict(os.environ, PATH=self.t + "/bin:" + os.environ["PATH"]),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)

    def read(self, p):
        try:
            with open(os.path.join(self.t, p)) as f:
                return f.read()
        except FileNotFoundError:
            return ""

    def test_a_new_profile_is_applied_once(self):
        import hashlib
        self.answer("profile " + hashlib.sha256(self.profile).hexdigest())
        self.run_agent()
        self.assertEqual(self.read("anchors/kiwi-home.pem").strip(), self.ca)        # the CA, trusted by the system
        self.assertIn("updated", self.read("trust"))
        self.assertEqual(self.read("imported"), self.conf)                           # the VPN, imported as kiwi-home
        nm = self.read("nm")
        self.assertIn("connection modify kiwi-home ipv4.never-default yes ipv4.dns-search ~home", nm)
        self.assertIn("connection up kiwi-home", nm)
        self.assertFalse([f for f in os.listdir(os.path.join(self.t, "run")) if f.startswith("kiwi-client.")])  # the config left no copy
        self.assertIn("its profile is applied", self.read("logged"))
        # the same profile again: nothing is done anew
        os.remove(os.path.join(self.t, "nm"))
        os.remove(os.path.join(self.t, "trust"))
        self.run_agent()
        self.assertEqual((self.read("nm"), self.read("trust")), ("", ""))

    def test_leave_takes_away_what_the_agent_put_there(self):
        import hashlib
        self.answer("profile " + hashlib.sha256(self.profile).hexdigest())
        self.run_agent()                                                  # CA and VPN in place
        self.w("conns", "kiwi-home\nkiwi-other\n")
        path = os.path.join(self.t, "bin", "kiwi-client-agent")
        script = self.script.replace('[ "$(id -u)" = 0 ] || { echo "as root: sudo $0 leave" >&2; exit 2; }', ":")
        with open(path, "w") as f:
            f.write(script)
        self.w("bin/systemctl", "exit 0\n", 0o755)
        r = subprocess.run([path, "leave"], env=dict(os.environ, PATH=self.t + "/bin:" + os.environ["PATH"]),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("connection delete kiwi-home", self.read("nm"))
        self.assertNotIn("kiwi-other", self.read("nm"))                   # only the one it made
        self.assertFalse(os.path.exists(os.path.join(self.t, "anchors", "kiwi-home.pem")))
        self.assertFalse(os.path.exists(os.path.join(self.t, "agent")))
        self.assertFalse(os.path.exists(path))
        self.assertIn("has left the network", r.stdout)

    def test_a_profile_not_signed_by_the_control_node_is_left(self):
        import hashlib
        self.w("profile.sign", clientagent.profile_signature("another key", "desk", self.profile))
        self.answer("profile " + hashlib.sha256(self.profile).hexdigest())
        self.run_agent()
        self.assertEqual((self.read("anchors/kiwi-home.pem"), self.read("nm")), ("", ""))
        self.assertIn("not signed by the control node", self.read("logged"))

    def clevis_stub(self):
        """clevis on a disk /dev/vda3: `luks list` from a file of slots, bind adds slot 2, pass and
        unbind are written down."""
        self.w("slots", "1: sss {}\n")
        self.w("bin/clevis", 'echo "clevis $*" >> %(t)s/clevis\n'
               'case "$1 $2" in "luks list") cat %(t)s/slots ;; "luks pass") echo the-old-key ;;'
               ' "luks bind") cat > %(t)s/bind-key; echo "2: sss {}" >> %(t)s/slots ;;'
               ' "luks unbind") grep -v "^$6:" %(t)s/slots > %(t)s/slots.new; mv %(t)s/slots.new %(t)s/slots ;; esac\n' % {"t": self.t}, 0o755)
        self.w("bin/lsblk", "echo '/dev/vda3 crypto_LUKS'\n", 0o755)

    def with_unlock(self, policy):
        import base64
        import hashlib
        body = self.profile.decode().replace("ca ", "unlock %s\nca " % base64.b64encode(policy.encode()).decode(), 1).encode()
        self.w("profile", body.decode())
        self.w("profile.sign", clientagent.profile_signature(KEY, "desk", body))
        self.answer("profile " + hashlib.sha256(body).hexdigest())

    def test_a_disk_policy_waits_for_a_person_to_bind_it(self):
        self.clevis_stub()
        self.with_unlock('{"t":1,"pins":{"tang":[{"url":"http://192.168.2.30:7500"}]}}')
        self.run_agent()
        self.assertIn("192.168.2.30", self.read("state/unlock.json"))
        self.assertIn("sudo kiwi-client-agent bind", self.read("logged"))
        self.assertNotIn("luks bind", self.read("clevis"))                 # nothing bound without the person

    def test_a_bound_disk_follows_its_policy_without_a_passphrase(self):
        self.clevis_stub()
        self.w("state/unlock.slot", "1\n")
        self.w("state/unlock.sum", "an-older-policy\n")
        self.with_unlock('{"t":1,"pins":{"tang":[{"url":"http://192.168.2.31:7500"}]}}')
        self.run_agent()
        said = self.read("clevis")
        self.assertIn("luks pass -d /dev/vda3 -s 1", said)
        self.assertIn("luks bind -y -k - -d /dev/vda3 sss", said)
        self.assertEqual(self.read("bind-key"), "the-old-key")             # the key from the binding it had
        self.assertIn("luks unbind -d /dev/vda3 -s 1 -f", said)
        self.assertEqual(self.read("state/unlock.slot").strip(), "2")
        # the policy taken away: the disk is unbound, its passphrase stays
        self.with_unlock("")
        self.w("profile", self.profile.decode())
        self.w("profile.sign", clientagent.profile_signature(KEY, "desk", self.profile))
        import hashlib
        self.answer("profile " + hashlib.sha256(self.profile).hexdigest())
        self.run_agent()
        self.assertIn("luks unbind -d /dev/vda3 -s 2 -f", self.read("clevis"))
        self.assertEqual(self.read("state/unlock.slot"), "")

    def test_orders_update_and_profile_and_nothing_else(self):
        at = int(time.time())

        def order(oid, what, key=KEY, when=at):
            return "order %s %s - %d %s" % (oid, what, when, clientagent.order_signature(key, "desk", oid, what, "-", when))
        self.answer(order("a1", "update"), order("a2", "reboot"), order("a3", "update", key="other"),
                    order("a4", "update", when=at - 3600))
        self.run_agent()
        said = self.read("said")
        self.assertIn("--unit=kiwi-order-a1 rpm-ostree upgrade", said)
        self.assertEqual(said.count("systemd-run"), 1)                                # a2, a3, a4: not run
        told = self.read("run/sent.1")                                                # the report after, at once
        self.assertIn("kiwi-done a1 0 started", told)
        self.assertIn("kiwi-done a2 2 not an order this agent takes: reboot", told)
        self.assertIn("kiwi-done a4 2 too old", told)
        self.assertNotIn("a3", told)
        self.assertIn("order a3 is not signed by the control node", self.read("logged"))
        self.run_agent()                                                              # an order runs once
        self.assertEqual(self.read("said").count("systemd-run"), 1)

    def test_a_machines_order_signature_is_no_clients(self):
        at = int(time.time())
        self.assertNotEqual(machineagent.order_signature(KEY, "desk", "a", "update", "-", at),
                            clientagent.order_signature(KEY, "desk", "a", "update", "-", at))

    def test_the_disk_policy_from_the_rings_the_client_names(self):
        import json
        from kiwiserver import unlock as unlockmod
        lime = types.SimpleNamespace(name="lime", cfg={"network": {"dhcp": False, "address": "192.168.2.64/16"}})
        sorbet = types.SimpleNamespace(name="sorbet", cfg={"network": {"dhcp": True}})
        servers = {"home": (lime, 7500), "infra": (lime, 7501), "desk": (lime, 7502), "far": (sorbet, 7500)}
        facts = lambda server, ring: ("thp-" + ring, json.dumps({"ring": ring}))      # noqa: E731
        self.assertIsNone(clientagent.unlock_tree("desk", {}, servers, facts))
        tree = clientagent.unlock_tree("desk", {"unlock": {"rings": ["home", "infra"]}}, servers, facts)
        self.assertEqual(tree, {"t": 2, "pins": {"tang": [{"url": "http://192.168.2.64:7500", "adv": {"ring": "home"}},
                                                          {"url": "http://192.168.2.64:7501", "adv": {"ring": "infra"}}]}})
        tree = clientagent.unlock_tree("desk", {"unlock": {"rings": ["home"], "switch": "desk"}}, servers, facts)
        self.assertEqual((tree["t"], tree["pins"]["tang"][0]["url"]), (2, "http://192.168.2.64:7502"))   # its rings AND its own
        with self.assertRaisesRegex(KiwiError, "no fixed address at home"):
            clientagent.unlock_tree("desk", {"unlock": {"rings": ["far"]}}, servers, facts)
        self.assertEqual(clientagent.unlock_tree("desk", {"unlock": {"rings": ["far"], "via": {"far": "http://203.0.113.7:7600"}}},
                                                 servers, facts)["pins"]["tang"][0]["url"], "http://203.0.113.7:7600")
        with self.assertRaisesRegex(KiwiError, "serves the ring nope"):
            clientagent.unlock_tree("desk", {"unlock": {"rings": ["nope"]}}, servers, facts)
        self.assertTrue(unlockmod.OperatorRing)

    def test_the_profile_says_split_or_full_from_the_config(self):
        import base64
        tmp = tempfile.mkdtemp()

        class F(FakeFleet):
            defaults = {"domain": "home"}

            def resolve_path(self, rel):
                return os.path.join(tmp, rel)
        f = F({"desk": {}})
        os.makedirs(os.path.join(tmp, "secrets", "ca"))
        os.makedirs(os.path.join(tmp, "secrets", "devices"))
        with open(os.path.join(tmp, "secrets", "ca", "kiwiCA.pem"), "w") as fh:
            fh.write(self.ca)
        body = clientagent.profile(f, "desk").decode()
        self.assertIn("connection kiwi-home\n", body)
        self.assertNotIn("vpn ", body)                                                # no place on the VPN: the CA only
        with open(os.path.join(tmp, "secrets", "devices", "desk.conf"), "w") as fh:
            fh.write(self.conf)
        body = clientagent.profile(f, "desk", linked=["kiwi"]).decode()
        self.assertIn("vpn_mode split\nsearch ~home,~kiwi\n", body)
        self.assertEqual(base64.b64decode(body.split("\nvpn ")[1].split("\n")[0]).decode(), self.conf)
        with open(os.path.join(tmp, "secrets", "devices", "desk.conf"), "w") as fh:
            fh.write(self.conf.replace("10.9.0.0/16", "0.0.0.0/0, ::/0"))
        self.assertIn("vpn_mode full", clientagent.profile(f, "desk").decode())


class TestClientAgent(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        for d in ("agent", "bin", "run"):
            os.makedirs(os.path.join(self.t, d))

        def w(p, text, mode=0o644):
            with open(os.path.join(self.t, p), "w") as f:
                f.write(text)
            os.chmod(os.path.join(self.t, p), mode)
        w("agent/conf", "URL=https://control.test\nNAME=pentest-1\n")
        w("agent/key", KEY, 0o600)
        w("agent/salt", "the-salt\n")
        w("agent/ca.pem", "")
        w("agent/facts.sh", clientagent.FACTS % {"version": clientagent.VERSION})
        # curl: keeps the headers and the body it was given, says 200
        w("bin/curl", "#!/bin/bash\nb=; : > %(t)s/run/head\nwhile [ $# -gt 0 ]; do case $1 in -H) echo \"$2\" >> %(t)s/run/head; shift ;;"
                      " --data-binary) b=${2#@}; shift ;; esac; shift; done\ncp \"$b\" %(t)s/run/sent; printf 200\n" % {"t": self.t}, 0o755)
        w("bin/logger", "#!/bin/bash\necho \"$*\" >> %s/run/logged\n" % self.t, 0o755)
        w("bin/flock", "#!/bin/bash\nexit 0\n", 0o755)
        self.script = (clientagent.SCRIPT % {"dir": self.t + "/agent"}).replace("/run/kiwi-client-agent", self.t + "/run/kiwi-client-agent")

    def run_agent(self):
        path = os.path.join(self.t, "bin", "kiwi-client-agent")
        with open(path, "w") as f:
            f.write(self.script)
        os.chmod(path, 0o755)
        r = subprocess.run([path], env=dict(os.environ, PATH=self.t + "/bin:" + os.environ["PATH"]),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(self.t, "run", "head")) as f:
            head = dict(line.rstrip("\n").split(": ", 1) for line in f if ": " in line)
        with open(os.path.join(self.t, "run", "sent"), "rb") as f:
            return head, f.read()

    def test_it_reports_signed_with_its_own_key(self):
        head, body = self.run_agent()
        self.assertEqual(head["X-Kiwi-Client"], "pentest-1")
        self.assertIsNone(clientagent.check(KEY, "pentest-1", head["X-Kiwi-Time"], head["X-Kiwi-Sign"], body))
        self.assertEqual(clientagent.check("another key", "pentest-1", head["X-Kiwi-Time"], head["X-Kiwi-Sign"], body),
                         "the signature is not this client's")
        with open(os.path.join(self.t, "run", "logged")) as f:
            self.assertIn("report to https://control.test: 200", f.read())    # said once, when it changes
        facts = clientagent.parse(body.decode())
        self.assertEqual(facts["agent"], str(clientagent.VERSION))
        self.assertTrue(facts.get("os") and facts.get("kernel"))
        self.assertIsInstance(facts.get("uptime"), int)
        self.assertIn("crypt", facts)
        self.assertNotIn(KEY, body.decode())

    def test_a_clients_report_never_passes_for_a_machines(self):
        """The same key, the same body: a client's signature is not a machine's, nor the other way round."""
        when, body = str(int(time.time())), b"facts\n"
        machine = machineagent.signature(KEY, "pentest-1", when, "", body)
        self.assertNotEqual(machine, clientagent.signature(KEY, "pentest-1", when, body))
        self.assertEqual(clientagent.check(KEY, "pentest-1", when, machine, body), "the signature is not this client's")
        self.assertIn("apart", clientagent.check(KEY, "pentest-1", str(int(time.time()) - 3600),
                                                 clientagent.signature(KEY, "pentest-1", str(int(time.time()) - 3600), body), body))

    def test_the_box_is_a_salted_sum_of_the_boards_id(self):
        """The same board gives the same sum in this network and another in the next: the sum alone
        tells nothing about the hardware outside the network."""
        uuid = "4c4c4544-0042-3510-8051-b4c04f4e3232"

        def box(salt):
            r = subprocess.run(["bash", "-c", "printf '%s\\n%s' \"$1\" \"$2\" | sha256sum | cut -c1-16", "-", salt, uuid],
                               capture_output=True, text=True)
            return r.stdout.strip()
        self.assertEqual(len(box("a")), 16)
        self.assertNotEqual(box("a"), box("b"))
        self.assertIn('box $(printf', clientagent.FACTS % {"version": 1})

    def test_the_install_script_carries_everything_but_the_key(self):
        f = FakeFleet({"pentest-1": {}})
        s = clientagent.install_script(f, "pentest-1", "https://control.papaya.home", "-----BEGIN CERTIFICATE-----\nX\n-----END CERTIFICATE-----")
        self.assertIn("URL=https://control.papaya.home\nNAME=pentest-1\n", s)
        self.assertIn(clientagent.salt(f), s)
        self.assertIn("-----END CERTIFICATE-----\nKIWI_EOF", s)
        self.assertNotIn(clientagent.key(f, "pentest-1"), s)           # the key comes on stdin
        self.assertIn("key=$(cat)", s)
        for text in (s, clientagent.SCRIPT % {"dir": "/etc/kiwi-client"}, clientagent.FACTS % {"version": 1}):
            r = subprocess.run(["bash", "-n"], input=text, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        with self.assertRaisesRegex(KiwiError, "no control node"):
            clientagent.install_script(f, "pentest-1", None, "ca")

    def test_the_join_script_carries_the_key_and_the_command_the_ca(self):
        f = FakeFleet({"kali": {}})
        s = clientagent.join_script(f, "kali", "https://control.papaya.home", "-----BEGIN CERTIFICATE-----\nX\n-----END CERTIFICATE-----")
        self.assertIn("key='%s'\n" % clientagent.key(f, "kali"), s)
        self.assertNotIn("key=$(cat)", s)
        self.assertEqual(subprocess.run(["bash", "-n"], input=s, text=True, capture_output=True).returncode, 0)
        c = clientagent.join_command("https://control.papaya.home", "abc_DEF-123", "QUJD")
        self.assertIn('echo QUJD | base64 -d > "$c"; s=$(curl -sS --fail-with-body --cacert "$c" '
                      'https://control.papaya.home/api/client/join/abc_DEF-123); r=$?; rm -f "$c"', c)
        # run for real against stand-ins: the script goes to sudo sh; a spent code fails and says why
        t = tempfile.mkdtemp()
        for name, body in (("curl", 'case "$*" in *spent*) echo "kiwi-server: used up"; exit 22 ;; esac; echo "echo ran-the-script"\n'),
                           ("sudo", '"$@"\n')):
            with open(os.path.join(t, name), "w") as f:
                f.write("#!/bin/bash\n" + body)
            os.chmod(os.path.join(t, name), 0o755)
        env = dict(os.environ, PATH=t + ":" + os.environ["PATH"])
        r = subprocess.run(["bash", "-c", c], env=env, capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "ran-the-script"))
        spent = clientagent.join_command("https://control.papaya.home", "spent", "QUJD")
        r = subprocess.run(["bash", "-c", spent], env=env, capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout, r.stderr.strip()), (1, "", "kiwi-server: used up"))

    def test_the_fleet_names_its_clients(self):
        self.assertEqual(clientagent.names(FakeFleet({"pentest-1": None, "desk": {"group": "admins"}})),
                         {"pentest-1": {}, "desk": {"group": "admins"}})
        with self.assertRaisesRegex(KiwiError, "not a plain name"):
            clientagent.names(FakeFleet({"Pentest_1": {}}))
        with self.assertRaisesRegex(KiwiError, "a machine of the fleet"):
            clientagent.names(FakeFleet({"lime": {}}, hosts=["lime"]))
        self.assertEqual(clientagent.names(FakeFleet(None)), {})

    def test_rows_say_whether_a_client_is_on_and_on_which_box(self):
        folder = tempfile.mkdtemp()
        clientagent.keep(folder, "desk", b"box abc\nos Bluefin\nvpn wg0 10.9.0.40/32\n")
        old = os.path.join(folder, "clients", "desk.txt")
        rows = clientagent.rows(folder, ["desk", "laptop"], boxes={"abc": "lemonaide"}, now=os.path.getmtime(old) + 10)
        self.assertEqual([(r["name"], r["state"], r["box_of"]) for r in rows], [("desk", "on", "lemonaide"), ("laptop", "never", None)])
        self.assertEqual(rows[0]["facts"]["vpn"], [{"link": "wg0", "address": "10.9.0.40/32"}])
        later = clientagent.rows(folder, ["desk"], now=os.path.getmtime(old) + clientagent.FRESH + 1)
        self.assertEqual(later[0]["state"], "away")
        self.assertEqual(oct(os.stat(old).st_mode & 0o777), "0o600")


if __name__ == "__main__":
    unittest.main()
