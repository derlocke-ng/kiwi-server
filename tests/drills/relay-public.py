#!/usr/bin/env python3
"""The public relay, tried from outside as a stranger's app reaches it.

For a machine that runs the nostr-relay module under a public name (relay_domain). Two keys:
one the fleet file lists (nostr-relay.write_pubkeys), one nobody knows. From this computer,
over the Internet: the relay says who it is, the listed key posts and reads its note back,
the unknown key is refused (also after proving who it is), and the note is deleted again.
With members: approved also: a key the admin puts on the members' list may post, and no longer
when it is taken off (the list is left as it was, signed by the drill's key).

    tests/drills/relay-public.py ~/kiwi/fleet.yaml sorbet key                # makes the drill's key, prints its public half
    (put that into the fleet file: nostr-relay: write_pubkeys: [...], and apply the machine)
    tests/drills/relay-public.py ~/kiwi/fleet.yaml sorbet --public 5.45.103.177

--public is the address the name is tried at (never taken from DNS: a wrong record must not
send the drill to another machine). The drill's key lives in the fleet's secrets folder
(drill-relay.key), which the secret store keeps in memory. Nothing but Python is needed.
"""
import argparse
import base64
import hashlib
import json
import os
import secrets
import socket
import ssl
import struct
import sys
import time

import yaml

P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
     0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)
fails = 0


def say(ok, text):
    global fails
    fails += 0 if ok else 1
    print("%s %s %s" % (time.strftime("%H:%M:%S"), "PASS" if ok else "FAIL", text), flush=True)


# --- BIP-340 (Schnorr signatures on secp256k1), as its reference code does it: slow and plain
def add(a, b):
    if a is None or b is None:
        return a or b
    if a[0] == b[0] and a[1] != b[1]:
        return None
    lam = (3 * a[0] * a[0] * pow(2 * a[1], P - 2, P) if a == b else (b[1] - a[1]) * pow(b[0] - a[0], P - 2, P)) % P
    x = (lam * lam - a[0] - b[0]) % P
    return x, (lam * (a[0] - x) - a[1]) % P


def mul(point, k):
    out = None
    for i in range(256):
        if (k >> i) & 1:
            out = add(out, point)
        point = add(point, point)
    return out


def tagged(tag, data):
    t = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(t + t + data).digest()


def b32(i):
    return i.to_bytes(32, "big")


def public(secret):
    return b32(mul(G, int.from_bytes(secret, "big"))[0])


def sign(message, secret, aux):
    d0 = int.from_bytes(secret, "big")
    point = mul(G, d0)
    d = d0 if point[1] % 2 == 0 else N - d0
    t = b32(d ^ int.from_bytes(tagged("BIP0340/aux", aux), "big"))
    k0 = int.from_bytes(tagged("BIP0340/nonce", t + b32(point[0]) + message), "big") % N
    r = mul(G, k0)
    k = N - k0 if r[1] % 2 else k0
    e = int.from_bytes(tagged("BIP0340/challenge", b32(r[0]) + b32(point[0]) + message), "big") % N
    return b32(r[0]) + b32((k + e * d) % N)


def selftest():
    """The first test vector of BIP-340: a signature made wrongly would only look like a refusal."""
    secret = b32(3)
    want = ("E907831F80848D1069A5371B402410364BDF1C5F8307B0084C55F1CE2DCA8215"
            "25F66A4A85EA8B71E482A74F382D2CE5EBEEE8FDB2172F477DF4900D310536C0")
    return (public(secret).hex().upper() == "F9308A019258C31049344F85F89D5229B531C845836F99B08601F113BCE036F9"
            and sign(bytes(32), secret, bytes(32)).hex().upper() == want)


def event(secret, kind, content, tags=(), back=0):
    """`back`: dated that many seconds ago (a phone's slow clock; a sealed message is dated back on purpose)."""
    pub = public(secret).hex()
    body = [0, pub, int(time.time()) - back, kind, [list(t) for t in tags], content]
    eid = hashlib.sha256(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()).digest()
    return {"id": eid.hex(), "pubkey": pub, "created_at": body[2], "kind": kind, "tags": body[4], "content": content,
            "sig": sign(eid, secret, secrets.token_bytes(32)).hex()}


# --- a WebSocket, as much of one as a relay needs
class Socket:
    def __init__(self, address, name, timeout=20):
        raw = socket.create_connection((address, 443), timeout=timeout)
        self.s = ssl.create_default_context().wrap_socket(raw, server_hostname=name)      # the world's CAs, the public name
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        self.s.sendall(("GET / HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (name, key)).encode())
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            more = self.s.recv(4096)
            if not more:
                raise OSError("the relay closed the connection")
            self.buf += more
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise OSError("no WebSocket there: %s" % head.split(b"\r\n")[0].decode(errors="replace"))
        self.issuer = dict(x[0] for x in self.s.getpeercert().get("issuer", ())).get("organizationName", "")

    def send(self, obj, opcode=1):
        data = obj if isinstance(obj, bytes) else json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode()
        mask = secrets.token_bytes(4)
        n = len(data)
        head = bytes([0x80 | opcode]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n)
                                         if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n))
        self.s.sendall(head + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(data)))

    def _take(self, n):
        while len(self.buf) < n:
            more = self.s.recv(65536)
            if not more:
                raise OSError("the relay closed the connection")
            self.buf += more
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def read(self, timeout=15):
        """The next message as JSON, or None when none comes in time."""
        self.s.settimeout(timeout)
        try:
            while True:
                a, b = self._take(2)
                n = b & 0x7F
                n = struct.unpack(">H", self._take(2))[0] if n == 126 else struct.unpack(">Q", self._take(8))[0] if n == 127 else n
                data = self._take(n)
                if a & 0x0F == 9:
                    self.send(data, opcode=10)
                elif a & 0x0F == 8:
                    raise OSError("the relay closed the connection")
                elif a & 0x0F == 1:
                    return json.loads(data.decode())
        except (socket.timeout, TimeoutError):
            return None

    def wait(self, kind, eid=None, timeout=15):
        """The next ["OK"|"EVENT"|"EOSE"|"AUTH", …] of that kind (for that id), others kept in .seen."""
        end = time.time() + timeout
        while time.time() < end:
            m = self.read(max(1, end - time.time()))
            if m is None:
                return None
            if m and m[0] == "AUTH":
                self.challenge = m[1]
            if m and m[0] == kind and (eid is None or eid in (m[1], (m[2] or {}).get("id") if kind == "EVENT" and len(m) > 2 else None)):
                return m
        return None

    challenge = ""

    def close(self):
        try:
            self.send(b"", opcode=8)
            self.s.close()
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("fleet")
    ap.add_argument("host")
    ap.add_argument("mode", nargs="?", default="", help="key: make the drill's key and print its public half")
    ap.add_argument("--public", help="the address the relay's public name is tried at")
    args = ap.parse_args()
    with open(args.fleet) as fh:
        data = yaml.safe_load(fh)
    host = data["hosts"][args.host]
    block = next(b for b in host.values() if isinstance(b, dict) and "nostr-relay" in (b.get("modules") or []))
    conf = block.get("nostr-relay") or {}
    name = conf.get("relay_domain") or sys.exit("drill: %s has no relay_domain: its relay is not public" % args.host)
    keyfile = os.path.join(os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(args.fleet)), "secrets")), "drill-relay.key")
    if not selftest():
        sys.exit("drill: this computer signs wrongly (BIP-340's first test vector fails)")
    if args.mode == "key":
        if not os.path.exists(keyfile):
            fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(secrets.token_bytes(32).hex() + "\n")
        with open(keyfile) as fh:
            print(public(bytes.fromhex(fh.read().strip())).hex())
        return
    if not args.public:
        sys.exit("drill: --public ADDRESS (the address %s is tried at)" % name)
    if not os.path.exists(keyfile):
        sys.exit("drill: first: %s %s %s key" % (sys.argv[0], args.fleet, args.host))
    with open(keyfile) as fh:
        listed = bytes.fromhex(fh.read().strip())
    stranger = secrets.token_bytes(32)                      # a key nobody knows, gone when the drill ends
    say(public(listed).hex() in [str(k).lower() for k in conf.get("write_pubkeys") or []],
        "the fleet file lists the drill's key (nostr-relay.write_pubkeys)")

    # 1. who it is (NIP-11), with a certificate the world knows
    try:
        raw = socket.create_connection((args.public, 443), timeout=20)
        s = ssl.create_default_context().wrap_socket(raw, server_hostname=name)
        s.sendall(("GET / HTTP/1.1\r\nHost: %s\r\nAccept: application/nostr+json\r\nConnection: close\r\n\r\n" % name).encode())
        got = b""
        while True:
            more = s.recv(65536)
            if not more:
                break
            got += more
        s.close()
        head, _, body = got.partition(b"\r\n\r\n")
        if b"chunked" in head.lower():
            body = body.split(b"\r\n", 1)[1].rsplit(b"\r\n0", 1)[0]
        info = json.loads(body.decode())
        say(True, "%s says who it is: %s (%s)" % (name, info.get("name") or "no name", info.get("software") or "?"))
    except (OSError, ValueError, IndexError) as e:
        say(False, "%s says who it is (NIP-11): %s" % (name, e))

    # 2. the listed key posts, and its note comes back
    try:
        ws = Socket(args.public, name)
    except (OSError, ssl.SSLError) as e:
        say(False, "a WebSocket to wss://%s at %s: %s" % (name, args.public, e))
        return
    say("Let's Encrypt" in ws.issuer or bool(ws.issuer), "wss://%s opens with a certificate the world knows (%s)" % (name, ws.issuer or "?"))
    note = event(listed, 1, "kiwi-server drill %s" % secrets.token_hex(4))
    ws.send(["EVENT", note])
    ok = ws.wait("OK", note["id"])
    say(bool(ok and ok[2]), "the listed key's note is taken%s" % ("" if ok and ok[2] else " (%s)" % (ok[3] if ok else "no answer")))
    ws.send(["REQ", "drill", {"ids": [note["id"]]}])
    back = ws.wait("EVENT", note["id"])
    say(bool(back), "and read back from the relay")
    ws.send(["CLOSE", "drill"])
    # a note dated back: a phone's clock is seconds off, a sealed message up to two days on purpose
    # (20 Nov 2026: with keep_days 0 the relay refused all but the very second it was in)
    days = int(conf.get("keep_days") or 0)
    for back, words in ((5, "five seconds"), (3600, "an hour"), (2 * 86400 - 60, "two days")):
        if days and back > days * 86400:
            continue
        old = event(listed, 1, "kiwi-server drill: dated %s back" % words, back=back)
        ws.send(["EVENT", old])
        r = ws.wait("OK", old["id"])
        say(bool(r and r[2]), "a note dated %s back is taken%s" % (words, "" if r and r[2] else " (%s)" % (r[3] if r else "no answer")))
        if r and r[2]:
            rid = event(listed, 5, "drill over", [("e", old["id"])])
            ws.send(["EVENT", rid])
            ws.wait("OK", rid["id"])

    # 3. a key nobody knows is refused: unproven, and after proving who it is (NIP-42)
    other = Socket(args.public, name)
    spam = event(stranger, 1, "kiwi-server drill: this must not be stored")
    other.send(["EVENT", spam])
    no = other.wait("OK", spam["id"])
    say(bool(no) and no[2] is False, "an unknown key's note is refused (%s)" % (no[3] if no else "no answer"))
    if other.challenge:
        proof = event(stranger, 22242, "", [("relay", "wss://" + name), ("challenge", other.challenge)])
        other.send(["AUTH", proof])
        other.wait("OK", proof["id"])
        spam2 = event(stranger, 1, "kiwi-server drill: nor this")
        other.send(["EVENT", spam2])
        no2 = other.wait("OK", spam2["id"])
        say(bool(no2) and no2[2] is False, "also after proving who it is (%s)" % (no2[3] if no2 else "no answer"))
    else:
        say(True, "the relay asked for no proof of key (no members here): the second refusal is not tried")
    other.send(["REQ", "drill2", {"authors": [public(stranger).hex()]}])
    kept = other.wait("EVENT", timeout=5)
    say(kept is None, "nothing of the unknown key is in the relay")
    other.close()

    # 4. members: approved. A key the admin's list names may post once it has proven who it is,
    #    and no longer when the list drops it. The list as it was is put back (signed by the drill's key).
    if conf.get("members") == "approved":
        admins = [str(k).lower() for k in conf.get("write_pubkeys") or []]
        ws.send(["REQ", "lists", {"kinds": [30000], "authors": admins, "#d": ["members"]}])
        newest, was = 0, []
        while True:
            m = ws.read(5)
            if not m or m[0] == "EOSE":
                break
            if m[0] == "EVENT" and m[2]["created_at"] >= newest:
                newest, was = m[2]["created_at"], [t[1] for t in m[2]["tags"] if len(t) > 1 and t[0] == "p"]
        ws.send(["CLOSE", "lists"])
        friend = secrets.token_bytes(32)
        fpub = public(friend).hex()

        def tries(text):
            """The friend's key, proven, posts a note: (taken, why not)."""
            c = Socket(args.public, name)
            first = event(friend, 1, "kiwi-server drill: asks for the proof")
            c.send(["EVENT", first])
            c.wait("OK", first["id"])
            if not c.challenge:
                c.close()
                return False, "the relay asked for no proof"
            proof = event(friend, 22242, "", [("relay", "wss://" + name), ("challenge", c.challenge)])
            c.send(["AUTH", proof])
            c.wait("OK", proof["id"])
            n = event(friend, 1, text)
            c.send(["EVENT", n])
            r = c.wait("OK", n["id"])
            if r and r[2]:
                c.send(["EVENT", event(friend, 5, "drill over", [("e", n["id"])])])
                c.read(3)
            c.close()
            return bool(r and r[2]), (r[3] if r else "no answer")

        took, why = tries("kiwi-server drill: not on the list yet")
        say(not took, "a key that is not on the members' list is refused (%s)" % why)
        time.sleep(1)
        onto = event(listed, 30000, "", [("d", "members")] + [("p", k) for k in was] + [("p", fpub)])
        ws.send(["EVENT", onto])
        r = ws.wait("OK", onto["id"])
        say(bool(r and r[2]), "the admin's key puts it on the list (%d were on it)" % len(was))
        took, why = tries("kiwi-server drill: a member now")
        say(took, "on the list, and proven: its note is taken%s" % ("" if took else " (%s)" % why))
        time.sleep(1)
        off = event(listed, 30000, "", [("d", "members")] + [("p", k) for k in was])
        ws.send(["EVENT", off])
        r = ws.wait("OK", off["id"])
        took, why = tries("kiwi-server drill: off the list again")
        say(bool(r and r[2]) and not took, "off the list again: refused (%s)" % why)
    else:
        say(True, "members: %s here: the members' list is not tried (members: approved)" % (conf.get("members") or "not set"))

    # 5. the note goes again
    gone = event(listed, 5, "drill over", [("e", note["id"])])
    ws.send(["EVENT", gone])
    ok = ws.wait("OK", gone["id"])
    time.sleep(1)
    ws.send(["REQ", "drill3", {"ids": [note["id"]]}])
    still = ws.wait("EVENT", note["id"], timeout=5)
    say(bool(ok and ok[2]) and still is None, "the drill's note is deleted again")
    ws.close()
    print("%s %d wrong" % (time.strftime("%H:%M:%S"), fails))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
