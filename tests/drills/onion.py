#!/usr/bin/env python3
"""A hidden service, tried for real.

    tests/drills/onion.py ~/kiwi/fleet.yaml sorbet vault.quince

For one hidden service of the fleet (`kiwi-server onion`) this asks three things:

1. Where does the machine's Tor leave? It must share the VPN client's network, and the
   address the world sees from there must not be the machine's own connection.
2. Does the address open through Tor? Asked through the master's Tor proxy, from the master.
   Another site's name on the same address must get nothing.
3. A service limited to named people: it must open with a person's key (a Tor client started
   on the master for the moment, the key handed to it on standard input) and must not be
   found without one.

Nothing is changed on the machines; the Tor client on the master is removed again. No Tor
traffic leaves from the computer this runs on.
"""
import json
import subprocess
import sys

from drill import Machine, kiwi, say, setup
from kiwiserver import cli, modules as modmod, roles as rolesmod

what = sys.argv.pop() if len(sys.argv) > 3 else sys.exit(__doc__)
args, fleet, m = setup(__doc__.splitlines()[0], roles=("master",))
master = m["master"]
rc, out = kiwi("onion", args.fleet, "--porcelain")
found = {"%s.%s" % (x["service"], x["host"]): x for x in json.loads("\n".join(x for x in out if not x.startswith(("::", "warning"))))["services"]}
if what not in found:
    sys.exit("drill: %s is no hidden service (there is: %s)" % (what, ", ".join(found) or "none"))
svc = found[what]
_fleet, roles, ms = cli.load(args)
host = _fleet.hosts[svc["host"]]
rolesmod.resolve_settings(roles[host.role], host, ms)
node = Machine(host)
prefix = modmod.PREFIX[roles[host.role].node_type]
socks = "%s:9050" % (master.host.module_settings["vpn-server"].get("server_ip") or "10.8.0.1")
failed = 0


def check(ok, text):
    global failed
    failed += not ok
    print("  %-4s %s" % ("ok" if ok else "FAIL", text))


say("%s: http://%s (%s)" % (what, svc["address"], ("only: " + ", ".join(svc["clients"])) if svc["clients"] else "anyone with the address"))
mode = node.out("podman inspect %s-onion --format '{{.HostConfig.NetworkMode}}'" % prefix)
vpn = node.out("podman inspect %s-vpn-client --format '{{.Id}}'" % prefix)
check(mode == "container:" + vpn, "Tor shares the VPN client's network (%s)" % (mode[:30] or "no container"))
tor_exit = node.out("podman exec %s-vpn-client sh -c 'wget -qO- -T 10 https://api.ipify.org'" % prefix).strip()
own = node.out("curl -s --max-time 10 https://api.ipify.org").strip()
check(bool(tor_exit) and tor_exit != own, "what Tor sends leaves somewhere else than the machine's own connection")
curl = "curl -s --max-time 90 --socks5-hostname %s -o /dev/null -w '%%{http_code}' " % socks
url = "http://%s/" % svc["address"]
if not svc["clients"]:
    code = master.out(curl + url)
    check(code[:1] in "23", "it opens through Tor (HTTP %s)" % code)
    other = master.out(curl + "-H 'Host: nothing.%s' %s" % (host.hostname, url))
    check(other == "000", "another site's name on that address gets nothing (HTTP %s)" % other)
else:
    code = master.out(curl + url)
    check(code == "000", "without a key it is not found (HTTP %s)" % code)
    person = svc["clients"][0]
    rc, key = kiwi("onion", args.fleet, what, "--show", person)
    line = "%s:descriptor:x25519:%s\n" % (svc["address"][:-len(".onion")], key[-1].strip())
    script = ("set -e; D=$(mktemp -d); mkdir -p $D/auth $D/data; cat > $D/auth/p.auth_private; "
              "printf 'SocksPort 127.0.0.1:9150\\nClientOnionAuthDir /auth\\nDataDirectory /data\\n' > $D/torrc; chown -R 101:102 $D/auth $D/data; "
              "podman rm -f kiwi-drill-tor >/dev/null 2>&1 || true; "
              "podman run -d --rm --name kiwi-drill-tor --network host --entrypoint su-exec -v $D/torrc:/torrc:ro,z -v $D/auth:/auth:z "
              "-v $D/data:/data:z docker.io/dockurr/tor tor tor -f /torrc >/dev/null; "
              "for i in $(seq 24); do podman logs kiwi-drill-tor 2>&1 | grep -q 'Bootstrapped 100' && break; sleep 5; done; "
              "curl -s --max-time 90 --socks5-hostname 127.0.0.1:9150 -o /dev/null -w '%{http_code}' " + url + "; "
              "podman rm -f kiwi-drill-tor >/dev/null 2>&1; rm -rf $D")
    r = subprocess.run(["ssh"] + cli.remote.ssh_opts() + ["-o", "ConnectTimeout=10", master.target, "sudo sh -c " + cli.shlex.quote(script)],
                       input=line, capture_output=True, text=True, timeout=600)
    code = r.stdout.strip()[-3:]
    check(code[:1] in "23", "with %s's key it opens (HTTP %s)" % (person, code))
print("\n%s" % ("as it should be" if not failed else "%d things are not as they should be" % failed))
sys.exit(1 if failed else 0)
