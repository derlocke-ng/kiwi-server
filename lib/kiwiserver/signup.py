"""The network's sign-up page: /join on the machine that keeps the accounts (the accounts module,
Kanidm), in its own small container.

Two ways in, as the people who run the network decided (docs/design/public-services.md):
- an invite code from someone who has one: the account is made at once;
- a request with a few words: it waits until the people who run the network accept it on the
  control page. The person keeps a request code and comes back with it.
Either way the person gets a link that sets their password or passkey at Kanidm's own page: no
email address is asked, as the network sends no mail.

What is kept (one JSON file in the container's folder, no password in it): the invite codes and
how often each may still be used, and the requests with their state. The link of an accepted
request is kept until it is shown or a day has passed. Kanidm is asked with the token of a
service account that may make people and their links, nothing else.
"""
import hashlib
import json
import os
import re
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.request

from .util import KiwiError

NAME = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
# names a stranger may not take: they would look like the network's own
RESERVED = {"admin", "administrator", "root", "system", "kiwi", "support", "help", "abuse", "postmaster", "hostmaster",
            "webmaster", "security", "noc", "info", "jabber", "xmpp", "mail", "www", "git", "cloud", "status", "anonymous"}
LINK_DAYS = 1           # a link to set the password is good for a day
ASK_PER_HOUR = 3        # requests from one address in an hour
PENDING_MAX = 100       # requests waiting at once: more are refused until some are answered


def _now():
    return time.time()


def _code(prefix, n=3):
    """kiwi-7f3a-9c21-b0d4: easy to read out, hard to guess."""
    return "-".join([prefix] + [secrets.token_hex(2) for _ in range(n)])


def _digest(code):
    return hashlib.sha256(code.strip().lower().encode()).hexdigest()


class Signup:
    def __init__(self, state_dir, kanidm="", token_file="", id_host="", opener=None, clock=_now):
        self.dir = state_dir
        self.path = os.path.join(state_dir, "signup.json")
        self.kanidm_url = kanidm.rstrip("/")
        self.token_file = token_file
        self.id_host = id_host
        self.clock = clock
        self.lock = threading.Lock()
        self.opener = opener            # tests: a stand-in for Kanidm
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

    def make_account(self, name, display):
        """The account in Kanidm, and the link its person sets a password with."""
        self.kanidm("POST", "/v1/person", {"attrs": {"name": [name], "displayname": [display or name]}})
        return self.link(name)

    def link(self, name):
        answer = self.kanidm("GET", "/v1/person/%s/_credential/_update_intent/%d" % (name, LINK_DAYS * 86400))
        token = answer.get("token") if isinstance(answer, dict) else answer
        if not token:
            raise KiwiError("the accounts gave no link")
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

    def join(self, code, name, display=""):
        """An invite code and a name: the account, and the link. The code counts only when the
        account was made."""
        name = self.check_name(name)
        with self.lock:
            st = self.load()
            inv = next((i for i in st["invites"] if i["digest"] == _digest(code or "")), None)
            if not inv or inv["used"] >= inv["uses"] or inv["until"] <= self.clock():
                raise KiwiError("That invite code is not good (any more). Ask the person who gave it to you for a new one.")
            link = self.make_account(name, display)
            inv["used"] += 1
            self.save(st)
        return link

    # ---- requests --------------------------------------------------------------------------
    def ask(self, name, why, address=""):
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
                                   "state": "waiting", "link": "", "link_until": 0})
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
                r["link"], r["link_until"] = self.make_account(r["name"], r["name"]), self.clock() + LINK_DAYS * 86400
                r["state"] = "accepted"
            else:
                r["state"] = "denied"
            r["answered"] = self.clock()
            self.save(st)
        return r["state"]

    def status(self, code):
        """What became of a request: (state, link or ""). An accepted request's link is shown
        until it expires; then the people who run the network make a new one."""
        st = self.load()
        r = next((x for x in st["requests"] if x["digest"] == _digest(code or "")), None)
        if not r:
            return "unknown", ""
        if r["state"] == "accepted" and r["link"] and r["link_until"] > self.clock():
            return "accepted", r["link"]
        return ("expired" if r["state"] == "accepted" else r["state"]), ""


# ---- the page ---------------------------------------------------------------------------------
JOIN = """{% extends "base.html" %}{% block title %}{{ brand }}: an account{% endblock %}{% block main %}
<style>main{max-width:720px}.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media (max-width:680px){.two{grid-template-columns:1fr}}
.link{font-size:1.05rem;word-break:break-all}</style>
<div class="row" style="margin-top:8px"><h1 style="display:flex;align-items:center;gap:10px;margin:0"><span style="width:26px;height:26px;display:inline-flex">{{ icon("kiwi") }}</span>{{ brand }}</h1>
<a class="small" href="https://{{ id_host }}/ui/login">I have an account: sign in</a></div>
<p class="sub">One account for the services of this network. No email address is asked: write down your name and
keep the link you get until you have set your password.</p>
{% if error %}<div class="banner bad">{{ error }}</div>{% endif %}
{% if link %}<div class="card"><h2 style="margin-top:0">Your account {{ name }} is made</h2>
<p>Set its password (or a passkey) now. The link works for a day, once:</p>
<p><a class="btn primary link" href="{{ link }}">Set my password</a></p>
<p class="muted small">Lost it? The people who run this network can make you a new one.</p></div>
{% elif asked %}<div class="card"><h2 style="margin-top:0">Your request is waiting</h2>
<p>Keep this code. Come back with it to see whether your account is ready:</p>
<p><b style="font-size:1.3rem;letter-spacing:.04em">{{ asked }}</b></p>
<p><a class="btn" href="/join/status?code={{ asked }}">See how it is</a></p></div>
{% elif state %}<div class="card"><h2 style="margin-top:0">{{ {"waiting": "Still waiting", "denied": "Not this time",
"expired": "The link has expired", "unknown": "No request with that code"}[state] }}</h2>
<p>{{ {"waiting": "The people who run this network have not answered yet. Come back later.",
"denied": "Your request was not accepted.", "expired": "Ask the people who run this network for a new link to set your password.",
"unknown": "Check the code, or ask again."}[state] }}</p></div>
{% else %}<div class="two">
<form class="card" method="post" action="/join/invite"><h2 style="margin-top:0">I have an invite code</h2>
<label for="i-code">Invite code</label><input id="i-code" name="code" placeholder="kiwi-xxxx-xxxx-xxxx" required autocomplete="off">
<label for="i-name">Your name here</label><input id="i-name" name="name" pattern="[a-z][a-z0-9_-]{1,31}" required autocomplete="username"
placeholder="lowercase letters and digits">
<div class="actions"><button class="primary">Make my account</button></div></form>
<form class="card" method="post" action="/join/ask"><h2 style="margin-top:0">Ask for an account</h2>
<label for="a-name">Your name here</label><input id="a-name" name="name" pattern="[a-z][a-z0-9_-]{1,31}" required autocomplete="username"
placeholder="lowercase letters and digits">
<label for="a-why">Who you are, and why</label><textarea id="a-why" name="why" rows="3" required minlength="10" maxlength="400"></textarea>
<div class="actions"><button>Ask</button></div>
<p class="muted small">The people who run this network read it. You get a code to come back with.</p></form></div>
<form method="get" action="/join/status" class="row" style="margin-top:6px;justify-content:flex-start">
<input name="code" placeholder="ask-xxxx-xxxx-xxxx" style="max-width:260px" aria-label="Request code"><button>See how my request is</button></form>
{% endif %}
{% if terms %}<h2>The rules</h2><div class="card">{% for line in terms.splitlines() if line.strip() %}<p>{{ line }}</p>{% endfor %}</div>{% endif %}
{% endblock %}"""

TERMS = """No spam, no malware, no harassment, nothing against the law.
The people who run this network may close an account that breaks these rules.
Nobody can reset your password by email: keep it safe, or add a passkey."""


def make_app(signup: Signup, brand="", terms=""):
    import jinja2
    from fastapi import FastAPI, Form, Request
    from fastapi.responses import HTMLResponse, RedirectResponse
    from . import web
    env = jinja2.Environment(loader=jinja2.DictLoader({"base.html": web.TEMPLATES["base.html"], "join.html": JOIN}), autoescape=True)
    env.globals.update(icon=web.icon, version="")
    app = FastAPI(title="Kiwi sign-up", docs_url=None, redoc_url=None, openapi_url=None)
    brand = brand or "Kiwi"
    terms = TERMS if terms is None or terms == "" else terms

    def page(**kw):
        return HTMLResponse(env.get_template("join.html").render(brand=brand, terms=terms, id_host=signup.id_host, nav=None, **kw))

    def address(request):
        return request.headers.get("x-real-ip") or (request.client.host if request.client else "")

    @app.get("/")
    def root():
        return RedirectResponse("/join", 302)

    @app.get("/join")
    def join_page():
        return page()

    @app.post("/join/invite")
    def join_invite(code: str = Form(""), name: str = Form("")):
        try:
            return page(link=signup.join(code, name), name=name.strip().lower())
        except KiwiError as e:
            return page(error=str(e))

    @app.post("/join/ask")
    def join_ask(request: Request, name: str = Form(""), why: str = Form("")):
        try:
            return page(asked=signup.ask(name, why, address(request)))
        except KiwiError as e:
            return page(error=str(e))

    @app.get("/join/status")
    def join_status(code: str = ""):
        state, link = signup.status(code)
        return page(link=link, name="", state="" if link else state)

    @app.get("/join/healthz")
    def healthz():
        return {"ok": True}

    return app
