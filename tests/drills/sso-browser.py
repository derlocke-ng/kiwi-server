#!/usr/bin/env python3
"""Drill: a person signs in to Forgejo with the network's account, in a real browser.

    tests/drills/sso-browser.py FLEET ACCOUNTS_ADDRESS ID_NAME GIT_ADDRESS GIT_NAME

The machine with the accounts module (at ACCOUNTS_ADDRESS, its page ID_NAME) makes an account and
its link; Firefox, told where ID_NAME and GIT_NAME are:
1. sets the account's password with the link;
2. opens Forgejo at GIT_NAME, chooses "Sign in with accounts", signs in at the accounts' page;
3. is back in Forgejo as that person: Forgejo made its user at the first sign-in.
The account is deleted afterwards (its Forgejo user stays: delete it in Forgejo's admin pages).
Needs `kiwi-server accounts <fleet> connect <host> git` and an apply of that host first.
"""
import json
import os
import secrets
import subprocess
import sys

fleet, acc_addr, idname, git_addr, gitname = sys.argv[1:6]
fd = os.path.dirname(os.path.abspath(fleet))
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "UserKnownHostsFile=" + os.path.join(fd, "secrets", "known_hosts"),
       "-i", os.path.join(fd, "secrets", "admin_ed25519"), "core@" + acc_addr]
SCRIPT = "S=$(ls /home/*/docker/kiwi/accounts.sh /var/home/*/docker/kiwi/accounts.sh 2>/dev/null | head -1); sudo sh $S "
name = "sso%s" % secrets.token_hex(3)
made = json.loads(subprocess.run(SSH + [SCRIPT + "create %s SSO-Drill" % name], capture_output=True, text=True, check=True).stdout)
pw = "kiwi-%s-%s" % (secrets.token_urlsafe(9), secrets.token_urlsafe(9))
shots = os.path.expanduser("~/kiwi-dev/browser")
os.makedirs(shots, exist_ok=True)
inner = r'''
import json, sys
from playwright.sync_api import sync_playwright
link, idname, gitname, name, pw = sys.argv[1:6]
out = {}
with sync_playwright() as p:
    b = p.firefox.launch()
    pg = b.new_page(ignore_https_errors=True)
    pg.goto(link, wait_until="networkidle")
    pg.get_by_text("Add Password").click(); pg.wait_for_timeout(1200)
    pg.fill("#new-password", pw); pg.fill("#new-password-check", pw)
    pg.get_by_role("button", name="Submit").click(); pg.wait_for_timeout(1500)
    pg.get_by_role("button", name="Save Changes").click(); pg.wait_for_timeout(2000)
    c = b.new_context(ignore_https_errors=True); q = c.new_page()
    q.goto("https://%s/user/login" % gitname, wait_until="networkidle")
    q.screenshot(path="/shots/sso-1-forgejo-login.png")
    q.get_by_text("Sign in with accounts").click(); q.wait_for_load_state("networkidle"); q.wait_for_timeout(1500)
    out["at_accounts"] = idname in q.url
    q.screenshot(path="/shots/sso-2-accounts-login.png")
    q.fill("input[name=username], #username", name); q.keyboard.press("Enter"); q.wait_for_timeout(1500)
    q.fill("input[type=password]", pw); q.keyboard.press("Enter"); q.wait_for_timeout(3000)
    q.screenshot(path="/shots/sso-3-after-password.png")
    for word in ("Proceed", "Allow", "Accept", "Continue"):        # a consent page, the first time
        btn = q.get_by_role("button", name=word)
        if btn.count():
            btn.first.click(); q.wait_for_timeout(3000); break
    q.wait_for_load_state("networkidle")
    q.screenshot(path="/shots/sso-4-back-in-forgejo.png", full_page=True)
    out["url"] = q.url
    out["text"] = q.inner_text("body")[:600]
    b.close()
print(json.dumps(out))
'''
with open(os.path.join(shots, "sso-inner.py"), "w") as fh:
    fh.write(inner)
r = subprocess.run(["podman", "run", "--rm", "--add-host", "%s:%s" % (idname, acc_addr), "--add-host", "%s:%s" % (gitname, git_addr),
                    "-v", "%s:/shots:z" % shots, "mcr.microsoft.com/playwright/python:v1.49.0-noble", "sh", "-c",
                    "pip install -q playwright==1.49.0 >/dev/null 2>&1; python3 /shots/sso-inner.py \"$@\"", "x",
                    made["link"], idname, gitname, name, pw], capture_output=True, text=True)
bad = 0
try:
    res = json.loads(r.stdout.strip().splitlines()[-1])
except (ValueError, IndexError):
    res = {}
    print((r.stderr or r.stdout)[-2000:])


def say(ok, what):
    global bad
    bad += 0 if ok else 1
    print("%s  %s" % ("PASS" if ok else "FAIL", what))


say(res.get("at_accounts"), "Forgejo's 'Sign in with accounts' leads to the accounts' page")
say(gitname in res.get("url", "") and name in res.get("text", ""),
    "back in Forgejo as %s: %s" % (name, res.get("url", "")))
subprocess.run(SSH + [SCRIPT + "delete %s" % name], capture_output=True)
print("%d wrong" % bad)
sys.exit(1 if bad else 0)
