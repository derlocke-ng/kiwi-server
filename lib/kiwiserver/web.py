"""`kiwi-server serve`: the control node's pages and API (design/control-plane.md). FastAPI
with Jinja templates and a few lines of JavaScript for a job's live log: no build toolchain.
Meant behind the reverse proxy on the mesh (the control module); the login is the password
set with `kiwi-server serve FLEET --set-password`; it opens the encrypted store (control.py).
"""
import asyncio
import base64
import hmac
import math
import os
import re
import subprocess
import threading
import time
import urllib.parse

import jinja2
import markupsafe
from fastapi import FastAPI, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from . import VERSION, form as formmod, health as healthmod
from .control import ACTIONS, Control
from .util import KiwiError

COOKIE = "kiwi_session"
COPY_MAX = 64 * 1024 * 1024         # a copy of a network is a few hundred KB; an upload larger than this is refused

BASE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{% block title %}Kiwi{% endblock %}</title>
<script>(function(){var t=document.cookie.match(/(?:^|; )kiwi_theme=(light|dark)/);if(t)document.documentElement.dataset.theme=t[1]})()</script>
<style>
:root{--bg:#f5f6f7;--fg:#16191b;--card:#fff;--line:#e4e7ea;--line2:#c9ced3;--muted:#667078;--accent:#13805c;--onaccent:#fff;
--tint:#e7f4ee;--hover:#f0f2f4;--off:#9aa2a8;
--ok:#13805c;--warn:#b26b00;--bad:#c4332b;--okbg:#e5f5ed;--warnbg:#fcf1de;--badbg:#fdeceb}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0c0e0f;--fg:#e7e9ea;--card:#141719;--line:#23282b;--line2:#353b3f;--muted:#8a9297;--accent:#38c793;--onaccent:#06150e;
--tint:#16251f;--hover:#1a1e21;--off:#5e666b;
--ok:#38c793;--warn:#f0a945;--bad:#f2665c;--okbg:#10241c;--warnbg:#2a2114;--badbg:#2c1715}}
:root[data-theme=dark]{--bg:#0c0e0f;--fg:#e7e9ea;--card:#141719;--line:#23282b;--line2:#353b3f;--muted:#8a9297;--accent:#38c793;--onaccent:#06150e;
--tint:#16251f;--hover:#1a1e21;--off:#5e666b;
--ok:#38c793;--warn:#f0a945;--bad:#f2665c;--okbg:#10241c;--warnbg:#2a2114;--badbg:#2c1715}
{% if brand_accent %}:root{--accent:{{ brand_accent }}}{% endif %}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 Inter,system-ui,-apple-system,'Segoe UI',sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:860px;margin:0 auto;padding:12px 16px 48px}
.shell{display:grid;grid-template-columns:236px minmax(0,1fr);min-height:100vh}
.shell main{max-width:1180px;width:100%;padding:22px 32px 64px}
.side{position:sticky;top:0;height:100vh;overflow-y:auto;display:flex;flex-direction:column;gap:2px;padding:14px 10px;
border-right:1px solid var(--line);background:var(--card)}
.brand{display:flex;align-items:center;gap:10px;padding:4px 10px 14px;font-weight:700;font-size:1.05rem;color:var(--fg);text-decoration:none}
.brand svg{width:26px;height:26px;flex:none}.brand small{font-weight:400;color:var(--muted);font-size:.8rem}
.nav{display:flex;align-items:center;gap:10px;padding:7px 10px;border-radius:6px;color:var(--fg);text-decoration:none;font-size:.9rem}
.nav:hover{background:var(--hover)}.nav.on{background:var(--hover);color:var(--fg);font-weight:600;box-shadow:inset 2px 0 0 var(--accent)}
.ico{width:18px;height:18px;flex:none;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.grp{display:block;margin:16px 10px 4px;font-size:.68rem;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.nav .ico{color:var(--muted)}.nav.on .ico{color:var(--accent)}.ico{width:17px;height:17px}.nav .count{margin-left:auto;font-size:.78rem;color:var(--muted);font-weight:400}
.side .foot{margin-top:auto;padding-top:14px;display:flex;flex-direction:column;gap:8px}
.health{border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:.85rem;display:grid;grid-template-columns:1fr auto;gap:2px 8px}
.health b{text-align:right}.health .t{grid-column:1/3;color:var(--muted);font-size:.78rem}
.side .foot .row2{display:flex;gap:6px}.side .foot button{padding:6px 10px;font-size:.88rem;flex:1}
button.icon{display:inline-flex;align-items:center;justify-content:center;gap:6px}
.top,.bar{display:none}
@media (max-width:860px){.shell{display:block}.side{display:none}.shell main{padding:12px 16px 96px}
.top{display:flex;align-items:center;gap:10px;padding:8px 16px;border-bottom:1px solid var(--line);background:var(--card);position:sticky;top:0;z-index:3}
.top .brand{padding:0;flex:1}.top button{padding:6px 9px}
.bar{display:grid;grid-template-columns:repeat(4,1fr);position:fixed;left:0;right:0;bottom:0;z-index:3;background:var(--card);
border-top:1px solid var(--line);padding:4px 4px calc(4px + env(safe-area-inset-bottom))}
.bar>a,.bar summary{display:flex;flex-direction:column;align-items:center;gap:2px;padding:6px 2px;border-radius:8px;color:var(--muted);
text-decoration:none;font-size:.74rem;list-style:none;cursor:pointer}.bar summary::-webkit-details-marker{display:none}
.bar .ico{width:22px;height:22px}.bar>a.on,.bar details[open] summary{color:var(--accent);font-weight:600}.bar details{margin:0}
.sheet{position:fixed;left:8px;right:8px;bottom:calc(66px + env(safe-area-inset-bottom));background:var(--card);border:1px solid var(--line);
border-radius:12px;padding:8px;box-shadow:0 8px 30px rgba(0,0,0,.25);max-height:70vh;overflow:auto}.sheet form{padding:6px 2px 2px}.sheet button{width:100%}}
.pill{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;font-size:.82rem;font-weight:600;border:1px solid var(--line)}
.pill.ok{color:var(--ok);background:var(--okbg);border-color:transparent}.pill.warn{color:var(--warn);background:var(--warnbg);border-color:transparent}
.pill.bad{color:var(--bad);background:var(--badbg);border-color:transparent}
h1{font-size:1.4rem;font-weight:650;margin:14px 0 2px;letter-spacing:-.015em}h2{font-size:.98rem;font-weight:650;margin:28px 0 8px}
.sub{color:var(--muted);font-size:.9rem;margin:0 0 14px}p{margin:8px 0}
a{color:var(--accent)}.muted{color:var(--muted);font-size:.92rem}.small{font-size:.85rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin:10px 0}
.row{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}
.banner{border-radius:8px;padding:12px 16px;margin:12px 0;font-size:1rem;font-weight:600;border:1px solid transparent}
.banner.ok{border-color:color-mix(in srgb,var(--ok) 35%,transparent)}.banner.bad{border-color:color-mix(in srgb,var(--bad) 35%,transparent)}.banner.warn{border-color:color-mix(in srgb,var(--warn) 35%,transparent)}
.banner.ok{background:var(--okbg);color:var(--ok)}.banner.warn{background:var(--warnbg);color:var(--warn)}
.banner.bad{background:var(--badbg);color:var(--bad)}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px;background:var(--off)}
.dot.ok{background:var(--ok)}.dot.warn{background:var(--warn)}.dot.bad,.dot.down,.dot.failed{background:var(--bad)}
.dot.busy{animation:pulse 1.4s ease-in-out infinite}@keyframes pulse{50%{opacity:.35}}
.state{font-weight:600}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad,.down,.failed{color:var(--bad)}
button,.btn{font:inherit;font-size:.92rem;padding:8px 14px;border-radius:6px;border:1px solid var(--line);background:var(--card);
color:var(--fg);cursor:pointer;text-decoration:none;display:inline-block}button:hover,.btn:hover{border-color:var(--line2)}
button.primary,.btn.primary{background:var(--accent);border-color:var(--accent);color:var(--onaccent);font-weight:600}
form.inline{display:inline}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px}
.tile{display:block;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;text-decoration:none;color:var(--fg)}
.tile:hover,.mach:hover,a.stat:hover{border-color:var(--line2)}
.tile b{display:block}.tile span{color:var(--muted);font-size:.82rem;overflow-wrap:anywhere}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin:12px 0}
.stat{display:flex;flex-direction:column;gap:2px;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;
text-decoration:none;color:var(--fg);min-width:0}.stat .lbl{display:flex;align-items:center;gap:6px;color:var(--muted);font-size:.68rem;font-weight:600;letter-spacing:.08em;text-transform:uppercase}
.stat.ok b{color:var(--ok)}.stat.bad b{color:var(--bad)}.stat.warn b{color:var(--warn)}
.stat .lbl .ico{width:15px;height:15px}.stat b{font-size:1.75rem;line-height:1.2;font-weight:600;letter-spacing:-.02em}
.stat b small{font-size:1rem;font-weight:500;color:var(--muted)}.stat .cap{color:var(--muted);font-size:.82rem;overflow-wrap:anywhere}
.stat .track{margin-top:6px}
.machines{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px}
.mach{display:flex;flex-direction:column;gap:4px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;
text-decoration:none;color:var(--fg)}.mach .cap{color:var(--muted);font-size:.82rem}.mach .state{font-size:.88rem}
.meter{display:grid;grid-template-columns:62px 1fr auto;align-items:center;gap:8px;font-size:.8rem;color:var(--muted)}.meter:first-of-type{margin-top:6px}
.track{display:block;height:6px;border-radius:3px;background:var(--hover);overflow:hidden}
.fill{display:block;height:100%;border-radius:3px;background:var(--accent)}.fill.warn{background:var(--warn)}.fill.bad{background:var(--bad)}
.svc{display:flex;flex-wrap:wrap;gap:4px 16px;align-items:center;padding:9px 0;border-top:1px solid var(--line)}.svc:first-child{border-top:0}
.svc .what{min-width:230px}.svc .tor{flex-basis:100%;overflow-wrap:anywhere;color:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}.chip{font-size:.76rem;padding:1px 8px;border-radius:999px;border:1px solid var(--line);color:var(--muted)}
.pic svg{display:block;width:100%;height:auto;margin:0 auto}.pic .narrow{display:none}
@media (max-width:640px){.pic .wide{display:none}.pic .narrow{display:block}}
.pic rect{fill:var(--card);stroke:var(--line2)}.pic rect.m{stroke:var(--accent);stroke-width:1.5}
.pic rect.zone{fill:var(--fg);fill-opacity:.035;stroke:var(--line2);stroke-dasharray:2 4}.pic text.zl{fill:var(--muted);font:400 11px system-ui,sans-serif}
.pic circle.ring{opacity:.22}
.pic text{fill:var(--fg);font:600 14px system-ui,sans-serif}.pic text.c{fill:var(--muted);font-weight:400;font-size:11px}
.pic path{fill:none;stroke:var(--line2);stroke-width:1.8;stroke-linecap:round}.pic path.up{stroke:var(--ok);stroke-opacity:.75}
.pic path.off{stroke:var(--off);stroke-dasharray:5 5}
.pic circle{fill:var(--off)}.pic circle.ok{fill:var(--ok)}.pic circle.warn{fill:var(--warn)}.pic circle.bad,.pic circle.down{fill:var(--bad)}
.what{display:grid;grid-template-columns:auto 1fr;gap:10px 14px;align-items:start;margin-top:8px}
.what p{margin:0}ul{margin:6px 0;padding-left:20px}li{margin:3px 0}
pre{white-space:pre-wrap;word-break:break-word;background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:12px;font-size:.85rem;max-height:60vh;overflow:auto}
input,select,textarea{font:inherit;padding:9px;border-radius:8px;border:1px solid var(--line);width:100%;background:var(--card);color:var(--fg)}
input:focus,select:focus,textarea:focus,button:focus-visible,a:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
input[type=checkbox]{width:auto;margin:0}.check{display:flex;gap:8px;align-items:baseline;font-weight:400;margin:8px 0}
details.card>summary{font-weight:600}
label{display:block;margin:10px 0 4px;font-weight:600}details{margin:8px 0}summary{cursor:pointer;color:var(--accent)}
.qr{background:#fff;padding:10px;border-radius:10px;display:inline-block}.qr svg{display:block;width:260px;height:260px}
dl{display:grid;grid-template-columns:auto 1fr;gap:4px 14px;margin:8px 0}dt{color:var(--muted)}dd{margin:0}
.scroll{overflow-x:auto;border-radius:8px}.tbl{width:100%;border:1px solid var(--line);border-radius:8px;background:var(--card);border-collapse:separate;border-spacing:0;overflow:hidden}
.tbl th{font-size:.68rem;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);text-align:left;padding:10px 14px;border-bottom:1px solid var(--line)}
.tbl td{padding:11px 14px;border-bottom:1px solid var(--line);vertical-align:middle;font-size:.9rem}.tbl tr:last-child td{border-bottom:0}
.tbl tbody tr:hover td{background:var(--hover)}.tbl .sm{color:var(--muted);font-size:.8rem}
.badge{display:inline-flex;align-items:center;gap:6px;padding:2px 9px;border-radius:6px;font-size:.78rem;font-weight:600;border:1px solid var(--line);white-space:nowrap}
.badge::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}.badge.plain::before{display:none}
.badge.ok{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 40%,transparent);background:var(--okbg)}
.badge.bad{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 40%,transparent);background:var(--badbg)}
.badge.warn{color:var(--warn);border-color:color-mix(in srgb,var(--warn) 40%,transparent);background:var(--warnbg)}
.spark{display:flex;align-items:flex-end;gap:2px;height:28px;min-width:120px}.spark span{flex:1;min-width:2px;border-radius:1px;background:var(--ok);opacity:.85}
.spark span.none{background:var(--line);height:30%!important}.spark span.dip{background:color-mix(in srgb,var(--ok) 55%,var(--warn))}
.spark span.part{background:var(--warn)}.spark span.down{background:var(--bad)}
@media (max-width:1250px){.shell .tbl .wide2{display:none}}
@media (max-width:700px){.tbl .wide{display:none}.tbl td,.tbl th{padding:10px}}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style></head><body>
{% if nav %}{% set h = shell %}{% macro links(items, cls) %}{% set seen = namespace(group="") %}{% for href, label, pic, group in items %}
{% if cls == "nav" and group != seen.group %}{% set seen.group = group %}<span class="grp">{{ group }}</span>{% endif %}
<a class="{{ cls }}{% if nav == href %} on{% endif %}" href="{{ href }}">{{ icon(pic) }}<span>{{ label }}</span>{% if cls == "nav" and h.counts.get(href) %}<span class="count">{{ h.counts[href] }}</span>{% endif %}</a>{% endfor %}{% endmacro %}
{% macro theme() %}<button type="button" class="icon" title="Light or dark" aria-label="Light or dark" onclick="var d=document.documentElement,t=(d.dataset.theme||(matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light'))=='dark'?'light':'dark';d.dataset.theme=t;document.cookie='kiwi_theme='+t+';path=/;max-age=31536000;samesite=strict'">{{ icon("moon") }}</button>{% endmacro %}
<div class="shell"><aside class="side"><a class="brand" href="/">{{ icon("kiwi") }}<span>Kiwi <small>{{ h.domain }}</small></span></a>
{{ links(navitems, "nav") }}
<div class="foot">{% if h.total %}<div class="health"><span class="t">Your machines</span>
<span><span class="dot ok"></span>fine</span><b>{{ h.ok }}</b>
{% if h.warn %}<span><span class="dot warn"></span>want a look</span><b>{{ h.warn }}</b>{% endif %}
{% if h.bad %}<span><span class="dot bad"></span>with a problem</span><b>{{ h.bad }}</b>{% endif %}
{% if h.down %}<span><span class="dot down"></span>not reachable</span><b>{{ h.down }}</b>{% endif %}
<a class="t" href="/status">What everyone sees: the status page</a></div>{% endif %}
<div class="row2">{{ theme() }}<form class="inline" method="post" action="/logout" style="flex:3;display:flex">{{ tok }}<button>Log out</button></form></div></div></aside>
<div><header class="top"><a class="brand" href="/">{{ icon("kiwi") }}<span>Kiwi <small>{{ h.domain }}</small></span></a>
{% if h.total %}<span class="pill {{ h.level }}">{{ h.word }}</span>{% endif %}{{ theme() }}</header>
<main>{% block main %}{% endblock %}</main></div>
<nav class="bar">{{ links(navitems[:3], "") }}<details><summary>{{ icon("more") }}<span>More</span></summary>
<div class="sheet">{{ links(navitems[3:], "nav") }}<form method="post" action="/logout">{{ tok }}<button>Log out</button></form></div></details></nav></div>
{% else %}<main>{{ self.main() }}</main>{% endif %}</body></html>"""

STEP = """{% macro step(m, tok) %}<div class="card"><div class="row"><strong>{{ m.name }}</strong>
<span class="muted">{{ kind(m.role) }}</span></div>
{% if m.phase == "invalid" %}<p class="bad">Its settings are not complete: {{ m.next }}</p>
<div class="actions"><a class="btn primary" href="/hosts/{{ m.name }}/settings">Open its settings</a></div>
{% elif m.phase == "bare" %}<p>It has no job yet. <span class="muted">Give it one in its settings, under Role.</span></p>
<div class="actions"><a class="btn primary" href="/hosts/{{ m.name }}/settings">Open its settings</a></div>
{% elif m.phase == "enroll" and not m.master_ready %}<p>Its turn comes when the master is set up.</p>
{% elif m.phase == "enroll" %}<p>First it gets its place on your VPN.</p>
<form method="post" action="/run">{{ tok }}<input type="hidden" name="action" value="setup"><input type="hidden" name="host" value="{{ m.name }}">
<button class="primary">Give it its place on the VPN</button></form>
{% elif m.phase == "build" and m.provider %}<p>It is a rented server at {{ m.provider }}: Kiwi Server makes its install media
and installs it there by itself. <strong>Everything on that server is erased.</strong>
<span class="muted">The first time, the task shows a page of {{ m.provider }} to sign in at.</span></p>
<form method="post" action="/run" onsubmit="return confirm('Install {{ m.name }} at {{ m.provider }} now? EVERYTHING ON THAT SERVER IS ERASED.')">{{ tok }}
<input type="hidden" name="action" value="setup"><input type="hidden" name="host" value="{{ m.name }}">
<button class="primary">Install it at {{ m.provider }}</button></form>
{% elif m.phase == "build" %}<p>Next: its install media, a file of about 1 GB. <span class="muted">Making it takes a few minutes.</span></p>
<form method="post" action="/run">{{ tok }}<input type="hidden" name="action" value="build"><input type="hidden" name="host" value="{{ m.name }}">
<button class="primary">Make its install media</button></form>
{% elif m.phase == "install" and m.here %}<p>It is the machine this page runs on: nothing is installed again. Setting it up
takes a few minutes; it downloads what it runs.</p>
<p><strong>This page moves then.</strong> Like every service of your network it opens on your VPN only, at
<code>https://control.{{ m.hostname }}</code>: put the phone or computer you hold on the VPN first
(<a href="/devices">Devices</a>), and log in there again when the machine is set up.</p>
<div class="actions"><form class="inline" method="post" action="/run" onsubmit="return confirm('Set up {{ m.name }} now? This page moves to https://control.{{ m.hostname }}, on your VPN.')">{{ tok }}<input type="hidden" name="action" value="setup">
<input type="hidden" name="host" value="{{ m.name }}"><button class="primary">Set it up</button></form></div>
{% elif m.phase == "install" %}{% if m.media %}<p><strong>1.</strong> Fetch its install media ({{ (m.media / 1048576) | round | int }} MB)
{% if m.role == "master" %}and upload them as an ISO at your hoster (a master at home: write them to a USB stick).
{% else %}and write them to a USB stick.{% endif %}
<span class="muted">Fedora Media Writer and balenaEtcher write sticks. The file holds this machine's keys: delete it
from your computer afterwards.</span></p>
<div class="actions"><a class="btn" href="/hosts/{{ m.name }}/media">Fetch the install media</a>
<form class="inline" method="post" action="/hosts/{{ m.name }}/media/drop">{{ tok }}<button>Remove them from here</button></form></div>
<p><strong>2.</strong> Start the machine from {{ "them" if m.role == "master" else "the stick" }}. It erases its disk, installs itself and
{{ "turns itself off: take the stick out and switch it on again" if m.on_done != "reboot" else "restarts" }}.</p>
<p><strong>3.</strong> When it is on again:</p>{% else %}<p>Its install media are not here any more (they are kept in memory
only). When the machine is installed and on:</p>{% endif %}
<div class="actions"><form class="inline" method="post" action="/run">{{ tok }}<input type="hidden" name="action" value="setup">
<input type="hidden" name="host" value="{{ m.name }}">{% if m.address == "dhcp" and m.role != "master" %}
<label for="at-{{ m.name }}">Its address right now</label><p class="muted" style="margin:0 0 4px">Your router gave it one;
the router's page lists it under the name {{ m.name }}.</p><input id="at-{{ m.name }}" name="at" required>{% endif %}
<button class="primary" style="margin-top:8px">Set it up</button></form>
{% if not m.media %}<form class="inline" method="post" action="/hosts/{{ m.name }}/media/make">{{ tok }}<button>Make the install media again</button></form>{% endif %}</div>
{% endif %}</div>{% endmacro %}"""

TEMPLATES = {
    "base.html": BASE,
    "step.html": STEP,
    "login.html": """{% extends "base.html" %}{% block main %}
<h1>Kiwi</h1><p class="muted">The control page of your network.</p>
{% if damaged %}<div class="banner bad">This machine's copy of your network is damaged: {{ damaged | join(", ") }}
{{ "is" if damaged|length == 1 else "are" }} empty. That is what a power cut in the middle of a change left behind on
older versions. No password opens it as it is.</div>
<div class="card"><p><strong>Bring it back.</strong> From a computer that manages the network, over SSH (this machine no longer
knows which computers may sync with it):</p>
<pre>kiwi-server control push &lt;fleet&gt; --ssh &lt;admin&gt;@&lt;this machine's address&gt; --force</pre>
<p>Without such a computer: the newest copy of the network is on your other machines and in this machine's own
backups. Start this machine's page anew and choose <em>bring it back from a copy</em>, with your recovery key
(<a href="/help">Help</a>).</p></div>{% endif %}
<form method="post" action="/login" class="card">
{% if locked %}<p class="warn"><strong>The network's keys are locked.</strong> That is normal after a restart or a
power cut. Your password unlocks them; until then this machine looks after nothing by itself.</p>
{% if auto %}<p class="muted">It also tries to unlock by itself, with the help of {{ auto.server }}, which has not
answered yet. Is that machine on?</p>{% endif %}{% endif %}
<label for="pw">Password</label><input id="pw" type="password" name="password" autocomplete="current-password" autofocus>
{% if error %}<p class="bad">{{ error }}</p>{% endif %}
<div class="actions"><button class="primary">{{ "Unlock and log in" if locked else "Log in" }}</button></div></form>
<details><summary>I forgot the password</summary><p><a href="/recover">Set a new one with your recovery key</a>,
the one you wrote down when the network was made.</p><p class="muted">Or on the computer the network was set up with:
<code>kiwi-server serve &lt;fleet&gt; --set-password</code>, then <code>kiwi-server control push &lt;fleet&gt;</code>.
The old password cannot be read anywhere.</p></details>{% endblock %}""",

    "setup.html": """{% extends "base.html" %}{% block main %}
{% macro field(q, name, value, err) %}{% if q.type == "choice" and q.options | length == 1 %}
<input type="hidden" name="{{ name }}" value="{{ q.options[0].value }}">{% else %}<div class="q"{% if q.when %} data-when="{{ q.when }}"{% endif %}>
<label for="f-{{ name }}">{{ q.label }}</label>
{% if q.help %}<p class="muted" style="margin:0 0 4px">{{ q.help }}</p>{% endif %}
{% if q.type == "choice" %}<select id="f-{{ name }}" name="{{ name }}">{% for o in q.options %}
<option value="{{ o.value }}"{% if o.value == value %} selected{% endif %}>{{ o.label }}</option>{% endfor %}</select>
{% else %}<input id="f-{{ name }}" name="{{ name }}" value="{{ value }}"{% if q.secret %} type="password" autocomplete="off"{% endif %}>{% endif %}
{% if err %}<p class="bad">{{ err }}</p>{% endif %}</div>{% endif %}{% endmacro %}
{% macro lookup(q) %}<div class="q lookup" data-when="{{ q.when }}" data-names="{{ q.names | tojson | forceescape }}" data-by="{{ q.lookup }}">
<label>{{ q.label }}</label><p class="muted" style="margin:0 0 4px">{{ q.help }}</p>
<input type="hidden" name="hoster" value=""><input type="hidden" name="provider_server" value="">
<div class="hoster-ask"><label for="h-code">Setup code</label><p class="muted" style="margin:0 0 4px">The same as below: it
proves that this is your machine.</p><input id="h-code" autocomplete="off">
<div class="actions"><button type="button" class="hoster-go">Sign in and pick it</button></div></div>
<select class="hoster-list" style="display:none" aria-label="{{ q.label }}"></select>
<p class="hoster-say muted" aria-live="polite"></p></div>{% endmacro %}
<h1>A new network</h1>
<p class="muted">Nothing is set up on this machine yet. Answer the questions; the suggestions are fine for most homes.</p>
<details class="card"{% if back_error %} open{% endif %}><summary>I already have a network: bring it back from a copy</summary>
<p>For a network whose computer or control node is gone. You need two things: a <strong>copy of the network</strong>
(a file named <code>fleet-….tar</code>, from "Fetch a copy" on the Backups page, or kept on one of your machines) and
its <strong>recovery key</strong>, the one you wrote down when the network was made.</p>
{% if back_error %}<p class="banner bad">{{ back_error }}</p>{% endif %}
<form method="post" action="/setup/restore" enctype="multipart/form-data" autocomplete="off">
<label for="b-copy">The copy</label><input id="b-copy" type="file" name="copy" required>
<label for="b-key">Recovery key</label><p class="muted" style="margin:0 0 4px">It starts with AGE-SECRET-KEY-.</p>
<input id="b-key" name="recovery" autocomplete="off" required>
<label for="b-code">Setup code</label><p class="muted" style="margin:0 0 4px">Shown where this page was started: in the
terminal, or with <code>podman logs kn-control</code>. It proves that this is your machine.</p>
<input id="b-code" name="code" value="{{ back_code }}" required>
<label for="b-pw">A new password for this page</label><p class="muted" style="margin:0 0 4px">12 characters at least.
The password of the page you had before stops working.</p>
<input id="b-pw" type="password" name="password" autocomplete="new-password" required>
<label for="b-pw2">The password again</label><input id="b-pw2" type="password" name="again" autocomplete="new-password" required>
<p class="muted">A copy carries no signature. When it is back, look at the machines' names on the first page: they
should be yours.</p>
<div class="actions"><button class="primary">Bring my network back</button></div></form></details>
{% if error %}<p class="banner bad">{{ error }}</p>{% endif %}
<form method="post" action="/setup" autocomplete="off">
{% for step in steps %}<div class="card"><h2 style="margin-top:0">{{ step.step }}</h2>
<p>{{ step.intro }}</p>{% if step.notes %}<ul>{% for n in step.notes %}<li>{{ n }}</li>{% endfor %}</ul>{% endif %}
{% for q in step.questions if not q.advanced and q.type != "machines" %}
{% if q.type == "lookup" %}{{ lookup(q) }}{% else %}{{ field(q, q.id, values.get(q.id, q.default or ""), errors.get(q.id)) }}{% endif %}{% endfor %}
{% for q in step.questions if q.type == "machines" %}
<div id="machines">{% for m in machines %}{% set i = loop.index0 %}<div class="card machine"><div class="row"><strong>{{ "This machine" if m.this else "A machine at home" }}</strong>
{% if not m.this %}<button type="button" class="drop">Leave it out</button>{% endif %}</div>
{% if m.this %}<p class="muted">The machine this page runs on. It becomes your first home server, as it is: nothing is
installed again, and it keeps the address it has now, {{ m.address }}.</p>
<input type="hidden" name="nodes.{{ i }}.address" value="{{ m.address }}">{% endif %}
{% for f in q.fields if not f.advanced and not (m.this and f.id == "address") %}{{ field(f, "nodes.%d.%s" % (i, f.id), m.get(f.id, ""), errors.get("nodes.%d.%s" % (i, f.id))) }}{% endfor %}
<details><summary>More settings</summary>
{% for f in q.fields if f.advanced and not (m.this and f.id in ("target", "disk")) %}{{ field(f, "nodes.%d.%s" % (i, f.id), m.get(f.id, ""), errors.get("nodes.%d.%s" % (i, f.id))) }}{% endfor %}
</details></div>{% endfor %}</div>
{% if errors.get("nodes") %}<p class="bad">{{ errors.nodes }}</p>{% endif %}
<p class="muted" id="none">{{ q.none }}</p>
<div class="actions"><button type="button" id="add">{{ q.add }}</button></div>
<template id="machine"><div class="card machine"><div class="row"><strong>A machine at home</strong>
<button type="button" class="drop">Leave it out</button></div>
{% for f in q.fields if not f.advanced %}{{ field(f, "nodes.N." + f.id, blank.get(f.id, ""), None) }}{% endfor %}
<details><summary>More settings</summary>
{% for f in q.fields if f.advanced %}{{ field(f, "nodes.N." + f.id, blank.get(f.id, ""), None) }}{% endfor %}
</details></div></template>{% endfor %}
{% set more = step.questions | selectattr("advanced") | list %}{% if more %}<details{% if step.id in open %} open{% endif %}>
<summary>More settings</summary>{% for q in more %}
{{ field(q, q.id, values.get(q.id, q.default or ""), errors.get(q.id)) }}{% endfor %}</details>{% endif %}
{% if step.id == "keys" %}
<label for="code">Setup code</label><p class="muted" style="margin:0 0 4px">Shown where this page was started: in the
terminal, or with <code>podman logs kn-control</code>. It proves that this is your machine.</p>
<input id="code" name="code" value="{{ values.get('code', '') }}">{% if errors.get("code") %}<p class="bad">{{ errors.code }}</p>{% endif %}
<label for="pw">A password for this page</label><p class="muted" style="margin:0 0 4px">12 characters at least. It opens
your network's keys after every restart of this machine.</p>
<input id="pw" type="password" name="password" autocomplete="new-password">
{% if errors.get("password") %}<p class="bad">{{ errors.password }}</p>{% endif %}
<label for="pw2">The password again</label><input id="pw2" type="password" name="again" autocomplete="new-password">
<div class="actions"><button class="primary">Make my network</button></div>{% endif %}
</div>{% endfor %}</form>
<script>
(function () {
  var box = document.getElementById("machines"), tpl = document.getElementById("machine"), free = {{ free | tojson }};
  function shown() {
    var v = {};
    document.querySelectorAll("form [name]").forEach(function (e) { v[e.name] = e.value; });
    document.querySelectorAll("[data-when]").forEach(function (q) {
      var w = q.dataset.when, m, key, on = true, card = q.closest(".machine"), pre = "";
      if (card) { var f = card.querySelector("[name]"); pre = f.name.split(".").slice(0, 2).join(".") + "."; }
      if ((m = w.match(/^(\\w+) == '(.*)'$/))) { on = (v[pre + m[1]] !== undefined ? v[pre + m[1]] : v[m[1]]) === m[2]; }
      else if ((m = w.match(/^(\\w+) in (.*)$/))) { key = v[pre + m[1]] !== undefined ? v[pre + m[1]] : v[m[1]]; on = m[2].split(",").indexOf(key) >= 0; }
      q.style.display = on ? "" : "none";
    });
    document.getElementById("none").style.display = box.children.length ? "none" : "";
  }
  function number() {
    Array.prototype.forEach.call(box.children, function (card, i) {
      card.querySelectorAll("[name]").forEach(function (e) {
        e.name = e.name.replace(/^nodes\\.[^.]+\\./, "nodes." + i + ".");
        e.id = "f-" + e.name; });
      card.querySelectorAll("label").forEach(function (l) { l.htmlFor = "f-" + l.htmlFor.replace(/^f-/, "").replace(/^nodes\\.[^.]+\\./, "nodes." + i + "."); });
    });
  }
  document.getElementById("add").addEventListener("click", function () {
    var card = tpl.content.firstElementChild.cloneNode(true), i = box.children.length;
    box.appendChild(card); number();
    var a = card.querySelector('[name$=".address"]'), n = card.querySelector('[name$=".name"]');
    if (a && free.addresses[i]) a.value = free.addresses[i];
    if (n && free.names[i]) n.value = free.names[i];
    shown();
  });
  document.addEventListener("click", function (e) {
    if (e.target.classList.contains("drop")) { e.target.closest(".machine").remove(); number(); shown(); }
  });
  document.addEventListener("change", shown);
  document.querySelectorAll(".lookup").forEach(function (box) {      // sign in at the hoster, pick the server
    var go = box.querySelector(".hoster-go"), say = box.querySelector(".hoster-say"), list = box.querySelector(".hoster-list");
    var code = box.querySelector("#h-code"), main = document.getElementById("code"), names = JSON.parse(box.dataset.names || "{}");
    var by = function () { return document.querySelector('[name="' + box.dataset.by + '"]').value; };
    var field = function (n) { return document.querySelector('[name="' + n + '"]'); };
    code.addEventListener("input", function () { if (main) main.value = code.value; });
    if (main) main.addEventListener("input", function () { code.value = main.value; });
    function failed(why) { say.textContent = "That did not work: " + why + ". Type the address below instead."; go.disabled = false; }
    function take() {
      var r = JSON.parse(list.value); field("provider_server").value = r.id; field("master_address").value = r.address;
      say.textContent = "Its address is " + r.address + ".";
    }
    list.addEventListener("change", take);
    function poll(handle, name, opened) {
      fetch("/setup/hoster/" + encodeURIComponent(handle), {cache: "no-store"}).then(function (r) { return r.json(); }).then(function (s) {
        if (s.error && !s.state) return failed(s.error);
        if (s.state === "failed") return failed(s.error || "no answer");
        if (s.url && !opened) {
          opened = true; say.innerHTML = "";
          var a = document.createElement("a"); a.href = s.url; a.target = "_blank"; a.rel = "noopener"; a.textContent = "Sign in to " + name;
          say.append(a, " in a new tab and confirm it there. This goes on by itself.");
        }
        if (s.state !== "listed") return setTimeout(function () { poll(handle, name, opened); }, 2000);
        list.innerHTML = "";
        s.rows.forEach(function (r) { var o = document.createElement("option"); o.value = JSON.stringify({id: r.id, address: r.address}); o.textContent = r.label; list.appendChild(o); });
        field("hoster").value = handle; list.style.display = ""; box.querySelector(".hoster-ask").style.display = "none"; take();
      }).catch(function () { setTimeout(function () { poll(handle, name, opened); }, 4000); });
    }
    go.addEventListener("click", function () {
      var p = by(), name = names[p] || p;
      go.disabled = true; say.textContent = "Asking " + name + "…";
      fetch("/setup/hoster", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({provider: p, code: code.value})})
        .then(function (r) { return r.json(); }).then(function (d) { if (d.error) return failed(d.error); poll(d.handle, name, false); })
        .catch(function () { failed("this page did not answer"); });
    });
  });
  if ({{ guess | tojson }}) {       // the machine could not tell (a container): what this browser says
    var f = function (n) { return document.querySelector('[name="' + n + '"]'); }, lang = (navigator.language || "en-US").split("-");
    var tz = Intl.DateTimeFormat().resolvedOptions().timeZone, cc = (lang[1] || "").toUpperCase();
    if (tz && f("timezone")) f("timezone").value = tz;
    if (cc && f("locale")) f("locale").value = lang[0] + "_" + cc + ".UTF-8";
    if (f("keyboard")) f("keyboard").value = lang[0] === "en" ? (cc === "GB" ? "gb" : "us") : (cc === "AT" || cc === "CH") ? cc.toLowerCase() : lang[0];
  }
  shown();
})();
</script>{% endblock %}""",
    "recovery.html": """{% extends "base.html" %}{% block main %}
<h1>Your network is made</h1>
<div class="card"><p><strong>Your recovery key. Write it down now</strong>, on paper or in a password manager.
It is shown once. If you forget the password of this page, it is the only way back to your network's keys.</p>
<pre style="font-size:1.05rem">{{ recovery }}</pre>
<div class="actions"><a class="btn primary" href="/">I have written it down</a></div></div>
<p class="muted">Next, on the page that follows: the machines, one after the other{% if not alone %}. The master first, then the
machines at home, then your phone{% endif %}.</p>{% endblock %}""",
    "recover.html": """{% extends "base.html" %}{% block main %}
<p><a href="/login">← Back</a></p><h1>A new password with the recovery key</h1>
<form method="post" action="/recover" class="card" autocomplete="off">
<label for="rk">Recovery key</label><p class="muted" style="margin:0 0 4px">It starts with AGE-SECRET-KEY-.</p>
<input id="rk" name="recovery" autocomplete="off">
<label for="pw">New password</label><p class="muted" style="margin:0 0 4px">12 characters at least.</p>
<input id="pw" type="password" name="password" autocomplete="new-password">
<label for="pw2">The password again</label><input id="pw2" type="password" name="again" autocomplete="new-password">
{% if error %}<p class="bad">{{ error }}</p>{% endif %}
<div class="actions"><button class="primary">Set the password and log in</button></div></form>{% endblock %}""",
    "index.html": """{% extends "base.html" %}{% block main %}
{% set bad = machines|selectattr("state", "in", ["bad", "down"])|list %}{% set warn = machines|selectattr("state", "equalto", "warn")|list %}
{% set running = machines|rejectattr("phase", "in", ["enroll", "build", "install", "invalid", "bare"])|list %}
{% if machines and not running %}<div class="banner ok">Your network is made. Now its machines, one after the other.</div>
{% elif looking or not looked %}<div class="banner warn">Checking your machines… this takes a minute.</div>
<script>setTimeout(function(){location.reload()},6000)</script>
{% elif bad %}<div class="banner bad">{{ bad|length }} of {{ machines|length }} machines {{ "has" if bad|length == 1 else "have" }} a problem</div>
{% elif silent %}<div class="banner bad">{{ silent|length }} {{ "service does" if silent|length == 1 else "services do" }} not answer</div>
{% elif warn %}<div class="banner warn">Everything runs. {{ warn|length }} {{ "machine wants" if warn|length == 1 else "machines want" }} a look.</div>
{% else %}<div class="banner ok">All {{ machines|length }} machines are fine</div>{% endif %}
<div class="row"><span class="muted">{% if looked %}Checked {{ ago(looked) }} ago. It checks every ten minutes by itself.{% endif %}
{% if error %}<span class="bad">{{ error }}</span>{% endif %}</span>
<form class="inline" method="post" action="/refresh">{{ tok }}<button>Check now</button></form></div>
{% if stats %}<div class="stats">{% for x in stats %}<a class="stat" href="{{ x.href }}"><span class="lbl">{{ icon(x.pic) }}{{ x.label }}</span>
<b class="{{ x.level }}">{{ x.value }}{% if x.of is defined %}<small> of {{ x.of }}</small>{% endif %}</b><span class="cap">{{ x.caption }}</span></a>{% endfor %}</div>{% endif %}
{% for e in gone %}<div class="card"><div class="row"><strong><span class="dot {{ "warn" if e.kind in ["waiting", "restarted", "unlocked"] else "bad" }}"></span>{{ e.host }}</strong>
<a class="btn" href="/hosts/{{ e.host }}">Open</a></div><p>{{ e.text }}.</p>
{% if e.kind == "waiting" %}<p class="muted">Nothing is done yet: a short break is normal.</p>
{% elif e.kind == "restarted" %}<p class="muted">It needs a few minutes to come back. Activity says when it was done.</p>
{% elif e.kind == "unlocked" %}<p class="muted">It needs a minute or two to start. Activity says when it was done.</p>
{% elif e.kind == "locked" %}<p class="muted">Open the machine's page: <em>Unlock it</em> is there.</p>
{% elif e.kind == "tell" %}<p class="muted">Nothing is done by itself here. <a href="/help#gone">What you can do, and how to let the control node restart it.</a></p>{% endif %}</div>{% endfor %}

{% for s in silent %}<div class="card"><div class="row"><strong><span class="dot bad"></span>{{ s.label }}</strong>
<a class="btn" href="/hosts/{{ s.host }}">Open {{ s.host }}</a></div><p>It does not answer at {{ s.site }}, for {{ age(now - s.since) }}.</p>
<p class="muted">Its machine answers and may say that everything runs: the service itself was asked for a page and gave none.
<em>Apply</em> on the machine's page sets it up again; what the machine says, word for word, is under Details there.</p></div>{% endfor %}
{% set todo = machines|selectattr("problems")|list %}{% if todo %}<h2>Needs a look</h2>
{% for m in todo %}{% for p in m.problems %}<div class="card"><div class="row"><strong><span class="dot {{ p.level }}"></span>{{ m.name }}</strong>
<a class="btn" href="/hosts/{{ m.name }}">Open</a></div><p>{{ p.text }}</p>
{% if hint(p.text) %}<p class="muted">{{ hint(p.text) }}</p>{% endif %}</div>{% endfor %}{% endfor %}{% endif %}

{% from "step.html" import step with context %}
{% set fresh = machines|selectattr("phase", "in", ["enroll", "build", "install", "invalid", "bare"])|list %}
{% if fresh %}<h2>Still to set up</h2><p class="muted">One machine after the other{% if not alone %}: the master first{% endif %}.</p>
{% for m in fresh %}{{ step(m, tok) }}{% endfor %}{% endif %}

{% if wide %}<h2>How they are linked</h2><div class="card pic"><div class="wide">{{ wide }}</div><div class="narrow">{{ narrow }}</div>
<p class="muted small" style="margin:10px 0 0">Every machine holds a link to the master. A green line: the machine answers. A grey, broken one: it does not.</p></div>{% endif %}
<h2>Go on</h2><div class="tiles">
<a class="tile" href="/machines"><b>Machines</b><span>How each one is, add one, update them all</span></a>
<a class="tile" href="/services"><b>Services</b><span>Open what runs on them, with its login</span></a>
<a class="tile" href="/devices"><b>Add a phone or laptop</b><span>{% if alone %}Without a master: your services at home{% else %}Put a device on your VPN with a QR code{% endif %}</span></a>
<a class="tile" href="/access"><b>Say who may reach what</b><span>Which devices and linked networks open which machine or service</span></a></div>
{% endblock %}""",
    "machines.html": """{% extends "base.html" %}{% block main %}
{% set running = machines|rejectattr("phase", "in", ["enroll", "build", "install", "invalid", "bare"])|list %}
<h1>Machines</h1><div class="row"><span class="muted">{% if looked %}Checked {{ ago(looked) }} ago.{% endif %}</span>
<form class="inline" method="post" action="/refresh">{{ tok }}<input type="hidden" name="back" value="/machines"><button>Check now</button></form></div>
<div class="machines">{% for m in machines %}<a class="mach" href="/hosts/{{ m.name }}"><div class="row">
<b><span class="dot {{ m.state }}{{ " busy" if looking }}"></span>{{ m.name }}</b><span class="state {{ m.state }}">{{ words(m.state) }}</span></div>
<span class="cap">{{ kind(m.role) }}{% for t in m.tags %} · {{ t }}{% endfor %}</span>
{% for b in m.bars %}<div class="meter" title="{{ b.more }}"><span>{{ b.label }}</span><span class="track"><span class="fill {{ b.level }}" style="width:{{ b.pct }}%"></span></span><span>{{ b.pct }} %</span></div>{% endfor %}
{% if m.chips %}<div class="chips">{% for c in m.chips[:3] %}<span class="chip">{{ c }}</span>{% endfor %}{% if m.chips|length > 3 %}<span class="chip">+{{ m.chips|length - 3 }}</span>{% endif %}</div>{% endif %}</a>
{% endfor %}</div>{% if not machines %}<p class="muted">No machines yet.</p>{% endif %}
<details{% if take_error %} open{% endif %}><summary>A machine with the starter stick is waiting</summary>
<form method="post" action="/machines/take-in" class="card">{{ tok }}
<p class="muted" style="margin:0 0 8px">A machine you started from the starter stick, on this home network, shows an address and a
setup code on its screen. Type both here: the code shows that you are at that machine.</p>
<label for="t_address">Its address</label><input id="t_address" name="address" placeholder="192.168.1.40" required>
<label for="t_code">Its setup code</label><input id="t_code" name="code" placeholder="abcd-1234" autocomplete="off" required>
<label for="t_preset">What it does</label><select id="t_preset" name="preset">{% for value, label in presets.items() %}
<option value="{{ value }}">{{ label }}</option>{% endfor %}</select>
<label for="t_name">Name</label><p class="muted" style="margin:0 0 4px">Empty: it is named after a fruit.</p><input id="t_name" name="name">
{% if take_error %}<p class="bad">{{ take_error }}</p>{% endif %}
<div class="actions"><button class="primary">Take it in</button></div></form></details>
<details><summary>Add a machine at home</summary><form method="post" action="/machines/add" class="card">{{ tok }}
<label for="preset">What it does</label><select id="preset" name="preset">{% for value, label in presets.items() %}
<option value="{{ value }}">{{ label }}</option>{% endfor %}</select>
<label for="address">Its address at home</label><p class="muted" style="margin:0 0 4px">A fixed address in your home
network that nothing else uses, like 192.168.1.30. Empty: your router gives it one.</p><input id="address" name="address">
<details><summary>More settings</summary><label for="mname">Name</label><p class="muted" style="margin:0 0 4px">Empty: it is
named after a fruit.</p><input id="mname" name="name">
<label for="target">System</label><select id="target" name="target"><option value="ucore">uCore (updates itself, rolls back)</option>
<option value="debian">Debian 13</option></select></details>
{% if add_error %}<p class="bad">{{ add_error }}</p>{% endif %}
<div class="actions"><button class="primary">Add it</button></div></form></details>

{% if running %}<h2>Keep it up to date</h2><div class="card"><p>Brings every machine to your network's current settings and the
newest versions of its services, one machine after the other. <span class="muted">Services that changed restart for a
moment. Do it now and then, and after changing the settings for every machine.</span></p>
<form method="post" action="/run" onsubmit="return confirm('Update every machine now? This takes a while; services that changed restart for a moment.')">{{ tok }}
<input type="hidden" name="action" value="update"><button class="primary">Update everything</button></form></div>{% endif %}

<p class="muted"><a href="/settings">Settings for every machine</a>: time zone, updates and what else they share.</p>
{% endblock %}""",
    "services.html": """{% extends "base.html" %}{% block main %}
<h1>Services</h1><p class="sub">What runs on your machines. A service opens from a device on your VPN; one that is
open to the Internet or to Tor says so.</p>
{% if everything %}<div class="stats"><div class="stat ok"><span class="lbl">Up</span><b>{{ up }}</b></div>
<div class="stat {{ "bad" if down else "" }}"><span class="lbl">Down</span><b>{{ down }}</b></div>
<div class="stat"><span class="lbl">Open to the Internet</span><b>{{ public }}</b></div>
<div class="stat"><span class="lbl">Hidden services</span><b>{{ tor }}</b></div></div>
<div class="scroll"><table class="tbl"><thead><tr><th>Service</th><th class="wide">Machine</th><th>Now</th><th class="wide">The last 90 days</th><th>Worked</th><th class="wide wide2">Answers in</th><th class="wide">Open to</th></tr></thead><tbody>
{% for t in everything %}<tr><td><b>{{ t.label }}</b><br><a class="sm" href="https://{{ t.site }}" target="_blank" rel="noopener">{{ t.site }}</a>
<br><span class="sm">{% if t.module %}<a href="/hosts/{{ t.machine }}/settings#s-mod-{{ t.module }}" title="What kiwi-server sets: names, who may sign up, sizes, switches">Settings</a>{% endif %}
{% if t.admin %} · <a href="https://{{ t.site }}{{ t.admin }}" target="_blank" rel="noopener" title="The app's own admin page: users, rooms, repositories">Admin page</a>{% endif %}
{% if t.login %} · <a href="/hosts/{{ t.machine }}#logins">login</a>{% endif %}</span></td>
<td class="wide"><a href="/hosts/{{ t.machine }}">{{ t.machine }}</a></td>
<td>{% if t.down %}<span class="badge bad">Down</span>{% elif t.asked %}<span class="badge ok">Up</span>{% else %}<span class="badge">not asked</span>{% endif %}
{% if t.since and t.asked %}<br><span class="sm">for {{ age(now - t.since) }}</span>{% endif %}</td>
<td class="wide"><div class="spark">{% for b in t.bars %}<span class="{% if b.pct is none %}none{% elif b.pct >= 99.9 %}up{% elif b.pct >= 99 %}dip{% elif b.pct >= 90 %}part{% else %}down{% endif %}" style="height:{{ 100 if b.pct is none or b.pct >= 99.9 else [b.pct, 25]|max|int }}%" title="{{ b.day }}: {% if b.pct is none %}not checked{% else %}{{ "%.1f"|format(b.pct) }} % worked{% endif %}"></span>{% endfor %}</div></td>
<td>{% if t.uptime is none %}<span class="sm">–</span>{% else %}<span class="badge plain {{ "ok" if t.uptime >= 99 else "warn" if t.uptime >= 90 else "bad" }}">{{ "%.2f"|format(t.uptime) if t.uptime < 100 else "100" }} %</span>{% endif %}</td>
<td class="wide wide2">{% if t.ms is not none %}{{ t.ms }} <span class="sm">ms</span>{% else %}<span class="sm">–</span>{% endif %}</td>
<td class="wide"><span class="badge plain">VPN</span>{% if t.public %} <span class="badge plain warn">Internet</span>{% endif %}
{% if t.onion %} <span class="badge plain" title="http://{{ t.onion.address }}{% if t.onion.clients %} — only {{ t.onion.clients|join(", ") }}{% endif %}" style="cursor:copy"
onclick="navigator.clipboard.writeText('http://{{ t.onion.address }}');this.textContent='copied'">Tor{% if t.onion.clients %} (named){% endif %}</span>{% endif %}</td></tr>{% endfor %}</tbody></table></div>
{% else %}<p class="muted">No services yet: they come with the machines.</p>{% endif %}
<p class="muted small"><b>Settings</b> is what kiwi-server sets for the service (its names, who may sign up, sizes, switches);
<b>Admin page</b> is the app's own, for what is inside it (users, rooms, repositories): its admin login is under the machine's Logins.<br>
90 days ago on the left, today on the right. A Tor badge copies the hidden service's address: it opens in Tor Browser,
without your VPN. <a href="/access#tor">Make a service a hidden service, or say who may open it.</a> · <a href="/status">What everyone sees: the status page.</a></p>
{% endblock %}""",

    "host.html": """{% extends "base.html" %}{% block main %}
<p><a href="/">← Home</a></p>
<h1><span class="dot {{ m.state }}"></span>{{ m.name }} <span class="state {{ m.state }}" style="font-size:1rem">{{ words(m.state) }}</span></h1>
<p class="muted">{{ kind(m.role) }}{% for t in m.tags %} · {{ t }}{% endfor %} · {{ m.hostname }}{% if m.facts.os %} · {{ m.facts.os }}{% endif %}</p>
{% if m.bars %}<div class="stats">{% if m.facts.uptime is number and m.facts.uptime %}<div class="stat"><span class="lbl">On for</span>
<b>{{ age(m.facts.uptime) }}</b><span class="cap">since it last started</span></div>{% endif %}
{% for b in m.bars %}<div class="stat"><span class="lbl">{{ b.label }}</span><b class="{{ b.level }}">{{ b.pct }}<small> %</small></b>
<span class="cap">{{ b.more }}</span><span class="track"><span class="fill {{ b.level }}" style="width:{{ b.pct }}%"></span></span></div>{% endfor %}</div>{% endif %}
{% for p in m.problems %}<div class="card"><p><span class="dot {{ p.level }}"></span>{{ p.text }}</p>
{% if hint(p.text) %}<p class="muted">{{ hint(p.text) }}</p>{% endif %}
{% if "disk is locked" in p.text|lower %}<form method="post" action="/run">{{ tok }}<input type="hidden" name="action" value="unlock">
<input type="hidden" name="host" value="{{ m.name }}"><button class="primary">Unlock it</button></form>{% endif %}</div>{% endfor %}
{% from "step.html" import step with context %}
{% if m.phase and m.phase != "applied" %}<h2>Still to set up</h2>{{ step(m, tok) }}
{% elif not m.known %}<div class="card"><p>This machine has not answered yet.</p></div>{% endif %}

<h2>What you can do</h2><div class="card"><div class="what">
<form method="post" action="/run">{{ tok }}<input type="hidden" name="action" value="status"><input type="hidden" name="host" value="{{ m.name }}"><button>Check now</button></form>
<p>Asks the machine how it is. <span class="muted">Changes nothing.</span></p>
<form method="post" action="/run">{{ tok }}<input type="hidden" name="action" value="plan"><input type="hidden" name="host" value="{{ m.name }}"><button>Preview</button></form>
<p>Shows what <em>Apply</em> would change on it. <span class="muted">Changes nothing.</span></p>
<form method="post" action="/run" onsubmit="return confirm('Apply to {{ m.name }} now? Services that changed restart for a moment.')">{{ tok }}
<input type="hidden" name="action" value="apply"><input type="hidden" name="host" value="{{ m.name }}"><button class="primary">Apply</button></form>
<p>Sets the machine up the way your network's settings say: new settings, updates of its services, repairs.
<span class="muted">Services that changed restart for a moment. Safe to run again.</span></p>
<a class="btn" href="/hosts/{{ m.name }}/settings">Settings</a>
<p>What this machine does and how: its parts, its address, its updates. <span class="muted">Apply brings a change to it.</span></p></div></div>

{% if tiles %}<h2>Its services</h2><div class="tiles">{% for t in tiles %}<a class="tile" href="https://{{ t.site }}" target="_blank" rel="noopener">
<b>{{ t.label }}</b><span>{{ t.site }}</span></a>{% endfor %}</div>{% endif %}

{% include "logins.html" %}

{% if m.notes or m.facts %}<h2>Details</h2><div class="card"><dl>
{% if m.facts.running is defined and m.facts.expected is defined %}<dt>Services</dt><dd>{{ m.facts.running|length if m.facts.running is iterable and m.facts.running is not string else m.facts.running }} running</dd>{% endif %}
{% if m.facts.backup and m.facts.backup.age is not none %}<dt>Last backup</dt><dd>{{ age(m.facts.backup.age) }} ago</dd>{% endif %}
{% if m.facts.reboot %}<dt>Updates</dt><dd>installed, it restarts in its update window</dd>{% endif %}
{% if m.vpn_ip %}<dt>VPN address</dt><dd>{{ m.vpn_ip }}</dd>{% endif %}
<dt>Parts</dt><dd>{{ m.modules|join(", ") }}</dd></dl>
{% if m.notes %}<ul class="muted">{% for n in m.notes %}<li>{{ n }}</li>{% endfor %}</ul>{% endif %}
{% if m.output %}<details><summary>What the machine said, word for word</summary><pre>{{ m.output }}</pre></details>{% endif %}</div>{% endif %}
<p class="muted small"><a href="/hosts/{{ m.name }}/script" target="_blank" rel="noopener">The role script</a>: what
<em>Apply</em> sends to {{ m.name }} and runs there, to read. It holds the machine's passwords.</p>

{% include "rebuild.html" %}
<details style="margin-top:28px"><summary>Remove this machine</summary><div class="card"><p>{{ m.name }} leaves the network
for good: its place on the VPN, its keys and its entry in the settings go. <span class="muted">The machine itself is not
touched: wipe it, or switch it off. A machine that keeps the backups of others, or helps another one unlock its disk,
is named first.</span></p>
<form method="post" action="/run" onsubmit="return confirm('Remove {{ m.name }} from the network for good?')">{{ tok }}
<input type="hidden" name="action" value="remove"><input type="hidden" name="host" value="{{ m.name }}">
<button>Remove {{ m.name }}</button></form></div></details>
{% endblock %}""",

    "settings.html": """{% extends "base.html" %}{% block main %}
{% macro field(f) %}{% set id = "f-" + f.name %}<div class="q">
{% if f.type == "modules" %}<label>{{ f.label }}</label><p class="muted" style="margin:0 0 4px">{{ f.help }}</p>
<input type="hidden" name="shown:{{ f.name }}" value="1">
{% for o in f.options %}<label class="check"><input type="checkbox" name="f:{{ f.name }}" value="{{ o.value }}"
{%- if ((o.value in typed[f.name]) if f.name in typed else o.on) %} checked{% endif %}><span>{{ o.label }}
<span class="muted small">{{ o.help }}{% if o.needs %} (needs {{ o.needs|join(", ") }}){% endif %}</span></span></label>{% endfor %}
{% else %}<label for="{{ id }}">{{ f.label }}</label>
{% if f.help %}<p class="muted" style="margin:0 0 4px">{{ f.help }}</p>{% endif %}
{% if f.secret %}<input id="{{ id }}" type="password" name="f:{{ f.name }}" autocomplete="new-password"
placeholder="{{ "set; leave empty to keep it" if f.set else "set for every machine; leave empty to keep it" if f.inherited_set else "" }}">
{% if f.set %}<label class="check"><input type="checkbox" name="clear:{{ f.name }}" value="1"><span>Remove it</span></label>{% endif %}
{% elif f.type in ("bool", "enum") %}{% set now = typed[f.name] if f.name in typed else yesno(f.value) if f.type == "bool" else (f.value or "") %}
{% set usual = yesno(f.inherited) if f.type == "bool" else f.inherited %}
<select id="{{ id }}" name="f:{{ f.name }}"><option value="">{{ same }}{% if usual %} ({{ usual }}){% endif %}</option>
{% for o in ([{"value": "yes", "label": "yes"}, {"value": "no", "label": "no"}] if f.type == "bool" else f.options) %}
<option value="{{ o.value }}"{% if o.value == now %} selected{% endif %}>{{ o.label }}</option>{% endfor %}</select>
{% elif f.type in ("list", "lines", "map", "text", "object") %}
<textarea id="{{ id }}" name="f:{{ f.name }}" rows="{{ 2 if f.type == "list" else 4 }}" placeholder="{{ to_text(f.type, f.inherited) }}">
{{- typed[f.name] if f.name in typed else to_text(f.type, f.value) }}</textarea>
{% else %}<input id="{{ id }}" name="f:{{ f.name }}" value="{{ typed[f.name] if f.name in typed else to_text(f.type, f.value) }}"
{%- if f.type == "int" %} inputmode="numeric"{% endif %} placeholder="{{ to_text(f.type, f.inherited) or (f.placeholder and "e.g. " + f.placeholder) }}">
{% endif %}{% endif %}{% if wrong.get(f.name) %}<p class="bad">{{ wrong[f.name] }}</p>{% endif %}</div>{% endmacro %}
<p><a href="{{ "/hosts/" + host if host else "/" }}">← {{ host or "Home" }}</a></p>
<h1>{{ "Settings of " + host if host else "Settings for every machine" }}</h1>
{% if error %}<p class="banner bad">{{ error }}</p>{% endif %}
{% if saved == "1" and host and running %}<p class="banner ok">Saved. Apply brings it to the machine.</p>
<form method="post" action="/run" onsubmit="return confirm('Apply to {{ host }} now? Services that changed restart for a moment.')">{{ tok }}
<input type="hidden" name="action" value="apply"><input type="hidden" name="host" value="{{ host }}"><button class="primary">Apply</button></form>
{% elif saved == "1" and host %}<p class="banner ok">Saved. The machine gets it when it is set up.</p>
{% elif saved == "1" and running %}<p class="banner ok">Saved. Update everything brings it to every machine.</p>
<form method="post" action="/run" onsubmit="return confirm('Update every machine now? This takes a while; services that changed restart for a moment.')">{{ tok }}
<input type="hidden" name="action" value="update"><button class="primary">Update everything</button></form>
{% elif saved == "1" %}<p class="banner ok">Saved. Every machine gets it when it is set up.</p>
{% elif saved %}<p class="banner warn">Nothing was changed.</p>{% endif %}
{% if groups %}<p class="muted">{% if host %}What you leave empty is as for every machine; the grey text says what that is.
{% else %}What a machine does not set for itself, it takes from here.{% endif %} Only what you change is saved.</p>
<form method="post" action="{{ "/hosts/" + host + "/settings" if host else "/settings" }}" autocomplete="off">{{ tok }}
{% for g in groups %}{% set more = g.fields|selectattr("more")|list %}
{% if g.advanced %}<details class="card" id="s-{{ g.id|replace(':', '-') }}" style="scroll-margin-top:64px"{% if g.id in open %} open{% endif %}><summary>{{ g.title }}</summary>
{% else %}<div class="card" id="s-{{ g.id|replace(':', '-') }}" style="scroll-margin-top:64px"><h2 style="margin-top:0">{{ g.title }}</h2>{% endif %}
{% if g.help %}<p class="muted">{{ g.help }}</p>{% endif %}
{% for f in g.fields if not f.more %}{{ field(f) }}{% else %}{% if not more %}<p class="muted">Nothing to set: it is on or off.</p>{% endif %}{% endfor %}
{% if more %}<details{% if more|selectattr("name", "in", touched)|list %} open{% endif %}><summary>More settings</summary>
{% for f in more %}{{ field(f) }}{% endfor %}</details>{% endif %}
{{ "</details>"|safe if g.advanced else "</div>"|safe }}{% endfor %}
<div class="actions"><button class="primary">Save</button></div></form>{% endif %}
<script>(function(){var e=location.hash&&document.getElementById(location.hash.slice(1));if(e){if(e.tagName=="DETAILS")e.open=true;
e.querySelectorAll("details").forEach(function(d){d.open=true});e.scrollIntoView();e.style.outline="2px solid var(--accent)"}})()</script>
{% if not host %}<div class="card"><h2 style="margin-top:0">Try the messages</h2><p>Sends one test message to every place set under
"Messages when something is wrong" (save first). <span class="muted">Real ones come when something has been wrong for a
while, and when it is fine again. What everyone on your network sees without a login is <a href="/status">the status page</a>.</span></p>
<form method="post" action="/run">{{ tok }}<input type="hidden" name="action" value="tell-test"><button>Send a test message</button></form></div>{% endif %}
{% endblock %}""",

    "job.html": """{% extends "base.html" %}{% block main %}
<p><a href="{{ back }}">← Back</a></p><h1>{{ title }}</h1>
<p id="say" class="banner {{ 'ok' if job.state == 'ok' else 'bad' if job.state == 'failed' else 'warn' }}">{{ say[job.state] }}</p>
<p id="open" class="card" style="display:none">Open this page to go on: <a id="openlink" target="_blank" rel="noopener"></a></p>
<details {% if job.state == 'failed' %}open{% endif %}><summary>What is happening, line by line</summary><pre id="log"></pre></details>
<script>
const say={{ say|tojson }};let off=0;const log=document.getElementById("log"),el=document.getElementById("say");
async function poll(){const r=await fetch("/api/jobs/{{ job.id }}/log?offset="+off);if(!r.ok)return;
const d=await r.json();log.textContent+=d.text;off=d.offset;el.textContent=say[d.state];
el.className="banner "+(d.state=="ok"?"ok":d.state=="failed"?"bad":"warn");
if(d.state=="failed")log.parentElement.open=true;
const u=log.textContent.match(/https:\\/\\/[^\\s"']+/g),o=document.getElementById("open");
if(u&&d.state=="running"){const a=document.getElementById("openlink");a.href=a.textContent=u[u.length-1];o.style.display=""}else o.style.display="none";
if(d.state=="waiting"||d.state=="running")setTimeout(poll,1500)}
poll();
</script>{% endblock %}""",

    "devices.html": """{% extends "base.html" %}{% block main %}
<h1>Devices</h1><p class="muted">Phones, tablets and laptops that use your VPN. On the VPN a device reaches your
services from anywhere and browses through your network.</p>
{% if alone %}<div class="banner warn">Your network has no master yet, so there is no VPN to put a device on.</div>
<div class="card"><p>At home, your services open by their names when a device asks {{ alone }} for names: set {{ alone }}'s
address as the DNS server in your router (or in the device's Wi-Fi settings). Without that, open them by address.
Away from home they do not open: a master gives your devices the VPN.</p></div>{% endif %}
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
{% if "backup" in m.modules %}<a class="btn" href="/backups/{{ m.name }}">Bring something back</a>{% endif %}
{% if b and b.held %}<p class="banner warn">Rebuilt: its backups wait. {{ b.held }}</p>
<p class="muted small">"Bring something back" fetches its data from the backups; "Start new" keeps what it has now and
backs that up instead (the old snapshots stay until they are pruned).</p>
<form class="inline" method="post" action="/run" onsubmit="return confirm('Start a new history for {{ m.name }}? It keeps what it has now and backs that up; the old snapshots stay until pruned.')">{{ tok }}
<input type="hidden" name="action" value="start-new"><input type="hidden" name="host" value="{{ m.name }}"><button>Start new</button></form>{% endif %}
{% if b and b.copies %}<p class="muted">Kept on: {% for c in b.copies %}{{ c.server }}{{ ", " if not loop.last }}{% endfor %}</p>{% endif %}
{% if "backup" not in m.modules %}<p class="muted small">Nothing on this machine is set to be saved. Machines with your data should have the <em>backup</em> part.</p>{% endif %}
{% if m.facts.backup_store %}<p class="muted">It keeps the backups of: {% for r in m.facts.backup_store.repos %}{{ r.client }} ({{ r.snapshots }} versions){{ ", " if not loop.last }}{% endfor %}
<span class="muted">· {{ size(m.facts.backup_store.free_kib) }} free</span></p>
<form class="inline" method="post" action="/run" onsubmit="return confirm('Remove the old snapshots on {{ m.name }}? It keeps as many days, weeks and months as its settings say.')">{{ tok }}
<input type="hidden" name="action" value="prune"><input type="hidden" name="host" value="{{ m.name }}"><button>Prune now</button></form>{% endif %}
</div>{% else %}<p class="muted">No machines yet.</p>{% endfor %}
<h2>The network itself</h2><div class="card"><p>Its settings and keys, as one encrypted file: a copy.
<strong>A copy and your recovery key are the whole network</strong>, so keep a copy somewhere else than on this machine:
a USB stick, another computer.</p>
{% if error %}<p class="banner bad">{{ error }}</p>{% endif %}
<div class="actions"><a class="btn primary" href="/backups/network.tar">Fetch a copy</a>
<form class="inline" method="post" action="/run">{{ tok }}<input type="hidden" name="action" value="backup"><button>Save it now</button></form></div>
<p class="muted">"Fetch a copy" gives you the file as it is now. "Save it now" puts one on each of your machines that is
set up. Only the recovery key opens a copy: the password of this page does not.</p></div>
<details><summary>How do I get something back?</summary><p class="muted">A file: open the service (Nextcloud, Immich…) and use its
own trash or versions first. A whole service as it was on another day: "Bring something back" at its machine above.
A machine that was lost: install it anew (Home lists the steps); it fetches its data by itself. The network itself: a new
control node asks on its first page ("I already have a network"); on a computer,
<code>kiwi-server restore</code>. Both take a copy and the recovery key.</p></details>
{% endblock %}""",

    "snapshots.html": """{% extends "base.html" %}{% block main %}
<p><a href="/backups">← Backups</a></p><h1>Bring something back on {{ host }}</h1>
<p class="muted">Each part of the machine is saved by itself, every night. Bringing one back puts it as it was at that
time: <strong>what it has now is replaced.</strong> The part stops for a moment.</p>
{% if error %}<p class="banner bad">{{ error }}</p>{% endif %}
{% if snaps %}{% if snaps.missed %}<div class="card"><p><span class="dot warn"></span>Nothing found on {{ snaps.missed|join(", ") }}.
<span class="muted">It did not answer, or it keeps no backups of this machine.</span></p></div>{% endif %}
{% for part, times in snaps.parts.items() %}<h2>{{ part }}</h2><div class="card">
{% for at, servers in times %}<div class="row" style="margin:6px 0"><span>{{ at[:16]|replace("T", " ") }}
<span class="muted">· kept on {{ servers|join(", ") }}</span></span>
<form class="inline" method="post" action="/run" onsubmit="return confirm('Bring {{ part }} on {{ host }} back to {{ at[:16]|replace("T", " ") }}? What it has now is replaced.')">{{ tok }}
<input type="hidden" name="action" value="restore"><input type="hidden" name="host" value="{{ host }}">
<input type="hidden" name="module" value="{{ part }}"><input type="hidden" name="at" value="{{ at }}">
<button>Bring back</button></form></div>{% endfor %}</div>
{% else %}<p class="muted">No snapshots yet. The first backup runs in the night, or with "Save now" on the Backups page.</p>{% endfor %}
{% if snaps.parts %}<h2>All of it</h2><div class="card"><p>Every part of {{ host }} back to its newest snapshot.</p>
<form method="post" action="/run" onsubmit="return confirm('Bring everything on {{ host }} back to the newest snapshot? What it has now is replaced.')">{{ tok }}
<input type="hidden" name="action" value="restore"><input type="hidden" name="host" value="{{ host }}">
<button>Bring everything back</button></form></div>{% endif %}{% endif %}
{% endblock %}""",

    "federation.html": """{% extends "base.html" %}{% block main %}
<p><a href="/">← Home</a></p><h1>Linked networks</h1>
<p class="muted">Other networks this one is linked to. Each reaches here only what is shared with it. Linking a new
network needs both networks' settings, so it is done on the computer that has them:
<code>kiwi-server federate</code>, or "Federation…" in the app.</p>
{% if saved %}<p class="banner ok">Saved. Apply this network's master, and the machines that share something, to bring it there.</p>{% endif %}
{% if error %}<p class="banner bad">{{ error }}</p>{% endif %}
{% if partners %}<form method="post" action="/federation">{{ tok }}
{% for name, p in partners.items() %}<div class="card"><div class="row"><strong>{{ name }}</strong>
<span class="muted">its names end in .{{ p.domain or "?" }} · {{ p.mesh }}</span></div>
<p>Shared with it: {{ p.share | join(", ") or "nothing" }}. <a href="/access">Change who may reach what</a></p>
<label for="f-{{ name }}">Who of it may come</label><p class="muted" style="margin:0 0 4px">Addresses or networks of
{{ name }}, like 10.10.3.0/24. Empty: all of {{ name }}.</p>
<input id="f-{{ name }}" name="from.{{ name }}" value="{{ p['from'] | join(' ') }}">
<label><input type="checkbox" name="exit.{{ name }}" style="width:auto"{% if p.exit %} checked{% endif %}>
Leave to the Internet through {{ name }}</label><p class="muted" style="margin:0">Your devices come out at its master.
It has to allow that on its side.</p>
<label><input type="checkbox" name="internet.{{ name }}" style="width:auto"{% if p.internet %} checked{% endif %}>
{{ name }} may leave to the Internet through this network</label><p class="muted" style="margin:0">Its devices come
out at your master. To the Internet only, never to your machines.</p>
</div>{% endfor %}
<div class="actions"><button class="primary">Save</button></div></form>
{% else %}<p class="muted">This network is linked to no other.</p>{% endif %}
{% endblock %}""",
    "access.html": """{% extends "base.html" %}{% block main %}
<p><a href="/">← Home</a></p><h1>Who may reach what</h1>
<p class="muted">A device on your VPN belongs to a group: you choose it when you add the device. A linked network is
another Kiwi network. Tick what each may open. A machine: all of it. A service: only that page.
The master is always reachable: it answers the names.</p>
{% if saved %}<div class="banner ok">Saved: {{ saved }}. The machines get it with the next Apply.
<form class="inline" method="post" action="/run">{{ tok }}<input type="hidden" name="action" value="update">
<button>Update everything</button></form></div>{% endif %}
{% if error %}<p class="banner bad">{{ error }}</p>{% endif %}
<form method="post" action="/access">{{ tok }}
{% for w in who %}<div class="card"><div class="row"><strong>{{ w.name }}</strong>
<span class="muted">{{ "group" if w.kind == "group" else "linked network" }} · {{ w.range }}{% if w.devices %} ·
{{ w.devices | join(", ") }}{% endif %}</span></div>
<p class="muted" style="margin:4px 0">Now: {{ w.says }}.</p>
{% if w.kind == "group" %}<label><input type="checkbox" name="all.{{ w.name }}" style="width:auto"{% if w.open.all %} checked{% endif %}>
Everything on the VPN</label>
<label><input type="checkbox" name="apps.{{ w.name }}" style="width:auto"{% if w.open.apps %} checked{% endif %}>
Every app <span class="muted">· the pages of every machine, no admin pages, nothing else of the machines</span></label>{% endif %}
{% for m in machines if not m.master %}{% set o = w.open.machines[m.name] %}<div style="margin:6px 0">
<label style="display:inline"><input type="checkbox" name="m.{{ w.name }}.{{ m.name }}" style="width:auto"{% if o.whole %} checked{% endif %}>
<strong>{{ m.name }}</strong>, all of it</label>{% if m.services %}<span class="muted"> · or only:</span>
{% for s in m.services if s not in m.admin %}<label style="display:inline;white-space:nowrap;margin-left:8px"><input type="checkbox"
name="s.{{ w.name }}.{{ m.name }}.{{ s }}" style="width:auto"{% if s in o.services %} checked{% endif %}> {{ s }}</label>{% endfor %}
{% if m.admin %}<span class="muted" style="margin-left:8px">· admin pages:</span>
{% for s in m.admin %}<label style="display:inline;white-space:nowrap;margin-left:8px"><input type="checkbox"
name="s.{{ w.name }}.{{ m.name }}.{{ s }}" style="width:auto"{% if s in o.services %} checked{% endif %}> {{ s }}</label>{% endfor %}{% endif %}{% endif %}
</div>{% endfor %}
<label for="p-{{ w.name }}">Single ports{{ " and addresses" if w.kind == "group" else "" }}</label>
<input id="p-{{ w.name }}" name="p.{{ w.name }}" placeholder="lychee:22" value="{{ w.extra | join(' ') }}">
{% if w.kind == "group" %}<div style="margin-top:8px">
<label style="display:inline"><input type="checkbox" name="peers.{{ w.name }}" style="width:auto"{% if w.peers %} checked{% endif %}> Other devices on the VPN</label>
<label style="display:inline;margin-left:12px"><input type="checkbox" name="internet.{{ w.name }}" style="width:auto"{% if w.internet %} checked{% endif %}> The Internet through the VPN</label>
<label style="display:inline;margin-left:12px"><input type="checkbox" name="admin.{{ w.name }}" style="width:auto"{% if w.admin %} checked{% endif %}> The admin pages</label>
<p class="muted" style="margin:4px 0 0">Admin pages are the network's controls: this control page, Portainer, Pi-hole, the
VPN's device page, backups. Without this tick a group does not open them, also not with "everything", unless one is
ticked above.</p></div>{% else %}<p class="muted" style="margin:6px 0 0">Who of {{ w.name }} may come, and the way out:
<a href="/federation">Linked networks</a>.</p>{% endif %}
</div>{% endfor %}
<div class="card"><label for="new">A new group</label><p class="muted" style="margin:0 0 4px">A name like kids or
work. A device joins it under <a href="/devices">Add a phone or laptop</a>.</p>
<input id="new" name="new" placeholder="kids" pattern="[a-z0-9][a-z0-9-]*">
<label for="newas">It starts with</label><select id="newas" name="new_as">
<option value="nothing">nothing: tick what it may open after saving</option>
<option value="apps">every app: for the people of the house</option>
<option value="all">everything and the admin pages: for your own devices</option></select></div>
<div class="actions"><button class="primary">Save</button></div></form>
<p class="muted">Devices outside every group reach everything{% if loose %}: {{ loose | join(", ") }}{% endif %}.</p>

<h2 id="tor" style="scroll-margin-top:64px">Hidden services</h2>
<p class="muted">A hidden service gives one service an address that anyone opens with Tor Browser, without your VPN:
for people you do not know. Leave the names empty and anyone with the address gets in, and meets the service's own
login. With names, only who holds a key finds it. Tor never leaves through this home's connection: it goes to the
master and leaves there.</p>
{% if key %}<div class="banner ok">{{ key.client }} opens <code>http://{{ key.address }}</code> in Tor Browser and
types this key when it asks:<br><code>{{ key.line }}</code></div>{% endif %}
{% if tor_saved %}<div class="banner ok">Saved: {{ tor_saved }}. The machine gets it with the next Apply.
<form class="inline" method="post" action="/run">{{ tok }}<input type="hidden" name="action" value="update">
<button>Update everything</button></form></div>{% endif %}
<form method="post" action="/onion">{{ tok }}
{% for m in machines %}{% set names = (m.tor or [])|list %}
{% for k in hidden if k.endswith("." ~ m.name) and k[:-(m.name|length + 1)] not in names %}{% set _ = names.append(k[:-(m.name|length + 1)]) %}{% endfor %}
{% if names %}<div class="card"><strong>{{ m.name }}</strong>
{% for s in names %}{% set h = hidden.get(s ~ "." ~ m.name) %}<div style="margin:6px 0">
<label style="display:inline"><input type="checkbox" name="on.{{ m.name }}.{{ s }}" style="width:auto"{% if h %} checked{% endif %}> {{ s }}</label>
{% if h %}<code class="small">http://{{ h.address }}</code>{% endif %}
<input name="who.{{ m.name }}.{{ s }}" value="{{ h.clients | join(' ') if h else '' }}" placeholder="anyone; or names, like anna ben"
style="margin-top:2px">
{% if h %}{% for c in h.clients %}<button class="small" name="show" value="{{ s }}.{{ m.name }}:{{ c }}">Show {{ c }}'s key</button>
{% endfor %}{% endif %}</div>{% endfor %}</div>{% endif %}{% endfor %}
<p class="muted small">Listed are the sites that were seen to work through Tor. Others send a visitor to their name on the VPN,
or need it; <code>kiwi-server onion SERVICE.MACHINE --anyway</code> makes one all the same.</p>
<div class="actions"><button class="primary">Save hidden services</button></div></form>
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
{% if invite_qr %}<div class="qr">{{ invite_qr }}</div><p class="muted small">The same link as a picture, for a camera.</p>{% endif %}
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
<h2 id="ssh">Let a key in for a while</h2>
<div class="card">{% if not ca.made %}<p>With the network's SSH certificate authority, each machine shows a certificate for its
SSH key, and a key you let in here opens every machine for as long as you said, then no more. Someone who helps
you for a week gets a key for a week.</p>
<form method="post" action="/controllers/sshca-init">{{ tok }}<button class="primary">Make it</button></form>
{% else %}{% if ca_made %}<p class="banner ok">Made. Apply each machine (Machines) so that it takes it.</p>{% endif %}<p class="muted small">The network's SSH certificate authority: <code>{{ ca.fingerprint }}</code></p>
{% if cert %}<p class="banner ok">This lets the key in on every machine for {{ cert_days }} days. Save it next to the key, with
<code>-cert.pub</code> at the end of the key's name (<code>id_ed25519-cert.pub</code> beside <code>id_ed25519.pub</code>): ssh finds it there.</p>
<pre class="mono" id="cert" style="white-space:pre-wrap;word-break:break-all">{{ cert }}</pre>
<button class="primary" onclick="navigator.clipboard.writeText(document.getElementById('cert').textContent);this.textContent='Copied'">Copy it</button>
{% endif %}
<form method="post" action="/controllers/sshca-sign">{{ tok }}
<label for="k">The public key (the contents of its .pub file)</label>
<textarea id="k" name="key" rows="3" placeholder="ssh-ed25519 AAAA… name@computer" required></textarea>
<label for="d">For how many days</label><input id="d" name="days" type="number" min="1" max="365" value="30">
<button>Let it in</button></form>{% endif %}</div>
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

    "joined.html": """{% extends "base.html" %}{% block main %}
<h1>This machine joins {{ network }}</h1>
<div class="banner ok">The network's control page has taken it in and sets it up now.</div>
<p>Nothing more to do here. In a few minutes this page goes away, and the machine shows up on the control page of
your network, under Machines.</p>
{% endblock %}""",
    "status.html": """{% extends "base.html" %}{% block title %}{{ brand_name or "Kiwi" }}: status{% endblock %}{% block main %}
<meta http-equiv="refresh" content="120"><style>main{max-width:1120px}.tbl td:first-child{min-width:190px}</style>
<div class="row" style="margin-top:8px"><h1 style="display:flex;align-items:center;gap:10px;margin:0"><span style="width:26px;height:26px;display:inline-flex">{{ icon("kiwi") }}</span>{{ brand_name or "Kiwi" }}</h1>
<span class="muted small"><a href="/">← all services</a>{% if h.checked %} · checked {{ age(now - h.checked) }} ago · every ten minutes{% endif %}</span></div>
<p class="sub">{{ "The services that are open to the Internet, asked the way you reach them." if h.strangers else "How the network is: every service, asked from the network itself." }}</p>
{% set down = h.services|rejectattr("ok")|list %}{% set off = (h.machines.of or 0) - (h.machines.on or 0) %}
{% if not h.checked %}<div class="banner warn">Nothing has been checked yet.</div>
{% elif stale %}<div class="banner warn">Not checked for {{ age(now - h.checked) }}. The machine that checks is off, or waits for
the people who run this network to unlock it.</div>
{% elif down %}<div class="banner bad">{{ down|length }} {{ "service does" if down|length == 1 else "services do" }} not answer</div>
{% elif off > 0 %}<div class="banner warn">The services answer. {{ off }} {{ "machine does" if off == 1 else "machines do" }} not.</div>
{% else %}<div class="banner ok">Everything works</div>{% endif %}
{% if h.services %}{% set known = h.services|selectattr("uptime", "ne", none)|map(attribute="uptime")|list %}
<div class="stats"><div class="stat ok"><span class="lbl">Up</span><b>{{ h.services|selectattr("ok")|list|length }}</b></div>
<div class="stat {{ "bad" if down else "" }}"><span class="lbl">Down</span><b>{{ down|length }}</b></div>
<div class="stat"><span class="lbl">Worked, 90 days</span><b>{% if known %}{{ "%.2f"|format(known|sum / known|length) }}<small> %</small>{% else %}–{% endif %}</b></div>
{% if h.machines.of %}<div class="stat {{ "warn" if off > 0 else "" }}"><span class="lbl">Machines</span><b>{{ h.machines.on }}<small> of {{ h.machines.of }}</small></b></div>{% endif %}</div>
<table class="tbl"><thead><tr><th>Service</th><th>Now</th><th class="wide">The last 90 days</th><th>Worked</th><th class="wide">Answers in</th></tr></thead><tbody>
{% for s in h.services %}<tr><td><b>{{ s.label }}</b><br><a class="sm" href="https://{{ s.site }}" rel="noopener">{{ s.site }}</a></td>
<td><span class="badge {{ "ok" if s.ok else "bad" }}">{{ "Up" if s.ok else "Down" }}</span><br><span class="sm">for {{ age(now - s.since) }}</span></td>
<td class="wide"><div class="spark" role="img" aria-label="{{ s.label }}: each of the last 90 days">{% for b in s.bars %}<span class="{% if b.pct is none %}none{% elif b.pct >= 99.9 %}up{% elif b.pct >= 99 %}dip{% elif b.pct >= 90 %}part{% else %}down{% endif %}" style="height:{{ 100 if b.pct is none or b.pct >= 99.9 else [b.pct, 25]|max|int }}%" title="{{ b.day }}: {% if b.pct is none %}not checked{% else %}{{ "%.1f"|format(b.pct) }} % worked{% if b.ms is not none %}, {{ b.ms }} ms{% endif %}{% endif %}"></span>{% endfor %}</div></td>
<td>{% if s.uptime is none %}<span class="sm">not yet</span>{% else %}<span class="badge plain {{ "ok" if s.uptime >= 99 else "warn" if s.uptime >= 90 else "bad" }}">{{ "%.2f"|format(s.uptime) if s.uptime < 100 else "100" }} %</span>{% endif %}</td>
<td class="wide">{% if s.ms is not none %}{{ s.ms }} <span class="sm">ms</span>{% else %}<span class="sm">–</span>{% endif %}</td></tr>{% endfor %}</tbody></table>
<p class="muted small">90 days ago on the left, today on the right: green worked all day, yellow had gaps, red was down most of the day.</p>{% endif %}
{% if h.checked %}<h2>What was wrong, the last seven days</h2>
{% for i in h.incidents %}<div class="card"><div class="row"><b>{{ i.text }}</b><span class="badge {{ "ok" if i.to else "bad" }}">{{ "fine again" if i.to else "still" }}</span></div>
<span class="muted small">{{ i.when }} · {% if i.to %}for {{ age(i.to - i.from) }}{% else %}for {{ age(now - i.from) }} so far{% endif %}</span></div>
{% else %}<p class="muted">Nothing was wrong.</p>{% endfor %}{% endif %}
{% if h.window %}<p class="muted small">When updates need it, the machines restart: {{ h.window }}.</p>{% endif %}
<p class="muted small" style="margin-top:28px">{% if h.strangers %}This page shows the services that are open to the Internet,
asked the way you reach them, and nothing else.{% else %}This page needs no login. It shows which services work, and no addresses or
settings. The people who run this network see more on their control page.{% endif %}</p>
{% endblock %}""",

    "accounts.html": """{% extends "base.html" %}{% block main %}
<h1>Accounts</h1><p class="sub">The network's accounts (Kanidm): who may sign in to its services, and who asked to.
People sign up at <a href="https://{{ id_host }}/join" target="_blank" rel="noopener">{{ id_host }}/join</a>, with an invite code or by asking.</p>
{% if error %}<div class="banner bad">{{ error }}</div>{% endif %}
{% if not available %}<div class="card"><p>No machine runs the accounts module yet. Add <code>accounts</code> to a machine's modules
(its Settings), then Apply it.</p></div>{% else %}
{% if shown %}<div class="banner ok">{{ shown.title }}</div><div class="card"><p>{{ shown.text }}</p>
<p><code style="font-size:1rem;word-break:break-all">{{ shown.value }}</code>
<button type="button" onclick="navigator.clipboard.writeText('{{ shown.value }}');this.textContent='Copied'">Copy</button></p>
<p class="muted small">{{ shown.note }}</p></div>{% endif %}
<div class="stats"><div class="stat"><span class="lbl">Accounts</span><b>{{ people|length }}</b></div>
<div class="stat {{ "warn" if waiting else "" }}"><span class="lbl">Waiting requests</span><b>{{ waiting|length }}</b></div>
<div class="stat"><span class="lbl">Invite codes</span><b>{{ live|length }}</b></div></div>
<h2>Asked for an account</h2>
{% if waiting %}<div class="scroll"><table class="tbl"><thead><tr><th>Name</th><th>Who, and why</th><th class="wide">When</th><th></th></tr></thead><tbody>
{% for r in waiting %}<tr><td><b>{{ r.name }}</b></td><td>{{ r.why }}</td><td class="wide sm">{{ age(now - r.at) }} ago</td>
<td style="white-space:nowrap"><form class="inline" method="post" action="/accounts/accept">{{ tok }}<input type="hidden" name="hint" value="{{ r.hint }}"><button class="primary">Accept</button></form>
<form class="inline" method="post" action="/accounts/deny" onsubmit="return confirm('Deny {{ r.name }}?')">{{ tok }}<input type="hidden" name="hint" value="{{ r.hint }}"><button>Deny</button></form></td></tr>{% endfor %}</tbody></table></div>
<p class="muted small">Accepted: the account is made, and the person sees the link that sets their password when they come back with their request code.</p>
{% else %}<p class="muted">Nobody is waiting.</p>{% endif %}
<h2>Invite codes</h2>
<form class="card row" method="post" action="/accounts/invite" style="justify-content:flex-start;align-items:flex-end">{{ tok }}
<div><label for="inv-note">For whom</label><input id="inv-note" name="note" placeholder="Anna, the climbing club" maxlength="80"></div>
<div style="max-width:120px"><label for="inv-uses">Accounts</label><input id="inv-uses" name="uses" type="number" min="1" max="50" value="1"></div>
<div style="max-width:120px"><label for="inv-days">Good for (days)</label><input id="inv-days" name="days" type="number" min="1" max="90" value="14"></div>
<button class="primary">Make an invite code</button></form>
{% if invites %}<div class="scroll"><table class="tbl"><thead><tr><th>Code</th><th>For whom</th><th>Used</th><th class="wide">Good until</th><th></th></tr></thead><tbody>
{% for i in invites|reverse %}<tr><td>…{{ i.hint }}</td><td>{{ i.note or "–" }}</td><td>{{ i.used }} of {{ i.uses }}</td>
<td class="wide sm">{% if i.live %}{{ age(i.until - now) }} more{% else %}<span class="badge plain">over</span>{% endif %}</td>
<td>{% if i.live %}<form class="inline" method="post" action="/accounts/revoke">{{ tok }}<input type="hidden" name="hint" value="{{ i.hint }}"><button>Withdraw</button></form>{% endif %}</td></tr>{% endfor %}</tbody></table></div>{% endif %}
<h2>The accounts</h2>
<form class="card row" method="post" action="/accounts/create" style="justify-content:flex-start;align-items:flex-end">{{ tok }}
<div><label for="acc-name">Make one yourself</label><input id="acc-name" name="name" pattern="[a-z][a-z0-9_-]{1,31}" placeholder="lowercase letters and digits" required></div>
<button>Make the account</button></form>
{% if people %}<div class="scroll"><table class="tbl"><thead><tr><th>Name</th><th class="wide">Shown as</th><th class="wide">Jabber</th><th></th></tr></thead><tbody>
{% for p in people %}<tr><td><b>{{ p.name }}</b></td><td class="wide">{{ p.display }}</td><td class="wide">{{ "yes" if p.jabber else "–" }}</td>
<td style="white-space:nowrap"><form class="inline" method="post" action="/accounts/link">{{ tok }}<input type="hidden" name="name" value="{{ p.name }}"><button>New password link</button></form>
<form class="inline" method="post" action="/accounts/delete" onsubmit="return confirm('Delete the account {{ p.name }}? Its person can no longer sign in anywhere.')">{{ tok }}<input type="hidden" name="name" value="{{ p.name }}"><button>Delete</button></form></td></tr>{% endfor %}</tbody></table></div>
{% else %}<p class="muted">No accounts yet.</p>{% endif %}
<h2>Services that sign people in with these accounts</h2>
{% if connectable %}<div class="scroll"><table class="tbl"><thead><tr><th>Service</th><th>Machine</th><th>Signs people in</th><th></th></tr></thead><tbody>
{% for c in connectable %}<tr><td><b>{{ c.label }}</b></td><td>{{ c.host }}</td><td>{% if c.connected %}<span class="badge ok">yes</span>{% else %}<span class="badge plain">not yet</span>{% endif %}</td>
<td>{% if not c.connected %}<form class="inline" method="post" action="/accounts/connect">{{ tok }}<input type="hidden" name="name" value="{{ c.host }}"><input type="hidden" name="note" value="{{ c.module }}">
<button>Connect</button></form>{% endif %}</td></tr>{% endfor %}</tbody></table></div>
<p class="muted small">Connected: after its next Apply the service shows "Sign in with accounts", and a person's first sign-in makes their user there.</p>
{% else %}<p class="muted">None of your machines runs a service that can do so yet (Forgejo and Nextcloud can).</p>{% endif %}{% endif %}
{% if jabber %}<h2>Jabber's sign-up page</h2>
<p class="muted small">Jabber addresses are kept apart from these accounts. People sign up at <code>/join</code> on Jabber's names.</p>
{% if jabber.waiting %}<div class="scroll"><table class="tbl"><thead><tr><th>Name</th><th>Who, and why</th><th class="wide">When</th><th></th></tr></thead><tbody>
{% for r in jabber.waiting %}<tr><td><b>{{ r.name }}</b></td><td>{{ r.why }}</td><td class="wide sm">{{ age(now - r.at) }} ago</td>
<td style="white-space:nowrap"><form class="inline" method="post" action="/accounts/jabber-accept">{{ tok }}<input type="hidden" name="hint" value="{{ r.hint }}"><button class="primary">Accept</button></form>
<form class="inline" method="post" action="/accounts/jabber-deny" onsubmit="return confirm('Deny {{ r.name }}?')">{{ tok }}<input type="hidden" name="hint" value="{{ r.hint }}"><button>Deny</button></form></td></tr>{% endfor %}</tbody></table></div>
{% else %}<p class="muted">Nobody is waiting for a Jabber address.</p>{% endif %}
<form class="card row" method="post" action="/accounts/jabber-invite" style="justify-content:flex-start;align-items:flex-end">{{ tok }}
<div><label for="jinv-note">For whom</label><input id="jinv-note" name="note" maxlength="80"></div>
<div style="max-width:120px"><label for="jinv-uses">Addresses</label><input id="jinv-uses" name="uses" type="number" min="1" max="50" value="1"></div>
<div style="max-width:120px"><label for="jinv-days">Good for (days)</label><input id="jinv-days" name="days" type="number" min="1" max="90" value="14"></div>
<button class="primary">Make a Jabber invite code</button></form>
{% if jabber.invites %}<div class="scroll"><table class="tbl"><thead><tr><th>Code</th><th>For whom</th><th>Used</th><th></th></tr></thead><tbody>
{% for i in jabber.invites|reverse %}<tr><td>…{{ i.hint }}</td><td>{{ i.note or "–" }}</td><td>{{ i.used }} of {{ i.uses }}</td>
<td>{% if i.live %}<form class="inline" method="post" action="/accounts/jabber-revoke">{{ tok }}<input type="hidden" name="hint" value="{{ i.hint }}"><button>Withdraw</button></form>{% else %}<span class="badge plain">over</span>{% endif %}</td></tr>{% endfor %}</tbody></table></div>{% endif %}{% endif %}
{% endblock %}""",

    "start.html": """{% extends "base.html" %}{% block title %}{{ brand_name or "Kiwi" }}{% endblock %}{% block main %}
<meta http-equiv="refresh" content="300">
<style>main{max-width:1120px}
.hero{display:flex;flex-wrap:wrap;gap:18px;align-items:center;justify-content:space-between;padding:26px 0 8px}
.hero h1{font-size:2rem;margin:0;display:flex;align-items:center;gap:12px}.hero .logo{width:40px;height:40px;display:inline-flex}
.hero p{max-width:640px;color:var(--muted);margin:8px 0 0;font-size:1.02rem}
.cta{display:flex;gap:10px;flex-wrap:wrap}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px;margin-top:10px}
.svc2{display:flex;flex-direction:column;gap:8px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.svc2 .top{display:flex;gap:12px;align-items:center}.svc2 .mono{width:40px;height:40px;border-radius:9px;display:inline-flex;align-items:center;
justify-content:center;font-weight:700;font-size:1.1rem;color:var(--onaccent);background:var(--accent);flex:none}
.svc2 .mono.off{background:var(--bad)}.svc2 h3{margin:0;font-size:1.02rem}.svc2 .about{color:var(--muted);font-size:.9rem;margin:0}
.svc2 .addr{font-size:.82rem;color:var(--muted);overflow-wrap:anywhere}.svc2 .addr code{font-size:.8rem}
.svc2 .foot{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-top:auto}
.mini{display:flex;align-items:flex-end;gap:1px;height:16px;width:110px}.mini span{flex:1;background:var(--ok);border-radius:1px;opacity:.85}
.mini span.none{background:var(--line)}.mini span.part{background:var(--warn)}.mini span.down{background:var(--bad)}
.note{border:1px dashed var(--line2);border-radius:10px;padding:14px 16px;margin-top:18px;color:var(--muted);font-size:.92rem}
</style>
{% set down = h.services|rejectattr("ok")|list %}
<div class="hero"><div><h1><span class="logo">{{ icon("kiwi") }}</span>{{ brand_name or ("Your network" if not h.strangers else "Kiwi") }}</h1>
<p>{% if about_text %}{{ about_text }}{% elif h.strangers %}Services run by people, not by a company: no tracking, no ads. Use them in your browser, or over Tor.
{% else %}The services of your network. They open from every device on your VPN.{% endif %}</p></div>
<div class="cta">{% if h.join and (not h.strangers or h.join_public) %}<a class="btn primary" href="{{ h.join }}">Get an account</a>{% endif %}
<a class="btn" href="/status">{% if not h.checked %}Status{% elif down %}<span class="dot bad"></span>{{ down|length }} not answering{% else %}<span class="dot ok"></span>Everything works{% endif %}</a></div></div>
{% if h.services %}<div class="grid">
{% for s in h.services %}<div class="svc2"><div class="top"><span class="mono {{ "" if s.ok else "off" }}">{{ s.label[:1] }}</span>
<div><h3>{{ s.label }}</h3>{% if s.about %}<p class="about">{{ s.about }}</p>{% endif %}</div></div>
<div class="addr"><a href="https://{{ s.site }}" rel="noopener">{{ s.site }}</a>{% if s.onion %}<br>Tor: <code>{{ s.onion }}</code>
<a href="#" class="small" onclick="navigator.clipboard.writeText('http://{{ s.onion }}');this.textContent='copied';return false">copy</a>{% endif %}</div>
<div class="foot"><div class="mini" title="the last 30 days">{% for b in s.bars[-30:] %}<span class="{% if b.pct is none %}none{% elif b.pct >= 99 %}up{% elif b.pct >= 90 %}part{% else %}down{% endif %}" style="height:{{ 100 if b.pct is none or b.pct >= 99 else [b.pct, 30]|max|int }}%"></span>{% endfor %}</div>
<span class="small">{% if not s.ok %}<span class="bad">not answering</span>{% elif s.uptime is not none %}{{ "%.1f"|format(s.uptime) if s.uptime < 99.95 else "100" }} % worked{% else %}<span class="ok">works</span>{% endif %}</span>
<a class="btn primary" href="https://{{ s.site }}" rel="noopener">Open</a></div></div>{% endfor %}</div>
{% else %}<div class="card"><p class="muted">{{ "Nothing is open to the Internet here yet." if h.strangers else "No services have been checked yet." }}</p></div>{% endif %}
{% if h.strangers and h.services %}<div class="grid" style="margin-top:22px">
<div class="card" style="margin:0"><b>Run by people, not by a company</b><p class="muted small">No tracking, no ads, no selling of what you do here.
The services are free software.</p></div>
{% if h.onion %}<div class="card" style="margin:0"><b>Over Tor too</b><p class="muted small">Every service with a Tor address opens in Tor Browser,
without anyone seeing who you are or where you are.</p></div>{% endif %}
{% if h.join and h.join_public %}<div class="card" style="margin:0"><b>One account for everything</b><p class="muted small">With an invite code,
or by asking. No email address is needed. <a href="{{ h.join }}">Get one</a>.</p></div>{% endif %}</div>{% endif %}
{% if not h.strangers %}<div class="note">On a phone or laptop that is not on the VPN yet, these addresses do not open: ask the people who run this network to add
it{% if h.onion %}, or use the Tor addresses with Tor Browser{% endif %}.</div>{% endif %}
<p class="muted small" style="margin-top:26px">{% if contact %}Contact: {{ contact }} · {% endif %}{{ brand_name or "This network" }} runs on kiwi-server. <a href="/status">Status</a>{% if h.join and (not h.strangers or h.join_public) %} · <a href="{{ h.join }}">Get an account</a>{% endif %}</p>
{% endblock %}""",

    "help.html": """{% extends "base.html" %}{% block main %}
<h1>Help</h1>
<h2>What is this page?</h2><p>This machine looks after your network. It checks every machine every ten minutes, and from
here you can see how they are, bring one up to date, add a phone to your VPN and let another computer help manage
it. Version {{ version }}.</p>
<h2>How do I…</h2><div class="card"><dl>
<dt>…use my services on the go?</dt><dd><a href="/devices">Add your phone</a>, scan the code with the WireGuard app, switch the VPN on.</dd>
<dt>…fix a machine with a problem?</dt><dd>Open it from <a href="/">Home</a>, read what it says, then <em>Apply</em>. If it does not answer at all, check that it is on.</dd>
<dt>…find the password of a service?</dt><dd>Open its machine from <a href="/">Home</a>. Its <em>Logins</em> list the
user name of each service and show the password when you ask.</dd>
<dt>…change a setting?</dt><dd>Open the machine from <a href="/">Home</a> and choose <em>Settings</em>. What all machines
share is under <a href="/settings">Settings for every machine</a>. Then <em>Apply</em> brings it to the machine.</dd>
<dt>…bring something back that broke?</dt><dd><a href="/recovery">If something breaks</a> says what happens by
itself and what to press: for a service's data, a lost machine, the master, a lost phone or computer. It also says
now whether you could.</dd>
<dt>…add a machine?</dt><dd>On <a href="/">Home</a>, under <em>Your machines</em>: <em>Add a machine at home</em>.</dd>
<dt id="gone" style="scroll-margin-top:64px">…get a machine back that stopped answering?</dt><dd>Check that it is on and has a
connection. A rented server: restart it in its provider's panel. This machine can do that by itself: under
<a href="/settings">Settings for every machine</a>, <em>When a machine is gone</em>, choose <em>restart</em>. It then
switches a master that has been gone for 15 minutes off and on at its provider, at most three times a day, and
<a href="/activity">Activity</a> says when.</dd>
<dt>…get in after a power cut?</dt><dd>{% if auto %}Nothing to do: this machine unlocks by itself with the help of
{{ auto.server }} (the ring “{{ auto.ring }}”). If that machine is off, log in here once.{% else %}Log in here once. That
unlocks the keys again. It can also unlock by itself with the help of another of your machines: in the app, give this
machine's settings <code>unlock: { control: &lt;a ring on another machine&gt; }</code>.{% endif %}</dd></dl></div>
<h2>This machine's own tools</h2><div class="card"><p>To make install media and check settings, this machine
needs a few programs of its own. The check lists each one; "!!" marks one that is missing (some are optional).</p>
<form class="inline" method="post" action="/run">{{ tok }}<input type="hidden" name="action" value="doctor"><button>Check them</button></form></div>
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


# a machine's page: what a person types to get into each of its services. The rows come from
# `list --porcelain`, which has no passwords; one is fetched from /api/hosts/<name>/logins
# when it is asked for, and is in the page only from then on
TEMPLATES["logins.html"] = """{% if logins %}<h2 id="logins" style="scroll-margin-top:64px">Logins</h2>
<div class="card"><p class="muted">What you type to get into this machine's services. A password is fetched
when you ask for it; Activity notes that it was shown.</p>
{% for l in logins %}{% set web = l.address.startswith("https://") or l.address.startswith("http://") %}
<div class="login" data-key="{{ l.module }}|{{ l.label }}|{{ l.address }}"
{%- if not loop.first %} style="border-top:1px solid var(--line);margin-top:12px;padding-top:12px"{% endif %}>
<div class="row"><strong>{% if web %}<a href="{{ l.address }}" target="_blank" rel="noopener">{{ l.label }}</a>{% else %}{{ l.label }}{% endif %}</strong>
{% if l.has_password %}<span><button type="button" class="showpw">Show password</button>
<button type="button" class="copypw" style="display:none">Copy</button></span>{% endif %}</div>
{% if l.user or l.has_password or (l.address and not web) %}<dl>
{% if l.address and not web %}<dt>Address</dt><dd>{{ l.address }}</dd>{% endif %}
{% if l.user %}<dt>User name</dt><dd>{{ l.user }}</dd>{% endif %}
{% if l.has_password %}<dt>Password</dt><dd><code class="pw" style="word-break:break-all">••••••••</code></dd>{% endif %}</dl>{% endif %}
{% if l.note %}<p class="muted small">{{ l.note }}</p>{% endif %}</div>{% endfor %}
<p class="bad" id="pwerr" style="display:none"></p></div>
<script>
(function () {
  var got = null, err = document.getElementById("pwerr"), dots = "••••••••";
  function all() {
    if (got) return Promise.resolve(got);
    return fetch("/api/hosts/" + encodeURIComponent({{ m.name | tojson }}) + "/logins", {cache: "no-store"})
      .then(function (r) {
        if (r.status == 401) throw new Error("You are logged out. Load the page again and log in.");
        return r.json().then(function (d) {
          if (!r.ok) throw new Error(d.error || "The password could not be fetched.");
          got = {};
          d.logins.forEach(function (l) { got[l.module + "|" + l.label + "|" + l.address] = l.password; });
          return got; });
      }, function () { throw new Error("This page did not answer. Try again in a moment."); });
  }
  document.querySelectorAll(".login").forEach(function (row) {
    var show = row.querySelector(".showpw"), copy = row.querySelector(".copypw"), pw = row.querySelector(".pw");
    if (!show) return;
    show.addEventListener("click", function () {
      err.style.display = "none";
      if (show.dataset.on) { pw.textContent = dots; show.textContent = "Show password"; delete show.dataset.on; copy.style.display = "none"; return; }
      all().then(function (found) {
        if (!found[row.dataset.key]) throw new Error("This login has changed. Load the page again.");
        pw.textContent = found[row.dataset.key]; show.textContent = "Hide password"; show.dataset.on = "1"; copy.style.display = "";
      }).catch(function (e) { err.textContent = e.message; err.style.display = ""; });
    });
    copy.addEventListener("click", function () {
      function said(text) { copy.textContent = text; setTimeout(function () { copy.textContent = "Copy"; }, 2000); }
      (navigator.clipboard ? navigator.clipboard.writeText(pw.textContent) : Promise.reject()).then(function () {
        said("Copied");
      }).catch(function () {             // the browser does not let the page copy: mark it, to copy by hand
        var range = document.createRange(); range.selectNodeContents(pw);
        window.getSelection().removeAllRanges(); window.getSelection().addRange(range);
        said("Marked: copy it");
      });
    });
  });
})();
</script>{% endif %}"""


# a machine that is set up and was lost or replaced: the steps of its first install again, with
# the "Still to set up" card's macro in the phase that fits (recovery.py, docs/recovery.md)
TEMPLATES["rebuild.html"] = """{% if m.phase == "applied" %}{% from "step.html" import step with context %}
<details id="rebuild" style="margin-top:28px;scroll-margin-top:64px"{% if m.media %} open{% endif %}>
<summary>This machine is lost or was replaced</summary>
<p>Install {{ m.name }} anew, under the same name. {% if m.provider %}A server that still answers is set up again as it
is. One that does not is erased and installed anew at {{ m.provider }}.{% else %}Its install media erase the disk of the machine they start.{% endif %}</p>
<p class="muted">{% if "backup" in m.modules %}Once it is set up, it finds itself in its backups and brings its data
back by itself, then backs up as before.{% elif m.role == "master" %}It keeps no data of its own. Its place on the VPN
comes back from the copy the fleet keeps of it; <a href="/recovery">If something breaks</a> says whether there is one.
{% else %}It has no backups: after the set-up it starts empty.{% endif %}</p>
{% if "backup" in m.modules and m.state != "down" %}<div class="card"><p><strong>First: save its data.</strong>
{{ m.name }} still answers. Installed anew, it gets back what its last backup holds{% if m.facts.backup and m.facts.backup.age is not none %},
and that one is {{ age(m.facts.backup.age) }} old{% endif %}. What changed since then would be gone.</p>
<form class="inline" method="post" action="/run">{{ tok }}<input type="hidden" name="action" value="save">
<input type="hidden" name="host" value="{{ m.name }}"><button class="primary">Save its data now</button></form></div>{% endif %}
{{ step(dict(m, phase="build" if m.provider or not m.media else "install"), tok) }}</details>{% endif %}"""

# if something breaks: the report of `kiwi-server recovery`, its checks and its cases
TEMPLATES["breaks.html"] = """{% extends "base.html" %}{% block main %}
<p><a href="/">← Home</a></p><h1>If something breaks</h1>
<p class="muted">Before anything breaks: could you bring your network back? When something breaks: what happens by
itself, and what you do.</p>
{% if error %}<p class="banner bad">{{ error }}</p>{% endif %}
{% if rep %}{% set gaps = rep.checks|selectattr("ok", "sameas", false)|list %}<h2>Ready?</h2>
{% if gaps %}<p class="banner warn">{{ gaps|length }} {{ "gap" if gaps|length == 1 else "gaps" }}: each says what to do.</p>
{% else %}<p class="banner ok">No gaps found.</p>{% endif %}
<div class="card">{% for c in rep.checks %}<div{% if not loop.first %} style="border-top:1px solid var(--line);margin-top:8px;padding-top:8px"{% endif %}>
<p><span class="dot {{ 'ok' if c.ok else 'warn' if c.ok is sameas false else '' }}"></span>{{ c.text }}</p>
{% if c.fix and c.ok is not sameas true %}<p class="muted small" style="margin-left:18px">{{ c.fix }}</p>{% endif %}</div>{% endfor %}
<p class="muted small" style="margin-top:12px">Green: ready. Amber: a gap. Grey: this page cannot tell.
{% if rep.looked %}The machines were last asked {{ ago(rep.looked) }} ago.{% endif %}</p></div>
<h2>If…</h2>
{% for k in rep.cases %}<details class="card" id="{{ k.id }}"><summary>{{ k.title }}</summary>
<p><strong>By itself:</strong> {{ k.by_itself }}</p><p><strong>What you do:</strong> {{ k.you }}</p>
{% set links = k.how|selectattr("page")|list %}{% if links %}<div class="actions">{% for h in links
if h.page.startswith("/") and not h.page.startswith("//") %}<a class="btn" href="{{ h.page }}">{{ h.label }}</a>{% endfor %}</div>{% endif %}
{% set cmds = k.how|selectattr("command")|list %}{% if cmds %}<details><summary>In a terminal</summary>
<pre>{% for h in cmds %}{{ h.command }}
{% endfor %}</pre></details>{% endif %}</details>{% endfor %}{% endif %}
{% endblock %}"""


# what the pages call things
KIND = {"master": "Master", "node": "Home server (node)", "node-gw": "Home server and gateway",
        "node-cloud": "Home server", "bare": "Bare machine"}
WORDS = {"ok": "fine", "warn": "wants a look", "bad": "has a problem", "down": "not reachable", "unknown": "not checked yet"}
JOBWORD = {"waiting": "waiting", "running": "running", "ok": "done", "failed": "did not work"}
FRESH = ("enroll", "build", "install", "invalid", "bare")      # a machine's phases before it is set up
VERB = {"status": "Checking", "plan": "Previewing", "apply": "Applying to", "check": "Checking the names",
        "backup": "Saving the network", "watch": "Looking for restarted masters", "save": "Saving the data of",
        "update": "Updating every machine", "rotate": "Replacing the SSH keys", "setup": "Setting up", "build": "Making the install media of", "restore": "Bringing back on", "unlock": "Unlocking", "remove": "Removing",
        "rotate-seed": "Replacing the secrets inside the services", "tell-test": "Sending a test message",
        "start-new": "Starting a new backup history of", "prune": "Removing old snapshots on",
        "doctor": "Checking the tools of this machine"}
SERVICES = healthmod.SERVICES        # a service by the first part of its name, in plain words
HINTS = (
    ("unreachable", "It did not answer. Is it switched on and online? After a restart, give it a minute, then check again."),
    ("disk is locked", "Its disk is encrypted and waits to be unlocked after a restart. \"Unlock it\" on its page "
                       "does that; so does the Kiwi Server app."),
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


# the menu: (address, words, picture); the first three are the phone's bar, the rest its "More"
# (address, label, picture, group): the first three stand alone and are the phone's bar; the
# rest come under the word of their group
NAV = (("/", "Home", "home", ""), ("/machines", "Machines", "server", ""), ("/services", "Services", "grid", ""),
       ("/devices", "Devices", "phone", "Access"), ("/accounts", "Accounts", "people", "Access"),
       ("/access", "Who may reach what", "shield", "Access"),
       ("/federation", "Linked networks", "link", "Access"),
       ("/backups", "Backups", "box", "Safety"), ("/recovery", "If something breaks", "buoy", "Safety"),
       ("/controllers", "Computers", "screen", "Safety"),
       ("/activity", "Activity", "pulse", "More"), ("/settings", "Settings", "sliders", "More"), ("/help", "Help", "help", "More"))
# line drawings, 24 by 24, drawn here: nothing is fetched from anywhere
ICONS = {
    "home": '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/><path d="M10 20v-6h4v6"/>',
    "people": '<circle cx="9" cy="8" r="3.2"/><path d="M3.5 19c.6-3.2 2.8-5 5.5-5s4.9 1.8 5.5 5"/><circle cx="17" cy="9" r="2.5"/><path d="M15.5 14.2c2.4.2 4.2 1.9 4.8 4.8"/>',
    "phone": '<rect x="7" y="3" width="10" height="18" rx="2"/><path d="M11 17.5h2"/>',
    "box": '<path d="M3 5h18v4H3z"/><path d="M5 9v10h14V9"/><path d="M10 13h4"/>',
    "shield": '<path d="M12 3l8 3v6c0 4.5-3.2 7.8-8 9-4.8-1.2-8-4.5-8-9V6z"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3A4 4 0 0 0 13 5.3l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3A4 4 0 0 0 11 18.7l1-1"/>',
    "buoy": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/>'
            '<path d="M5.6 5.6l3.6 3.6M14.8 14.8l3.6 3.6M18.4 5.6l-3.6 3.6M9.2 14.8l-3.6 3.6"/>',
    "screen": '<rect x="3" y="5" width="18" height="11" rx="1.5"/><path d="M8 20h8M12 16v4"/>',
    "pulse": '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
    "sliders": '<path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.5a2.5 2.5 0 1 1 3.6 2.2c-.8.5-1.2 1-1.2 1.9M12 17h.01"/>',
    "more": '<path stroke-width="3" d="M5 12h.01M12 12h.01M19 12h.01"/>',
    "grid": '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/>'
            '<rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    "moon": '<path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/>',
    "server": '<rect x="3" y="4" width="18" height="7" rx="1.5"/><rect x="3" y="13" width="18" height="7" rx="1.5"/><path d="M7 7.5h.01M7 16.5h.01"/>',
    "stack": '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 12l9 5 9-5"/><path d="M3 16l9 5 9-5"/>',
}
KIWI = ('<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="11" fill="#7b5a3c"/>'
        '<circle cx="12" cy="12" r="9.3" fill="#8dc63f"/><circle cx="12" cy="12" r="3.3" fill="#f4f1d6"/>%s</svg>'
        % "".join('<circle cx="%.1f" cy="%.1f" r=".75" fill="#1f2a1a"/>' % (12 + 5.7 * math.cos(i * math.pi / 5), 12 + 5.7 * math.sin(i * math.pi / 5))
                  for i in range(10)))


def icon(name):
    if name == "kiwi":
        return markupsafe.Markup(KIWI)
    return markupsafe.Markup('<svg class="ico" viewBox="0 0 24 24" aria-hidden="true">%s</svg>' % ICONS.get(name, ""))


def size(kib):
    g = (kib or 0) / 1048576
    return "%.0f GB" % g if g >= 10 else "%.1f GB" % g if g >= 1 else "%d MB" % ((kib or 0) / 1024)


def bars(facts):
    """How busy and how full a machine is: its load, its memory and its fullest disk, in percent."""
    out = []

    def row(label, pct, warn, bad, more):
        pct = max(0, min(100, int(round(pct))))
        out.append({"label": label, "pct": pct, "level": "bad" if pct >= bad else "warn" if pct >= warn else "", "more": more})
    load, cpus = facts.get("load") or [], facts.get("cpus") or 0
    if load and cpus:
        row("CPU", 100 * load[0] / cpus, 90, 101, "load %.2f on %d %s" % (load[0], cpus, "core" if cpus == 1 else "cores"))
    mem = facts.get("memory") or {}
    if mem.get("total"):
        used = mem["total"] - (mem.get("available") or 0)
        row("Memory", 100 * used / mem["total"], 85, 95, "%s of %s in use" % (size(used), size(mem["total"])))
    disks = [d for d in facts.get("disks") or [] if d.get("size")]
    if disks:
        d = max(disks, key=lambda d: d["used"] / d["size"])
        row("Disk", 100 * d["used"] / d["size"], 80, 92, "%s of %s in use on %s" % (size(d["used"]), size(d["size"]), d["mount"]))
    return out


def tags(modules):
    """What a machine is for the others, from its parts."""
    return [word for part, word in (("control", "control node"), ("gateway", "gateway"), ("backup-server", "keeps backups"))
            if part in (modules or [])]


def stats(machines, devices):
    """Home's row of numbers: the machines that answer, their services, the oldest last backup, the devices."""
    run = [m for m in machines if m["phase"] not in FRESH]
    if not run:
        return []
    out = []
    on = [m for m in run if m["state"] in ("ok", "warn", "bad")]
    down = [m["name"] for m in run if m["state"] == "down"]
    cap = ("%s is not reachable" % down[0] if len(down) == 1 else "%d are not reachable" % len(down) if down
           else "all answer" if len(on) == len(run) else "not all checked yet")
    out.append({"label": "Machines on", "pic": "server", "value": len(on), "of": len(run), "caption": cap,
                "level": "bad" if down else "ok" if len(on) == len(run) else "", "href": "/machines"})
    running = sum(m["facts"].get("running") or 0 for m in on)
    expected = sum(m["facts"].get("expected") or 0 for m in on)
    if expected:
        out.append({"label": "Services", "pic": "stack", "value": running, "of": expected,
                    "caption": "all run" if running >= expected else "%d not running" % (expected - running),
                    "level": "ok" if running >= expected else "warn", "href": "/services"})
    saved = [(m["facts"]["backup"].get("age"), m["name"]) for m in on if m["facts"].get("backup")]
    if saved:
        never = [n for a, n in saved if a is None]
        worst, who = max(((a, n) for a, n in saved if a is not None), default=(None, ""))
        if never:
            out.append({"label": "Last backup", "pic": "box", "value": "never", "caption": "%s has not saved its data yet" % never[0],
                        "level": "bad", "href": "/backups"})
        else:
            out.append({"label": "Last backup", "pic": "box", "value": age(worst), "caption": "ago at the longest: %s" % who,
                        "level": "bad" if worst > 3 * 86400 else "warn" if worst > 36 * 3600 else "ok", "href": "/backups"})
    else:
        out.append({"label": "Last backup", "pic": "box", "value": "none", "caption": "no machine saves its data", "level": "warn",
                    "href": "/backups"})
    out.append({"label": "Devices", "pic": "phone", "value": len(devices), "caption": "phones and laptops on your VPN", "level": "",
                "href": "/devices"})
    return out


def picture(machines, devices=0, per_row=4):
    """The network as it is (SVG, drawn here; the app draws the same): the master out on the
    Internet, the machines at home, and the tunnel each holds to the master, green while it
    answers. `per_row=1` is the narrow one for a phone: a list with a branch to each machine."""
    masters = [m for m in machines if m["role"] == "master"]
    rest = [m for m in machines if m["role"] and m["role"] != "master"]
    if not masters or not rest:
        return ""
    esc = markupsafe.escape
    lines, boxes = [], []

    def box(m, x, y, w, h, cap, cls=""):
        boxes.append('<a href="/hosts/%s"><rect %sx="%d" y="%d" width="%d" height="%d" rx="12"/>'
                     '<circle class="ring %s" cx="%d" cy="%d" r="9"/><circle class="%s" cx="%d" cy="%d" r="5"/>'
                     '<text x="%d" y="%d">%s</text><text class="c" x="%d" y="%d">%s</text></a>'
                     % (esc(m["name"]), 'class="%s" ' % cls if cls else "", x, y, w, h, esc(m["state"]), x + 20, y + h // 2,
                        esc(m["state"]), x + 20, y + h // 2, x + 36, y + h // 2 - 4, esc(m["name"]), x + 36, y + h // 2 + 13, esc(cap)))

    def line(d, m=None):
        cls = "" if m is None else ' class="%s"' % ("up" if m["state"] in ("ok", "warn", "bad") else "off")
        lines.append('<path%s d="%s"/>' % (cls, d))
    master = masters[0]
    mcap = "master" + (" · %d %s on its VPN" % (devices, "device" if devices == 1 else "devices") if devices else "")
    cap = lambda m: ", ".join(tags(m["modules"])[:2]) or ("master" if m["role"] == "master" else "node")   # noqa: E731
    bh, mh, gap, zp = 54, 58, 18, 34
    rest = masters[1:] + rest
    if per_row == 1:
        w = 340
        box(master, 10, 8, 260, mh, mcap, "m")
        zy, tx, y = 8 + mh + 30, 32, 8 + mh + 60
        for m in rest:
            line("M%d %d Q%d %d %d %d H%d" % (tx, y + bh // 2 - 10, tx, y + bh // 2, tx + 10, y + bh // 2, 58), m)
            box(m, 58, y, w - 68, bh, cap(m))
            y += bh + 10
        lines.insert(0, '<path d="M%d %d V%d"/>' % (tx, 8 + mh, y - 10 - bh // 2))
        zone = (4, zy, w - 8, y - 10 + 18 - zy)
    else:
        bw = 172
        if len(rest) > 2 * per_row - 1:         # two rows hold them when narrower
            bw, per_row = 140, per_row + 1
        rows, left = [], list(rest)
        while left:                             # later rows: one less, between the ones above
            take = per_row if not rows else per_row - 1
            rows.append(left[:take])
            left = left[take:]
        w = per_row * bw + (per_row - 1) * gap + 2 * zp + 20
        mw, mx = 260, (w - 260) // 2
        box(master, mx, 8, mw, mh, mcap, "m")
        zy, y, placed = 8 + mh + 58, 8 + mh + 88, []
        for row in rows:
            x = (w - (len(row) * bw + (len(row) - 1) * gap)) // 2
            for m in row:
                placed.append((x, y, m))
                box(m, x, y, bw, bh, cap(m))
                x += bw + gap
            y += bh + gap
        placed.sort(key=lambda p: (p[0], p[1]))
        for i, (x, by, m) in enumerate(placed):
            sx = mx + 24 + (mw - 48) * (i + 0.5) / len(placed)
            ex, ez, sy = x + bw // 2, zy + 12, 8 + mh
            mid = (sy + ez) / 2
            line("M%.1f %d C%.1f %.1f %d %.1f %d %d V%d" % (sx, sy, sx, mid, ex, mid, ex, ez, by), m)
        zone = (10, zy, w - 20, y - gap + 22 - zy)
    h = zone[1] + zone[3] + 8
    ground = '<rect class="zone" x="%d" y="%d" width="%d" height="%d" rx="14"/><text class="zl" x="%d" y="%d">At home</text>' % (
        zone[0], zone[1], zone[2], zone[3], zone[0] + 14, zone[1] + 18)
    return markupsafe.Markup('<svg viewBox="0 0 %d %d" style="max-width:%dpx" role="img" aria-label="Your network: the master and '
                             'the machines linked to it">%s%s%s</svg>' % (w, h, w, ground, "".join(lines), "".join(boxes)))

# a service by the first part of its name: the module whose settings it has, and the page in the
# app where its admin changes what is inside it (users, rooms, repositories)
SITE_MODULE = {"cloud": "cloud", "vault": "vault", "immich": "immich", "photos": "immich", "paperless": "paperless", "git": "git",
               "jabber": "xmpp", "xmpp": "xmpp", "paste": "paste", "id": "accounts", "pay": "btcpay", "ffsync": "firefox-sync",
               "auth": "authenticator", "opencloud": "opencloud", "dl": "downloader", "jd": "downloader", "portainer": "portainer",
               "pihole": "dns", "wg": "vpn-server", "control": "control", "status": "control", "hub": "hub", "relay": "nostr-relay",
               "backup": "backup-server", "cache": "cache"}
ADMIN_PATH = {"cloud": "/settings/admin", "vault": "/admin", "immich": "/admin", "photos": "/admin", "paperless": "/admin/",
              "git": "/-/admin", "jabber": "/admin/", "xmpp": "/admin/", "pay": "/server/users", "auth": "/admin", "pihole": "/admin/",
              "portainer": "/", "wg": "/", "opencloud": "/admin-settings"}


def tiles(machines):
    out = []
    for m in machines:
        for site in m["sites"]:
            first = site.split(".", 1)[0]
            out.append({"site": site, "label": SERVICES.get(first, first.capitalize()), "machine": m["name"],
                        "module": SITE_MODULE.get(first, ""), "admin": ADMIN_PATH.get(first)})
    return out


def logins_of(info, name):
    """A machine's logins as `list --porcelain` has them: every row, and no password."""
    return next((h.get("logins") or [] for h in (info or {}).get("hosts") or [] if h.get("name") == name), [])


def with_logins(tiles, info):
    """The tiles, each with `login`: does its machine's Logins card have a row for that site?"""
    for t in tiles:
        t["login"] = any(urllib.parse.urlsplit(x.get("address") or "").hostname == t["site"]
                         for x in logins_of(info, t["machine"]))
    return tiles


def make_app(control: Control):
    env = jinja2.Environment(loader=jinja2.DictLoader(TEMPLATES), autoescape=True)
    # the network's own name, colour and words on the pages for everyone (the control module's settings)
    env.globals.update(brand_name=os.environ.get("KIWI_BRAND", ""), brand_accent=re.sub(r"[^#0-9a-fA-F]", "", os.environ.get("KIWI_ACCENT", ""))[:7],
                       about_text=os.environ.get("KIWI_ABOUT", ""), contact=os.environ.get("KIWI_CONTACT", ""))
    env.globals.update(version=VERSION, ago=ago, age=age, hint=hint, words=lambda s: WORDS.get(s, s),
                       kind=lambda r: KIND.get(r, r), verb=lambda a: VERB.get(a, a), jobword=lambda s: JOBWORD.get(s, s),
                       to_text=formmod.to_text, icon=icon, size=size, navitems=NAV, yesno=lambda v: "" if v is None else "yes" if v else "no")
    app = FastAPI(title="Kiwi control", version=VERSION, docs_url=None, redoc_url=None)

    def page(template, request=None, nav=None, **kw):
        # tok: the hidden field every form of a logged-in page carries (see posted())
        tok = ""
        if request is not None:
            tok = markupsafe.Markup('<input type="hidden" name="csrf" value="%s">'
                                    % control.csrf(request.cookies.get(COOKIE)))
        kw.setdefault("alone", alone())
        return HTMLResponse(env.get_template(template).render(tok=tok, nav=nav, shell=shell_state() if nav else None, **kw))

    def alone():
        """A network without a master (docs/design/no-master.md): the name of the machine that answers
        for the names at home (its dns part), or "" when the network has a master or no machines."""
        if control.locked() or not os.path.isfile(os.path.join(control.run, "fleet.json")):
            return ""                   # only what is known already: a page never waits for it
        hosts = (control.fleet_info() or {}).get("hosts") or []
        if not hosts or any(h.get("role") == "master" for h in hosts):
            return ""
        return next((h["name"] for h in hosts if "dns" in (h.get("modules") or [])), hosts[0]["name"])

    def shell_state():
        """What every page's frame shows: how the machines are, and how many there are."""
        info = control.fleet_info() or {}
        states = [h.get("state") or "unknown" for h in (control.status() or {}).get("hosts") or []]
        n = {k: states.count(k) for k in ("ok", "warn", "bad", "down")}
        wrong = n["bad"] + n["down"]
        return dict(n, total=sum(n.values()), level="bad" if wrong else "warn" if n["warn"] else "ok",
                    word="%d with a problem" % wrong if wrong else "%d to look at" % n["warn"] if n["warn"] else "all fine",
                    domain="." + info["domain"] if info.get("domain") else "",
                    counts={"/machines": len(info.get("hosts") or []), "/devices": len(control.devices())})

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
                        "phase": h.get("phase") or "", "provider": h.get("provider") or "", "address": h.get("address") or "",
                        "here": bool(h.get("here")),
                        "media": (control.media(h["name"]) or (None, 0))[1], "on_done": h.get("on_done") or "poweroff",
                        "known": bool(s), "state": (s or {}).get("state") or "unknown",
                        "problems": (s or {}).get("problems") or [], "notes": (s or {}).get("notes") or [],
                        "facts": (s or {}).get("facts") or {}, "output": (s or {}).get("output") or ""})
        ready = all(m["phase"] == "applied" for m in out if m["role"] == "master")
        for m in out:
            m["master_ready"] = ready
            m["bars"], m["tags"] = bars(m["facts"]), tags(m["modules"])
            m["chips"] = [SERVICES.get(x.split(".", 1)[0], x.split(".", 1)[0].capitalize()) for x in m["sites"]]
        for name, s in said.items():            # a look at a machine the fleet file no longer names
            if name not in [m["name"] for m in out]:
                out.append({"name": name, "hostname": name, "role": "", "modules": [], "sites": [], "vpn_ip": "",
                            "next": "", "phase": "", "provider": "", "address": "", "media": 0, "on_done": "", "master_ready": True, "here": False, "known": True, "bars": bars(s.get("facts") or {}), "tags": [], "chips": [], "state": s.get("state") or "unknown",
                            "problems": s.get("problems") or [], "notes": s.get("notes") or [],
                            "facts": s.get("facts") or {}, "output": s.get("output") or ""})
        return out, st, info

    @app.get("/status")
    def status_page():
        """How the network is, for everyone on it: no login, nothing but names people use and whether they work."""
        now = time.time()
        h = healthmod.public(control.health(), now)
        return page("status.html", h=h, now=now, stale=bool(h["checked"]) and now - h["checked"] > 45 * 60)

    @app.get("/start")
    def start_page():
        """The network's start page for everyone on it: what each service is for, where it is, whether it works."""
        now = time.time()
        h = healthmod.public(control.health(), now)
        # a start page: by name, not by state; the status page itself is the button at the top
        h["services"] = sorted((s for s in h["services"] if not s["site"].startswith("status.")), key=lambda s: (s["label"], s["site"]))
        return page("start.html", h=h, now=now)

    @app.get("/start/public")
    def start_public():
        """The front page for strangers, under the public name: the services open to the Internet, their
        addresses on the web and over Tor, whether they work, and how to get an account."""
        now = time.time()
        h = healthmod.public(control.health(), now, strangers=True)
        h["services"] = sorted((s for s in h["services"] if not s["site"].startswith("status.")), key=lambda s: (s["label"], s["site"]))
        h["join_public"] = bool(h.get("join")) and not h["join"].split("/")[2].endswith("." + str((control.fleet_info() or {}).get("domain") or "home"))
        return page("start.html", h=h, now=now)

    @app.get("/status/public")
    def status_public():
        """The same for the Internet (the control module's public_status_hostname): the services
        that are open to it, whether they work, and what was wrong with them. Nothing else."""
        now = time.time()
        h = healthmod.public(control.health(), now, strangers=True)
        return page("status.html", h=h, now=now, stale=bool(h["checked"]) and now - h["checked"] > 45 * 60)

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "version": VERSION, "locked": control.locked(), "damaged": bool(control.damaged())}

    def setup_page(values=None, errors=None, error=None, host="", back_error=None, back_code=""):
        """The questionnaire of `kiwi-server init`, as the terminal and the app ask it; above it,
        the way back for a network that exists (back_error: what went wrong with that)."""
        from . import setup as setupmod
        found = setupmod.detect()
        at = host.rsplit(":", 1)[0].strip("[]")
        if not found["subnet"] and any(at.startswith(p) for p in ("192.168.", "10.", "172.")) and at.count(".") == 3:
            # a container sees no home network: the address this page was opened at is in it
            found["subnet"], found["gateway"] = at.rsplit(".", 1)[0] + ".0/24", at.rsplit(".", 1)[0] + ".1"
        steps = setupmod.questions(found)
        for st in steps:            # found by a machine that may sit in a container: shown, to be checked
            st["notes"] = [n for n in st.get("notes") or [] if not n.startswith("Found on this computer")]
            for q in st["questions"]:
                if q["id"] in ("lan_subnet", "lan_gateway"):
                    q["advanced"] = False
                    q["help"] = "Check that it is your home's. " + q.get("help", "")
        values = dict(values or {})
        if found.get("this") and "nodes" not in values:         # the starter's machine: the first one at home
            values["nodes"] = [{"address": found["this"], "name": setupmod.free_name("node")}]
        fields = next(q["fields"] for st in steps for q in st["questions"] if q.get("type") == "machines")
        blank = {f["id"]: f.get("default", "") for f in fields}
        machines = [dict(blank, **m) for m in values.get("nodes") or []]
        for m in machines:
            m["this"] = bool(found.get("this")) and str(m.get("address") or "").split("/")[0] == found["this"]
        free = {"addresses": found.get("addresses") or [], "names": list(setupmod.NODES)}
        errors = errors or {}
        opened = [st["id"] for st in steps if any(q.get("advanced") and q["id"] in errors for q in st["questions"])]
        return page("setup.html", steps=steps, values=values, errors=errors, error=error, machines=machines,
                    blank=blank, free=free, open=opened, guess=not values and found["timezone"] in ("UTC", "Etc/UTC"),
                    back_error=back_error, back_code=back_code)

    def setup_answers(form):
        """The posted form as the answers `setup.make` takes; a question that does not apply is left out."""
        from . import setup as setupmod
        steps = setupmod.questions(setupmod.detect(probe=False))
        a, nodes = {}, {}
        for key, value in form.items():
            if key.startswith("nodes."):
                _n, i, field = key.split(".", 2)
                if i.isdigit():
                    nodes.setdefault(int(i), {})[field] = str(value).strip()
        for st in steps:
            for q in st["questions"]:
                if q.get("type") in ("machines", "lookup"):
                    continue
                a[q["id"]] = str(form.get(q["id"], q.get("default") or "")).strip()
        picked = str(form.get("provider_server") or "").strip()
        if picked and form.get("hoster"):       # picked from the hoster's list after signing in there
            a["provider_server"] = picked
        for st in steps:
            for q in st["questions"]:
                if q.get("when") and q["id"] in a and not setupmod._when(q, a):
                    del a[q["id"]]
        this = setupmod.detect(probe=False).get("this")
        if this:
            a["this_address"] = this        # from the machine, never from the form
        taken = set()
        a["nodes"] = []
        for i in sorted(nodes)[:8]:
            m = nodes[i]
            if not m.get("name"):
                m["name"] = setupmod.free_name("node", taken | {a.get("master_name")})
            taken.add(m["name"])
            if m.get("preset") != "gateway":
                m.pop("download_dir", None)
            a["nodes"].append(m)
        return a

    @app.post("/setup/hoster")
    async def setup_hoster(request: Request):
        """The first run's "Sign in and pick it": a device login at the hoster, guarded by the setup code."""
        if not control.fresh():
            return JSONResponse({"error": "this machine has a network already"}, status_code=403)
        if not request.headers.get("content-type", "").startswith("application/json"):
            return JSONResponse({"error": "JSON only"}, status_code=415)     # a form from another site cannot send it
        try:
            body = await request.json()
        except ValueError:
            body = {}
        try:
            handle = await run_in_threadpool(control.hoster_start, str(body.get("provider") or ""),
                                             str(body.get("code") or ""), address(request))
        except KiwiError as e:
            time.sleep(1)
            return JSONResponse({"error": str(e)}, status_code=403)
        return JSONResponse({"handle": handle}, headers={"Cache-Control": "no-store"})

    @app.get("/setup/hoster/{handle}")
    def setup_hoster_state(handle: str):
        s = control.hoster_state(handle) if control.fresh() else None
        if s is None:
            return JSONResponse({"error": "no such sign-in"}, status_code=404)
        return JSONResponse(s, headers={"Cache-Control": "no-store"})

    @app.post("/api/join-me")
    async def api_join_me(request: Request):
        """A starter, taken into a network that exists: its control node sends the code from this
        machine's screen and the key it will come in with (Control.join_as_starter)."""
        try:
            body = await request.json()
        except ValueError:
            body = {}
        try:
            out = await run_in_threadpool(control.join_as_starter, str(body.get("code") or ""), str(body.get("key") or ""),
                                          str(body.get("network") or ""), address(request))
        except KiwiError as e:
            return JSONResponse({"error": str(e)}, status_code=403)
        return out

    @app.get("/setup")
    def setup_form(request: Request):
        if not control.fresh():
            return login_page()
        if control.joined():
            return page("joined.html", network=control.joined())
        return setup_page(host=request.headers.get("host", ""))

    @app.post("/setup")
    async def setup_do(request: Request):
        if not control.fresh():
            return login_page()
        from . import setup as setupmod
        form = dict(await request.form())
        answers = await run_in_threadpool(setup_answers, form)
        shown = dict(answers, code=form.get("code", ""))
        errors = setupmod.check(answers)
        pw = str(form.get("password", "")).strip()
        if len(pw) < 12:
            errors["password"] = "The password needs 12 characters at least."
        elif pw != str(form.get("again", "")).strip():
            errors["password"] = "The two passwords are not the same."
        if not errors and not control.setup_code_ok(str(form.get("code", "")), address(request)):
            time.sleep(1)
            errors["code"] = "That is not the setup code. After five wrong tries, wait ten minutes."
        if errors:
            return await run_in_threadpool(setup_page, shown, errors, "Something is missing below.", request.headers.get("host", ""))
        try:
            recovery, token = await run_in_threadpool(control.first_run, str(form.get("code", "")), pw, answers, address(request),
                                                      str(form.get("hoster") or ""))
        except KiwiError as e:
            return await run_in_threadpool(setup_page, shown, {}, str(e), request.headers.get("host", ""))
        r = page("recovery.html", recovery=recovery, alone=answers.get("master_where") == "none")
        secure = request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https"
        r.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=secure, max_age=12 * 3600)
        return r

    async def upload(request, limit):
        """A multipart form whose body may be `limit` bytes: ({field: text}, {field: bytes}),
        or "large" or "broken" when it is not. The file stays in memory. What is over the limit
        is read and dropped, not kept: a browser shows the answer only once it has sent it all."""
        from starlette.formparsers import MultiPartException, MultiPartParser
        seen = 0

        async def body():
            nonlocal seen
            async for chunk in request.stream():
                seen += len(chunk)
                if seen <= limit:
                    yield chunk
            if seen > limit:
                raise MultiPartException("too large")     # (the parser closes what it opened)
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            declared = 0
        multipart = request.headers.get("content-type", "").startswith("multipart/form-data")
        if declared > limit or not multipart:
            async for _chunk in request.stream():
                pass
            return "large" if declared > limit else "broken"
        parser = MultiPartParser(request.headers, body(), max_files=1, max_fields=8)
        parser.spool_max_size = limit
        try:
            form = await parser.parse()
        except MultiPartException:
            return "large" if seen > limit else "broken"
        try:
            fields, files = {}, {}
            for key, value in form.multi_items():
                if isinstance(value, str):
                    fields[key] = value
                else:
                    files[key] = await value.read()
            return fields, files
        finally:
            await form.close()

    @app.post("/setup/restore")
    async def setup_restore(request: Request):
        """A network back from its copy, on a machine with nothing on it (Control.restore_run)."""
        if not control.fresh():
            return login_page()
        host = request.headers.get("host", "")

        async def again(why, code="", status=200):
            r = await run_in_threadpool(setup_page, None, None, None, host, why, code)
            r.status_code = status
            return r
        large = "That file is too large to be a copy of a network (%d MB at most)." % (COPY_MAX >> 20)
        got = await upload(request, COPY_MAX + 65536)      # (the other fields and the form's own lines)
        if got == "large":
            return await again(large, status=413)
        if got == "broken":
            return await again("The form did not arrive whole. Choose the copy and send it again.")
        fields, files = got
        code, pw = fields.get("code", "").strip(), fields.get("password", "").strip()
        if not files.get("copy"):
            return await again("Choose the copy: a file named fleet-….tar.", code)
        if len(files["copy"]) > COPY_MAX:
            return await again(large, code, 413)
        if len(pw) < 12:
            return await again("The password needs 12 characters at least.", code)
        if pw != fields.get("again", "").strip():
            return await again("The two passwords are not the same.", code)
        if not control.setup_code_ok(code, address(request)):
            await asyncio.sleep(1)
            return await again("That is not the setup code. After five wrong tries, wait ten minutes.", code)
        try:
            token = await run_in_threadpool(control.restore_run, code, pw, files["copy"], fields.get("recovery", ""),
                                            address(request))
        except KiwiError as e:
            await asyncio.sleep(1)
            said = str(e)
            return await again(said[:1].upper() + said[1:] + ("" if said.endswith(".") else "."), code)
        if not token:
            return login_page()
        control.look()
        r = RedirectResponse("/", status_code=303)
        secure = request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https"
        r.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=secure, max_age=12 * 3600)
        return r

    @app.get("/login")
    def login_form():
        if control.fresh():
            return RedirectResponse("/setup", status_code=303)
        return page("login.html", locked=control.locked(), auto=control.auto_state(), damaged=control.damaged())

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

    @app.get("/recover")
    def recover_form():
        if control.fresh():
            return RedirectResponse("/setup", status_code=303)
        return page("recover.html")

    @app.post("/recover")
    def recover(request: Request, recovery: str = Form(""), password: str = Form(""), again: str = Form("")):
        if control.fresh():
            return RedirectResponse("/setup", status_code=303)
        if len(password.strip()) < 12:
            return page("recover.html", error="The password needs 12 characters at least.")
        if password.strip() != again.strip():
            return page("recover.html", error="The two passwords are not the same.")
        try:
            token = control.reset_password(recovery, password, address(request))
        except KiwiError as e:
            return page("recover.html", error=str(e))
        if not token:
            time.sleep(1)
            return page("recover.html", error="That is not this network's recovery key. After five wrong tries, wait ten minutes.")
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
        if control.fresh():
            return RedirectResponse("/setup", status_code=303)
        if not user(request):
            return login_page()
        ms, st, _info = machines()
        from . import setup as setupmod
        devs = control.devices()
        up = {m["name"] for m in ms if m["state"] in ("ok", "warn", "bad")}
        silent = [s for s in control.health().get("services") or [] if not s["ok"] and s["host"] in up]
        return page("index.html", request, "/", machines=ms, tiles=with_logins(tiles(ms), _info), looked=st.get("time"),
                    stats=stats(ms, devs), wide=picture(ms, len(devs), 4), narrow=picture(ms, len(devs), 1), silent=silent, now=time.time(),
                    error=st.get("error"), looking=control.looking, presets=setupmod.PRESETS, gone=control.gone(),
                    add_error=request.query_params.get("add_error"))

    @app.get("/machines")
    def machines_page(request: Request):
        if not user(request):
            return login_page()
        ms, st, _info = machines()
        from . import setup as setupmod
        return page("machines.html", request, "/machines", machines=ms, looked=st.get("time"), looking=control.looking,
                    presets=setupmod.PRESETS, add_error=request.query_params.get("add_error"),
                    take_error=request.query_params.get("take_error"))

    @app.get("/services")
    def services_page(request: Request):
        """Every service, under its machine: to open, with its login and whether it answered."""
        if not user(request):
            return login_page()
        ms, _st, info = machines()
        domain = "." + str((control.fleet_info() or {}).get("domain") or "home")
        state = control.health()
        rows = {s["site"]: s for s in state.get("services") or []}
        asked = {k: s["ok"] for k, s in rows.items()}
        now = time.time()
        try:
            hidden = control.onion()
        except KiwiError:               # the services are listed all the same
            hidden = {}
        by = {}
        for t in with_logins(tiles([m for m in ms if m["phase"] not in ("enroll", "build", "install", "invalid", "bare")]), info):
            t["public"] = not t["site"].endswith(domain)
            t["asked"], t["down"] = t["site"] in asked, asked.get(t["site"]) is False
            t["onion"] = hidden.get("%s.%s" % (t["site"].split(".", 1)[0], t["machine"]))
            t["bars"], t["uptime"] = healthmod.history(state, t["site"], now)
            t["ms"] = next((b["ms"] for b in reversed(t["bars"]) if b["ms"] is not None), None)
            t["since"] = (rows.get(t["site"]) or {}).get("since")
            by.setdefault(t["machine"], []).append(t)
        everything = [t for _m, ts in by.items() for t in ts]
        return page("services.html", request, "/services", by_machine=list(by.items()), everything=everything, now=now,
                    up=sum(1 for t in everything if t["asked"] and not t["down"]), down=sum(1 for t in everything if t["down"]),
                    public=sum(1 for t in everything if t["public"]), tor=sum(1 for t in everything if t["onion"]))

    @app.post("/machines/take-in")
    def machines_take_in(request: Request, address: str = Form(""), code: str = Form(""), preset: str = Form("cloud"),
                         name: str = Form(""), csrf: str = Form("")):
        u = posted(request, csrf)
        if not u:
            return login_page()
        try:
            jid = control.take_in(address, code, preset, name, user=u)
        except KiwiError as e:
            return RedirectResponse("/machines?" + urllib.parse.urlencode({"take_error": str(e)}), status_code=303)
        return RedirectResponse("/jobs/%s" % jid, status_code=303)

    @app.post("/machines/add")
    def machines_add(request: Request, preset: str = Form("cloud"), address: str = Form(""), name: str = Form(""),
                     target: str = Form("ucore"), csrf: str = Form("")):
        u = posted(request, csrf)
        if not u:
            return login_page()
        try:
            made = control.add_machine(preset, address, name, target, user=u)
        except KiwiError as e:
            return RedirectResponse("/machines?" + urllib.parse.urlencode({"add_error": str(e)}), status_code=303)
        return RedirectResponse("/hosts/" + made, status_code=303)

    @app.get("/hosts/{name}/media")
    def media(request: Request, name: str):
        if not user(request):
            return login_page()
        m = control.media(name)
        if not m:
            return RedirectResponse("/hosts/" + name, status_code=303)
        control.audit(user(request), "install media fetched", hosts=[name], address=address(request))
        return FileResponse(m[0], media_type="application/x-iso9660-image", filename=name + ".iso")

    @app.post("/hosts/{name}/media/{what}")
    def media_do(request: Request, name: str, what: str, csrf: str = Form("")):
        u = posted(request, csrf)
        if not u:
            return login_page()
        if what == "drop":
            control.drop_media(name, user=u)
            return RedirectResponse("/hosts/" + name, status_code=303)
        try:
            jid = control.start("build", [name], user=u)
        except KiwiError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return RedirectResponse("/jobs/" + jid, status_code=303)

    @app.get("/hosts/{name}")
    def host(request: Request, name: str):
        if not user(request):
            return login_page()
        ms, _st, _info = machines()
        m = next((x for x in ms if x["name"] == name), None)
        if not m:
            return RedirectResponse("/", status_code=303)
        return page("host.html", request, "/", m=m, tiles=tiles([m]), logins=logins_of(_info, name))

    @app.get("/api/hosts/{name}/logins")
    def api_logins(request: Request, name: str):
        """A machine's logins with their passwords, for "Show password" on its page: the one
        place a page hands a password out. The audit says that it did, with the machine's
        name and nothing of what was shown."""
        u = user(request)
        if not u:
            return JSONResponse({"error": "log in"}, status_code=401)
        try:
            found = control.logins(name)
        except KiwiError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        control.audit(u, "logins shown", hosts=[name])
        return JSONResponse({"host": name, "logins": found}, headers={"Cache-Control": "no-store"})

    @app.get("/hosts/{name}/script")
    def host_script(request: Request, name: str):
        """The role script, as plain text: like "Show password", in the audit by name only."""
        u = user(request)
        if not u:
            return login_page()
        try:
            text = control.role_script(name)
        except KiwiError as e:
            return Response("The role script could not be made: %s\n" % e, media_type="text/plain; charset=utf-8",
                            status_code=400, headers={"Cache-Control": "no-store"})
        control.audit(u, "role script shown", hosts=[name])
        return Response(text, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"})

    # ---- settings: a machine's, and the defaults every machine inherits (form.py) -----------
    def settings_page(request, host, typed=None, wrong=None, error=None, touched=()):
        rows = {h["name"]: h for h in (control.fleet_info() or {}).get("hosts") or []}
        if host is not None and host not in rows:
            return RedirectResponse("/", status_code=303)
        try:
            groups = control.settings(host)["groups"]
        except KiwiError as e:
            groups, error = [], error or str(e)
        # Apply reaches a machine that is set up; one that is not gets its settings with its first step
        running = [n for n, h in rows.items() if (h.get("phase") or "") not in FRESH and host in (None, n)]
        return page("settings.html", request, "/" if host else "/settings", host=host, groups=groups, typed=typed or {}, wrong=wrong or {},
                    error=error, touched=list(touched), running=running, saved=request.query_params.get("saved"),
                    open=[g["id"] for g in groups if any(f["name"] in touched for f in g["fields"])],
                    same="as inherited" if host else "the usual")

    def settings_save(request, host, sent, u):
        """The posted form against what the form showed: only what was changed is written."""
        try:
            groups = control.settings(host)["groups"]
        except KiwiError as e:
            return settings_page(request, host, error=str(e))
        typed, secrets = {}, {}
        for g in groups:
            for f in g["fields"]:
                name = f["name"]
                if f["type"] == "modules":
                    if sent.get("shown:" + name):
                        typed[name] = [str(x) for x in sent.getlist("f:" + name)]
                elif f["secret"]:               # never shown again, not even after a mistake elsewhere
                    if sent.get("clear:" + name):
                        secrets[name] = None
                    elif str(sent.get("f:" + name) or "").strip():
                        secrets[name] = str(sent.get("f:" + name))
                elif "f:" + name in sent:
                    typed[name] = str(sent.get("f:" + name)).replace("\r\n", "\n")
        changes, wrong = formmod.changes(groups, dict(typed, **secrets))
        touched = set(wrong) | {".".join(path) for path in changes}
        if wrong:
            return settings_page(request, host, typed, wrong, "Not saved: %s" % next(iter(wrong.values())), touched)
        try:
            keys = control.save_settings(host, changes, u)
        except KiwiError as e:
            return settings_page(request, host, typed, None, "Not saved: %s" % e, touched)
        where = "/hosts/%s/settings" % host if host else "/settings"
        return RedirectResponse("%s?saved=%d" % (where, 1 if keys else 0), status_code=303)

    async def settings_post(request, host):
        sent = await request.form()
        u = posted(request, str(sent.get("csrf") or ""))
        if not u:
            return login_page()
        return await run_in_threadpool(settings_save, request, host, sent, u)

    @app.get("/settings")
    def settings(request: Request):
        if not user(request):
            return login_page()
        return settings_page(request, None)

    @app.post("/settings")
    async def settings_do(request: Request):
        return await settings_post(request, None)

    @app.get("/hosts/{name}/settings")
    def host_settings(request: Request, name: str):
        if not user(request):
            return login_page()
        return settings_page(request, name)

    @app.post("/hosts/{name}/settings")
    async def host_settings_do(request: Request, name: str):
        return await settings_post(request, name)

    @app.post("/refresh")
    def refresh(request: Request, csrf: str = Form(""), back: str = Form("")):
        if not posted(request, csrf):
            return login_page()
        control.look()                          # in the background; Home says it is checking
        return RedirectResponse("/machines" if back == "/machines" else "/", status_code=303)

    @app.post("/run")
    def run(request: Request, action: str = Form(...), host: str = Form(""), csrf: str = Form(""), at: str = Form(""),
            module: str = Form("")):
        u = posted(request, csrf)
        if not u:
            return login_page()
        if action not in ACTIONS:
            return JSONResponse({"error": "unknown action"}, status_code=400)
        try:
            jid = control.start(action, [host] if host else [], user=u, at=at if action in ("setup", "restore") else "",
                                module=module if action == "restore" else "")
        except KiwiError as e:
            if action == "backup":              # said where the button is
                ms, _st, _info = machines()
                return page("backups.html", request, "/backups", machines=ms, error="Nothing was saved: %s" % e)
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
        title = "%s %s" % (VERB.get(j["action"], j["action"]), what if j["action"] in ("status", "plan", "apply", "save", "setup", "build", "restore", "unlock", "remove", "start-new", "prune") else "")
        say = {"waiting": "Waiting for another task to finish…", "running": "Working… this can take a few minutes.",
               "ok": "Done.", "failed": "That did not work. The lines below say why."}
        if j["action"] == "plan":
            say["ok"] = "Done. Nothing was changed; the lines below say what Apply would do."
        if j["action"] == "build":
            say["ok"] = "Done. Go back: the machine's page has the install media and says what to do with them."
        if j["action"] == "setup":
            say["ok"] = "Done. Go back for the next step."
        if j["action"] in ("rotate", "rotate-seed"):      # a machine that is off is no failure: it waits
            say["failed"] = ("Not every machine could be changed. The ones that wait are listed under Computers; "
                             "the lines below say why.")
        back = ("/" if j["action"] == "remove" else "/backups" if j["action"] in ("save", "backup", "restore", "start-new", "prune") else "/controllers" if j["action"] in ("rotate", "rotate-seed")
                else "/help" if j["action"] == "doctor"
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

    def accounts_page(request, **kw):
        available, people, invites, requests, id_host, error = control.has_accounts(), [], [], [], "", kw.pop("error", None)
        if available:
            try:
                for p in control.accounts("list") or []:
                    name = (p.get("name") or [""])[0]
                    people.append({"name": name, "display": (p.get("displayname") or [name])[0],
                                   "jabber": any(g.startswith("jabber@") for g in p.get("memberof") or [])})
                invites = control.accounts("invites") or []
                requests = control.accounts("requests") or []
            except KiwiError as e:
                error = error or str(e)
        info = control.fleet_info() or {}
        holder = next((h for h in info.get("hosts") or [] if "accounts" in (h.get("modules") or [])), None) or {}
        id_host = next((s for s, label in (holder.get("site_labels") or {}).items() if label == "Accounts"),
                       "id.%s" % holder.get("hostname", ""))
        now = time.time()
        jabber = None
        if control.has_jabber_join():
            try:
                jr = control.accounts("requests", "--jabber") or []
                jabber = {"waiting": [r for r in jr if r.get("state") == "waiting"], "invites": control.accounts("invites", "--jabber") or []}
            except KiwiError as e:
                error = error or str(e)
        connectable = [{"host": h["name"], "module": m, "label": label,
                        "connected": os.path.isfile(os.path.join(control.fleet_dir, "secrets", "accounts", "%s-%s.json" % (h["name"], m)))}
                       for h in info.get("hosts") or [] for m, label in (("git", "Forgejo (git)"), ("cloud", "Nextcloud (files)"), ("paperless", "Paperless (documents)"), ("vault", "Vaultwarden (passwords)"), ("immich", "Immich (photos)"))
                       if m in (h.get("modules") or [])]
        return page("accounts.html", request, "/accounts", available=available, connectable=connectable, jabber=jabber, people=sorted(people, key=lambda p: p["name"]),
                    invites=invites, live=[i for i in invites if i.get("live")], waiting=[r for r in requests if r.get("state") == "waiting"],
                    id_host=id_host, now=now, error=error, **kw)

    @app.get("/accounts")
    def accounts_get(request: Request):
        if not user(request):
            return login_page()
        return accounts_page(request)

    @app.post("/accounts/{what}")
    def accounts_do(request: Request, what: str, hint: str = Form(""), name: str = Form(""), note: str = Form(""),
                    uses: int = Form(1), days: int = Form(14), csrf: str = Form("")):
        u = posted(request, csrf)
        if not u:
            return login_page()
        try:
            if what.startswith("jabber-"):
                op = what[7:]
                if op == "invite":
                    code = control.accounts("invite", "--jabber", "--uses", max(1, min(50, uses)), "--days", max(1, min(90, days)),
                                            "--note", note.strip()[:80], user=u)["code"]
                    return accounts_page(request, shown={"title": "A Jabber invite code", "value": code,
                                                         "text": "Give it to %s. It makes %d Jabber address%s, for %d days:" % (
                                                             note.strip() or "the person", uses, "" if uses == 1 else "es", days),
                                                         "note": "Shown this once. They enter it at /join on Jabber's page, with a name and a password."})
                if op in ("accept", "deny", "revoke"):
                    control.accounts(op, hint.strip(), "--jabber", user=u)
                    return accounts_page(request)
            if what == "invite":
                code = control.accounts("invite", "--uses", max(1, min(50, uses)), "--days", max(1, min(90, days)),
                                        "--note", note.strip()[:80], user=u)["code"]
                return accounts_page(request, shown={"title": "An invite code", "value": code,
                                                     "text": "Give it to %s. It makes %d account%s, for %d days:" % (
                                                         note.strip() or "the person", uses, "" if uses == 1 else "s", days),
                                                     "note": "It is shown this once. They enter it at the sign-up page with a name of their choice."})
            if what in ("accept", "deny", "revoke"):
                control.accounts(what, hint.strip(), user=u)
                return accounts_page(request)
            if what in ("create", "link"):
                answer = control.accounts(what, name.strip().lower(), user=u)
                return accounts_page(request, shown={"title": "The account %s" % answer["name"] if what == "create" else "A new password link for %s" % answer["name"],
                                                     "value": answer["link"], "text": "Hand this link to its person: it sets the password or a passkey.",
                                                     "note": "It works for a day, once. Nobody else should see it."})
            if what == "delete":
                control.accounts("delete", name.strip().lower(), user=u)
                return accounts_page(request)
            if what == "connect":
                labels = {"git": "Forgejo", "cloud": "Nextcloud", "paperless": "Paperless", "vault": "Vaultwarden", "immich": "Immich"}
                module = note.strip() if note.strip() in labels else "git"
                label = labels[module]
                control.accounts("connect", name.strip(), module, user=u)
                return accounts_page(request, shown={"title": "%s on %s is connected" % (label, name.strip()), "value": "kiwi-server apply %s" % name.strip(),
                                                     "text": "Apply that machine now (Machines, %s, Apply): then its %s signs people in with these accounts." % (name.strip(), label),
                                                     "note": "Nothing changes for people who already have a Forgejo user of the same name: they are linked on their first sign-in."})
        except KiwiError as e:
            return accounts_page(request, error=str(e))
        return accounts_page(request, error="not a thing to do here")

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

    @app.get("/backups/network.tar")
    def network_copy(request: Request):
        """A copy of the network as it is now, as a download: made in memory, nothing kept here."""
        u = user(request)
        if not u:
            return login_page()
        try:
            name, data = control.network_copy(u)
        except KiwiError as e:
            ms, _st, _info = machines()
            return page("backups.html", request, "/backups", machines=ms, error="No copy was made: %s" % e)
        name = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
        return Response(data, media_type="application/x-tar", headers={
            "Content-Disposition": 'attachment; filename="%s"' % name, "Cache-Control": "no-store"})

    @app.get("/backups")
    def backups(request: Request):
        if not user(request):
            return login_page()
        ms, _st, _info = machines()
        return page("backups.html", request, "/backups", machines=ms)

    @app.get("/backups/{name}")
    def snapshots(request: Request, name: str):
        if not user(request):
            return login_page()
        try:
            return page("snapshots.html", request, "/backups", host=name, snaps=control.snapshots(name))
        except (KiwiError, OSError, subprocess.TimeoutExpired) as e:
            said = "Its backup machines took too long to answer." if isinstance(e, subprocess.TimeoutExpired) else str(e)
            return page("snapshots.html", request, "/backups", host=name, snaps=None, error=said)

    def federation_page(request, **kw):
        try:
            partners = control.federation()
        except KiwiError as e:
            partners, kw = {}, dict(kw, error=kw.get("error") or str(e))
        return page("federation.html", request, "/federation", partners=partners, **kw)

    def access_page(request, **kw):
        try:
            a = control.access()
        except KiwiError as e:
            return page("access.html", request, "/access", who=[], machines=[], loose=[], error=str(e))
        for w in a["who"]:          # what the ticks do not show: single ports, addresses, names of no machine
            w["extra"] = ["%s:%s" % (n, p) for n, o in w["open"]["machines"].items() for p in o["ports"]] + w["open"]["other"]
        grouped = {d for w in a["who"] for d in w["devices"]}
        loose = sorted(d for d in control.devices() if d not in grouped)
        try:
            hidden = control.onion()
        except KiwiError:
            hidden = {}
        return page("access.html", request, "/access", who=a["who"], machines=a["machines"], loose=loose, hidden=hidden, **kw)

    @app.post("/onion")
    async def onion_do(request: Request):
        form = dict(await request.form())
        u = posted(request, str(form.get("csrf", "")))
        if not u:
            return login_page()
        try:
            if form.get("show"):                        # one person's key, shown once on the page
                what, _, client = str(form["show"]).partition(":")
                found = control.onion().get(what)
                if not found:
                    raise KiwiError("%s is no hidden service" % what)
                return access_page(request, key={"client": client, "address": found["address"],
                                                 "line": control.onion_key(what, client, user=u)})
            wanted = {}
            for m in control.access()["machines"]:
                for s in m["services"]:
                    what = "%s.%s" % (s, m["name"])
                    wanted[what] = str(form.get("who.%s.%s" % (m["name"], s), "")).lower().split() \
                        if "on.%s.%s" % (m["name"], s) in form else None
            done = control.save_onion(wanted, user=u)
        except KiwiError as e:
            return access_page(request, error=str(e))
        return RedirectResponse("/access?tor=" + urllib.parse.quote(", ".join(done)) + "#tor" if done else "/access#tor", status_code=303)

    @app.get("/access")
    def access(request: Request):
        if not user(request):
            return login_page()
        return access_page(request, saved=request.query_params.get("saved", "")[:200],
                           tor_saved=request.query_params.get("tor", "")[:200])

    @app.post("/access")
    async def access_do(request: Request):
        form = dict(await request.form())
        u = posted(request, str(form.get("csrf", "")))
        if not u:
            return login_page()
        try:
            a = control.access()
            changes = {}
            for w in a["who"]:
                n = w["name"]
                if "all." + n in form and w["kind"] == "group":
                    reach = ["all"]
                else:
                    reach = ["apps"] if "apps." + n in form and w["kind"] == "group" else []
                    for m in a["machines"]:
                        if m["master"]:
                            continue
                        if "m.%s.%s" % (n, m["name"]) in form:
                            reach.append(m["name"])
                        else:
                            reach += ["%s.%s" % (s, m["name"]) for s in m["services"] if "s.%s.%s.%s" % (n, m["name"], s) in form]
                    reach += [e for e in str(form.get("p." + n, "")).split() if e not in reach]
                changes[n] = dict({"reach": reach}, **{k: "%s.%s" % (k, n) in form for k in ("peers", "internet", "admin")})
            done = control.save_access(changes, new=str(form.get("new", "")).strip().lower(),
                                       new_as=str(form.get("new_as", "nothing")), user=u)
        except KiwiError as e:
            return access_page(request, error=str(e))
        return RedirectResponse("/access?saved=" + urllib.parse.quote(", ".join(done)) if done else "/access", status_code=303)

    @app.get("/federation")
    def federation(request: Request):
        if not user(request):
            return login_page()
        return federation_page(request, saved=request.query_params.get("saved") == "1")

    @app.post("/federation")
    async def federation_do(request: Request):
        form = dict(await request.form())
        u = posted(request, str(form.get("csrf", "")))
        if not u:
            return login_page()
        try:
            names = list(control.federation())
            had = control.federation()
            changes = {n: {"share": str(form["share." + n]).split() if "share." + n in form else had[n]["share"],
                           "from": str(form.get("from." + n, "")).split(),
                           "exit": "exit." + n in form, "internet": "internet." + n in form} for n in names}
            await run_in_threadpool(control.save_federation, changes, u)
        except KiwiError as e:
            return federation_page(request, error=str(e))
        return RedirectResponse("/federation?saved=1", status_code=303)

    @app.get("/help")
    def help_page(request: Request):
        if not user(request):
            return login_page()
        return page("help.html", request, "/help", auto=control.auto_state())

    @app.get("/recovery")
    def recovery(request: Request):
        """If something breaks: could the network be brought back, and what to do (recovery.py)."""
        if not user(request):
            return login_page()
        try:
            rep, error = control.recovery(), None
        except (KiwiError, OSError) as e:
            rep, error = None, "The report could not be made: %s" % e
        return page("breaks.html", request, "/recovery", rep=rep, error=error)

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
        return page("controllers.html", request, "/controllers", ways=control.controllers(), rot=control.rotation(),
                    ca=control.sshca(), **kw)

    @app.get("/controllers")
    def controllers(request: Request):
        if not user(request):
            return login_page()
        return controllers_page(request)

    @app.post("/controllers/{what}")
    def controllers_do(request: Request, what: str, name: str = Form(""), csrf: str = Form(""),
                       old: str = Form(""), new: str = Form(""), again: str = Form(""), key: str = Form(""),
                       days: int = Form(30)):
        u = posted(request, csrf)
        if not u:
            return login_page()
        try:
            if what == "invite":
                link = control.new_invite(base_url(request), u)
                return controllers_page(request, invite=link, invite_qr=markupsafe.Markup(control.qr_svg(link)))
            if what == "remove":
                control.remove(name, u)
            if what == "sshca-init":
                control.sshca_init(u)
                return controllers_page(request, ca_made=True)
            if what == "sshca-sign":
                cert = control.sshca_sign(key, days, u)
                return controllers_page(request, cert=cert, cert_days=max(1, min(days, 365)))
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

    @app.post("/api/invite")
    def api_invite(request: Request):
        """A computer that manages the network already makes the link for one more: the same
        invite as the page's button, asked for with that computer's key instead of a login."""
        who = bearer(request)
        if not who:
            return JSONResponse({"error": "who?"}, status_code=401)
        try:
            return {"link": control.new_invite(base_url(request), who)}
        except KiwiError as e:
            return JSONResponse({"error": str(e)}, status_code=409)

    @app.get("/api/activity")
    def api_activity(request: Request):
        """The Activity page for a computer that manages the network (`control activity`, the app's
        Activity…): the newest tasks and the log of who did what. Names and times; no output, no secret."""
        if not bearer(request):
            return JSONResponse({"error": "who?"}, status_code=401)
        keep = ("id", "action", "hosts", "user", "state", "started", "ended", "rc")
        return {"jobs": [{k: j.get(k) for k in keep} for j in control.jobs(15)], "entries": control.audit_tail(40)}

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
