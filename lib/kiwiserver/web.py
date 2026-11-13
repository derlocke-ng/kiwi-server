"""`kiwi-server serve`: the control node's pages and API (design/control-plane.md). FastAPI
with Jinja templates and a few lines of JavaScript for a job's live log: no build toolchain.
Meant behind the reverse proxy on the mesh (the control module); the login is the password
set with `kiwi-server serve FLEET --set-password`; it opens the encrypted store (control.py).
"""
import threading
import time

import jinja2
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import VERSION
from .control import ACTIONS, Control
from .util import KiwiError

COOKIE = "kiwi_session"

BASE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{% block title %}Kiwi control{% endblock %}</title>
<style>
:root{--bg:#f6f7f5;--fg:#1d2321;--card:#fff;--line:#d9ddd8;--muted:#5d6763;--accent:#2f7d4a;
--ok:#2f7d4a;--warn:#a86b00;--bad:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#141716;--fg:#e6ebe8;--card:#1d2120;--line:#2e3432;
--muted:#9aa5a0;--accent:#6cc28b;--ok:#6cc28b;--warn:#e0a63a;--bad:#ff7a70}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.45 system-ui,sans-serif}main{max-width:760px;margin:0 auto;padding:16px}
header{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
h1{font-size:1.25rem;margin:0}h2{font-size:1.05rem;margin:24px 0 8px}
a{color:var(--accent)}.muted{color:var(--muted);font-size:.9rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;margin:8px 0}
.row{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
.state{font-weight:600}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad,.down,.failed{color:var(--bad)}
button,.btn{font:inherit;padding:8px 14px;border-radius:8px;border:1px solid var(--line);
background:var(--card);color:var(--fg);cursor:pointer;text-decoration:none;display:inline-block}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
form.inline{display:inline}.actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
ul{margin:4px 0;padding-left:20px}pre{white-space:pre-wrap;word-break:break-word;background:var(--card);
border:1px solid var(--line);border-radius:10px;padding:12px;font-size:.85rem;max-height:70vh;overflow:auto}
input{font:inherit;padding:8px;border-radius:8px;border:1px solid var(--line);width:100%;
background:var(--card);color:var(--fg)}
</style></head><body><main>{% block main %}{% endblock %}</main></body></html>"""

TEMPLATES = {
    "base.html": BASE,
    "login.html": """{% extends "base.html" %}{% block main %}
<h1>Kiwi control</h1>
<form method="post" action="login" class="card">
{% if locked %}<p class="warn">The fleet is locked. The password opens it; until then this node does nothing.</p>{% endif %}
<p><label>Password<br><input type="password" name="password" autocomplete="current-password" autofocus></label></p>
{% if error %}<p class="bad">{{ error }}</p>{% endif %}
<button class="primary">Log in</button></form>{% endblock %}""",
    "index.html": """{% extends "base.html" %}{% block main %}
<header><h1>Kiwi control</h1>
<span class="muted">kiwi-server {{ version }} · <a href="audit">log</a> ·
<form class="inline" method="post" action="logout"><button>Log out</button></form>
<form class="inline" method="post" action="lock" onsubmit="return confirm('Lock the fleet? Nothing runs by itself until the next login.')"><button>Lock</button></form></span></header>
<div class="actions">
<form class="inline" method="post" action="refresh"><button>Look now</button></form>
<form class="inline" method="post" action="run"><input type="hidden" name="action" value="check"><button>Check names</button></form>
</div>
<p class="muted">{% if status and status.time %}Last look {{ ago(status.time) }} ago{% else %}No look yet{% endif %}
{% if status and status.error %} · <span class="bad">{{ status.error }}</span>{% endif %}</p>
{% for h in hosts %}<div class="card"><div class="row">
<strong><a href="hosts/{{ h.host }}">{{ h.host }}</a></strong>
<span class="state {{ h.state }}">{{ h.state }}</span></div>
{% if h.facts and h.facts.applied %}<div class="muted">applied {{ h.facts.applied }}</div>{% endif %}
{% if h.problems %}<ul>{% for p in h.problems %}<li class="{{ p.level }}">{{ p.text }}</li>{% endfor %}</ul>{% endif %}
</div>{% endfor %}
<h2>Jobs</h2>{% for j in jobs %}<div class="row card"><a href="jobs/{{ j.id }}">{{ j.action }} {{ j.hosts|join(" ") }}</a>
<span class="state {{ j.state }}">{{ j.state }}</span></div>{% else %}<p class="muted">None yet.</p>{% endfor %}
{% endblock %}""",
    "host.html": """{% extends "base.html" %}{% block main %}
<header><h1>{{ name }}</h1><a href="../">back</a></header>
{% if host %}<p class="state {{ host.state }}">{{ host.state }}</p>
{% if host.problems %}<ul>{% for p in host.problems %}<li class="{{ p.level }}">{{ p.text }}</li>{% endfor %}</ul>{% endif %}
{% if host.notes %}<ul class="muted">{% for n in host.notes %}<li>{{ n }}</li>{% endfor %}</ul>{% endif %}
<pre>{{ host.output }}</pre>{% else %}<p class="muted">Not in the last look.</p>{% endif %}
<div class="actions">{% for a in ["status", "plan", "apply"] %}
<form class="inline" method="post" action="../run" {% if a == "apply" %}onsubmit="return confirm('Apply {{ name }} now?')"{% endif %}>
<input type="hidden" name="action" value="{{ a }}"><input type="hidden" name="host" value="{{ name }}">
<button {% if a == "apply" %}class="primary"{% endif %}>{{ a|capitalize }}</button></form>{% endfor %}</div>
{% endblock %}""",
    "job.html": """{% extends "base.html" %}{% block main %}
<header><h1>{{ job.action }} {{ job.hosts|join(" ") }}</h1><a href="../">back</a></header>
<p>State: <span id="state" class="state {{ job.state }}">{{ job.state }}</span></p><pre id="log"></pre>
<script>
let off=0;const log=document.getElementById("log"),st=document.getElementById("state");
async function poll(){const r=await fetch("../api/jobs/{{ job.id }}/log?offset="+off);if(!r.ok)return;
const d=await r.json();log.textContent+=d.text;off=d.offset;st.textContent=d.state;st.className="state "+d.state;
if(d.text)log.scrollTop=log.scrollHeight;if(d.state=="waiting"||d.state=="running")setTimeout(poll,1500)}
poll();
</script>{% endblock %}""",
    "audit.html": """{% extends "base.html" %}{% block main %}
<header><h1>Log</h1><a href="./">back</a></header>
{% for e in entries %}<div class="card row"><span>{{ e.what }} {{ (e.hosts or [])|join(" ") }}</span>
<span class="muted">{{ e.time }} · {{ e.user }}{% if e.address %} · {{ e.address }}{% endif %}</span></div>
{% else %}<p class="muted">Empty.</p>{% endfor %}{% endblock %}""",
}


def ago(t):
    s = max(0, int(time.time() - t))
    return "%ds" % s if s < 90 else "%dmin" % (s // 60) if s < 5400 else "%dh" % (s // 3600)


def make_app(control: Control):
    env = jinja2.Environment(loader=jinja2.DictLoader(TEMPLATES), autoescape=True)
    env.globals.update(version=VERSION, ago=ago)
    app = FastAPI(title="Kiwi control", version=VERSION, docs_url=None, redoc_url=None)

    def page(name, **kw):
        return HTMLResponse(env.get_template(name).render(**kw))

    def user(request):
        return control.session(request.cookies.get(COOKIE))

    def address(request):
        return request.headers.get("x-real-ip") or (request.client.host if request.client else "?")

    def same_origin(request):
        """A form posted from another site carries its own Origin: refused (the cookie is
        SameSite=Strict as well)."""
        origin = request.headers.get("origin")
        return not origin or origin.split("://", 1)[-1] == request.headers.get("host")

    def login_page():
        return RedirectResponse("login", status_code=303)

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "version": VERSION, "locked": control.locked()}

    @app.get("/login")
    def login_form():
        return page("login.html", locked=control.locked())

    @app.post("/login")
    def login(request: Request, password: str = Form("")):
        token = control.login(password, address(request)) if same_origin(request) else None
        if not token:
            time.sleep(1)
            return page("login.html", locked=control.locked(),
                        error="Wrong password, or too many tries: wait ten minutes.")
        r = RedirectResponse("./", status_code=303)
        secure = request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https"
        r.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=secure, max_age=12 * 3600)
        return r

    @app.post("/logout")
    def logout():
        r = RedirectResponse("login", status_code=303)
        r.delete_cookie(COOKIE)
        return r

    @app.post("/lock")
    def lock(request: Request):
        u = user(request)
        if not u or not same_origin(request):
            return login_page()
        control.lock_store(u)
        return login_page()

    @app.get("/")
    def index(request: Request):
        if not user(request):
            return login_page()
        st = control.status() or {}
        return page("index.html", status=st, hosts=st.get("hosts") or [], jobs=control.jobs(10))

    @app.get("/hosts/{name}")
    def host(request: Request, name: str):
        if not user(request):
            return RedirectResponse("../login", status_code=303)
        st = control.status() or {}
        found = next((h for h in st.get("hosts") or [] if h.get("host") == name), None)
        return page("host.html", name=name, host=found)

    @app.post("/refresh")
    def refresh(request: Request):
        if not user(request) or not same_origin(request):
            return login_page()
        threading.Thread(target=control.refresh_status, daemon=True).start()
        return RedirectResponse("./", status_code=303)

    @app.post("/run")
    def run(request: Request, action: str = Form(...), host: str = Form("")):
        u = user(request)
        if not u or not same_origin(request):
            return login_page()
        if action not in ACTIONS:
            return JSONResponse({"error": "unknown action"}, status_code=400)
        try:
            jid = control.start(action, [host] if host else [], user=u)
        except KiwiError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return RedirectResponse("jobs/" + jid, status_code=303)

    @app.get("/jobs/{jid}")
    def job(request: Request, jid: str):
        if not user(request):
            return RedirectResponse("../login", status_code=303)
        j = control.job(jid)
        return page("job.html", job=j) if j else JSONResponse({"error": "no such job"}, status_code=404)

    @app.get("/api/jobs/{jid}/log")
    def job_log(request: Request, jid: str, offset: int = 0):
        if not user(request):
            return JSONResponse({"error": "log in"}, status_code=401)
        j = control.job(jid)
        if not j:
            return JSONResponse({"error": "no such job"}, status_code=404)
        text, off = control.log(jid, offset)
        return {"text": text, "offset": off, "state": control.job(jid)["state"]}

    @app.get("/api/status")
    def api_status(request: Request):
        if not user(request):
            return JSONResponse({"error": "log in"}, status_code=401)
        return control.status() or {}

    @app.get("/audit")
    def audit(request: Request):
        if not user(request):
            return login_page()
        return page("audit.html", entries=control.audit_tail())

    return app


def serve(control: Control, listen="127.0.0.1:8740"):
    import uvicorn
    host, port = listen.rsplit(":", 1)
    stop = threading.Event()
    threading.Thread(target=control.loop, args=(stop,), daemon=True).start()
    try:
        uvicorn.run(make_app(control), host=host.strip("[]"), port=int(port), log_level="warning",
                    proxy_headers=True, forwarded_allow_ips="*")
    finally:
        stop.set()
