"""The game page (the control module's `play_page`): people with an account of the network, in
one group, start, stop and wake the game servers and read their logs, without being admins.

It is served by the control node at play.<control node>, where nothing but /play/… answers. The
sign-in is the network's accounts (Kanidm, OpenID Connect with PKCE): `kiwi-server accounts
<fleet> connect <control node> control` makes the client, which only the group's members may
use, and its secret lands in secrets/accounts/<control node>-control.json. Each click is a job
of the control node (`kiwi-server service`), in the machine's History with the person's name.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets as pysecrets
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from .util import KiwiError

COOKIE = "kiwi_play"
STATE_COOKIE = "kiwi_play_state"
HOURS = 12                      # a sign-in lasts this long
ACTIONS = ("start", "stop", "restart", "logs")
CONTAINER = re.compile(r"^k[nm]-[a-z0-9][a-z0-9-]{0,40}$")
NAME = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
GAME = re.compile(r"^k[nm]-game-([a-z0-9][a-z0-9-]{0,30})$")
PAUSE = 15                      # seconds between two clicks on one server


def settings():
    """(host name, group, [(machine, container)] more than the game servers) from the control
    module's settings, which reach the control part as its environment."""
    extra = []
    for word in os.environ.get("KIWI_PLAY_EXTRA", "").split():
        machine, _, container = word.partition("/")
        if machine and CONTAINER.match(container):
            extra.append((machine, container))
    group = os.environ.get("KIWI_PLAY_GROUP", "") or "games"
    return os.environ.get("KIWI_PLAY_HOST", ""), group if NAME.match(group) else "games", extra


def _key(session_key):
    # its own key: a game page's sign-in is never an admin's session, whatever the cookie is called
    return hmac.new(session_key, b"kiwi game page 1", hashlib.sha256).digest()


def token(session_key, name, now=None):
    now = int(now if now is not None else time.time())
    body = "%s|%d" % (name, now + HOURS * 3600)
    return "%s|%s" % (body, hmac.new(_key(session_key), body.encode(), hashlib.sha256).hexdigest())


def who(session_key, tok, now=None):
    """The person a game page's token belongs to, or None."""
    parts = (tok or "").split("|")
    if len(parts) != 3 or not NAME.match(parts[0]):
        return None
    good = hmac.new(_key(session_key), ("%s|%s" % (parts[0], parts[1])).encode(), hashlib.sha256).hexdigest()
    try:
        alive = int(parts[1]) > (now if now is not None else time.time())
    except ValueError:
        return None
    return parts[0] if alive and hmac.compare_digest(good, parts[2]) else None


def csrf(session_key, tok):
    return hmac.new(_key(session_key), ("csrf|%s" % (tok or "")).encode(), hashlib.sha256).hexdigest()


def begin(session_key, client, issuer_conf, back):
    """(where the browser goes to sign in, the state cookie that remembers it): PKCE S256, a
    state and a nonce, the verifier kept in the signed cookie for the way back."""
    verifier = pysecrets.token_urlsafe(48)
    state = pysecrets.token_urlsafe(24)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    q = {"response_type": "code", "client_id": client, "redirect_uri": back, "scope": "openid profile groups",
         "state": state, "nonce": pysecrets.token_urlsafe(16), "code_challenge": challenge, "code_challenge_method": "S256"}
    body = "%s|%s|%d" % (state, verifier, int(time.time()) + 600)
    cookie = "%s|%s" % (body, hmac.new(_key(session_key), ("state|" + body).encode(), hashlib.sha256).hexdigest())
    return issuer_conf["authorization_endpoint"] + "?" + urllib.parse.urlencode(q), cookie


def verifier_of(session_key, cookie, state):
    """The PKCE verifier of a sign-in that this page began, for this state, not older than ten minutes."""
    parts = (cookie or "").split("|")
    if len(parts) != 4:
        return None
    body = "|".join(parts[:3])
    good = hmac.new(_key(session_key), ("state|" + body).encode(), hashlib.sha256).hexdigest()
    try:
        fresh = int(parts[2]) > time.time()
    except ValueError:
        return None
    if fresh and hmac.compare_digest(good, parts[3]) and hmac.compare_digest(parts[0], state or ""):
        return parts[1]
    return None


def _context(ca):
    ctx = ssl.create_default_context()
    if ca and os.path.isfile(ca):
        ctx.load_verify_locations(ca)           # the network's own CA: id.<machine> on the VPN
    return ctx


def _get(url, ca, headers=None, data=None, opener=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=20, context=_context(ca)) as r:
            return json.loads(r.read(200000) or b"{}")
    except urllib.error.HTTPError as e:
        raise KiwiError("the accounts said no (%d) at %s" % (e.code, url.split("?")[0]))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise KiwiError("the accounts do not answer at %s (%s)" % (url.split("?")[0], getattr(e, "reason", e)))


def discover(issuer, ca, opener=None):
    conf = _get(issuer.rstrip("/") + "/.well-known/openid-configuration", ca, opener=opener)
    for k in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint"):
        if not str(conf.get(k, "")).startswith("https://"):
            raise KiwiError("the accounts' configuration has no %s" % k)
    return conf


def finish(conf, client, secret, code, verifier, back, ca, group, opener=None):
    """The person who signed in, once the accounts gave the code back: the code traded for a
    token over TLS (the client's secret with it), then the person's name and groups asked for.
    Only a member of `group` gets a name back."""
    auth = base64.b64encode(("%s:%s" % (urllib.parse.quote(client, safe=""), urllib.parse.quote(secret, safe=""))).encode()).decode()
    form = urllib.parse.urlencode({"grant_type": "authorization_code", "code": code, "redirect_uri": back,
                                   "code_verifier": verifier, "client_id": client}).encode()
    tok = _get(conf["token_endpoint"], ca, {"Authorization": "Basic " + auth,
                                            "Content-Type": "application/x-www-form-urlencoded"}, form, opener=opener)
    if not tok.get("access_token"):
        raise KiwiError("the accounts gave no token")
    me = _get(conf["userinfo_endpoint"], ca, {"Authorization": "Bearer " + tok["access_token"]}, opener=opener)
    name = str(me.get("preferred_username") or "").split("@")[0]
    groups = {str(g).split("@")[0] for g in me.get("groups") or []}
    if not NAME.match(name):
        raise KiwiError("the accounts named nobody")
    if group not in groups:
        raise KiwiError("%s is not in the group %s: the people who run the network can add you" % (name, group))
    return name


def servers(info, status, extra=()):
    """Every game server of the network and every container of `extra`: machine, container,
    what it is, where players connect, and how it is now (running, asleep, stopped, unknown)."""
    said = {h.get("host"): (h.get("facts") or {}) for h in (status or {}).get("hosts") or []}
    rows = []
    for h in (info or {}).get("hosts") or []:
        facts = said.get(h["name"]) or {}
        now = {s["name"]: s for s in facts.get("services") or []}
        wanted = [(g.get("container"), g) for g in h.get("games") or []]
        wanted += [(c, {}) for m, c in extra if m == h["name"] and c not in [w[0] for w in wanted]]
        for container, g in wanted:
            if not container or not CONTAINER.match(container):
                continue
            s = now.get(container)
            state = "unknown" if not s else "asleep" if s.get("asleep") else "running" if s.get("running") else "stopped"
            rows.append({"machine": h["name"], "container": container, "state": state,
                         "name": g.get("server") or container, "title": g.get("title") or "",
                         "address": g.get("address") or "", "game": bool(g), "health": (s or {}).get("health") or ""})
    return rows


def allowed(info, extra, machine, container):
    return any(r["machine"] == machine and r["container"] == container for r in servers(info, {}, extra))
