#!/usr/bin/env python3
"""A Bitwarden client in a few lines, for drills against a Vaultwarden: makes an account, puts
a login item in, and later signs in again and reads the item back. What it proves when the two
halves run before and after a move of the vault's data: the account, its keys and its items came
along, since nothing but the right master password opens the item.

    vault-client.py make URL STATE      a new account with one item; what the check needs goes
                                        into the file STATE (mode 0600: it holds the stand-in
                                        account's master password)
    vault-client.py check URL STATE     signs in with STATE's account and compares the item

URL: the vault as this computer reaches it (http://127.0.0.1:PORT through an SSH forward will
do: the API needs no HTTPS). `--resolve NAME:ADDRESS` and `--ca FILE` for a name the DNS does
not know yet and the fleet's CA. Needs the Python package `cryptography`. Never for a real
account: the master password is made up here and kept in STATE.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

ITERATIONS = 600000


def b64(b):
    return base64.b64encode(b).decode()


def expand(key, info):          # HKDF-Expand (SHA-256), one block
    return hmac.new(key, info + b"\x01", hashlib.sha256).digest()


def seal(plain, enc, mac):      # Bitwarden's EncString type 2: AES-256-CBC, HMAC-SHA256
    iv = os.urandom(16)
    p = padding.PKCS7(128).padder()
    e = Cipher(algorithms.AES(enc), modes.CBC(iv)).encryptor()
    ct = e.update(p.update(plain) + p.finalize()) + e.finalize()
    return "2.%s|%s|%s" % (b64(iv), b64(ct), b64(hmac.new(mac, iv + ct, hashlib.sha256).digest()))


def unseal(text, enc, mac):
    kind, rest = text.split(".", 1)
    iv, ct, tag = (base64.b64decode(x) for x in rest.split("|"))
    if kind != "2" or not hmac.compare_digest(tag, hmac.new(mac, iv + ct, hashlib.sha256).digest()):
        raise SystemExit("FAIL the item does not open with this account's key")
    d = Cipher(algorithms.AES(enc), modes.CBC(iv)).decryptor()
    u = padding.PKCS7(128).unpadder()
    return u.update(d.update(ct) + d.finalize()) + u.finalize()


def keys_of(email, password):
    master = hashlib.pbkdf2_hmac("sha256", password.encode(), email.lower().encode(), ITERATIONS, 32)
    proof = b64(hashlib.pbkdf2_hmac("sha256", master, password.encode(), 1, 32))
    return proof, expand(master, b"enc"), expand(master, b"mac")


class Vault:
    def __init__(self, url, ca=None, resolve=None):
        self.url, self.token = url.rstrip("/"), None
        self.ctx = ssl.create_default_context(cafile=ca) if url.startswith("https") else None
        if resolve:
            name, addr = resolve.split(":", 1)
            real = socket.getaddrinfo
            socket.getaddrinfo = lambda h, *a, **k: real(addr if h == name else h, *a, **k)

    def call(self, path, body=None, form=None):
        data, headers = None, {"Bitwarden-Client-Name": "cli", "Bitwarden-Client-Version": "2026.6.0", "Device-Type": "8"}
        if form is not None:
            data, headers["Content-Type"] = urllib.parse.urlencode(form).encode(), "application/x-www-form-urlencoded"
        elif body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(self.url + path, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60, context=self.ctx) as r:
                text = r.read().decode(errors="replace")
            try:
                return json.loads(text or "{}")
            except ValueError:
                return text.strip()             # the sign-up token comes as bare text
        except urllib.error.HTTPError as e:
            raise SystemExit("FAIL %s answered %d: %s" % (path, e.code, e.read().decode(errors="replace")[:200]))

    def sign_in(self, email, proof, device):
        got = self.call("/identity/connect/token", form={
            "grant_type": "password", "username": email, "password": proof, "scope": "api offline_access",
            "client_id": "cli", "deviceType": "8", "deviceIdentifier": device, "deviceName": "kiwi-server drill"})
        self.token = got["access_token"]
        return got


def make(v, state):
    email = "drill-%s@example.org" % secrets.token_hex(4)
    password = secrets.token_urlsafe(18)
    proof, enc, mac = keys_of(email, password)
    user = os.urandom(64)                       # the account's own key: 32 bytes to encrypt, 32 to sign
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    # Vaultwarden since 1.33: a token first (it comes back at once where the vault sends no mail),
    # then the account with it
    token = v.call("/identity/accounts/register/send-verification-email", body={"email": email, "name": "Drill"})
    if not isinstance(token, str) or not token:
        raise SystemExit("FAIL the vault gave no sign-up token: it sends mail to verify, or sign-ups are off")
    v.call("/identity/accounts/register/finish", body={
        "email": email, "emailVerificationToken": token, "masterPasswordHash": proof, "masterPasswordHint": None,
        "userSymmetricKey": seal(user, enc, mac), "kdf": 0, "kdfIterations": ITERATIONS,
        "userAsymmetricKeys": {
            "publicKey": b64(private.public_key().public_bytes(serialization.Encoding.DER,
                                                               serialization.PublicFormat.SubjectPublicKeyInfo)),
            "encryptedPrivateKey": seal(private.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                                              serialization.NoEncryption()), user[:32], user[32:])}})
    device = str(uuid.uuid4())
    v.sign_in(email, proof, device)
    name, secret = "kept before the move " + secrets.token_hex(3), secrets.token_urlsafe(12)
    item = {"type": 1, "name": seal(name.encode(), user[:32], user[32:]), "notes": None, "favorite": False,
            "folderId": None, "organizationId": None, "reprompt": 0,
            "login": {"username": seal(b"drill", user[:32], user[32:]), "password": seal(secret.encode(), user[:32], user[32:]),
                      "totp": None, "uris": None}}
    me = v.call("/api/accounts/profile")
    item["encryptedFor"] = me.get("id") or me.get("Id")        # asked for since Vaultwarden 1.34
    v.call("/api/ciphers", body=item)
    with open(os.open(state, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump({"email": email, "password": password, "device": device, "name": name,
                   "secret_sha256": hashlib.sha256(secret.encode()).hexdigest()}, f)
    print("PASS an account with one item is in the vault")


def check(v, state):
    with open(state) as f:
        s = json.load(f)
    proof, enc, mac = keys_of(s["email"], s["password"])
    got = v.sign_in(s["email"], proof, s["device"])
    print("PASS the account signs in with its master password")
    user = unseal(got["Key"] if "Key" in got else got["key"], enc, mac)
    items = v.call("/api/sync").get("ciphers") or v.call("/api/sync").get("Ciphers") or []
    names = [unseal(c.get("name") or c.get("Name"), user[:32], user[32:]).decode() for c in items]
    if s["name"] not in names:
        raise SystemExit("FAIL the item is not there (%d items)" % len(items))
    c = items[names.index(s["name"])]
    login = c.get("login") or c.get("Login")
    secret = unseal(login.get("password") or login.get("Password"), user[:32], user[32:])
    if hashlib.sha256(secret).hexdigest() != s["secret_sha256"]:
        raise SystemExit("FAIL the item's password is not the one that was kept")
    print("PASS the item is there and its password is the one that was kept")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["make", "check"])
    ap.add_argument("url")
    ap.add_argument("state")
    ap.add_argument("--ca")
    ap.add_argument("--resolve", metavar="NAME:ADDRESS")
    a = ap.parse_args()
    v = Vault(a.url, a.ca, a.resolve)
    (make if a.what == "make" else check)(v, a.state)


if __name__ == "__main__":
    sys.exit(main())
