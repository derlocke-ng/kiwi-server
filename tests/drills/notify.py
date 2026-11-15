#!/usr/bin/env python3
"""A service stops. Does the network say so, and say when it works again?

The fleet's `notify:` is pointed at a listener: a small web server on a device at home that
keeps what is posted to it, as a push service (ntfy) and as a webhook. Then a service is
stopped on a machine that itself stays fine. The drill watches for the message that it does
not answer, looks at the status page, starts the service and watches for "answers again".

    tests/drills/notify.py ~/kiwi/fleet.yaml papaya quince --unit kn-vault.service \\
        --site vault.quince.home --listener user@192.168.2.104 [--limit 40]

`control` is the control node, `victim` the machine whose service is stopped. The listener is
reached with the fleet's admin key and must be on the network (it asks the status page). The
control node looks every ten minutes, so a round takes about half an hour. At the end the
fleet file is as it was, byte for byte, the control node has it again, and the listener is gone.
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
import time

from drill import kiwi, say, setup, wait
from kiwiserver import fleettext, util

extra = argparse.ArgumentParser()
extra.add_argument("--unit", required=True, help="the service to stop on the victim, e.g. kn-vault.service")
extra.add_argument("--site", required=True, help="the name it serves, e.g. vault.quince.home")
extra.add_argument("--listener", required=True, help="user@address of a device at home that the control node reaches")
extra.add_argument("--port", type=int, default=18999)
extra.add_argument("--limit", type=float, default=40, help="minutes to wait for each message")
opts, rest = extra.parse_known_args()
sys.argv = [sys.argv[0]] + rest
args, fleet, m = setup(__doc__.splitlines()[0], roles=("control", "victim"))
control, victim = m["control"], m["victim"]
key = os.path.join(os.path.dirname(os.path.abspath(args.fleet)), "secrets", "admin_ed25519")
address = opts.listener.rsplit("@", 1)[-1]
kept = "/tmp/kiwi-notify-drill.jsonl"
SERVER = r'''
import http.server, json, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace")
        with open(sys.argv[2], "a") as f:
            f.write(json.dumps({"path": self.path, "title": self.headers.get("Title") or "", "body": body}) + "\n")
        self.send_response(200); self.end_headers()
    def log_message(self, *a):
        pass
http.server.HTTPServer(("0.0.0.0", int(sys.argv[1])), H).serve_forever()
'''


def there(script, timeout=60):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
                        "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-i", key,
                        opts.listener, script], capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def posted():
    rc, out = there("cat %s 2>/dev/null" % kept)
    rows = []
    for line in out.splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def told(words):
    """The posts (ntfy, webhook) that speak of the site with these words."""
    hits = [r for r in posted() if opts.site in r["body"] and words in r["body"]]
    return {("webhook" if r["path"].endswith("/hook") else "ntfy") for r in hits}


fails = 0


def check(ok, what):
    global fails
    say("PASS" if ok else "FAIL", what)
    fails += not ok


original = util.read_text(args.fleet)                      # never shown: put back as it was at the end
pid = ""
try:
    rc, pid = there("rm -f %s; nohup python3 -c %s %d %s >/dev/null 2>&1 & echo $!" % (kept, shlex.quote(SERVER), opts.port, kept))
    check(rc == 0 and pid.isdigit(), "a listener runs on %s, port %d" % (address, opts.port))
    url = "http://%s:%d" % (address, opts.port)
    changed = fleettext.put(original, ["defaults", "notify"], {"ntfy": {"url": url + "/kiwi-drill"}, "webhook": {"url": url + "/hook"},
                                                               "after": 1})
    check(changed is not None, "the fleet's notify: points at it (after: 1 minute)")
    if changed is None:
        raise SystemExit(1)
    util.write_text(args.fleet, changed, 0o600)
    rc, out = kiwi("control", "push", args.fleet)
    check(rc == 0, "the control node has it")
    rc, out = kiwi("notify", args.fleet, "--test")
    time.sleep(2)
    check(rc == 0 and len([r for r in posted() if "test message" in r["body"]]) == 2, "a test message arrives as a push and at the webhook")
    code, _o = victim.run("systemctl stop %s" % shlex.quote(opts.unit))
    t0 = time.time()
    check(code == 0, "%s is stopped on %s (the machine itself stays fine)" % (opts.unit, victim.name))
    took = wait(lambda: told("does not answer") == {"ntfy", "webhook"}, opts.limit * 60, every=30)
    check(took is not None, "the message that %s does not answer came, on both ways (%s)" % (
        opts.site, "after %d minutes" % round((time.time() - t0) / 60) if took is not None else "not within %d minutes" % opts.limit))
    for r in [r for r in posted() if opts.site in r["body"]][:2]:
        say("   ", r["path"], "|", (r["title"] or r["body"])[:150].replace("\n", " / "))
    rc, page = there("curl -sk --max-time 20 https://status.%s/" % control.host.hostname)
    part = page.split(opts.site, 1)[0][-400:] if opts.site in page else ""
    check("does not answer" in part and "csrf" not in page, "the status page, asked without a login from %s, says that it does not answer" % address)
    code, _o = victim.run("systemctl start %s" % shlex.quote(opts.unit))
    t1 = time.time()
    check(code == 0, "%s is started again" % opts.unit)
    took = wait(lambda: told("answers again") == {"ntfy", "webhook"}, opts.limit * 60, every=30)
    check(took is not None, "the message that it answers again came (%s)" % (
        "after %d minutes" % round((time.time() - t1) / 60) if took is not None else "not within %d minutes" % opts.limit))
    for r in [r for r in posted() if "answers again" in r["body"]][:1]:
        say("   ", r["path"], "|", (r["title"] or r["body"])[:150].replace("\n", " / "))
finally:
    victim.run("systemctl start %s" % shlex.quote(opts.unit))
    util.write_text(args.fleet, original, 0o600)
    rc, _out = kiwi("control", "push", args.fleet)
    say("the fleet file is as it was%s" % ("" if rc == 0 else " (the control node did not take it back: kiwi-server control push)"))
    if pid.isdigit():
        there("kill %s; rm -f %s" % (pid, kept))
say("%d checks failed" % fails if fails else "the network said that it was wrong, and that it was fine again")
sys.exit(1 if fails else 0)
