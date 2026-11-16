#!/usr/bin/env python3
"""Drill: a person gets an account the way the network hands it out, in a real browser.

    tests/drills/accounts-browser.py FLEET NODE ADDRESS ID_NAME [--keep]

On NODE (reached at ADDRESS) kiwi/accounts.sh makes an account and its link. Firefox, in the
Playwright image, told that ID_NAME is at ADDRESS:
1. opens the link and sets a password on Kanidm's page,
2. signs in with it at https://ID_NAME/ui/login and sees the account's own page,
3. a wrong password is refused.
The account is deleted afterwards unless --keep. Screenshots: ~/kiwi-dev/browser/accounts-*.png.
"""
import json
import os
import secrets
import subprocess
import sys

fleet, node, address, idname = sys.argv[1:5]
keep = "--keep" in sys.argv
fd = os.path.dirname(os.path.abspath(fleet))
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "UserKnownHostsFile=" + os.path.join(fd, "secrets", "known_hosts"),
       "-i", os.path.join(fd, "secrets", "admin_ed25519"), "core@" + address]
SCRIPT = "S=$(ls /home/*/docker/kiwi/accounts.sh /var/home/*/docker/kiwi/accounts.sh 2>/dev/null | head -1); sudo sh $S "
name = "drill%s" % secrets.token_hex(3)
made = json.loads(subprocess.run(SSH + [SCRIPT + "create %s Drill" % name], capture_output=True, text=True, check=True).stdout)
pw = "kiwi-%s-%s" % (secrets.token_urlsafe(9), secrets.token_urlsafe(9))
shots = os.path.expanduser("~/kiwi-dev/browser")
os.makedirs(shots, exist_ok=True)
inner = r'''
import json, sys
from playwright.sync_api import sync_playwright
link, idname, name, pw = sys.argv[1:5]
out = {}
with sync_playwright() as p:
    b = p.firefox.launch()
    pg = b.new_page(ignore_https_errors=True)
    pg.goto(link, wait_until="networkidle")
    pg.screenshot(path="/shots/accounts-1-link.png")
    out["link_page"] = pg.inner_text("body")[:200]
    pg.get_by_text("Add Password").click()
    pg.wait_for_timeout(1500)
    pg.screenshot(path="/shots/accounts-2-password.png")
    pg.fill("#new-password", pw); pg.fill("#new-password-check", pw)
    pg.get_by_role("button", name="Submit").click()
    pg.wait_for_timeout(2000)
    pg.get_by_role("button", name="Save Changes").click()
    pg.wait_for_timeout(2500)
    pg.screenshot(path="/shots/accounts-3-saved.png")
    out["saved"] = pg.inner_text("body")[:300]
    def login(password):
        c = b.new_context(ignore_https_errors=True); q = c.new_page()
        q.goto("https://%s/ui/login" % idname, wait_until="networkidle")
        q.fill("input[name=username], #username", name); q.keyboard.press("Enter"); q.wait_for_timeout(1500)
        q.fill("input[type=password]", password); q.keyboard.press("Enter"); q.wait_for_timeout(2500)
        text = q.inner_text("body")[:400]; q.screenshot(path="/shots/accounts-4-%s.png" % ("right" if password == pw else "wrong"))
        c.close(); return text
    out["right"] = login(pw)
    out["wrong"] = login(pw + "x")
    b.close()
print(json.dumps(out))
'''
with open(os.path.join(shots, "accounts-inner.py"), "w") as fh:
    fh.write(inner)
r = subprocess.run(["podman", "run", "--rm", "--add-host", "%s:%s" % (idname, address), "-v", "%s:/shots:z" % shots,
                    "mcr.microsoft.com/playwright/python:v1.49.0-noble", "sh", "-c",
                    "pip install -q playwright==1.49.0 >/dev/null 2>&1; python3 /shots/accounts-inner.py \"$@\"", "x",
                    made["link"], idname, name, pw], capture_output=True, text=True)
try:
    res = json.loads(r.stdout.strip().splitlines()[-1])
except (ValueError, IndexError):
    sys.exit("drill: the browser did not finish:\n" + (r.stderr or r.stdout)[-2000:])
bad = 0
def say(ok, what):
    global bad
    bad += 0 if ok else 1
    print("%s  %s" % ("PASS" if ok else "FAIL", what))
say("Updating Credentials" in res.get("link_page", ""), "the link opens the page that sets a password")
say("Save Changes" not in res.get("saved", "Save Changes") or "success" in res.get("saved", "").lower(), "the password is saved")
say("Sign out" in res.get("right", ""), "signed in with it: " + res.get("right", "")[:80].replace("\n", " "))
say(name not in res.get("wrong", "") and ("incorrect" in res.get("wrong", "").lower() or "denied" in res.get("wrong", "").lower() or "invalid" in res.get("wrong", "").lower()),
    "a wrong password is refused: " + res.get("wrong", "")[:80].replace("\n", " "))
if not keep:
    subprocess.run(SSH + [SCRIPT + "delete %s" % name], capture_output=True)
print("%d wrong" % bad)
sys.exit(1 if bad else 0)
