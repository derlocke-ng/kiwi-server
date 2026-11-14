#!/usr/bin/env python3
"""The logins of a network, tried on the services themselves.

`kiwi-server logins` says what to type into each service. This asks every service, through
its own API, whether that user name and password let one in, from a device that is on the
network (its DNS and gateway): this computer, or another one with `--via user@address`. It
also says whether the service's certificate is trusted there. Nothing is changed: a session
it opens is closed again where the service allows it.

    tests/drills/logins.py ~/kiwi/fleet.yaml [machine ...] [--via user@dev-vm]

The passwords travel on the ssh connection's input, never as an argument or into a file
other than a moment's askpass helper in memory (for SFTP); the drill prints none of them.
"""
import argparse
import json
import os
import subprocess
import sys

ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument("fleet")
ap.add_argument("machines", nargs="*")
ap.add_argument("--via", help="user@address of a device on the network to ask from (default: here)")
ap.add_argument("--key", help="ssh key for --via (default: the fleet's admin key)")
args = ap.parse_args()
ks = os.environ.get("KIWI_SERVER", "kiwi-server")
r = subprocess.run([ks, "logins", args.fleet] + args.machines + ["--porcelain"], capture_output=True, text=True)
if r.returncode != 0:
    sys.exit("drill: kiwi-server logins: %s" % r.stderr.strip()[-300:])
hosts = json.loads(r.stdout)["hosts"]

# runs where the device is: plain Python, nothing to install
CHECK = r'''
import json, os, ssl, stat, subprocess, sys, tempfile, urllib.error, urllib.parse, urllib.request
data = json.loads(sys.stdin.readline())
ctx = ssl.create_default_context()

class Stay(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None
import http.cookiejar
jar = http.cookiejar.CookieJar()       # a login page that hands out a cookie first (JDownloader's)
opener = urllib.request.build_opener(Stay, urllib.request.HTTPSHandler(context=ctx), urllib.request.HTTPCookieProcessor(jar))

def ask(url, method="GET", body=None, headers=None, auth=None):
    h = dict(headers or {})
    if auth:
        import base64
        h["Authorization"] = "Basic " + base64.b64encode(("%s:%s" % auth).encode()).decode()
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with opener.open(req, timeout=20) as r:
            return r.status, r.read(200000).decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.read(200000).decode("utf-8", "replace"), dict(e.headers)
    except ssl.SSLCertVerificationError as e:
        return "cert", str(e.verify_message), {}
    except (urllib.error.URLError, OSError) as e:
        return "net", str(getattr(e, "reason", e)), {}

def jpost(url, obj, headers=None):
    return ask(url, "POST", json.dumps(obj).encode(), dict({"Content-Type": "application/json"}, **(headers or {})))

def base(addr):
    p = urllib.parse.urlsplit(addr)
    return "%s://%s" % (p.scheme, p.netloc)

def verdict(code, ok, bad):
    if code in ("cert", "net"):
        return "FAIL", code
    if code in ok:
        return "PASS", code
    if code in bad:
        return "FAIL", code
    return "?", code

def try_one(x):
    m, addr, user, pw = x["module"], x["address"] or "", x["user"] or "", x["password"] or ""
    if not pw:
        return "SKIP", "nothing to type: " + (x["note"] or "no password")[:60]
    b = base(addr) if addr.startswith("https://") else ""
    if m == "cloud":
        code, _t, _h = ask(b + "/remote.php/dav/files/%s/" % urllib.parse.quote(user), "PROPFIND", None, {"Depth": "0"}, (user, pw))
        return verdict(code, (207,), (401,))
    if m == "paperless":
        code, _t, _h = ask(b + "/api/documents/?page_size=1", auth=(user, pw), headers={"Accept": "application/json"})
        return verdict(code, (200,), (401, 403))
    if m == "portainer":
        code, _t, _h = jpost(b + "/api/auth", {"Username": user, "Password": pw})
        return verdict(code, (200,), (401, 422))
    if m == "dns":
        code, t, _h = jpost(b + "/api/auth", {"password": pw})
        if code == 200:
            try:
                sid = json.loads(t)["session"]["sid"]
                ask(b + "/api/auth", "DELETE", headers={"sid": sid})      # the session goes again: Pi-hole keeps few
            except (ValueError, KeyError, TypeError):
                pass
        return verdict(code, (200,), (401,))
    if m == "vpn-server":
        code, _t, _h = jpost(b + "/api/session", {"password": pw})
        return verdict(code, (200, 204), (401,))
    if m == "downloader" and "transmission" in x["label"].lower():
        code, _t, _h = ask(b + "/transmission/rpc", "POST", b"{}", auth=(user, pw))
        return verdict(code, (409, 200), (401,))      # 409: in, and asked for the session id
    if m == "downloader":
        jar.clear()
        ask(b + "/")                                        # the way a browser comes: the cookie the form needs
        ask(b + "/login/")
        code, t, h = ask(b + "/login/login", "POST", urllib.parse.urlencode({"username": user, "password": pw}).encode(),
                         {"Content-Type": "application/x-www-form-urlencoded"})
        if any(c.name == "login_result" and c.value == "INVALID_CREDENTIALS" for c in jar):
            return "FAIL", "incorrect username or password"
        where = {k.lower(): v for k, v in h.items()}.get("location", "")
        if code in (302, 303) and not where.rstrip("/").endswith("login"):
            return "PASS", "%s to %s" % (code, where)
        return "?", "%s to %s" % (code, where or "?")
    if m == "vault":
        code, t, _h = ask(b + "/admin", "POST", urllib.parse.urlencode({"token": pw}).encode(),
                          {"Content-Type": "application/x-www-form-urlencoded"})
        if "invalid admin token" in t.lower():
            return "FAIL", "invalid admin token"
        return verdict(code, (200, 303), (401,))
    if m == "opencloud":
        # its sign-in page's own call: 204 for a wrong password, 200 with who signed in for a right one
        code, t, _h = jpost(b + "/signin/v1/identifier/_/logon", {"params": [user, pw, "1"]},
                            {"Kopano-Konnect-XSRF": "1", "Referer": b + "/signin/v1/identifier"})
        return verdict(code, (200,), (204, 401, 403))
    if m == "sftp":
        p = urllib.parse.urlsplit(addr)
        d = tempfile.mkdtemp(dir=os.environ.get("XDG_RUNTIME_DIR") or None)
        helper = os.path.join(d, "a")
        try:
            fd = os.open(helper, os.O_WRONLY | os.O_CREAT, 0o700)
            with os.fdopen(fd, "w") as fh:
                fh.write("#!/bin/sh\ncat <<'EOF'\n%s\nEOF\n" % pw)
            env = dict(os.environ, SSH_ASKPASS=helper, SSH_ASKPASS_REQUIRE="force", DISPLAY=":0")
            # not -b: a batch file switches password logins off
            r = subprocess.run(["sftp", "-P", str(p.port or 22), "-o", "BatchMode=no", "-o", "StrictHostKeyChecking=no",
                                "-o", "UserKnownHostsFile=/dev/null", "-o", "PubkeyAuthentication=no",
                                "-o", "PreferredAuthentications=password,keyboard-interactive", "-o", "NumberOfPasswordPrompts=1",
                                "%s@%s" % (user, p.hostname)], input="pwd\nbye\n", capture_output=True, text=True, timeout=30, env=env)
        finally:
            for f in (helper,):
                if os.path.exists(f):
                    os.remove(f)
            os.rmdir(d)
        if r.returncode == 0:
            return "PASS", "sftp"
        return "FAIL", (r.stderr.strip().splitlines() or ["sftp failed"])[-1][:60]
    return "?", "no check for " + m

out = []
for h in data:
    for x in h["logins"]:
        try:
            v, why = try_one(x)
        except Exception as e:          # one service must not end the look at the others
            v, why = "?", "%s: %s" % (type(e).__name__, str(e)[:60])
        out.append({"machine": h["name"], "label": x["label"], "address": x["address"], "user": x["user"],
                    "verdict": v, "why": str(why)})
print(json.dumps(out))
'''

payload = json.dumps([{"name": h["name"], "logins": h["logins"]} for h in hosts]) + "\n"
# the check as its first line of input, the logins as the second: nothing on the command line
run_it = "import sys; exec(sys.stdin.readline().encode().decode('unicode_escape'))"
feed = CHECK.encode("unicode_escape").decode() + "\n" + payload
if args.via:
    key = args.key or os.path.join(os.path.dirname(os.path.abspath(args.fleet)), "secrets", "admin_ed25519")
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
            "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-i", key,
            args.via, 'python3 -c "%s"' % run_it]
else:
    argv = [sys.executable, "-c", run_it]
r = subprocess.run(argv, input=feed, capture_output=True, text=True, timeout=900)
if r.returncode != 0 or not r.stdout.strip():
    sys.exit("drill: the check did not run there: %s" % r.stderr.strip()[-400:])
rows = json.loads(r.stdout.strip().splitlines()[-1])
fails = 0
for x in rows:
    fails += x["verdict"] == "FAIL"
    print("%-5s %-8s %-28s %-8s %s" % (x["verdict"], x["machine"], x["label"][:28], (x["user"] or "-")[:8], x["why"]))
print("%d logins tried, %d failed, %d not decided" % (sum(x["verdict"] in ("PASS", "FAIL") for x in rows), fails,
                                                      sum(x["verdict"] == "?" for x in rows)))
sys.exit(1 if fails else 0)
