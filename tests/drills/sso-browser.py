#!/usr/bin/env python3
"""Drill: a person signs in to Forgejo with the network's account, in a real browser.

    tests/drills/sso-browser.py FLEET ACCOUNTS_ADDRESS ID_NAME APP_ADDRESS APP_NAME [--nextcloud|--paperless|--vault]

APP_NAME is Forgejo's name (git.<host>), with --nextcloud Nextcloud's (cloud.<host>), with --paperless
Paperless' (paperless.<host>), with --vault Vaultwarden's (vault.<host>): there the person's first
sign-on ends at "set a master password", which the network never sees.

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
nextcloud = "--nextcloud" in sys.argv
app = "nextcloud" if nextcloud else "paperless" if "--paperless" in sys.argv else "vault" if "--vault" in sys.argv else "forgejo"
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
link, idname, gitname, name, pw, app = sys.argv[1:7]
out = {}
with sync_playwright() as p:
    b = p.firefox.launch()
    pg = b.new_page(ignore_https_errors=True)
    pg.goto(link, wait_until="networkidle")
    pg.get_by_text("Add Password").click(); pg.wait_for_timeout(1200)
    pg.fill("#new-password", pw); pg.fill("#new-password-check", pw)
    pg.get_by_role("button", name="Submit").click(); pg.wait_for_timeout(1500)
    pg.get_by_role("button", name="Save Changes").click(); pg.wait_for_timeout(2000)
    # the test browser's Firefox is older than Nextcloud likes: it says it is a current one
    c = b.new_context(ignore_https_errors=True, user_agent="Mozilla/5.0 (X11; Linux x86_64; rv:145.0) Gecko/20100101 Firefox/145.0")
    q = c.new_page()
    if app == "nextcloud":
        q.goto("https://%s/login" % gitname, wait_until="networkidle")
        q.screenshot(path="/shots/sso-1-nextcloud-login.png")
        q.locator("a[href*='user_oidc/login']").first.click()
    elif app == "paperless":
        q.goto("https://%s/accounts/login/" % gitname, wait_until="networkidle")
        q.screenshot(path="/shots/sso-1-paperless-login.png")
        q.get_by_text("accounts", exact=True).first.click()   # "or sign in via: accounts"
        q.wait_for_load_state("networkidle")
        go_on = q.get_by_role("button", name="Continue")   # allauth asks once before it leaves
        if go_on.count() and idname not in q.url:
            go_on.first.click()
    elif app == "vault":
        q.goto("https://%s/#/login" % gitname, wait_until="networkidle"); q.wait_for_timeout(2000)
        q.screenshot(path="/shots/sso-1-vault-login.png")
        q.get_by_text("single sign-on", exact=False).first.click(); q.wait_for_timeout(2500)
        if idname not in q.url:                    # the web vault may ask for an identifier first
            box = q.locator("input[formcontrolname=identifier], #identifier, input[type=text]")
            if box.count():
                box.first.fill("kiwi"); q.keyboard.press("Enter")
    else:
        q.goto("https://%s/user/login" % gitname, wait_until="networkidle")
        q.screenshot(path="/shots/sso-1-forgejo-login.png")
        q.get_by_text("Sign in with accounts").click()
    q.wait_for_load_state("networkidle"); q.wait_for_timeout(1500)
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
    q.wait_for_timeout(4000)
    q.screenshot(path="/shots/sso-4-back-in-%s.png" % app, full_page=True)
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
                    made["link"], idname, gitname, name, pw, app], capture_output=True, text=True)
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


label = {"nextcloud": "Nextcloud", "paperless": "Paperless", "vault": "Vaultwarden", "forgejo": "Forgejo"}[app]
say(res.get("at_accounts"), "%s's 'with accounts' leads to the accounts' page" % label)
shows_person = name in res.get("text", "") or "SSO-Drill" in res.get("text", "") or (
    app == "vault" and "master password" in res.get("text", "").lower())
say(gitname in res.get("url", "") and shows_person,
    "back in %s as %s: %s" % (label, name, res.get("url", "")))
subprocess.run(SSH + [SCRIPT + "delete %s" % name], capture_output=True)
print("%d wrong" % bad)
sys.exit(1 if bad else 0)
