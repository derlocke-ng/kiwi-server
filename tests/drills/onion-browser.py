#!/usr/bin/env python3
"""A hidden service in a real browser, through Tor, the way Tor Browser treats it.

    tests/drills/onion-browser.py ~/kiwi/fleet.yaml --master core@5.45.103.177 --master-port 41022 git.medlar [pay.quince ...] [--login]

Tor runs on the master (a container that is removed again); this computer reaches it through
ssh, so no Tor traffic leaves from here. Firefox (Playwright's, in a container) is told what Tor
Browser is told about .onion addresses: names go to Tor, and a .onion is a secure context (so
cookies marked Secure are kept over http). For each service: the page it ends at, its title,
what the browser refused (console errors, failed requests, blocked mixed content), and the
cookies it kept. With --login and a login the network keeps for it (`kiwi-server logins`): the
service's own login form filled in and sent, and whether the page after it is a logged-in one.
A screenshot of each lands in ~/kiwi-dev/browser/.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time

signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))
ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument("fleet")
ap.add_argument("sites", nargs="+", help="service.machine, as `kiwi-server onion` lists them")
ap.add_argument("--master", required=True)
ap.add_argument("--master-port", default="22")
ap.add_argument("--login", action="store_true")
ap.add_argument("--image", default="mcr.microsoft.com/playwright/python:v1.55.0-noble")
args = ap.parse_args()
ks = os.environ.get("KIWI_SERVER", "kiwi-server")
fdir = os.path.dirname(os.path.abspath(args.fleet))
rows = {"%s.%s" % (x["service"], x["host"]): x for x in json.loads(subprocess.run(
    [ks, "onion", args.fleet, "--porcelain"], capture_output=True, text=True).stdout)["services"]}
logins = {}
if args.login:
    for h in json.loads(subprocess.run([ks, "logins", args.fleet, "--porcelain"], capture_output=True, text=True).stdout)["hosts"]:
        for lg in h["logins"]:
            if lg.get("password") and lg.get("address", "").startswith("https://"):
                logins.setdefault(lg["address"].split("//", 1)[1].split("/")[0], lg)
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
       "-o", "LogLevel=ERROR", "-o", "UserKnownHostsFile=%s" % os.path.join(fdir, "secrets", "known_hosts"),
       "-i", os.path.join(fdir, "secrets", "admin_ed25519"), "-p", str(args.master_port)]
PORT = 19152
up = """D=$(mktemp -d); mkdir -p $D/data
printf 'SocksPort 0.0.0.0:9151\\nSocksPolicy accept 127.0.0.1\\nSocksPolicy reject *\\nDataDirectory /data\\n' > $D/torrc
chown -R 101:102 $D/data
podman rm -f kiwi-drill-tor-b >/dev/null 2>&1
podman run -d --rm --name kiwi-drill-tor-b --network host --entrypoint su-exec -v $D/torrc:/torrc:ro,z \\
    -v $D/data:/data:z docker.io/dockurr/tor tor tor -f /torrc >/dev/null
for i in $(seq 30); do podman logs kiwi-drill-tor-b 2>&1 | grep -q 'Bootstrapped 100' && break; sleep 5; done
podman logs kiwi-drill-tor-b 2>&1 | grep -c 'Bootstrapped 100'
"""
ready = subprocess.run(SSH + [args.master, "sudo sh -s"], input=up.replace("0.0.0.0:9151", "127.0.0.1:9151"),
                       capture_output=True, text=True, timeout=400).stdout.strip()
if not ready or ready.splitlines()[-1] == "0":
    sys.exit("drill: Tor on the master did not get ready")
tunnel = subprocess.Popen(SSH + ["-N", "-L", "127.0.0.1:%d:127.0.0.1:9151" % PORT, args.master])
SCRIPT = r'''
import json, sys
from playwright.sync_api import sync_playwright
job = json.loads(sys.stdin.read())
out = []
with sync_playwright() as p:
    b = p.firefox.launch(proxy={"server": "socks5://127.0.0.1:%d" % job["port"]}, firefox_user_prefs={
        "network.proxy.socks_remote_dns": True, "network.dns.blockDotOnion": False,
        "dom.securecontext.allowlist_onions": True, "network.http.referer.hideOnionSource": True})
    for s in job["sites"]:
        ctx = b.new_context(ignore_https_errors=False, viewport={"width": 1280, "height": 900})
        pg = ctx.new_page()
        notes = []
        pg.on("console", lambda m: m.type in ("error", "warning") and notes.append("console %s: %s" % (m.type, m.text[:160])))
        pg.on("requestfailed", lambda r: notes.append("failed: %s %s" % (r.url[:90], r.failure)))
        r = {"site": s["name"], "notes": notes}
        try:
            for attempt in range(3):
                try:
                    resp = pg.goto("http://%s/" % s["address"], timeout=120000, wait_until="load")
                    break
                except Exception as e:
                    if attempt == 2:
                        raise
                    pg.wait_for_timeout(15000)
            r["status"] = resp.status if resp else None
            r["url"], r["title"] = pg.url, pg.title()
            if s.get("login"):
                lg = s["login"]
                if s["name"].startswith("git."):
                    pg.goto("http://%s/user/login" % s["address"], timeout=120000)
                    pg.fill("#user_name", lg["user"]); pg.fill("#password", lg["password"])
                    pg.click("form.ui.form button.ui.primary.button")
                elif s["name"].startswith("pay."):
                    pg.goto("http://%s/login" % s["address"], timeout=120000)
                    pg.fill("#Email", lg["user"]); pg.fill("#Password", lg["password"]); pg.click("#LoginButton")
                elif s["name"].startswith("paperless."):
                    pg.goto("http://%s/accounts/login/" % s["address"], timeout=120000)
                    pg.fill("input[name=login]", lg["user"]); pg.fill("input[name=password]", lg["password"])
                    pg.click("button[type=submit]")
                pg.wait_for_load_state("load", timeout=120000)
                pg.wait_for_timeout(3000)
                r["after_login"], r["after_title"] = pg.url, pg.title()
                r["logged_in"] = pg.locator("text=/sign out|log out|logout|abmelden/i").count() > 0 or pg.locator("[href*=logout],[action*=logout]").count() > 0
            r["cookies"] = ["%s%s" % (c["name"], " secure" if c["secure"] else "") for c in ctx.cookies()]
            pg.screenshot(path="/shots/%s.png" % s["name"].replace(".", "-"), full_page=False)
        except Exception as e:
            r["error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
        out.append(r)
        ctx.close()
    b.close()
print(json.dumps(out))
'''
try:
    time.sleep(4)
    job = {"port": PORT, "sites": []}
    for name in args.sites:
        x = rows.get(name)
        if not x:
            print("SKIP  %s: no such hidden service" % name)
            continue
        job["sites"].append({"name": name, "address": x["address"], "login": logins.get(x["site"]) if args.login else None})
    r = subprocess.run(["podman", "run", "--rm", "-i", "--network", "host", "-v", "%s:/shots:z" % os.path.expanduser("~/kiwi-dev/browser"),
                        args.image, "sh", "-c", 'pip install -q --break-system-packages "playwright==1.55.0" >/dev/null 2>&1; '
                        'exec python3 -c "$0"', SCRIPT], input=json.dumps(job), capture_output=True, text=True, timeout=3000)
    lines = r.stdout.strip().splitlines()
    if not lines:
        sys.exit("drill: the browser said nothing: %s" % r.stderr.strip()[-600:])
    for x in json.loads(lines[-1]):
        hide = lambda t: str(t).replace(rows[x["site"]]["address"], "<address>")      # noqa: E731
        print("== %s: %s %s, %r" % (x["site"], x.get("status"), hide(x.get("url")), x.get("title")))
        if "after_login" in x:
            print("   after the login: %s %r, logged in: %s" % (hide(x["after_login"]), x["after_title"], x["logged_in"]))
        if x.get("error"):
            print("   error: %s" % hide(x["error"]))
        print("   cookies kept: %s" % ", ".join(x.get("cookies") or []) or "-")
        for n in x["notes"][:8]:
            print("   %s" % hide(n))
finally:
    tunnel.terminate()
    subprocess.run(SSH + [args.master, "sudo podman rm -f kiwi-drill-tor-b >/dev/null 2>&1"])
