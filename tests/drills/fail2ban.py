#!/usr/bin/env python3
"""Does fail2ban ban who attacks a public site, and only them? Run against a node's public name,
from a stranger's address: a Tor exit, through a Tor that runs on the master for the drill only
(no Tor traffic from this computer).

    tests/drills/fail2ban.py FLEET MASTER[:PORT] NODE https://paste.example.org [--app git]

MASTER is its public address (and SSH port), NODE its mesh address, e.g.
    tests/drills/fail2ban.py ~/kiwi/fleet.yaml 5.45.103.177:41022 10.9.0.13 https://paste.dlhost.eu

1. a page that loads a hundred and fifty files at once: nothing refused, nobody banned
2. a scanner asking for /wp-login.php: banned within seconds, by the node's nginx (403)
3. let out again on the node: the page answers again
4. the node never bans the network's own addresses: it got the master's list
5. with --app NAME: a wrong login at that app reaches the jail's file, and the jail's pattern
   matches it (made from the node itself, an address fail2ban ignores, so nobody is banned)
Written 16 November 2026, after one page load had banned the owner's home for an hour."""
import argparse
import os
import shlex
import subprocess
import sys
import time


a = argparse.ArgumentParser()
a.add_argument("fleet"); a.add_argument("master"); a.add_argument("node"); a.add_argument("url")
a.add_argument("--app", default="")
o = a.parse_args()
fleet_dir = os.path.dirname(os.path.abspath(o.fleet))
key = os.path.join(fleet_dir, "secrets", "admin_ed25519")
known = os.path.join(fleet_dir, "secrets", "known_hosts")


def ssh(host):
    """core@ADDRESS[:PORT] as given (the master by its public address, the node by its mesh address)."""
    user_addr, _, port = host.rpartition(":") if host.count(":") == 1 else (host, "", "")
    return ["ssh", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=10", "-o", "UserKnownHostsFile=" + known,
            "-i", key, "-p", port or "22", user_addr if "@" in user_addr else "core@" + user_addr]


def run(host, script, timeout=300):
    r = subprocess.run(ssh(host) + ["sudo sh -s"], input=script, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()


bad = 0


def say(ok, what):
    global bad
    bad += 0 if ok else 1
    print("%s  %s" % ("PASS" if ok else "FAIL", what), flush=True)


up = """D=$(mktemp -d); mkdir -p $D/data
printf 'SocksPort 127.0.0.1:9150\\nDataDirectory /data\\n' > $D/torrc
chown -R 101:102 $D/data
podman rm -f kiwi-drill-tor >/dev/null 2>&1
podman run -d --rm --name kiwi-drill-tor --network host --entrypoint su-exec -v $D/torrc:/torrc:ro,z \\
    -v $D/data:/data:z docker.io/dockurr/tor tor tor -f /torrc >/dev/null
for i in $(seq 30); do podman logs kiwi-drill-tor 2>&1 | grep -q 'Bootstrapped 100' && break; sleep 5; done
podman logs kiwi-drill-tor 2>&1 | grep -c 'Bootstrapped 100'
"""
if run(o.master, up, 400).splitlines()[-1:] != ["1"]:
    run(o.master, "podman rm -f kiwi-drill-tor >/dev/null 2>&1")
    sys.exit("drill: Tor on the master did not get ready")
# one circuit for the whole drill: Tor keeps a SOCKS user's streams on one exit
C = "curl -s -m 30 --proxy socks5h://kiwi-drill-%d:x@127.0.0.1:9150" % os.getpid()
url = o.url.rstrip("/")
try:
    f2b = "podman exec kn-fail2ban fail2ban-client"
    mark = "kiwi-drill-%d" % os.getpid()
    first = run(o.master, C + " -o /dev/null -w '%%{http_code}' %s/?%s" % (shlex.quote(url), mark))
    # the stranger's address as the node saw it (an exit can differ from what a check site says)
    ip = run(o.node, "grep -h %s /var/home/*/docker/kn-nginx/log/access.log /home/*/docker/kn-nginx/log/access.log 2>/dev/null | tail -n 1 | cut -d' ' -f1"
             % shlex.quote(mark))
    if not ip:
        sys.exit("drill: the node did not log the stranger's request")
    print("the stranger: a Tor exit (%s.x.x.x)" % ip.split(".")[0], flush=True)
    say(first.startswith(("2", "3")), "the page answers the stranger (%s)" % first)
    # 1. a web app's first load: many files at once
    page = run(o.master, "seq 150 | xargs -P 30 -I{} %s -o /dev/null -w '%%{http_code}\\n' %s/kiwi-drill-{}.js | sort | uniq -c"
               % (C, shlex.quote(url)))
    say("429" not in page, "150 files at once, none refused: " + " ".join(page.split()))
    time.sleep(8)
    again = run(o.master, C + " -o /dev/null -w '%%{http_code}' %s/" % shlex.quote(url))
    say(again == first, "and nobody is banned for it (%s)" % again)
    # 2. a scanner
    run(o.master, "for i in 1 2 3 4; do %s -o /dev/null %s/wp-login.php; done" % (C, shlex.quote(url)))
    code = ""
    for _ in range(12):
        time.sleep(5)
        code = run(o.master, C + " -o /dev/null -w '%%{http_code}' %s/" % shlex.quote(url))
        if code == "403":
            break
    say(code == "403", "a scanner is banned, by the node's nginx (%s)" % code)
    jails = run(o.node, "%s banned %s 2>/dev/null" % (f2b, shlex.quote(ip)))
    say("kiwi-nginx-probe" in jails, "the jail that banned it: kiwi-nginx-probe")
    # 3. let out
    run(o.node, "%s unban %s >/dev/null" % (f2b, shlex.quote(ip)))
    time.sleep(3)
    code = run(o.master, C + " -o /dev/null -w '%%{http_code}' %s/" % shlex.quote(url))
    say(code == first, "let out, the page answers again (%s)" % code)
    # 4. the network's own addresses
    master_list = run(o.master, "cat /var/home/*/docker/km-nginx/own/list /home/*/docker/km-nginx/own/list 2>/dev/null | head -c 2000").split()
    run(o.node, "rm -f /run/kiwi-fail2ban-own; systemctl start kn-fail2ban-own.service")
    ignored = run(o.node, "%s get kiwi-nginx-probe ignoreip" % f2b)
    say(bool(master_list) and all(x in ignored for x in master_list),
        "the node never bans the network's own addresses (%d from the master)" % len(master_list))
    # 5. an app's wrong logins reach its jail
    if o.app:
        line = run(o.node, "tail -n 1 /var/home/*/docker/kn-fail2ban/apps/%s.log /home/*/docker/kn-fail2ban/apps/%s.log 2>/dev/null | tail -n 1"
                   % (o.app, o.app))
        say(bool(line), "a wrong login at %s is in its jail's file" % o.app)
        hit = run(o.node, "podman exec kn-fail2ban sh -c %s" % shlex.quote(
            "tail -n 1 /kiwi-apps/%s.log > /tmp/l; re=$(awk '/^\\[kiwi-%s\\]/{f=1} f&&/^failregex = /{sub(/^failregex = /,\"\"); print; exit}' "
            "/config/fail2ban/jail.d/kiwi.local); fail2ban-regex /tmp/l \"$re\" | grep -c '1 matched'" % (o.app, o.app)))
        say(hit == "1", "and the jail's pattern finds the address in it")
finally:
    run(o.master, "podman rm -f kiwi-drill-tor >/dev/null 2>&1")
print("%d wrong" % bad)
sys.exit(1 if bad else 0)
