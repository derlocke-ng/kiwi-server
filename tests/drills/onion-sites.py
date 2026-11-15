#!/usr/bin/env python3
"""Which sites work as hidden services? Every hidden service of a network, opened through Tor.

    tests/drills/onion-sites.py ~/kiwi/fleet.yaml --master core@5.45.103.177 [--master-port 41022] [service.machine ...]

For each hidden service (`kiwi-server onion`):

1. its front page through Tor, following where it sends the visitor: does the visitor stay
   at the .onion address? (A service that sends them to its name on the VPN is no use to a
   stranger.)
2. the files the page asks for (scripts, styles, pictures): do they come from that address?
3. what an app in the browser is told about where it lives (config files the app fetches):
   the name on the VPN there means a stranger's browser is sent to a name it cannot reach.
4. its login form, when the page has one: sent the way a browser at that address sends it
   (its Origin and Referer), with a user that does not exist. A service that answers "wrong
   password" takes logins from there; one that refuses the form itself (403, a CSRF or
   origin error) does not, whatever its front page looks like.

It reads, and tries one login that cannot succeed; nothing is changed. Tor runs on the master,
in a container that is removed again; this computer reaches it through ssh, so no Tor traffic
leaves from here. The answer is a table to decide by which sites a module marks as usable
over Tor.
"""
import argparse
import html
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.parse

import signal
signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))      # stopped from outside: the Tor on the master is still removed
ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument("fleet")
ap.add_argument("only", nargs="*", help="service.machine, as `kiwi-server onion` lists them (default: all)")
ap.add_argument("--master", required=True, help="user@address of the master, for ssh")
ap.add_argument("--master-port", default="22")
ap.add_argument("--logins", action="store_true",
                help="also log in through Tor where the network has a login for the service (kiwi-server logins): "
                     "Nextcloud, Forgejo and Paperless by their APIs; Immich's login is asked with a user that does not exist")
args = ap.parse_args()
ks = os.environ.get("KIWI_SERVER", "kiwi-server")
fdir = os.path.dirname(os.path.abspath(args.fleet))
r = subprocess.run([ks, "onion", args.fleet, "--porcelain"], capture_output=True, text=True)
try:
    rows = json.loads(r.stdout)["services"]
except (ValueError, KeyError):
    sys.exit("drill: kiwi-server onion: %s" % (r.stderr or r.stdout).strip()[-200:])
rows = [x for x in rows if not args.only or "%s.%s" % (x["service"], x["host"]) in args.only]
for x in rows:
    if x["clients"]:
        print("SKIP  %-18s only for %s: a key would be needed" % ("%s.%s" % (x["service"], x["host"]), ", ".join(x["clients"])))
rows = [x for x in rows if not x["clients"]]
if not rows:
    sys.exit("drill: no hidden service to try")
known = {}          # site -> the login the network has for it: kept here, printed nowhere
if args.logins:
    r = subprocess.run([ks, "logins", args.fleet, "--porcelain"], capture_output=True, text=True)
    try:
        for h in json.loads(r.stdout)["hosts"]:
            for lg in h["logins"]:
                site = urllib.parse.urlsplit(lg.get("address") or "").hostname
                if site and lg.get("password"):
                    known.setdefault(site, lg)
    except (ValueError, KeyError):
        sys.exit("drill: kiwi-server logins: %s" % (r.stderr or r.stdout).strip()[-200:])


def api_login(x, addr):
    """A real login through Tor by the service's own API, or None when there is none to try."""
    import base64
    lg, first = known.get(x["site"]), x["site"].split(".", 1)[0]
    basic = {"Authorization": "Basic " + base64.b64encode(("%s:%s" % (lg["user"], lg["password"])).encode()).decode()} if lg else {}
    base = "http://%s" % addr
    if lg and lg["module"] == "cloud":
        c, _h, _b = fetch("PROPFIND", base + "/remote.php/dav/files/%s/" % urllib.parse.quote(lg["user"]), dict(basic, Depth="0"))
        return c == 207, "its files open with the network's login (WebDAV %s)" % c
    if lg and lg["module"] == "git":
        c, _h, _b = fetch("GET", base + "/api/v1/user", dict(basic, Accept="application/json"))
        return c == 200, "the network's login is taken by its API (%s)" % c
    if lg and lg["module"] == "paperless":
        c, _h, _b = fetch("GET", base + "/api/documents/?page_size=1", dict(basic, Accept="application/json"))
        return c == 200, "the network's login is taken by its API (%s)" % c
    if first == "immich":            # people make their own account there: the login itself is asked, with nobody
        c, _h, b = fetch("POST", base + "/api/auth/login", {"Content-Type": "application/json", "Origin": base, "Accept": "application/json"},
                         json.dumps({"email": "kiwi-drill-nobody@example.org", "password": "no-such-password"}).encode())
        return c == 401, "its login answers a request from this address (a wrong password: %s)" % c
    return None
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
       "-o", "LogLevel=ERROR", "-o", "UserKnownHostsFile=%s" % os.path.join(fdir, "secrets", "known_hosts"),
       "-i", os.path.join(fdir, "secrets", "admin_ed25519"), "-p", str(args.master_port)]
PORT = 19151


def fetch(method, url, headers=None, body=b"", timeout=60):
    """(status, headers, body) of one request through Tor: the name goes to the proxy, which finds the hidden service."""
    u = urllib.parse.urlsplit(url)
    global tunnel
    if tunnel.poll() is not None:           # the connection to the master dropped: once more
        tunnel = subprocess.Popen(SSH + ["-N", "-L", "127.0.0.1:%d:127.0.0.1:9150" % PORT, args.master])
        time.sleep(4)
    try:
        s = socket.create_connection(("127.0.0.1", PORT), 20)
    except OSError:
        return 0, {}, b""
    try:
        s.settimeout(timeout)
        s.sendall(b"\x05\x01\x00"); s.recv(2)
        name = u.hostname.encode()
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(name)]) + name + (u.port or 80).to_bytes(2, "big"))
        rep = s.recv(10)
        if len(rep) < 2 or rep[1] != 0:
            return 0, {}, b""
        h = {"Host": u.netloc, "Connection": "close", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0",
             "Accept": "text/html,*/*;q=0.8"}
        h.update(headers or {})
        if body:
            h["Content-Length"] = str(len(body))
        s.sendall(("%s %s HTTP/1.1\r\n%s\r\n\r\n" % (method, (u.path or "/") + ("?" + u.query if u.query else ""),
                                                    "\r\n".join("%s: %s" % kv for kv in h.items()))).encode() + body)
        raw, want = b"", None
        while len(raw) < 3000000:
            d = s.recv(65536)
            if not d:
                break
            raw += d
            if want is None and b"\r\n\r\n" in raw:       # a length that was promised: no waiting for the line to close
                m = re.search(rb"(?i)\r\ncontent-length:\s*(\d+)", raw.split(b"\r\n\r\n", 1)[0])
                want = len(raw.split(b"\r\n\r\n", 1)[0]) + 4 + int(m.group(1)) if m else -1
            if want and want > 0 and len(raw) >= want:
                break
    except OSError:
        return 0, {}, b""
    finally:
        s.close()
    head, _, rest = raw.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    try:
        code = int(lines[0].split()[1])
    except (IndexError, ValueError):
        return 0, {}, b""
    hd = {}
    for line in lines[1:]:
        k, _, v = line.partition(":")
        hd.setdefault(k.strip().lower(), []).append(v.strip())
    if "chunked" in " ".join(hd.get("transfer-encoding", [])).lower():
        out, i = b"", 0
        while i < len(rest):
            j = rest.find(b"\r\n", i)
            try:
                n = int(rest[i:j].split(b";")[0], 16)
            except ValueError:
                break
            if n == 0:
                break
            out += rest[j + 2:j + 2 + n]
            i = j + 2 + n + 2
        rest = out
    return code, hd, rest


def page(url, jar):
    """Follow the service from `url`: (status, the address it ends at, body)."""
    for _hop in range(8):
        code, hd, body = fetch("GET", url, {"Cookie": "; ".join("%s=%s" % kv for kv in jar.items())} if jar else None)
        for c in hd.get("set-cookie", []):
            k, _, v = c.split(";", 1)[0].partition("=")
            jar[k.strip()] = v.strip()
        if code in (301, 302, 303, 307, 308) and hd.get("location"):
            url = urllib.parse.urljoin(url, hd["location"][0])
            continue
        return code, url, body.decode("utf-8", "replace")
    return 0, url, ""


def login_form(text):
    """(action, method, fields) of the first form with a password in it, or None."""
    for m in re.finditer(r"<form\b([^>]*)>(.*?)</form>", text, re.S | re.I):
        inside = m.group(2)
        if not re.search(r'type=["\']?password', inside, re.I):
            continue
        attr = dict((k.lower(), html.unescape(v)) for k, _q, v in re.findall(r'([\w-]+)\s*=\s*(["\'])(.*?)\2', m.group(1)))
        fields = {}
        for tag in re.findall(r"<input\b[^>]*>", inside, re.I):
            a = dict((k.lower(), html.unescape(v)) for k, _q, v in re.findall(r'([\w-]+)\s*=\s*(["\'])(.*?)\2', tag))
            if not a.get("name"):
                continue
            kind = a.get("type", "text").lower()
            fields[a["name"]] = ("no-such-password-%d" % os.getpid() if kind == "password" else a.get("value", "")
                                 if kind in ("hidden", "submit") else "kiwi-drill-nobody" if kind in ("text", "email") else a.get("value", ""))
        return attr.get("action", ""), attr.get("method", "post").upper(), fields
    return None


up = """D=$(mktemp -d); mkdir -p $D/data
printf 'SocksPort 127.0.0.1:9150\\nDataDirectory /data\\n' > $D/torrc
chown -R 101:102 $D/data
podman rm -f kiwi-drill-tor >/dev/null 2>&1
podman run -d --rm --name kiwi-drill-tor --network host --entrypoint su-exec -v $D/torrc:/torrc:ro,z \\
    -v $D/data:/data:z docker.io/dockurr/tor tor tor -f /torrc >/dev/null
for i in $(seq 30); do podman logs kiwi-drill-tor 2>&1 | grep -q 'Bootstrapped 100' && break; sleep 5; done
podman logs kiwi-drill-tor 2>&1 | grep -c 'Bootstrapped 100'
"""
ready = subprocess.run(SSH + [args.master, "sudo sh -s"], input=up, capture_output=True, text=True, timeout=400).stdout.strip()
if not ready or ready.splitlines()[-1] == "0":
    subprocess.run(SSH + [args.master, "sudo podman rm -f kiwi-drill-tor >/dev/null 2>&1"])
    sys.exit("drill: Tor on the master did not get ready")
tunnel = subprocess.Popen(SSH + ["-N", "-L", "127.0.0.1:%d:127.0.0.1:9150" % PORT, args.master])
bad = 0
try:
    time.sleep(4)
    for x in rows:
        name, addr = "%s.%s" % (x["service"], x["host"]), x["address"]
        jar, code, where, text = {}, 0, "", ""
        for _try in range(4):               # a new hidden service takes a few minutes to be known
            code, where, text = page("http://%s/" % addr, jar)
            if code:
                break
            time.sleep(30)
        why, notes = [], []
        stays = urllib.parse.urlsplit(where).hostname == addr
        if not (200 <= code < 300 or code == 401):
            why.append("the page answers %s" % (code or "nothing"))
        if code and not stays:
            why.append("it sends the visitor to %s" % urllib.parse.urlsplit(where).hostname)
        files = sorted(set(re.findall(r'(?:src|href)="([^"#]+\.(?:js|mjs|css|png|svg|ico|woff2?)(?:\?[^"]*)?)"', text)))[:8]
        away = sorted({urllib.parse.urlsplit(urllib.parse.urljoin(where, f)).hostname for f in files} - {addr})
        if away:
            why.append("files come from %s" % ", ".join(str(a) for a in away))
        broken = [f for f in files if urllib.parse.urlsplit(urllib.parse.urljoin(where, f)).hostname == addr
                  and not 200 <= fetch("GET", urllib.parse.urljoin(where, f))[0] < 400]
        if broken:
            why.append("%d of its %d files do not load" % (len(broken), len(files)))
        form = login_form(text) if stays else None
        for path in ("/login", "/user/login", "/accounts/login/", "/index.php/login") if stays and not form and code else ():
            c1, w1, t1 = page("http://%s%s" % (addr, path), jar)       # the login is on a page of its own
            if 200 <= c1 < 300 and urllib.parse.urlsplit(w1).hostname == addr and login_form(t1):
                form, where = login_form(t1), w1
                break
        if form:
            action, method, fields = form
            target = urllib.parse.urljoin(where, action or where)
            origin = "http://%s" % addr
            c2, h2, b2 = fetch(method, target, {"Content-Type": "application/x-www-form-urlencoded", "Origin": origin, "Referer": where,
                                                "Cookie": "; ".join("%s=%s" % kv for kv in jar.items())},
                               urllib.parse.urlencode(fields).encode())
            said = b2.decode("utf-8", "replace").lower()
            refused = c2 in (0, 403, 419) or (c2 >= 400 and ("csrf" in said or "origin" in said or "forbidden" in said))
            if refused:
                why.append("its login form is refused from this address (%s%s)" % (c2 or "no answer", ", CSRF" if "csrf" in said else ""))
            else:
                notes.append("its login form is taken (a wrong password answers %s)" % c2)
        elif stays and 200 <= code < 300:
            # an app in the browser: what the server tells it about where it lives. The proxy rewrites the
            # VPN's name in pages, not in what an app fetches for itself: an app sent there is lost to a stranger
            told = []
            for path in ("/config.json", "/api/config", "/api/server/config", "/api/server-info/config", "/status.php",
                         "/manifest.json", "/.well-known/openid-configuration"):
                c3, h3, b3 = fetch("GET", "http://%s%s" % (addr, path), {"Accept": "application/json"})
                # (what an app reads, not a page that answers every path with itself)
                if 200 <= c3 < 300 and x["site"].encode() in b3 and "json" in " ".join(h3.get("content-type", [])).lower():
                    told.append(path)
            if told:
                why.append("its app is told the name on the VPN (%s)" % ", ".join(told))
            else:
                notes.append("no login form in the page (an app in the browser, or none needed); its app is not sent to the VPN's name")
        if args.logins and stays:
            tried = api_login(x, addr)
            if tried is not None:
                (notes if tried[0] else why).append(tried[1] if tried[0] else "logging in through Tor did not work: " + tried[1])
        ok = not why
        bad += not ok
        print("%-5s %-18s %s" % ("PASS" if ok else "FAIL", name, "; ".join(why) if why else "page %s, %d files from the address; %s"
                                 % (code, len(files), "; ".join(notes) or "-")), flush=True)
finally:
    tunnel.terminate()
    subprocess.run(SSH + [args.master, "sudo podman rm -f kiwi-drill-tor >/dev/null 2>&1"])
print("%d of %d would not do as a hidden service" % (bad, len(rows)))
sys.exit(1 if bad else 0)
