"""The network's sign-up page: /join on the machine that keeps the accounts (the accounts module,
Kanidm), in its own small container.

Two ways in, as the people who run the network decided (docs/design/public-services.md):
- an invite code from someone who has one: the account is made at once;
- a request with a few words: it waits until the people who run the network accept it on the
  control page. The person keeps a request code and comes back with it.
A Jabber server may also be open (its `registration` setting): then the same page makes an
address right away after a picture test, one per Internet address in ten minutes.
Either way the person gets a link that sets their password or passkey at Kanidm's own page: no
email address is asked, as the network sends no mail.

What is kept (one JSON file in the container's folder, no password in it): the invite codes and
how often each may still be used, and the requests with their state. The link of an accepted
request is kept until it is shown or a day has passed. Kanidm is asked with the token of a
service account that may make people and their links, nothing else.
"""
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.request

from . import util
from .util import KiwiError

NAME = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
# names a stranger may not take: they would look like the network's own
RESERVED = {"admin", "administrator", "root", "system", "kiwi", "support", "help", "abuse", "postmaster", "hostmaster",
            "webmaster", "security", "noc", "info", "jabber", "xmpp", "mail", "www", "git", "cloud", "status", "anonymous"}
LINK_DAYS = 1           # a link to set the password is good for a day
ASK_PER_HOUR = 3        # requests from one address in an hour
PENDING_MAX = 100       # requests waiting at once: more are refused until some are answered
OPEN_EVERY = 600        # the open way: one new address per Internet address in ten minutes
PICTURE_SECONDS = 600   # a picture test is good for ten minutes, once
PICTURES_MAX = 2000     # picture tests kept at once (in memory)
PICTURE_LETTERS = "abcdefghjkmnpqrstuvwxyz23456789"     # no 0/o, 1/l/i


def _now():
    return time.time()


def _code(prefix, n=3):
    """kiwi-7f3a-9c21-b0d4: easy to read out, hard to guess."""
    return "-".join([prefix] + [secrets.token_hex(2) for _ in range(n)])


def _digest(code):
    return hashlib.sha256(code.strip().lower().encode()).hexdigest()


class Signup:
    """`ejabberd`: {page name: {"api": URL of its HTTP API, "host": the Jabber domain}} makes Jabber
    accounts (the person chooses a password, or gets one) instead of the network's accounts."""
    def __init__(self, state_dir, kanidm="", token_file="", id_host="", opener=None, clock=_now, ejabberd=None,
                 invites=True, requests=True, captcha=False, id_onion=""):
        self.dir = state_dir
        self.path = os.path.join(state_dir, "signup.json")
        self.kanidm_url = kanidm.rstrip("/")
        self.token_file = token_file
        self.id_host = id_host
        self.clock = clock
        self.lock = threading.Lock()
        self.opener = opener            # tests: a stand-in for Kanidm (or ejabberd)
        self.ejabberd = ejabberd or {}
        self.id_onion = id_onion        # the accounts' own hidden service: links stay on Tor for who came over Tor
        self.ways = {"invites": invites, "requests": requests, "captcha": captcha}
        self.pictures = {}              # picture test id -> [its letters, their digest, good until]; memory only
        os.makedirs(state_dir, mode=0o700, exist_ok=True)

    # ---- what is kept ------------------------------------------------------------------
    def load(self):
        try:
            with open(self.path) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {"invites": [], "requests": [], "asked": {}}

    def save(self, state):
        tmp = self.path + ".new"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump(state, fh, indent=1)
        os.replace(tmp, self.path)

    # ---- Kanidm --------------------------------------------------------------------------
    def kanidm(self, method, path, body=None):
        if self.opener:
            return self.opener(method, path, body)
        with open(self.token_file) as fh:
            token = fh.read().strip()
        req = urllib.request.Request(self.kanidm_url + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20, context=ssl._create_unverified_context()) as r:
                text = r.read().decode() or "null"
                return json.loads(text)
        except urllib.error.HTTPError as e:
            with e:
                said = e.read().decode(errors="replace")[:200]
            if e.code == 409 or "duplicate" in said.lower() or "exists" in said.lower():
                raise KiwiError("that name is taken")
            raise KiwiError("the accounts did not take it (%s %s)" % (e.code, said))
        except (urllib.error.URLError, OSError) as e:
            raise KiwiError("the accounts do not answer just now (%s)" % getattr(e, "reason", e))

    def make_account(self, name, display, page="", password=None):
        """The account, and what its person needs: Kanidm's link that sets the password, or a
        Jabber address (with the password made for it when the person chose none)."""
        if self.ejabberd:
            where = self.ejabberd.get(page) or next(iter(self.ejabberd.values()))
            given = password is None
            # easy to read out and type: no 0/O, 1/l
            password = password or "-".join("".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(5)) for _ in range(4))
            self.jabber(where, "check_account", {"user": name, "host": where["host"]}, exists_is_error=True)
            self.jabber(where, "register", {"user": name, "host": where["host"], "password": password})
            return {"address": "%s@%s" % (name, where["host"]), "password": password if given else ""}
        self.kanidm("POST", "/v1/person", {"attrs": {"name": [name], "displayname": [display or name],
                                                     "mail": ["%s@%s" % (name, self.id_host)]}})
        return {"link": self.link(name, onion=bool(self.id_onion) and page == self.id_onion)}

    def jabber(self, where, command, body, exists_is_error=False):
        """One command of ejabberd's HTTP API (only register and check_account are allowed)."""
        if self.opener:
            answer = self.opener("POST", "%s/%s" % (where["api"], command), body)
            if exists_is_error and answer == 0:
                raise KiwiError("that name is taken")
            return answer
        req = urllib.request.Request("%s/%s" % (where["api"], command), method="POST", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                answer = json.loads(r.read().decode() or "null")
        except urllib.error.HTTPError as e:
            with e:
                said = e.read().decode(errors="replace")[:200]
            if "exist" in said.lower() or e.code == 409:
                raise KiwiError("that name is taken")
            raise KiwiError("Jabber did not take it (%s %s)" % (e.code, said))
        except (urllib.error.URLError, OSError) as e:
            raise KiwiError("Jabber does not answer just now (%s)" % getattr(e, "reason", e))
        if exists_is_error and answer == 0:        # check_account: 0 is "it exists"
            raise KiwiError("that name is taken")
        return answer

    def link(self, name, onion=False):
        answer = self.kanidm("GET", "/v1/person/%s/_credential/_update_intent/%d" % (name, LINK_DAYS * 86400))
        token = answer.get("token") if isinstance(answer, dict) else answer
        if not token:
            raise KiwiError("the accounts gave no link")
        if onion and self.id_onion:
            return "http://%s/ui/reset?token=%s" % (self.id_onion, token)
        return "https://%s/ui/reset?token=%s" % (self.id_host, token)

    # ---- checks --------------------------------------------------------------------------
    @staticmethod
    def check_name(name):
        name = (name or "").strip().lower()
        if not NAME.match(name):
            raise KiwiError("A name is 2 to 32 lowercase letters, digits, - and _, starting with a letter.")
        if name in RESERVED or name.startswith("idm_"):
            raise KiwiError("That name is kept for the network itself. Choose another one.")
        return name

    # ---- invite codes --------------------------------------------------------------------
    def invite(self, uses=1, days=14, note=""):
        """A new invite code (shown once: only its digest is kept)."""
        code = _code("kiwi")
        with self.lock:
            st = self.load()
            st["invites"].append({"digest": _digest(code), "hint": code[-4:], "uses": int(uses), "used": 0,
                                  "until": self.clock() + int(days) * 86400, "note": str(note)[:80], "made": self.clock()})
            self.save(st)
        return code

    def invites(self):
        now = self.clock()
        return [dict(i, live=i["used"] < i["uses"] and i["until"] > now) for i in self.load()["invites"]]

    def revoke(self, hint):
        with self.lock:
            st = self.load()
            before = len(st["invites"])
            st["invites"] = [i for i in st["invites"] if i["hint"] != hint]
            self.save(st)
        return before - len(st["invites"])

    def join(self, code, name, display="", page="", password=None):
        """An invite code and a name: the account, and what its person needs (see make_account).
        The code counts only when the account was made."""
        name = self.check_name(name)
        if password is not None and len(password) < 10:
            raise KiwiError("A password has 10 characters at least.")
        with self.lock:
            st = self.load()
            inv = next((i for i in st["invites"] if i["digest"] == _digest(code or "")), None)
            if not inv or inv["used"] >= inv["uses"] or inv["until"] <= self.clock():
                raise KiwiError("That invite code is not good (any more). Ask the person who gave it to you for a new one.")
            made = self.make_account(name, display, page, password)
            inv["used"] += 1
            self.save(st)
        return made

    # ---- the open way: a picture test ------------------------------------------------------
    def picture_test(self):
        """A new picture test: (its id, the letters it shows), kept in memory for ten minutes."""
        text = "".join(secrets.choice(PICTURE_LETTERS) for _ in range(6))
        pid, now = secrets.token_urlsafe(16), self.clock()
        with self.lock:
            self.pictures = {k: v for k, v in self.pictures.items() if v[2] > now}
            if len(self.pictures) >= PICTURES_MAX:
                raise KiwiError("Too many people at once. Try again in a few minutes.")
            self.pictures[pid] = [text, _digest(text), now + PICTURE_SECONDS]
        return pid, text

    def picture_text(self, pid):
        with self.lock:
            p = self.pictures.get(pid or "")
        return p[0] if p and p[2] > self.clock() else None

    def open_join(self, pid, answer, name, password, address="", page=""):
        """The open way (a Jabber server with registration on): the picture test's letters, a name
        and a password make the address at once. A test counts once, right or wrong."""
        if not (self.ways["captcha"] and self.ejabberd):
            raise KiwiError("Addresses without a code are not made here.")
        with self.lock:
            p = self.pictures.pop(pid or "", None)
        if not p or p[2] <= self.clock() or not hmac.compare_digest(p[1], _digest("".join((answer or "").split()))):
            raise KiwiError("The letters did not match the picture. Here is a new one.")
        name = self.check_name(name)
        if len(password or "") < 10:
            raise KiwiError("A password has 10 characters at least.")
        now = self.clock()
        with self.lock:
            st = self.load()
            opened = {k: v for k, v in st.get("opened", {}).items() if v > now - OPEN_EVERY}
            if _digest(address) in opened:
                raise KiwiError("One new address per Internet address in ten minutes. Try again later.")
            made = self.make_account(name, name, page, password)
            opened[_digest(address)] = now
            st["opened"] = opened
            self.save(st)
        return made

    # ---- requests --------------------------------------------------------------------------
    def ask(self, name, why, address="", page=""):
        """A request: its code (shown once), with which the person comes back."""
        name = self.check_name(name)
        why = " ".join(str(why or "").split())[:400]
        if len(why) < 10:
            raise KiwiError("Say in a few words who you are and why you would like an account.")
        now = self.clock()
        with self.lock:
            st = self.load()
            hour = [t for t in st["asked"].get(_digest(address), []) if t > now - 3600]
            if len(hour) >= ASK_PER_HOUR:
                raise KiwiError("Too many requests from here. Try again in an hour.")
            pending = [r for r in st["requests"] if r["state"] == "waiting"]
            if len(pending) >= PENDING_MAX:
                raise KiwiError("Too many requests are waiting. Try again in a few days.")
            if any(r["name"] == name and r["state"] in ("waiting", "accepted") for r in st["requests"]):
                raise KiwiError("Someone asked for that name already. Choose another one.")
            code = _code("ask")
            st["requests"].append({"digest": _digest(code), "hint": code[-4:], "name": name, "why": why, "at": now,
                                   "state": "waiting", "link": "", "link_until": 0, "page": page})
            st["asked"][_digest(address)] = hour + [now]
            st["asked"] = {k: v for k, v in st["asked"].items() if v and max(v) > now - 3600}
            self.save(st)
        return code

    def requests(self, waiting_only=False):
        rs = self.load()["requests"]
        return [dict({k: v for k, v in r.items() if k != "link"}, has_link=bool(r["link"]))
                for r in rs if not waiting_only or r["state"] == "waiting"]

    def answer(self, hint, accept):
        """Accept (the account is made, its link waits for its person) or deny a request."""
        with self.lock:
            st = self.load()
            r = next((x for x in st["requests"] if x["hint"] == hint and x["state"] == "waiting"), None)
            if not r:
                raise KiwiError("no waiting request %s" % hint)
            if accept:
                # what the person needs, kept until they come for it or a day has passed
                r["link"], r["link_until"] = json.dumps(self.make_account(r["name"], r["name"], r.get("page", ""))), self.clock() + LINK_DAYS * 86400
                r["state"] = "accepted"
            else:
                r["state"] = "denied"
            r["answered"] = self.clock()
            self.save(st)
        return r["state"]

    def status(self, code):
        """What became of a request: (state, what its person needs or {}). Kanidm's link is shown
        until it expires; a Jabber password made for the person is shown once, then forgotten."""
        with self.lock:
            st = self.load()
            r = next((x for x in st["requests"] if x["digest"] == _digest(code or "")), None)
            if not r:
                return "unknown", {}
            if r["state"] == "accepted" and r["link"] and r["link_until"] > self.clock():
                made = json.loads(r["link"]) if r["link"].startswith("{") else {"link": r["link"]}
                if made.get("password"):
                    r["link"] = json.dumps(dict(made, password=""))      # once
                    self.save(st)
                return "accepted", made
            return ("expired" if r["state"] == "accepted" else r["state"]), {}


def draw_picture(text):
    """The picture test: its letters turned, moved and crossed out on a noisy ground (PNG).
    Images only: it works in a browser without scripts, as Tor Browser's safest level."""
    from PIL import Image, ImageDraw, ImageFont
    rnd = secrets.SystemRandom()
    w, h = 300, 90
    img = Image.new("RGB", (w, h), (246, 245, 240))
    d = ImageDraw.Draw(img)
    for _ in range(700):
        d.point((rnd.randrange(w), rnd.randrange(h)), fill=tuple(rnd.randrange(110, 220) for _ in range(3)))
    try:
        font = ImageFont.load_default(size=46)
    except (TypeError, ValueError, OSError):    # an old Pillow, or one without FreeType
        font = ImageFont.load_default()
    x = 12
    for ch in text:
        glyph = Image.new("RGBA", (64, 76), (0, 0, 0, 0))
        ImageDraw.Draw(glyph).text((12, 6), ch, font=font, fill=tuple(rnd.randrange(10, 90) for _ in range(3)) + (255,))
        glyph = glyph.rotate(rnd.uniform(-16, 16), resample=Image.BICUBIC)
        img.paste(glyph, (x, rnd.randrange(0, h - 76 + 1)), glyph)
        x += rnd.randrange(38, 46)
    for _ in range(5):
        d.line([(rnd.randrange(w), rnd.randrange(h)), (rnd.randrange(w), rnd.randrange(h))],
               fill=tuple(rnd.randrange(60, 160) for _ in range(3)), width=2)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ---- the page ---------------------------------------------------------------------------------
JOIN = """{% extends "base.html" %}{% block title %}{{ brand }}: {{ "a Jabber address" if jabber else "an account" }}{% endblock %}{% block main %}
<style>main{max-width:760px}.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media (max-width:680px){.two{grid-template-columns:1fr}}
.link{font-size:1.05rem;word-break:break-all}.big{font-size:1.25rem;font-weight:700;letter-spacing:.02em;word-break:break-all}</style>
<div class="row" style="margin-top:8px"><h1 style="display:flex;align-items:center;gap:10px;margin:0"><span style="width:26px;height:26px;display:inline-flex">{{ icon("kiwi") }}</span>{{ brand }}</h1>
{% if not jabber %}<a class="small" href="{{ ("http://" ~ page_host) if over_tor else ("https://" ~ id_host) }}/ui/login">I have an account: sign in</a>{% endif %}</div>
<p class="sub">{% if jabber %}A Jabber address at {{ host }}: chat with anyone on Jabber (XMPP), in an app like Conversations, Monal, Dino or Gajim.
{% else %}One account for the services of this network.{% endif %} No email address is asked{% if not jabber %}: write down your name and
keep the link you get until you have set your password{% endif %}.{% if onion %} Over Tor this page is <code>{{ onion }}</code>.{% endif %}</p>
{% if error %}<div class="banner bad">{{ error }}</div>{% endif %}
{% if made %}<div class="card"><h2 style="margin-top:0">{{ "Your Jabber address is ready" if made.address else "Your account " ~ name ~ " is made" }}</h2>
{% if made.address %}<p class="big">{{ made.address }}</p>
{% if made.password %}<p>Its password, shown this once: <b class="big">{{ made.password }}</b><br><span class="muted small">Sign in with it in your app, then change it there.</span></p>
{% else %}<p>Sign in with it and your password in your app.</p>{% endif %}
{% else %}<p>Set its password{% if not over_tor %} (or a passkey){% endif %} now. The link works for a day, once:</p>{% if over_tor %}<p class="small">Over Tor a password is the way: a passkey belongs to the network's usual name ({{ id_host }}).</p>{% endif %}
<p><a class="btn primary link" href="{{ made.link }}">Set my password</a></p>{% endif %}
<p class="muted small">Lost {{ "the password" if made.address else "it" }}? The people who run this network can help.</p></div>
{% elif asked %}<div class="card"><h2 style="margin-top:0">Your request is waiting</h2>
<p>Keep this code. Come back with it to see whether your {{ "address" if jabber else "account" }} is ready:</p>
<p><b class="big">{{ asked }}</b></p>
<p><a class="btn" href="/join/status?code={{ asked }}">See how it is</a></p></div>
{% elif state %}<div class="card"><h2 style="margin-top:0">{{ {"waiting": "Still waiting", "denied": "Not this time",
"expired": "That was shown already, or has expired", "unknown": "No request with that code"}[state] }}</h2>
<p>{{ {"waiting": "The people who run this network have not answered yet. Come back later.",
"denied": "Your request was not accepted.", "expired": "Ask the people who run this network for help.",
"unknown": "Check the code, or ask again."}[state] }}</p></div>
{% else %}<div class="two">
{% if ways.invites %}<form class="card" method="post" action="/join/invite"><h2 style="margin-top:0">I have an invite code</h2>
<label for="i-code">Invite code</label><input id="i-code" name="code" placeholder="kiwi-xxxx-xxxx-xxxx" required autocomplete="off">
<label for="i-name">{{ "Your name (before the @)" if jabber else "Your name here" }}</label><input id="i-name" name="name" pattern="[a-z][a-z0-9_-]{1,31}" required autocomplete="username"
placeholder="lowercase letters and digits">
{% if jabber %}<label for="i-pw">A password</label><input id="i-pw" name="password" type="password" minlength="10" required autocomplete="new-password">{% endif %}
<div class="actions"><button class="primary">{{ "Make my address" if jabber else "Make my account" }}</button></div></form>{% endif %}
{% if ways.requests %}<form class="card" method="post" action="/join/ask"><h2 style="margin-top:0">{{ "Ask for an address" if jabber else "Ask for an account" }}</h2>
<label for="a-name">{{ "Your name (before the @)" if jabber else "Your name here" }}</label><input id="a-name" name="name" pattern="[a-z][a-z0-9_-]{1,31}" required autocomplete="username"
placeholder="lowercase letters and digits">
<label for="a-why">Who you are, and why</label><textarea id="a-why" name="why" rows="3" required minlength="10" maxlength="400"></textarea>
<div class="actions"><button>Ask</button></div>
<p class="muted small">The people who run this network read it. You get a code to come back with.</p></form>{% endif %}
{% if ways.captcha and jabber and picture %}<form class="card" method="post" action="/join/open"><h2 style="margin-top:0">Without a code</h2>
<p class="small">Your address right away{% if not ways.invites and not ways.requests %}: it is the way here{% endif %}.</p>
<label for="o-name">Your name (before the @)</label><input id="o-name" name="name" pattern="[a-z][a-z0-9_-]{1,31}" required autocomplete="username"
placeholder="lowercase letters and digits">
<label for="o-pw">A password</label><input id="o-pw" name="password" type="password" minlength="10" required autocomplete="new-password">
<label for="o-pic">The letters in the picture</label>
<img src="/join/picture/{{ picture }}" alt="Letters to type in the box below" width="300" height="90" style="display:block;max-width:100%;height:auto;border-radius:8px;margin:4px 0 6px">
<input id="o-pic" name="answer" required autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="12">
<input type="hidden" name="picture" value="{{ picture }}">
<div class="actions"><button class="{{ "primary" if not ways.invites and not ways.requests else "" }}">Make my address</button></div>
<p class="muted small">One new address per Internet address in ten minutes. Your app can do this too.</p></form>{% endif %}
</div>
{% if jabber %}<p class="muted small">To change your password, use your Jabber app. Lost it? The people who run this network can help.</p>{% endif %}
{% if ways.requests %}<form method="get" action="/join/status" class="row" style="margin-top:6px;justify-content:flex-start">
<input name="code" placeholder="ask-xxxx-xxxx-xxxx" style="max-width:260px" aria-label="Request code"><button>See how my request is</button></form>{% endif %}
{% endif %}
{% if terms %}<h2>The rules</h2><div class="card">{% for line in terms.splitlines() if line.strip() %}<p>{{ line }}</p>{% endfor %}</div>{% endif %}
{% endblock %}"""

TERMS = """No spam, no malware, no harassment, nothing against the law.
The people who run this network may close an account that breaks these rules.
Nobody can reset your password by email: keep it safe, or add a passkey."""


def make_app(signup: Signup, brand="", terms=""):
    import jinja2
    from fastapi import FastAPI, Form, Request
    from fastapi.responses import HTMLResponse, RedirectResponse, Response
    from . import web
    env = jinja2.Environment(loader=jinja2.DictLoader({"base.html": web.TEMPLATES["base.html"], "join.html": JOIN}), autoescape=True)
    env.globals.update(icon=web.icon, version="")
    app = FastAPI(title="Kiwi sign-up", docs_url=None, redoc_url=None, openapi_url=None)
    brand = brand or "Kiwi"
    terms = TERMS if terms is None or terms == "" else terms

    def page(request=None, **kw):
        host = (request.headers.get("host") or "").split(":")[0] if request is not None else ""
        picture = ""
        if signup.ways["captcha"] and signup.ejabberd and not (kw.get("made") or kw.get("asked") or kw.get("state")):
            try:
                picture = signup.picture_test()[0]
            except KiwiError as e:
                kw.setdefault("error", str(e))
        where = signup.ejabberd.get(host) or (next(iter(signup.ejabberd.values())) if signup.ejabberd else {})
        return HTMLResponse(env.get_template("join.html").render(
            brand=brand, terms=terms, id_host=signup.id_host, nav=None, jabber=bool(signup.ejabberd), host=where.get("host", ""),
            onion=where.get("onion", "") or (signup.id_onion if not signup.ejabberd and host != signup.id_onion else ""),
            over_tor=bool(signup.id_onion) and host == signup.id_onion, page_host=host, ways=signup.ways, picture=picture, **kw))

    def address(request):
        return util.client_address(request, none="")

    @app.get("/")
    def root():
        return RedirectResponse("/join", 302)

    def page_host(request):
        return (request.headers.get("host") or "").split(":")[0]

    @app.get("/join")
    def join_page(request: Request):
        return page(request)

    @app.post("/join/invite")
    def join_invite(request: Request, code: str = Form(""), name: str = Form(""), password: str = Form(None)):
        if not signup.ways["invites"]:
            return page(request, error="Invite codes are not taken here.")
        try:
            made = signup.join(code, name, page=page_host(request), password=password if signup.ejabberd else None)
            return page(request, made=made, name=name.strip().lower())
        except KiwiError as e:
            return page(request, error=str(e))

    @app.post("/join/ask")
    def join_ask(request: Request, name: str = Form(""), why: str = Form("")):
        if not signup.ways["requests"]:
            return page(request, error="Requests are not taken here.")
        try:
            return page(request, asked=signup.ask(name, why, address(request), page=page_host(request)))
        except KiwiError as e:
            return page(request, error=str(e))

    @app.get("/join/picture/{pid}")
    def join_picture(pid: str):
        text = signup.picture_text(pid)
        if not text:
            return Response(status_code=404)
        return Response(draw_picture(text), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/join/open")
    def join_open(request: Request, picture: str = Form(""), answer: str = Form(""), name: str = Form(""),
                  password: str = Form("")):
        try:
            made = signup.open_join(picture, answer, name, password, address(request), page=page_host(request))
            return page(request, made=made, name=name.strip().lower())
        except KiwiError as e:
            return page(request, error=str(e))

    @app.get("/join/status")
    def join_status(request: Request, code: str = ""):
        state, made = signup.status(code)
        return page(request, made=made, name="", state="" if made else state)

    @app.get("/join/healthz")
    def healthz():
        return {"ok": True}

    return app
