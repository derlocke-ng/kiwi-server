#!/usr/bin/env python3
"""Drill: the game page, in a real browser. A person of the group signs in and reads a game
server's log; a person outside the group does not get in.

    tests/drills/play-browser.py FLEET ACCOUNTS_ADDRESS ID_NAME PLAY_ADDRESS PLAY_NAME [SERVER]

ID_NAME is the accounts' page (id.<host>), PLAY_NAME the game page (play.<control node>), SERVER
the game server whose Log is clicked (its name on the page, e.g. doom). The machine with the
accounts module makes two throwaway accounts, one in the group `games` (or $PLAY_GROUP), and
their password links; Firefox sets each password, then signs in at the game page. Both accounts
are deleted afterwards. Needs `kiwi-server accounts <fleet> connect <control node> control`, the
control node's `play_page: true`, and the fleet pushed to the control node.
"""
import json
import os
import secrets
import subprocess
import sys

fleet, acc_addr, idname, play_addr, playname = sys.argv[1:6]
server = sys.argv[6] if len(sys.argv) > 6 else ""
group = os.environ.get("PLAY_GROUP", "games")
fd = os.path.dirname(os.path.abspath(fleet))
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "UserKnownHostsFile=" + os.path.join(fd, "secrets", "known_hosts"),
       "-i", os.path.join(fd, "secrets", "admin_ed25519"), "core@" + acc_addr]
SCRIPT = "S=$(ls /home/*/docker/kiwi/accounts.sh /var/home/*/docker/kiwi/accounts.sh 2>/dev/null | head -1); sudo sh $S "


def accounts(*words):
    r = subprocess.run(SSH + [SCRIPT + " ".join(words)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout.strip().splitlines()[-1])


tag = secrets.token_hex(3)
inside, outside = "play%s" % tag, "out%s" % tag
made = {n: accounts("create", n, "Play-Drill") for n in (inside, outside)}
accounts("member", group, inside, "on")
pw = {n: "kiwi-%s-%s" % (secrets.token_urlsafe(9), secrets.token_urlsafe(9)) for n in (inside, outside)}
shots = os.path.expanduser("~/kiwi-dev/browser")
os.makedirs(shots, exist_ok=True)
inner = r'''
import json, sys
from playwright.sync_api import sync_playwright
idname, playname, server = sys.argv[1:4]
people = json.loads(sys.argv[4])
out = {}
with sync_playwright() as p:
    b = p.firefox.launch()
    for who, (link, pw) in people.items():
        pg = b.new_page(ignore_https_errors=True)
        pg.goto(link, wait_until="networkidle")
        pg.get_by_text("Add Password").click(); pg.wait_for_timeout(1200)
        pg.fill("#new-password", pw); pg.fill("#new-password-check", pw)
        pg.get_by_role("button", name="Submit").click(); pg.wait_for_timeout(1500)
        pg.get_by_role("button", name="Save Changes").click(); pg.wait_for_timeout(2000)
        c = b.new_context(ignore_https_errors=True)
        q = c.new_page()
        q.goto("https://%s/" % playname, wait_until="networkidle")
        q.screenshot(path="/shots/play-1-%s-start.png" % who)
        q.get_by_text("Sign in", exact=True).first.click()
        q.wait_for_load_state("networkidle"); q.wait_for_timeout(1500)
        res = {"at_accounts": idname in q.url}
        q.fill("input[name=username], #username", who); q.keyboard.press("Enter"); q.wait_for_timeout(1500)
        q.fill("input[type=password]", pw); q.keyboard.press("Enter"); q.wait_for_timeout(3000)
        for word in ("Proceed", "Allow", "Accept", "Continue"):        # a consent page, the first time
            btn = q.get_by_role("button", name=word)
            if btn.count():
                btn.first.click(); q.wait_for_timeout(3000); break
        q.wait_for_load_state("networkidle"); q.wait_for_timeout(2000)
        q.screenshot(path="/shots/play-2-%s-back.png" % who, full_page=True)
        res["url"], res["text"] = q.url, q.inner_text("body")[:1500]
        if server and playname in q.url and "Sign out" in res["text"]:
            row = q.locator("tr", has_text=server).first
            row.get_by_role("button", name="Log").click()
            q.wait_for_load_state("networkidle"); q.wait_for_timeout(6000); q.reload(); q.wait_for_timeout(1500)
            q.screenshot(path="/shots/play-3-%s-log.png" % who, full_page=True)
            res["log_url"], res["log"] = q.url, q.inner_text("body")[-1500:]
        out[who] = res
        c.close()
    b.close()
print(json.dumps(out))
'''
with open(os.path.join(shots, "play-inner.py"), "w") as fh:
    fh.write(inner)
people = {n: (made[n]["link"], pw[n]) for n in (inside, outside)}
r = subprocess.run(["podman", "run", "--rm", "--add-host", "%s:%s" % (idname, acc_addr), "--add-host", "%s:%s" % (playname, play_addr),
                    "-v", "%s:/shots:z" % shots, "mcr.microsoft.com/playwright/python:v1.49.0-noble", "sh", "-c",
                    "pip install -q playwright==1.49.0 >/dev/null 2>&1; python3 /shots/play-inner.py \"$@\"", "x",
                    idname, playname, server, json.dumps(people)], capture_output=True, text=True)
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


a, o = res.get(inside, {}), res.get(outside, {})
say(a.get("at_accounts"), "Sign in leads to the accounts' page")
say(playname in a.get("url", "") and inside in a.get("text", "") and "Sign out" in a.get("text", ""),
    "a member of %s is back at the game page, signed in: %s" % (group, a.get("url", "")))
if server:
    say("log?job=" in a.get("log_url", "") and "service" in a.get("log", ""), "Log of %s shows what the machine said" % server)
say("Sign out" not in o.get("text", "") and not ("Game servers" in o.get("text", "") and outside in o.get("text", "")),
    "someone outside the group does not get in (%s)" % (o.get("text", "").strip().splitlines() or ["?"])[-1][:120])
for n in (inside, outside):
    subprocess.run(SSH + [SCRIPT + "delete %s" % n], capture_output=True)
print("%d wrong" % bad)
sys.exit(1 if bad else 0)
