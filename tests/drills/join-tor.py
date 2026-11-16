#!/usr/bin/env python3
"""Drill: a stranger gets an account over Tor only, in a real browser.

    tests/drills/join-tor.py FLEET MASTER_ADDRESS MASTER_PORT ONION_ADDRESS

ONION_ADDRESS is the accounts' hidden service (`kiwi-server onion <fleet> id.<machine> --anyway`).
Tor runs on the master for the drill only; this computer reaches it through ssh, so no Tor traffic
leaves from here. Firefox is told what Tor Browser is told about .onion addresses. Then:
1. the onion address opens the sign-up page;
2. an invite code (made as the control page makes it) makes an account, and its password link is
   on the onion address;
3. the link sets a password, and the person signs in over the onion address.
The account is deleted afterwards.
"""
import json
import os
import random
import secrets
import subprocess
import sys
import time

fleet, master, mport, onion = sys.argv[1:5]
fd = os.path.dirname(os.path.abspath(fleet))
OPTS = ["ssh", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=10",
        "-o", "UserKnownHostsFile=" + os.path.join(fd, "secrets", "known_hosts"), "-i", os.path.join(fd, "secrets", "admin_ed25519"), "-p", mport]
SSH = OPTS + ["core@" + master]
PORT = random.randint(20000, 29000)
up = """D=$(mktemp -d); mkdir -p $D/data
printf 'SocksPort 127.0.0.1:9152\\nDataDirectory /data\\n' > $D/torrc
chown -R 101:102 $D/data
podman rm -f kiwi-drill-tor-j >/dev/null 2>&1
podman run -d --rm --name kiwi-drill-tor-j --network host --entrypoint su-exec -v $D/torrc:/torrc:ro,z \\
    -v $D/data:/data:z docker.io/dockurr/tor tor tor -f /torrc >/dev/null
for i in $(seq 30); do podman logs kiwi-drill-tor-j 2>&1 | grep -q 'Bootstrapped 100' && break; sleep 5; done
podman logs kiwi-drill-tor-j 2>&1 | grep -c 'Bootstrapped 100'
"""
ready = subprocess.run(SSH + ["sudo sh -s"], input=up, capture_output=True, text=True, timeout=400).stdout.strip()
if not ready or ready.splitlines()[-1] == "0":
    sys.exit("drill: Tor on the master did not get ready")
# ssh's own options go before the host: after it they would be a command for the master
tunnel = subprocess.Popen(OPTS + ["-N", "-L", "127.0.0.1:%d:127.0.0.1:9152" % PORT, "core@" + master])
code = json.loads(subprocess.run(["kiwi-server", "accounts", fleet, "invite", "--note", "tor drill"], capture_output=True,
                                 text=True, check=True).stdout.strip().splitlines()[-1])["code"]
name = "tor%s" % secrets.token_hex(3)
pw = "kiwi-%s-%s" % (secrets.token_urlsafe(9), secrets.token_urlsafe(9))
shots = os.path.expanduser("~/kiwi-dev/browser")
inner = r'''
import json, sys
from playwright.sync_api import sync_playwright
port, onion, code, name, pw = sys.argv[1:6]
out = {}
def go(pg, url):
    for attempt in range(3):
        try:
            return pg.goto(url, timeout=120000, wait_until="load")
        except Exception as e:
            out.setdefault("retries", []).append(str(e)[:80])
with sync_playwright() as p:
    b = p.firefox.launch(proxy={"server": "socks5://127.0.0.1:%s" % port}, firefox_user_prefs={
        "network.proxy.socks_remote_dns": True, "network.dns.blockDotOnion": False,
        "dom.securecontext.allowlist_onions": True, "network.http.referer.hideOnionSource": True})
    pg = b.new_page()
    log = out.setdefault("log", [])
    pg.on("console", lambda m: log.append("console %s: %s" % (m.type, m.text[:200])))
    pg.on("requestfailed", lambda r: log.append("failed %s %s" % (r.method, r.url.split("?")[0][-80:])))
    pg.on("response", lambda r: r.status >= 400 and log.append("%d %s %s" % (r.status, r.request.method, r.url.split("?")[0][-80:])))
    go(pg, "http://%s/" % onion); out["front_door"] = pg.url
    pg.screenshot(path="/shots/tor-1-join.png")
    pg.fill("#i-code", code); pg.fill("#i-name", name); pg.click("form[action='/join/invite'] button")
    pg.wait_for_load_state("load", timeout=120000)
    pg.screenshot(path="/shots/tor-2-made.png")
    link = pg.get_attribute("a.link", "href") or ""
    out["link"] = link.split("?")[0]
    go(pg, link)
    T = 120000  # Tor: every step may take a while
    try:
        pg.get_by_text("Add Password").click(timeout=T)
        pg.wait_for_selector("#new-password", timeout=T)
        pg.fill("#new-password", pw); pg.fill("#new-password-check", pw)
        pg.get_by_role("button", name="Submit").click(timeout=T)
        pg.get_by_role("button", name="Save Changes").click(timeout=T)
        pg.wait_for_timeout(6000)
        pg.screenshot(path="/shots/tor-3-saved.png")
        q = b.new_context().new_page()
        go(q, "http://%s/ui/login" % onion)
        q.fill("input[name=username], #username", name, timeout=T); q.keyboard.press("Enter")
        q.wait_for_selector("input[type=password]", timeout=T)
        q.fill("input[type=password]", pw); q.keyboard.press("Enter")
        q.get_by_text("Sign out").first.wait_for(timeout=T)
        q.screenshot(path="/shots/tor-4-signed-in.png")
        out["signed_in"] = True; out["at"] = q.url.split("/")[2]
    except Exception as e:
        out["error"] = str(e).splitlines()[0][:160]
        for i, page in enumerate(b.contexts[-1].pages if b.contexts else []):
            page.screenshot(path="/shots/tor-fail-%d.png" % i)
    b.close()
print(json.dumps(out))
'''
with open(os.path.join(shots, "tor-inner.py"), "w") as fh:
    fh.write(inner)
bad = 0


def say(ok, what):
    global bad
    bad += 0 if ok else 1
    print("%s  %s" % ("PASS" if ok else "FAIL", what), flush=True)


try:
    time.sleep(4)
    r = subprocess.run(["podman", "run", "--rm", "--network", "host", "-v", "%s:/shots:z" % shots,
                        "mcr.microsoft.com/playwright/python:v1.49.0-noble", "sh", "-c",
                        "pip install -q playwright==1.49.0 >/dev/null 2>&1; python3 /shots/tor-inner.py \"$@\"", "x",
                        str(PORT), onion, code, name, pw], capture_output=True, text=True, timeout=1500)
    try:
        res = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        res = {}
        print((r.stderr or r.stdout)[-1500:])
    if res.get("error"):
        print("   stopped at: " + res["error"])
        for line in res.get("log", [])[-15:]:
            print("   " + line.replace(onion, "<onion>"))
    say(res.get("front_door", "").endswith("/join"), "the onion address opens the sign-up page")
    say(res.get("link", "").startswith("http://%s/ui/reset" % onion), "the password link stays on Tor: %s" % res.get("link", "").replace(onion, "<onion>"))
    say(res.get("signed_in") and res.get("at") == onion, "the person signs in over Tor")
finally:
    tunnel.terminate()
    subprocess.run(SSH + ["sudo podman rm -f kiwi-drill-tor-j >/dev/null 2>&1"])
    subprocess.run(["kiwi-server", "accounts", fleet, "delete", name], capture_output=True)
print("%d wrong" % bad)
sys.exit(1 if bad else 0)
