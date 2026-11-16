#!/usr/bin/env python3
"""Drill: a stranger gets an account at the network's sign-up page, in a real browser.

    tests/drills/join-browser.py FLEET NODE ADDRESS ID_NAME

On NODE (the machine with the accounts module, reached at ADDRESS) the sign-up container makes an
invite code (as the control page would). Firefox, told that ID_NAME is at ADDRESS:
1. with the invite code: makes an account at https://ID_NAME/join, follows the link, sets a
   password, signs in;
2. by asking: sends a request, gets a request code; the request is accepted on the node; the
   request code then shows the link, which sets the password, and the person signs in.
The accounts are deleted afterwards. Screenshots: ~/kiwi-dev/browser/join-*.png.
"""
import json
import os
import secrets
import shlex
import subprocess
import sys

fleet, node, address, idname = sys.argv[1:5]
fd = os.path.dirname(os.path.abspath(fleet))
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "UserKnownHostsFile=" + os.path.join(fd, "secrets", "known_hosts"),
       "-i", os.path.join(fd, "secrets", "admin_ed25519"), "core@" + address]


def on_node(cmd):
    return subprocess.run(SSH + [cmd], capture_output=True, text=True, check=True).stdout


def signup(*args):
    return json.loads(on_node("sudo podman exec kn-signup kiwi-server signup %s --state /state" % " ".join(shlex.quote(a) for a in args)))


code = signup("invite", "--note", "drill")["code"]
a, b = "join%s" % secrets.token_hex(2), "ask%s" % secrets.token_hex(2)
pw = "kiwi-%s-%s" % (secrets.token_urlsafe(9), secrets.token_urlsafe(9))
shots = os.path.expanduser("~/kiwi-dev/browser")
os.makedirs(shots, exist_ok=True)
inner = r'''
import json, sys, time
from playwright.sync_api import sync_playwright
idname, code, a, b, pw, step = sys.argv[1:7]
out = {}
def set_password(pg, tag):
    pg.wait_for_load_state("networkidle")
    pg.get_by_text("Add Password").click(); pg.wait_for_timeout(1200)
    pg.fill("#new-password", pw); pg.fill("#new-password-check", pw)
    pg.get_by_role("button", name="Submit").click(); pg.wait_for_timeout(1500)
    pg.get_by_role("button", name="Save Changes").click(); pg.wait_for_timeout(2000)
    pg.screenshot(path="/shots/join-%s-saved.png" % tag)
def sign_in(b_, name, tag):
    c = b_.new_context(ignore_https_errors=True); q = c.new_page()
    q.goto("https://%s/ui/login" % idname, wait_until="networkidle")
    q.fill("input[name=username], #username", name); q.keyboard.press("Enter"); q.wait_for_timeout(1500)
    q.fill("input[type=password]", pw); q.keyboard.press("Enter"); q.wait_for_timeout(2500)
    q.screenshot(path="/shots/join-%s-signed-in.png" % tag)
    t = q.inner_text("body"); c.close(); return "Sign out" in t
with sync_playwright() as p:
    br = p.firefox.launch()
    pg = br.new_page(ignore_https_errors=True)
    if step == "invite":
        pg.goto("https://%s/" % idname, wait_until="networkidle")
        out["front_door"] = pg.url
        pg.screenshot(path="/shots/join-1-page.png", full_page=True)
        pg.fill("#i-code", code); pg.fill("#i-name", a); pg.click("form[action='/join/invite'] button")
        pg.wait_for_load_state("networkidle"); pg.screenshot(path="/shots/join-2-made.png")
        out["made"] = "is made" in pg.inner_text("body")
        pg.get_by_text("Set my password").click()
        set_password(pg, "invite")
        out["signed_in"] = sign_in(br, a, "invite")
        pg.goto("https://%s/join" % idname); pg.fill("#i-code", code); pg.fill("#i-name", a + "x"); pg.click("form[action='/join/invite'] button")
        pg.wait_for_load_state("networkidle"); out["code_used_up"] = "not good" in pg.inner_text("body")
    elif step == "ask":
        pg.goto("https://%s/join" % idname, wait_until="networkidle")
        pg.fill("#a-name", b); pg.fill("#a-why", "A drill of the sign-up page, asking for an account"); pg.click("form[action='/join/ask'] button")
        pg.wait_for_load_state("networkidle"); pg.screenshot(path="/shots/join-3-asked.png")
        t = pg.inner_text("body"); out["waiting"] = "waiting" in t
        out["request_code"] = next((w for w in t.split() if w.startswith("ask-")), "")
    else:     # step "status": the request code shows the link once it is accepted
        pg.goto("https://%s/join/status?code=%s" % (idname, code), wait_until="networkidle")
        pg.screenshot(path="/shots/join-4-ready.png")
        out["ready"] = "Set my password" in pg.inner_text("body")
        pg.get_by_text("Set my password").click()
        set_password(pg, "ask")
        out["signed_in"] = sign_in(br, b, "ask")
    br.close()
print(json.dumps(out))
'''
with open(os.path.join(shots, "join-inner.py"), "w") as fh:
    fh.write(inner)


def browser(step, arg=""):
    r = subprocess.run(["podman", "run", "--rm", "--add-host", "%s:%s" % (idname, address), "-v", "%s:/shots:z" % shots,
                        "mcr.microsoft.com/playwright/python:v1.49.0-noble", "sh", "-c",
                        "pip install -q playwright==1.49.0 >/dev/null 2>&1; python3 /shots/join-inner.py \"$@\"", "x",
                        idname, arg or code, a, b, pw, step], capture_output=True, text=True)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        sys.exit("drill: the browser did not finish (%s):\n%s" % (step, (r.stderr or r.stdout)[-1500:]))


bad = 0


def say(ok, what):
    global bad
    bad += 0 if ok else 1
    print("%s  %s" % ("PASS" if ok else "FAIL", what), flush=True)


try:
    r = browser("invite")
    say(r.get("front_door", "").endswith("/join"), "the accounts' name opens the sign-up page")
    say(r.get("made"), "an invite code makes an account")
    say(r.get("signed_in"), "its link sets a password, and the person signs in with it")
    say(r.get("code_used_up"), "the code is used up")
    r = browser("ask")
    say(r.get("waiting") and r.get("request_code"), "a request waits, with a code to come back with")
    reqs = [x for x in signup("requests") if x["name"] == b and x["state"] == "waiting"]
    say(bool(reqs), "the request is there for the people who run the network")
    if reqs:
        signup("accept", reqs[0]["hint"], "--kanidm", "https://kn-accounts:8443", "--token-file", "/state/token", "--id-host", idname)
        r = browser("status", r["request_code"])
        say(r.get("ready"), "accepted: the request code shows the link")
        say(r.get("signed_in"), "the link sets a password, and the person signs in with it")
finally:
    for n in (a, b):
        subprocess.run(SSH + ["S=$(ls /home/*/docker/kiwi/accounts.sh /var/home/*/docker/kiwi/accounts.sh 2>/dev/null | head -1); sudo sh $S delete %s" % n],
                       capture_output=True)
print("%d wrong" % bad)
sys.exit(1 if bad else 0)
