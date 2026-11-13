"""`kiwi-server serve`: the control node's pages and API (design/control-plane.md). FastAPI
with Jinja templates and a few lines of JavaScript for a job's live log: no build toolchain.
Meant behind the reverse proxy on the mesh (the control module); the login is the password
set with `kiwi-server serve FLEET --set-password`; it opens the encrypted store (control.py).
"""
import asyncio
import base64
import hmac
import threading
import time

import jinja2
import markupsafe
from fastapi import FastAPI, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import VERSION
from .control import ACTIONS, Control
from .util import KiwiError

COOKIE = "kiwi_session"

BASE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{% block title %}Kiwi{% endblock %}</title>
<style>
:root{--bg:#f6f7f5;--fg:#1d2321;--card:#fff;--line:#d9ddd8;--muted:#5d6763;--accent:#2f7d4a;
--ok:#2f7d4a;--warn:#a86b00;--bad:#b3261e;--okbg:#e6f3ea;--warnbg:#fbf1dc;--badbg:#fbe6e4}
@media (prefers-color-scheme:dark){:root{--bg:#141716;--fg:#e6ebe8;--card:#1d2120;--line:#2e3432;
--muted:#9aa5a0;--accent:#6cc28b;--ok:#6cc28b;--warn:#e0a63a;--bad:#ff7a70;--okbg:#17281d;--warnbg:#2c2414;--badbg:#301a18}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
main{max-width:820px;margin:0 auto;padding:12px 16px 48px}
nav{display:flex;gap:4px;align-items:center;overflow-x:auto;padding:8px 16px;border-bottom:1px solid var(--line);background:var(--card);
position:sticky;top:0;z-index:2}nav a{padding:6px 12px;border-radius:999px;text-decoration:none;color:var(--fg);white-space:nowrap}
nav a.on{background:var(--accent);color:#fff}nav .sp{flex:1}
h1{font-size:1.4rem;margin:16px 0 4px}h2{font-size:1.1rem;margin:28px 0 8px}p{margin:8px 0}
a{color:var(--accent)}.muted{color:var(--muted);font-size:.92rem}.small{font-size:.85rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin:10px 0}
.row{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}
.banner{border-radius:12px;padding:16px;margin:12px 0;font-size:1.1rem;font-weight:600}
.banner.ok{background:var(--okbg);color:var(--ok)}.banner.warn{background:var(--warnbg);color:var(--warn)}
.banner.bad{background:var(--badbg);color:var(--bad)}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px;background:var(--muted)}
.dot.ok{background:var(--ok)}.dot.warn{background:var(--warn)}.dot.bad,.dot.down,.dot.failed{background:var(--bad)}
.state{font-weight:600}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad,.down,.failed{color:var(--bad)}
button,.btn{font:inherit;padding:9px 16px;border-radius:10px;border:1px solid var(--line);background:var(--card);
color:var(--fg);cursor:pointer;text-decoration:none;display:inline-block}
button.primary,.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
form.inline{display:inline}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.tile{display:block;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;text-decoration:none;color:var(--fg)}
.tile b{display:block}.tile span{color:var(--muted);font-size:.82rem;word-break:break-all}
.what{display:grid;grid-template-columns:auto 1fr;gap:10px 14px;align-items:start;margin-top:8px}
.what p{margin:0}ul{margin:6px 0;padding-left:20px}li{margin:3px 0}
pre{white-space:pre-wrap;word-break:break-word;background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:12px;font-size:.85rem;max-height:60vh;overflow:auto}
input,select{font:inherit;padding:9px;border-radius:10px;border:1px solid var(--line);width:100%;background:var(--card);color:var(--fg)}
label{display:block;margin:10px 0 4px;font-weight:600}details{margin:8px 0}summary{cursor:pointer;color:var(--accent)}
.qr{background:#fff;padding:10px;border-radius:12px;display:inline-block}.qr svg{display:block;width:260px;height:260px}
dl{display:grid;grid-template-columns:auto 1fr;gap:4px 14px;margin:8px 0}dt{color:var(--muted)}dd{margin:0}
</style></head><body>
{% if nav %}<nav>{% for href, label in [("/", "Home"), ("/devices", "Devices"), ("/backups", "Backups"),
("/controllers", "Computers"), ("/activity", "Activity"), ("/help", "Help")] %}
<a href="{{ href }}" {% if nav == href %}class="on"{% endif %}>{{ label }}</a>{% endfor %}<span class="sp"></span>
<form class="inline" method="post" action="/logout">{{ tok }}<button style="padding:6px 12px">Log out</button></form></nav>{% endif %}
<main>{% block main %}{% endblock %}</main></body></html>"""

TEMPLATES = {
    "base.html": BASE,
    "login.html": """{% extends "base.html" %}{% block main %}
<h1>Kiwi</h1><p class="muted">The control page of your network.</p>
<form method="post" action="/login" class="card">
{% if locked %}<p class="warn"><strong>The network's keys are locked.</strong> That is normal after a restart or a
power cut. Your password unlocks them; until then this machine looks after nothing by itself.</p>
{% if auto %}<p class="muted">It also tries to unlock by itself, with the help of {{ auto.server }}, which has not
answered yet. Is that machine on?</p>{% endif %}{% endif %}
<label for="pw">Password</label><input id="pw" type="password" name="password" autocomplete="current-password" autofocus>
{% if error %}<p class="bad">{{ error }}</p>{% endif %}
<div class="actions"><button class="primary">{{ "Unlock and log in" if locked else "Log in" }}</button></div></form>
<details><summary>I forgot the password</summary><p class="muted">On the computer the network was set up with:
<code>kiwi-server serve &lt;fleet&gt; --set-password</code>, then <code>kiwi-server control push &lt;fleet&gt;</code>.
The old password cannot be read anywhere.</p></details>{% endblock %}""",

    "index.html": """{% extends "base.html" %}{% block main %}
{% set bad = machines|selectattr("state", "in", ["bad", "down"])|list %}{% set warn = machines|selectattr("state", "equalto", "warn")|list %}
{% if looking or not looked %}<div class="banner warn">Checking your machines… this takes a minute.</div>
<script>setTimeout(function(){location.reload()},6000)</script>
{% elif bad %}<div class="banner bad">{{ bad|length }} of {{ machines|length }} machines {{ "has" if bad|length == 1 else "have" }} a problem</div>
{% elif warn %}<div class="banner warn">Everything runs. {{ warn|length }} {{ "machine wants" if warn|length == 1 else "machines want" }} a look.</div>
{% else %}<div class="banner ok">All {{ machines|length }} machines are fine</div>{% endif %}
<div class="row"><span class="muted">{% if looked %}Checked {{ ago(looked) }} ago. It checks every ten minutes by itself.{% endif %}
{% if error %}<span class="bad">{{ error }}</span>{% endif %}</span>
<form class="inline" method="post" action="/refresh">{{ tok }}<button>Check now</button></form></div>

{% set todo = machines|selectattr("problems")|list %}{% if todo %}<h2>Needs a look</h2>
{% for m in todo %}{% for p in m.problems %}<div class="card"><div class="row"><strong><span class="dot {{ p.level }}"></span>{{ m.name }}</strong>
<a class="btn" href="/hosts/{{ m.name }}">Open</a></div><p>{{ p.text }}</p>
{% if hint(p.text) %}<p class="muted">{{ hint(p.text) }}</p>{% endif %}</div>{% endfor %}{% endfor %}{% endif %}

<h2>Your machines</h2>
{% for m in machines %}<a class="tile" href="/hosts/{{ m.name }}" style="margin:8px 0"><div class="row">
<b><span class="dot {{ m.state }}"></span>{{ m.name }}</b><span class="state {{ m.state }}">{{ words(m.state) }}</span></div>
<span>{{ kind(m.role) }}{% if m.sites %} · {{ m.sites|length }} services{% endif %}</span></a>
{% else %}<p class="muted">No machines yet.</p>{% endfor %}

{% if tiles %}<h2>Open a service</h2><p class="muted">These open from a device that is on your VPN.</p>
<div class="tiles">{% for t in tiles %}<a class="tile" href="https://{{ t.site }}" target="_blank" rel="noopener">
<b>{{ t.label }}</b><span>{{ t.site }}</span></a>{% endfor %}</div>{% endif %}

<h2>Keep it up to date</h2><div class="card"><p>Brings every machine to your network's current settings and the
newest versions of its services, one machine after the other. <span class="muted">Services that changed restart for a
moment. Do it now and then, and after changing settings in the app.</span></p>
<form method="post" action="/run" onsubmit="return confirm('Update every machine now? This takes a while; services that changed restart for a moment.')">{{ tok }}
<input type="hidden" name="action" value="update"><button class="primary">Update everything</button></form></div>

<h2>Do something</h2><div class="tiles">
<a class="tile" href="/devices"><b>Add a phone or laptop</b><span>Put a device on your VPN with a QR code</span></a>
<a class="tile" href="/controllers"><b>Add a computer</b><span>Let another computer manage the network</span></a>
<a class="tile" href="/backups"><b>Backups</b><span>See when everything was last saved</span></a>
<a class="tile" href="/help"><b>Help</b><span>What the words on this page mean</span></a></div>
{% endblock %}""",

    "host.html": """{% extends "base.html" %}{% block main %}
<p><a href="/">← Home</a></p>
<h1><span class="dot {{ m.state }}"></span>{{ m.name }} <span class="state {{ m.state }}" style="font-size:1rem">{{ words(m.state) }}</span></h1>
<p class="muted">{{ kind(m.role) }} · {{ m.hostname }}{% if m.facts.os %} · {{ m.facts.os }}{% endif %}{% if m.facts.uptime is number %} · on for {{ age(m.facts.uptime) }}{% elif m.facts.uptime %} · {{ m.facts.uptime }}{% endif %}</p>
{% for p in m.problems %}<div class="card"><p><span class="dot {{ p.level }}"></span>{{ p.text }}</p>
{% if hint(p.text) %}<p class="muted">{{ hint(p.text) }}</p>{% endif %}</div>{% endfor %}
{% if not m.known %}<div class="card"><p>This machine has not answered yet.</p><p class="muted">{{ m.next }}</p></div>{% endif %}

<h2>What you can do</h2><div class="card"><div class="what">
<form method="post" action="/run">{{ tok }}<input type="hidden" name="action" value="status"><input type="hidden" name="host" value="{{ m.name }}"><button>Check now</button></form>
<p>Asks the machine how it is. <span class="muted">Changes nothing.</span></p>
<form method="post" action="/run">{{ tok }}<input type="hidden" name="action" value="plan"><input type="hidden" name="host" value="{{ m.name }}"><button>Preview</button></form>
<p>Shows what <em>Apply</em> would change on it. <span class="muted">Changes nothing.</span></p>
<form method="post" action="/run" onsubmit="return confirm('Apply to {{ m.name }} now? Services that changed restart for a moment.')">{{ tok }}
<input type="hidden" name="action" value="apply"><input type="hidden" name="host" value="{{ m.name }}"><button class="primary">Apply</button></form>
<p>Sets the machine up the way your network's settings say: new settings, updates of its services, repairs.
<span class="muted">Services that changed restart for a moment. Safe to run again.</span></p></div></div>

{% if tiles %}<h2>Its services</h2><div class="tiles">{% for t in tiles %}<a class="tile" href="https://{{ t.site }}" target="_blank" rel="noopener">
<b>{{ t.label }}</b><span>{{ t.site }}</span></a>{% endfor %}</div>{% endif %}

{% if m.notes or m.facts %}<h2>Details</h2><div class="card"><dl>
{% if m.facts.running is defined and m.facts.expected is defined %}<dt>Services</dt><dd>{{ m.facts.running|length if m.facts.running is iterable and m.facts.running is not string else m.facts.running }} running</dd>{% endif %}
{% if m.facts.backup and m.facts.backup.age is not none %}<dt>Last backup</dt><dd>{{ age(m.facts.backup.age) }} ago</dd>{% endif %}
{% if m.facts.reboot %}<dt>Updates</dt><dd>installed, it restarts in its update window</dd>{% endif %}
{% if m.vpn_ip %}<dt>VPN address</dt><dd>{{ m.vpn_ip }}</dd>{% endif %}
<dt>Parts</dt><dd>{{ m.modules|join(", ") }}</dd></dl>
{% if m.notes %}<ul class="muted">{% for n in m.notes %}<li>{{ n }}</li>{% endfor %}</ul>{% endif %}
{% if m.output %}<details><summary>What the machine said, word for word</summary><pre>{{ m.output }}</pre></details>{% endif %}</div>{% endif %}
{% endblock %}""",

    "job.html": """{% extends "base.html" %}{% block main %}
<p><a href="{{ back }}">← Back</a></p><h1>{{ title }}</h1>
<p id="say" class="banner {{ 'ok' if job.state == 'ok' else 'bad' if job.state == 'failed' else 'warn' }}">{{ say[job.state] }}</p>
<details {% if job.state == 'failed' %}open{% endif %}><summary>What is happening, line by line</summary><pre id="log"></pre></details>
<script>
const say={{ say|tojson }};let off=0;const log=document.getElementById("log"),el=document.getElementById("say");
async function poll(){const r=await fetch("/api/jobs/{{ job.id }}/log?offset="+off);if(!r.ok)return;
const d=await r.json();log.textContent+=d.text;off=d.offset;el.textContent=say[d.state];
el.className="banner "+(d.state=="ok"?"ok":d.state=="failed"?"bad":"warn");
if(d.state=="failed")log.parentElement.open=true;
if(d.state=="waiting"||d.state=="running")setTimeout(poll,1500)}
poll();
</script>{% endblock %}""",

    "devices.html": """{% extends "base.html" %}{% block main %}
<h1>Devices</h1><p class="muted">Phones, tablets and laptops that use your VPN. On the VPN a device reaches your
services from anywhere and browses through your network.</p>
{% if config %}<div class="card"><h2 style="margin-top:0">{{ shown }} is ready</h2>
<ol><li>Install the <strong>WireGuard</strong> app on the device (App Store, Play Store, wireguard.com).</li>
<li>In the app: <strong>+</strong>, then <strong>Scan from QR code</strong>. Scan this:</li></ol>
{% if qr %}<div class="qr">{{ qr }}</div>{% else %}<p class="warn">No QR code could be made here; use the file.</p>{% endif %}
<p class="muted">On a laptop, import the file instead:</p>
<button onclick="const b=new Blob([document.getElementById('conf').textContent],{type:'text/plain'});const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='{{ shown }}.conf';a.click()">Download {{ shown }}.conf</button>
<pre id="conf" hidden>{{ config }}</pre>
<p class="warn small">Whoever has this code or file is on your VPN as this device. Do not send it around.</p></div>{% endif %}
{% if error %}<p class="banner bad">{{ error }}</p>{% endif %}
<h2>Add a phone or laptop</h2><form method="post" action="/devices/add" class="card">{{ tok }}
<label for="n">Name</label><input id="n" name="name" placeholder="annas-phone" pattern="[a-z0-9][a-z0-9-]*" required>
<p class="muted small">Lowercase letters, digits and dashes.</p>
{% if groups %}<label for="g">Who is it for?</label><select id="g" name="group">
{% for g in groups %}<option value="{{ g }}" {% if g == "devices" %}selected{% endif %}>{{ g }}</option>{% endfor %}</select>
<p class="muted small">The group decides what the device may reach.</p>{% endif %}
<div class="actions"><button class="primary">Add it</button></div></form>
<h2>Devices on your VPN</h2>
{% for d in devices %}<div class="card row"><strong>{{ d }}</strong>
<span><form class="inline" method="post" action="/devices/show">{{ tok }}<input type="hidden" name="name" value="{{ d }}"><button>Show its QR code</button></form>
<form class="inline" method="post" action="/devices/remove" onsubmit="return confirm('Take {{ d }} off the VPN? Its connection stops working at once.')">{{ tok }}
<input type="hidden" name="name" value="{{ d }}"><button>Remove</button></form></span></div>
{% else %}<p class="muted">None yet.</p>{% endfor %}
{% if removed %}<p class="banner ok">{{ removed }} is off the VPN. Its connection no longer works.</p>{% endif %}
<details><summary>A phone was lost or stolen</summary><p class="muted">Remove it here. From that moment it cannot reach
your network, even with the WireGuard app still on it.</p></details>
{% endblock %}""",

    "backups.html": """{% extends "base.html" %}{% block main %}
<h1>Backups</h1><p class="muted">Two things are saved: what is <strong>on</strong> each machine (your files, photos,
passwords), every night, and the <strong>network's own settings and keys</strong>.</p>
<h2>Your machines' data</h2>
{% for m in machines %}{% set b = m.facts.backup %}<div class="card"><div class="row"><strong>{{ m.name }}</strong>
{% if b and b.last %}<span class="state {{ 'ok' if b.age < 129600 else 'bad' }}">saved {{ age(b.age) }} ago</span>
{% elif "backup" in m.modules %}<span class="state warn">no backup seen yet</span>
{% else %}<span class="muted">not backed up</span>{% endif %}</div>
{% if "backup" in m.modules %}<form class="inline" method="post" action="/run">{{ tok }}<input type="hidden" name="action" value="save">
<input type="hidden" name="host" value="{{ m.name }}"><button>Save now</button></form>{% endif %}
{% if b and b.copies %}<p class="muted">Kept on: {% for c in b.copies %}{{ c.server }}{{ ", " if not loop.last }}{% endfor %}</p>{% endif %}
{% if "backup" not in m.modules %}<p class="muted small">Nothing on this machine is set to be saved. Machines with your data should have the <em>backup</em> part.</p>{% endif %}
{% if m.facts.backup_store %}<p class="muted">It keeps the backups of: {% for r in m.facts.backup_store.repos %}{{ r.client }} ({{ r.snapshots }} versions){{ ", " if not loop.last }}{% endfor %}</p>{% endif %}
</div>{% else %}<p class="muted">No machines yet.</p>{% endfor %}
<h2>The network itself</h2><div class="card"><p>Its settings and keys, encrypted, copied to your machines.
With it and your recovery key, the whole network can be rebuilt on a new computer.</p>
<form method="post" action="/run">{{ tok }}<input type="hidden" name="action" value="backup"><button class="primary">Save it now</button></form></div>
<details><summary>How do I get something back?</summary><p class="muted">A file: open the service (Nextcloud, Immich…) and use its
own trash or versions first. A whole machine: on the computer the network was set up with, <code>kiwi-server restore</code>
and the machine's page in the Kiwi Server app.</p></details>
{% endblock %}""",

    "controllers.html": """{% extends "base.html" %}{% block main %}
{% if changed %}<p class="banner ok">The password is changed. Use the new one from now on.</p>{% endif %}
<h1>Computers</h1><p class="muted">The computers that can manage this network with the Kiwi Server app. Each has its own key.</p>
{% for name, key in ways %}<div class="card row"><span><strong>{{ name }}</strong>
{% if name == "control" %}<span class="muted"> · this machine (your password)</span>
{% elif name == "recovery" %}<span class="muted"> · your recovery key, on paper</span>
{% elif name == "desktop" %}<span class="muted"> · the computer the network was set up with</span>{% endif %}</span>
{% if name not in ("control", "recovery") %}<form class="inline" method="post" action="/controllers/remove"
onsubmit="return confirm('Remove {{ name }}? It can no longer open the network.')">{{ tok }}
<input type="hidden" name="name" value="{{ name }}"><button>Remove</button></form>{% endif %}</div>{% endfor %}
<h2>Add a computer</h2>
{% if invite %}<div class="card"><ol><li>On the new computer, open <strong>Kiwi Server</strong>.</li>
<li>Choose <strong>Join a fleet</strong> and paste this link.</li></ol>
<pre id="inv">{{ invite }}</pre>
<button class="primary" onclick="navigator.clipboard.writeText(document.getElementById('inv').textContent);this.textContent='Copied'">Copy the link</button>
<p class="muted small">It works once, for fifteen minutes. The computer must be on your VPN.</p></div>
{% else %}<form method="post" action="/controllers/invite" class="card">{{ tok }}
<p>Makes a link. Pasted into Kiwi Server on another computer, it gives that computer the whole network to manage.</p>
<button class="primary">Add a computer</button></form>{% endif %}
{% if error %}<p class="banner bad">{{ error }}</p>{% endif %}
<h2>If a computer was lost or stolen</h2>
<div class="card"><p>Removing it stops it from getting anything new. It still knows the keys it saw.
Replace them here, one after the other.</p>
<p><strong>1. The keys that open your machines</strong><br><span class="muted small">
{% if rot.pending %}Under way: {{ rot.pending|join(", ") }} still on the old keys. Switch them on, then press again.
{% elif rot.last %}Last replaced {{ rot.last[:10] }}.{% else %}Never replaced.{% endif %}
Every machine gets a new key; the old one stops working when the last machine has changed. Nothing restarts.</span></p>
<form method="post" action="/run" onsubmit="return confirm('Replace the keys on every machine now?')">{{ tok }}
<input type="hidden" name="action" value="rotate"><button class="primary">Replace the keys</button></form>
{% for n in rot.pending %}<form class="inline" method="post" action="/run" style="margin-top:8px"
onsubmit="return confirm('Leave {{ n }} out? Its old key stops working when the others are done. Only for a machine that is gone or will be installed anew.')">{{ tok }}
<input type="hidden" name="action" value="rotate"><input type="hidden" name="host" value="{{ n }}">
<button>Leave {{ n }} out</button></form>{% endfor %}
<p class="muted small">Something outside this network that you gave the network's key by hand (a desktop, a NAS) is not
reached: it needs the new key.</p>
<p style="margin-top:18px"><strong>2. The secrets inside your services</strong><br><span class="muted small">
{% if rot.seed_pending %}Under way: {{ rot.seed_pending|join(", ") }} still on the old ones. Switch them on, then press again.
{% elif rot.seed_last %}Last replaced {{ rot.seed_last[:10] }}.{% else %}Never replaced.{% endif %}
The passwords your services use among themselves, and the ones of the ad blocker's and the VPN's admin pages. Each
machine is updated; some services restart for a moment and may ask you to sign in again. Database and
admin passwords of Nextcloud, Immich and the like stay as they are for now.</span></p>
<form method="post" action="/run" onsubmit="return confirm('Replace the secrets on every machine now? Some services restart.')">{{ tok }}
<input type="hidden" name="action" value="rotate-seed"><button class="primary">Replace the secrets</button></form>
{% for n in rot.seed_pending %}<form class="inline" method="post" action="/run" style="margin-top:8px"
onsubmit="return confirm('Leave {{ n }} out? It takes the new secrets at its next update.')">{{ tok }}
<input type="hidden" name="action" value="rotate-seed"><input type="hidden" name="host" value="{{ n }}">
<button>Leave {{ n }} out</button></form>{% endfor %}
<p style="margin-top:18px"><strong>3. The password of this page</strong></p>
<form method="post" action="/controllers/password">{{ tok }}
<input type="password" name="old" placeholder="Current password" autocomplete="current-password" required>
<input type="password" name="new" placeholder="New password (12 characters or more)" autocomplete="new-password" minlength="12" required>
<input type="password" name="again" placeholder="New password again" autocomplete="new-password" minlength="12" required>
<button>Change the password</button></form>
<p class="muted small" style="margin-top:18px">Not replaced yet by this page: database and admin passwords of the
services, and the VPN keys of the machines. That comes in a later version.</p></div>
<details><summary>What happens when I remove one?</summary><p class="muted">It can no longer open the network or get
anything new. It still knows what it saw before; if that computer was lost or stolen, replace the keys above.</p></details>
{% endblock %}""",

    "activity.html": """{% extends "base.html" %}{% block main %}
<h1>Activity</h1><p class="muted">What was done here, by whom.</p>
<h2>Tasks</h2>{% for j in jobs %}<a class="tile" href="/jobs/{{ j.id }}" style="margin:8px 0"><div class="row">
<b>{{ verb(j.action) }} {{ j.hosts|join(" ") }}</b><span class="state {{ j.state }}">{{ jobword(j.state) }}</span></div></a>
{% else %}<p class="muted">None since this page was last unlocked.</p>{% endfor %}
<h2>Log</h2>{% for e in entries %}<div class="card row"><span>{{ e.what }} {{ (e.hosts or [])|join(" ") }}</span>
<span class="muted small">{{ e.time[:16]|replace("T", " ") }} · {{ e.user }}{% if e.address %} · {{ e.address }}{% endif %}</span></div>
{% else %}<p class="muted">Empty.</p>{% endfor %}
<div class="actions"><form class="inline" method="post" action="/lock"
onsubmit="return confirm('Lock the network\\'s keys? Nothing is checked or done by itself until the next login.')">{{ tok }}<button>Lock the keys now</button></form></div>
{% endblock %}""",

    "help.html": """{% extends "base.html" %}{% block main %}
<h1>Help</h1>
<h2>What is this page?</h2><p>This machine looks after your network. It checks every machine every ten minutes, and from
here you can see how they are, bring one up to date, add a phone to your VPN and let another computer help manage
it. Version {{ version }}.</p>
<h2>How do I…</h2><div class="card"><dl>
<dt>…use my services on the go?</dt><dd><a href="/devices">Add your phone</a>, scan the code with the WireGuard app, switch the VPN on.</dd>
<dt>…fix a machine with a problem?</dt><dd>Open it from <a href="/">Home</a>, read what it says, then <em>Apply</em>. If it does not answer at all, check that it is on.</dd>
<dt>…change a setting or add a machine?</dt><dd>In the Kiwi Server app on your computer. It sends the change here by itself.</dd>
<dt>…get in after a power cut?</dt><dd>{% if auto %}Nothing to do: this machine unlocks by itself with the help of
{{ auto.server }} (the ring “{{ auto.ring }}”). If that machine is off, log in here once.{% else %}Log in here once. That
unlocks the keys again. It can also unlock by itself with the help of another of your machines: in the app, give this
machine's settings <code>unlock: { control: &lt;a ring on another machine&gt; }</code>.{% endif %}</dd></dl></div>
<h2>The words</h2><div class="card"><dl>
<dt>Machine</dt><dd>A computer of your network: a small server at home, or a rented one.</dd>
<dt>Master</dt><dd>The machine in the middle, usually a rented server. Everything connects to it; it holds none of your data.</dd>
<dt>Home server (node)</dt><dd>A machine at your place. Your files, photos and passwords live here.</dd>
<dt>VPN</dt><dd>The private network between your machines and devices. On it, your services open from anywhere.</dd>
<dt>Device</dt><dd>A phone, tablet or laptop that uses the VPN.</dd>
<dt>Apply</dt><dd>Set a machine up the way the settings say. Safe to repeat.</dd>
<dt>Locked</dt><dd>The network's keys are stored encrypted on this machine. Your password opens them into memory;
a restart closes them again. A thief with this machine's disk gets nothing readable.</dd>
<dt>Computer</dt><dd>A computer with the Kiwi Server app that may manage the network.</dd></dl></div>
{% endblock %}""",
}


# what the pages call things
KIND = {"master": "Master", "node": "Home server (node)", "node-gw": "Home server and gateway",
        "node-cloud": "Home server", "bare": "Bare machine"}
WORDS = {"ok": "fine", "warn": "wants a look", "bad": "has a problem", "down": "not reachable", "unknown": "not checked yet"}
JOBWORD = {"waiting": "waiting", "running": "running", "ok": "done", "failed": "did not work"}
VERB = {"status": "Checking", "plan": "Previewing", "apply": "Applying to", "check": "Checking the names",
        "backup": "Saving the network", "watch": "Looking for restarted masters", "save": "Saving the data of",
        "update": "Updating every machine", "rotate": "Replacing the SSH keys",
        "rotate-seed": "Replacing the secrets inside the services"}
SERVICES = {"cloud": "Files (Nextcloud)", "vault": "Passwords (Vaultwarden)", "pihole": "Ad blocker (Pi-hole)",
            "portainer": "Containers (Portainer)", "immich": "Photos (Immich)", "paperless": "Documents (Paperless)",
            "opencloud": "Files (OpenCloud)", "dl": "Downloads (Transmission)", "jd": "Downloads (JDownloader)",
            "wg": "VPN devices (wg-easy)", "control": "This page", "backup": "Backup store", "cache": "Local cache",
            "hub": "Hub page", "relay": "Relay", "office": "Office (Collabora)"}
HINTS = (
    ("unreachable", "It did not answer. Is it switched on and online? After a restart, give it a minute, then check again."),
    ("disk is locked", "Its disk is encrypted and waits to be unlocked after a restart. Unlock it from the computer the "
                       "network was set up with."),
    ("reboot", "It installed updates and restarts by itself in its update window. Nothing to do."),
    ("backup", "Its last backup is older than it should be. Is the machine that keeps the backups on?"),
    ("not running", "A service on it stopped. Apply usually brings it back."),
    ("role", "Its setup did not finish. Apply runs it again."),
    ("clock", "Its clock is off, which breaks certificates and backups. Apply sets it right."),
)


def hint(text):
    low = (text or "").lower()
    return next((h for key, h in HINTS if key in low), "")


def age(seconds):
    s = max(0, int(seconds or 0))
    return ("%d seconds" % s if s < 90 else "%d minutes" % (s // 60) if s < 5400
            else "%d hours" % (s // 3600) if s < 172800 else "%d days" % (s // 86400))


def ago(t):
    return age(time.time() - t)


def tiles(machines):
    out = []
    for m in machines:
        for site in m["sites"]:
            first = site.split(".", 1)[0]
            out.append({"site": site, "label": SERVICES.get(first, first.capitalize()), "machine": m["name"]})
    return out


def make_app(control: Control):
    env = jinja2.Environment(loader=jinja2.DictLoader(TEMPLATES), autoescape=True)
    env.globals.update(version=VERSION, ago=ago, age=age, hint=hint, words=lambda s: WORDS.get(s, s),
                       kind=lambda r: KIND.get(r, r), verb=lambda a: VERB.get(a, a), jobword=lambda s: JOBWORD.get(s, s))
    app = FastAPI(title="Kiwi control", version=VERSION, docs_url=None, redoc_url=None)

    def page(template, request=None, nav=None, **kw):
        # tok: the hidden field every form of a logged-in page carries (see posted())
        tok = ""
        if request is not None:
            tok = markupsafe.Markup('<input type="hidden" name="csrf" value="%s">'
                                    % control.csrf(request.cookies.get(COOKIE)))
        return HTMLResponse(env.get_template(template).render(tok=tok, nav=nav, **kw))

    def user(request):
        return control.session(request.cookies.get(COOKIE))

    def address(request):
        return request.headers.get("x-real-ip") or (request.client.host if request.client else "?")

    def posted(request, csrf):
        """The user, when the form came from one of this session's own pages. Not by the
        Origin header: under the proxy's no-referrer policy browsers send `Origin: null` with
        a form, the same as a hostile page can. The session's token in the form is what another
        site cannot know, and the cookie is SameSite=Strict besides."""
        u = user(request)
        if u and hmac.compare_digest(control.csrf(request.cookies.get(COOKIE)), csrf or ""):
            return u
        return None

    def login_page():
        return RedirectResponse("/login", status_code=303)

    def machines():
        """Every machine of the fleet file with what it last said: (machines, status, fleet)."""
        st = control.status() or {}
        info = control.fleet_info() or {}
        said = {h.get("host"): h for h in st.get("hosts") or []}
        out = []
        for h in info.get("hosts") or []:
            s = said.get(h["name"])
            out.append({"name": h["name"], "hostname": h["hostname"], "role": h["role"], "modules": h.get("modules") or [],
                        "sites": h.get("sites") or [], "vpn_ip": h.get("vpn_ip") or "", "next": h.get("next") or "",
                        "known": bool(s), "state": (s or {}).get("state") or "unknown",
                        "problems": (s or {}).get("problems") or [], "notes": (s or {}).get("notes") or [],
                        "facts": (s or {}).get("facts") or {}, "output": (s or {}).get("output") or ""})
        for name, s in said.items():            # a look at a machine the fleet file no longer names
            if name not in [m["name"] for m in out]:
                out.append({"name": name, "hostname": name, "role": "", "modules": [], "sites": [], "vpn_ip": "",
                            "next": "", "known": True, "state": s.get("state") or "unknown",
                            "problems": s.get("problems") or [], "notes": s.get("notes") or [],
                            "facts": s.get("facts") or {}, "output": s.get("output") or ""})
        return out, st, info

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "version": VERSION, "locked": control.locked()}

    @app.get("/login")
    def login_form():
        return page("login.html", locked=control.locked(), auto=control.auto_state())

    @app.post("/login")
    def login(request: Request, password: str = Form("")):
        was_locked = control.locked()
        token = control.login(password, address(request))    # needs the password itself: nothing to forge
        if not token:
            time.sleep(1)
            return page("login.html", locked=control.locked(), auto=control.auto_state(),
                        error="That password did not work. After five wrong tries, wait ten minutes.")
        if was_locked:                          # just unlocked: look at the machines right away
            control.look()
        r = RedirectResponse("/", status_code=303)
        secure = request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https"
        r.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=secure, max_age=12 * 3600)
        return r

    @app.post("/logout")
    def logout(request: Request, csrf: str = Form("")):
        r = login_page()
        if posted(request, csrf):
            r.delete_cookie(COOKIE)
        return r

    @app.post("/lock")
    def lock(request: Request, csrf: str = Form("")):
        u = posted(request, csrf)
        if u:
            control.lock_store(u)
        return login_page()

    @app.get("/")
    def index(request: Request):
        if not user(request):
            return login_page()
        ms, st, _info = machines()
        return page("index.html", request, "/", machines=ms, tiles=tiles(ms), looked=st.get("time"),
                    error=st.get("error"), looking=control.looking)

    @app.get("/hosts/{name}")
    def host(request: Request, name: str):
        if not user(request):
            return login_page()
        ms, _st, _info = machines()
        m = next((x for x in ms if x["name"] == name), None)
        if not m:
            return RedirectResponse("/", status_code=303)
        return page("host.html", request, "/", m=m, tiles=tiles([m]))

    @app.post("/refresh")
    def refresh(request: Request, csrf: str = Form("")):
        if not posted(request, csrf):
            return login_page()
        control.look()                          # in the background; Home says it is checking
        return RedirectResponse("/", status_code=303)

    @app.post("/run")
    def run(request: Request, action: str = Form(...), host: str = Form(""), csrf: str = Form("")):
        u = posted(request, csrf)
        if not u:
            return login_page()
        if action not in ACTIONS:
            return JSONResponse({"error": "unknown action"}, status_code=400)
        try:
            jid = control.start(action, [host] if host else [], user=u)
        except KiwiError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return RedirectResponse("/jobs/" + jid, status_code=303)

    @app.get("/jobs/{jid}")
    def job(request: Request, jid: str):
        if not user(request):
            return login_page()
        j = control.job(jid)
        if not j:
            return RedirectResponse("/activity", status_code=303)
        what = " ".join(j["hosts"]) or "all machines"
        title = "%s %s" % (VERB.get(j["action"], j["action"]), what if j["action"] in ("status", "plan", "apply", "save") else "")
        say = {"waiting": "Waiting for another task to finish…", "running": "Working… this can take a few minutes.",
               "ok": "Done.", "failed": "That did not work. The lines below say why."}
        if j["action"] == "plan":
            say["ok"] = "Done. Nothing was changed; the lines below say what Apply would do."
        if j["action"] in ("rotate", "rotate-seed"):      # a machine that is off is no failure: it waits
            say["failed"] = ("Not every machine could be changed. The ones that wait are listed under Computers; "
                             "the lines below say why.")
        back = ("/backups" if j["action"] in ("save", "backup") else "/controllers" if j["action"] in ("rotate", "rotate-seed")
                else "/hosts/" + j["hosts"][0] if j["hosts"] else "/")
        return page("job.html", request, "/activity", job=j, title=title.strip(), say=say, back=back)

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

    # ---- devices ------------------------------------------------------------------------
    def devices_page(request, **kw):
        info = control.fleet_info() or {}
        return page("devices.html", request, "/devices", devices=control.devices(),
                    groups=sorted(info.get("groups") or {}), **kw)

    @app.get("/devices")
    def devices(request: Request):
        if not user(request):
            return login_page()
        return devices_page(request)

    @app.post("/devices/{what}")
    def devices_do(request: Request, what: str, name: str = Form(""), group: str = Form(""), csrf: str = Form("")):
        u = posted(request, csrf)
        if not u:
            return login_page()
        name = name.strip().lower()
        try:
            if what == "remove":
                control.remove_device(name, u)
                return devices_page(request, removed=name)
            config = control.add_device(name, group, u) if what == "add" else control.device_config(name)
        except KiwiError as e:
            return devices_page(request, error=str(e))
        if not config:
            return devices_page(request, error="There is no device called %s." % name)
        return devices_page(request, config=config, shown=name, qr=markupsafe.Markup(control.qr_svg(config)))

    @app.get("/backups")
    def backups(request: Request):
        if not user(request):
            return login_page()
        ms, _st, _info = machines()
        return page("backups.html", request, "/backups", machines=ms)

    @app.get("/help")
    def help_page(request: Request):
        if not user(request):
            return login_page()
        return page("help.html", request, "/help", auto=control.auto_state())

    @app.get("/activity")
    def activity(request: Request):
        if not user(request):
            return login_page()
        return page("activity.html", request, "/activity", jobs=control.jobs(15), entries=control.audit_tail(40))

    @app.get("/audit")
    def audit():
        return RedirectResponse("/activity", status_code=303)

    # ---- computers (design/controllers.md) -------------------------------------------------
    def base_url(request):
        proto = "https" if request.headers.get("x-forwarded-proto") == "https" else request.url.scheme
        return "%s://%s" % (proto, request.headers.get("host") or request.url.netloc)

    def controllers_page(request, **kw):
        return page("controllers.html", request, "/controllers", ways=control.controllers(), rot=control.rotation(), **kw)

    @app.get("/controllers")
    def controllers(request: Request):
        if not user(request):
            return login_page()
        return controllers_page(request)

    @app.post("/controllers/{what}")
    def controllers_do(request: Request, what: str, name: str = Form(""), csrf: str = Form(""),
                       old: str = Form(""), new: str = Form(""), again: str = Form("")):
        u = posted(request, csrf)
        if not u:
            return login_page()
        try:
            if what == "invite":
                return controllers_page(request, invite=control.new_invite(base_url(request), u))
            if what == "remove":
                control.remove(name, u)
            if what == "password":
                if new != again:
                    raise KiwiError("the two new passwords are not the same")
                control.change_password(old, new, u)
                return controllers_page(request, changed=True)
        except KiwiError as e:
            return controllers_page(request, error=str(e))
        return RedirectResponse("/controllers", status_code=303)

    @app.get("/api/ca")
    def api_ca():
        """Public: the fleet CA's certificate and the store's id, for a computer that joins."""
        pem = control.ca_pem()
        if not pem:
            return JSONResponse({"error": "no CA yet"}, status_code=404)
        return {"ca": pem, "store": control.store.store_id()}

    @app.post("/api/join")
    async def api_join(request: Request):
        try:
            body = await request.json()
        except ValueError:
            body = {}
        name = await run_in_threadpool(control.join, str(body.get("code") or ""), str(body.get("name") or ""),
                                       str(body.get("key") or ""), address(request))
        if not name:
            await asyncio.sleep(1)
            return JSONResponse({"error": "refused"}, status_code=403)
        return {"name": name}

    @app.post("/api/controller/hello")
    async def api_hello(request: Request):
        try:
            body = await request.json()
        except ValueError:
            body = {}
        blob = control.challenge(str(body.get("name") or ""))
        if blob is None:
            return JSONResponse({"error": "unknown"}, status_code=404)
        return {"challenge": base64.b64encode(blob).decode()}

    def bearer(request):
        auth = request.headers.get("authorization") or ""
        return control.controller(auth[7:]) if auth.startswith("Bearer ") else None

    @app.get("/api/fleet/revision")
    def api_revision(request: Request):
        if not bearer(request):
            return JSONResponse({"error": "who?"}, status_code=401)
        return {"revision": control.revision()}

    @app.get("/api/fleet")
    def api_fleet(request: Request):
        who = bearer(request)
        if not who:
            return JSONResponse({"error": "who?"}, status_code=401)
        revision, files = control.export()
        control.audit(who, "fetched the fleet", address=address(request))
        return {"revision": revision, "files": {n: base64.b64encode(b).decode() for n, b in files.items()}}

    @app.put("/api/fleet")
    async def api_fleet_put(request: Request):
        who = bearer(request)
        if not who:
            return JSONResponse({"error": "who?"}, status_code=401)
        try:
            body = await request.json()
            files = {str(n): base64.b64decode(b) for n, b in (body.get("files") or {}).items()}
            new = await run_in_threadpool(control.replace, str(body.get("base") or ""), files, who)
        except (ValueError, KiwiError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        if new is None:
            return JSONResponse({"error": "the fleet here is another than the one you built on"}, status_code=409)
        return {"revision": new}

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
