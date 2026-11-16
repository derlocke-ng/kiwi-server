#!/usr/bin/env python3
"""Drill: strangers get Jabber addresses at Jabber's sign-up page, in a real browser.

    tests/drills/jabber-join-browser.py FLEET NODE_ADDRESS PUBLIC_NAME INTERNAL_NAME

The node runs xmpp with `invites` and `requests` (and two instances: the public one at PUBLIC_NAME,
the internal one at INTERNAL_NAME, reached at NODE_ADDRESS). Firefox:
1. at https://PUBLIC_NAME/join, with an invite code (made as the control page makes it), chooses a
   name and a password: the address is made;
2. at https://INTERNAL_NAME/join asks for an address; the request is accepted (as on the control
   page); the request code shows a password once, and not a second time.
ejabberd itself is asked whether both passwords are right. The addresses are deleted afterwards.
"""
import json
import os
import secrets
import shlex
import subprocess
import sys

fleet, node, public, internal = sys.argv[1:5]
fd = os.path.dirname(os.path.abspath(fleet))
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "UserKnownHostsFile=" + os.path.join(fd, "secrets", "known_hosts"),
       "-i", os.path.join(fd, "secrets", "admin_ed25519"), "core@" + node]


def kiwi(*args):
    r = subprocess.run(["kiwi-server", "accounts", fleet, "--jabber"] + list(args), capture_output=True, text=True)
    if r.returncode:
        sys.exit("drill: kiwi-server accounts --jabber %s: %s" % (" ".join(args), r.stderr.strip()[-300:]))
    return json.loads(r.stdout.strip().splitlines()[-1])


def ejabberd(container, *args):
    return subprocess.run(SSH + ["sudo podman exec %s ejabberdctl %s" % (container, " ".join(shlex.quote(a) for a in args))],
                          capture_output=True, text=True).returncode


code = kiwi("invite", "--note", "drill")["code"]
a, b = "jj%s" % secrets.token_hex(2), "ja%s" % secrets.token_hex(2)
pw = "kiwi-%s" % secrets.token_urlsafe(12)
shots = os.path.expanduser("~/kiwi-dev/browser")
os.makedirs(shots, exist_ok=True)
inner = r'''
import json, sys
from playwright.sync_api import sync_playwright
step, host, code, name, pw = sys.argv[1:6]
out = {}
with sync_playwright() as p:
    br = p.firefox.launch(); pg = br.new_page(ignore_https_errors=True)
    if step == "invite":
        pg.goto("https://%s/" % host, wait_until="networkidle"); out["front_door"] = pg.url
        pg.screenshot(path="/shots/jabber-1-page.png", full_page=True)
        pg.fill("#i-code", code); pg.fill("#i-name", name); pg.fill("#i-pw", pw)
        pg.click("form[action='/join/invite'] button"); pg.wait_for_load_state("networkidle")
        pg.screenshot(path="/shots/jabber-2-made.png"); out["text"] = pg.inner_text("body")[:600]
    elif step == "ask":
        pg.goto("https://%s/join" % host, wait_until="networkidle")
        pg.fill("#a-name", name); pg.fill("#a-why", "A drill of Jabber's sign-up page, asking for an address")
        pg.click("form[action='/join/ask'] button"); pg.wait_for_load_state("networkidle")
        t = pg.inner_text("body"); out["code"] = next((w for w in t.split() if w.startswith("ask-")), "")
        pg.screenshot(path="/shots/jabber-3-asked.png")
    else:
        for n in (1, 2):
            pg.goto("https://%s/join/status?code=%s" % (host, code), wait_until="networkidle")
            pg.screenshot(path="/shots/jabber-4-status-%d.png" % n)
            out["text%d" % n] = pg.inner_text("body")[:600]
    br.close()
print(json.dumps(out))
'''
with open(os.path.join(shots, "jabber-inner.py"), "w") as fh:
    fh.write(inner)


def browser(step, host, arg, name):
    r = subprocess.run(["podman", "run", "--rm", "--add-host", "%s:%s" % (internal, node), "-v", "%s:/shots:z" % shots,
                        "mcr.microsoft.com/playwright/python:v1.49.0-noble", "sh", "-c",
                        "pip install -q playwright==1.49.0 >/dev/null 2>&1; python3 /shots/jabber-inner.py \"$@\"", "x",
                        step, host, arg, name, pw], capture_output=True, text=True)
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
    r = browser("invite", public, code, a)
    say(r.get("front_door", "").endswith("/join"), "the public Jabber name opens the sign-up page")
    say("%s@%s" % (a, public) in r.get("text", ""), "an invite code makes %s@%s" % (a, public))
    say(ejabberd("kn-xmpp-public", "check_password", a, public, pw) == 0, "ejabberd takes the password the person chose")
    r = browser("ask", internal, "", b)
    say(bool(r.get("code")), "a request on the VPN's name waits, with a code")
    waiting = [x for x in kiwi("requests") if x["name"] == b and x["state"] == "waiting"]
    say(bool(waiting), "the request is there for the people who run the network")
    if waiting:
        kiwi("accept", waiting[0]["hint"])
        s = browser("status", internal, r["code"], b)
        made = next((w for w in s.get("text1", "").split() if w.count("-") == 2 and len(w) > 20 and not w.startswith("ask-")), "")
        say("%s@%s" % (b, internal) in s.get("text1", "") and bool(made), "accepted: the address and a password, shown once")
        say(made and made not in s.get("text2", ""), "and not a second time")
        say(made and ejabberd("kn-xmpp", "check_password", b, internal, made) == 0, "ejabberd takes that password")
finally:
    ejabberd("kn-xmpp-public", "unregister", a, public)
    ejabberd("kn-xmpp", "unregister", b, internal)
print("%d wrong" % bad)
sys.exit(1 if bad else 0)
