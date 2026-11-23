"""`kiwi-server serve`: the control node's pages and API (design/control-plane.md). FastAPI
with Jinja templates and a few lines of JavaScript for a job's live log: no build toolchain.
Meant behind the reverse proxy on the mesh (the control module); the login is the password
set with `kiwi-server serve FLEET --set-password`; it opens the encrypted store (control.py).
"""
import asyncio
import base64
import hashlib
import hmac
import json
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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from . import VERSION, form as formmod, health as healthmod, machtable, play as playmod, providers as providersmod, site as sitemod, timeline, util
from .control import ACTIONS, Control

controlmod_job = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")      # a job's id, as Control.start makes it
from .util import KiwiError

COOKIE = "kiwi_session"
COPY_MAX = 64 * 1024 * 1024         # a copy of a network is a few hundred KB; an upload larger than this is refused

# the kiwi icon (data/icons), inline: the pages answer under many names, a path of its own might not pass
FAVICON = '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxMjggMTI4Ij48Y2lyY2xlIGN4PSI2NCIgY3k9IjY0IiByPSI1OCIgZmlsbD0iIzhCNjEwMCIvPjxjaXJjbGUgY3g9IjY0IiBjeT0iNjQiIHI9IjUxIiBmaWxsPSIjRTdCNjQzIi8+PGcgZmlsbD0iIzQ3MkQwMCI+PGVsbGlwc2UgY3g9IjY0IiBjeT0iMzMiIHJ4PSI0IiByeT0iNS42Ii8+PGVsbGlwc2UgY3g9IjkwLjgiIGN5PSI0OC41IiByeD0iNCIgcnk9IjUuNiIgdHJhbnNmb3JtPSJyb3RhdGUoNjAgOTAuOCA0OC41KSIvPjxlbGxpcHNlIGN4PSI5MC44IiBjeT0iNzkuNSIgcng9IjQiIHJ5PSI1LjYiIHRyYW5zZm9ybT0icm90YXRlKDEyMCA5MC44IDc5LjUpIi8+PGVsbGlwc2UgY3g9IjY0IiBjeT0iOTUiIHJ4PSI0IiByeT0iNS42Ii8+PGVsbGlwc2UgY3g9IjM3LjIiIGN5PSI3OS41IiByeD0iNCIgcnk9IjUuNiIgdHJhbnNmb3JtPSJyb3RhdGUoNjAgMzcuMiA3OS41KSIvPjxlbGxpcHNlIGN4PSIzNy4yIiBjeT0iNDguNSIgcng9IjQiIHJ5PSI1LjYiIHRyYW5zZm9ybT0icm90YXRlKDEyMCAzNy4yIDQ4LjUpIi8+PC9nPjxjaXJjbGUgY3g9IjY0IiBjeT0iNjQiIHI9IjIxIiBmaWxsPSIjRkVGNERGIi8+PGcgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoNjQgNjQpIHNjYWxlKDAuOTUpIHRyYW5zbGF0ZSgtNjQgLTY0KSI+PHBhdGggZD0iTTU2IDU0LjVINzJhMiAyIDAgMCAxIDIgMnYzYTIgMiAwIDAgMS0yIDJINTZhMiAyIDAgMCAxLTItMnYtM2EyIDIgMCAwIDEgMi0yWk01NiA2Ni41SDcyYTIgMiAwIDAgMSAyIDJ2M2EyIDIgMCAwIDEtMiAySDU2YTIgMiAwIDAgMS0yLTJ2LTNhMiAyIDAgMCAxIDItMloiIGZpbGw9Im5vbmUiIHN0cm9rZT0iIzQ3MkQwMCIgc3Ryb2tlLXdpZHRoPSIzLjYiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCIvPjwvZz48L3N2Zz4=">'

# Pushed changes (docs/design/ui.md, step 5): a page that shows the network's state follows
# /api/v1/events and takes its new content when the mark there moves, without a reload. Someone who
# has typed, ticked, opened or clicked something on it keeps the page as it is and gets a line
# that says so instead. A hidden tab lets go of its stream (a browser keeps only six per address).
LIVE = """<script>(function () {
  var meta = document.querySelector('meta[name="kiwi-mark"]'), es = null, touched = false;
  if (!meta || !window.EventSource || !window.fetch || !window.DOMParser) return;
  var mark = meta.content;
  function main() { return document.querySelector("main"); }
  /* what a person does (not "toggle": an open <details> sends one by itself as the page loads) */
  ["input", "change", "click", "keydown"].forEach(function (t) {
    document.addEventListener(t, function (e) {
      var m = main(); if (m && m.contains(e.target) && !(t === "click" && e.target.closest("a"))) touched = true;
    }, true);
  });
  function say() {
    if (document.getElementById("kiwi-changed")) return;
    var b = document.createElement("div"); b.id = "kiwi-changed"; b.className = "banner";
    b.innerHTML = 'Something changed meanwhile. <a href="">Show it</a>';
    main().insertBefore(b, main().firstChild);
  }
  var inflight = false, again = false, last = 0;
  function take() {                                  /* at most one fetch every four seconds */
    if (touched) return say();
    var wait = 4000 - (Date.now() - last);
    if (inflight || wait > 0) {
      if (!again) { again = true; setTimeout(function () { again = false; take(); }, Math.max(wait, 500)); }
      return;
    }
    inflight = true; last = Date.now();
    var u = new URL(location.href); u.searchParams.set("auto", "1");
    fetch(u, {cache: "no-store", credentials: "same-origin", redirect: "manual"}).then(function (r) {
      if (r.status !== 200) throw 0; return r.text();
    }).then(function (t) {
      var d = new DOMParser().parseFromString(t, "text/html"), nm = d.querySelector('meta[name="kiwi-mark"]');
      if (!nm) { stop(); return; }                  /* the login ended: the page stays as it is */
      if (touched) return say();
      [["main"], ["aside.side"], ["header.top"]].forEach(function (p) {
        var n = d.querySelector(p[0]), o = document.querySelector(p[0]); if (n && o) o.innerHTML = n.innerHTML;
      });
      main().querySelectorAll("script").forEach(function (old) {
        var x = document.createElement("script"); x.textContent = old.textContent; old.replaceWith(x);
      });
      mark = nm.content;
    }).catch(function () {}).then(function () { inflight = false; });
  }
  function stop() { if (es) { es.close(); es = null; } }
  function start() {
    if (es) return;
    es = new EventSource("/api/v1/events");
    es.addEventListener("change", function (e) { var m = JSON.parse(e.data).mark; if (m !== mark) { mark = m; take(); } });
    es.addEventListener("gone", stop);
  }
  document.addEventListener("visibilitychange", function () { document.hidden ? stop() : start(); });
  if (!document.hidden) start();
})();</script>"""

BASE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{% if live %}<meta name="kiwi-mark" content="{{ mark }}">{% endif %}
<title>{% block title %}Kiwi{% endblock %}</title>
""" + FAVICON + """
<script>(function(){var t=document.cookie.match(/(?:^|; )kiwi_theme=(light|dark)/);if(t)document.documentElement.dataset.theme=t[1]})()</script>
<style>
:root{--bg:#f5f6f7;--fg:#16191b;--card:#fff;--line:#e4e7ea;--line2:#c9ced3;--muted:#667078;--accent:#13805c;--onaccent:#fff;--fed:#7a4fd0;
--tint:#e7f4ee;--hover:#f0f2f4;--off:#9aa2a8;
--ok:#13805c;--warn:#b26b00;--bad:#c4332b;--okbg:#e5f5ed;--warnbg:#fcf1de;--badbg:#fdeceb}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0c0e0f;--fg:#e7e9ea;--card:#141719;--line:#23282b;--line2:#353b3f;--muted:#8a9297;--accent:#38c793;--onaccent:#06150e;--fed:#a98bf0;
--tint:#16251f;--hover:#1a1e21;--off:#5e666b;
--ok:#38c793;--warn:#f0a945;--bad:#f2665c;--okbg:#10241c;--warnbg:#2a2114;--badbg:#2c1715}}
:root[data-theme=dark]{--bg:#0c0e0f;--fg:#e7e9ea;--card:#141719;--line:#23282b;--line2:#353b3f;--muted:#8a9297;--accent:#38c793;--onaccent:#06150e;--fed:#a98bf0;
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
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin:14px 0 6px;overflow-x:auto}
.tabs a{padding:8px 12px;color:var(--muted);text-decoration:none;font-size:.92rem;border-bottom:2px solid transparent;white-space:nowrap}
.tabs a.on{color:var(--fg);font-weight:600;border-bottom-color:var(--accent)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 4px}.chip{border:1px solid var(--line);border-radius:999px;padding:2px 10px;font-size:.8rem}
.chip.ok{border-color:color-mix(in srgb,var(--ok) 40%,transparent)}.chip.warn{border-color:color-mix(in srgb,var(--warn) 45%,transparent)}
.chip.bad{border-color:color-mix(in srgb,var(--bad) 45%,transparent)}
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
.counts{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}.count{border:1px solid var(--line);border-radius:8px;padding:5px 11px;text-decoration:none;color:var(--fg);background:var(--card);font-size:.88rem}.count:hover,.count.on{border-color:var(--accent)}.count.bad b{color:var(--bad)}.count.warn b{color:var(--warn)}.count.ok b{color:var(--ok)}
.mfilter{display:flex;gap:8px;margin:0 0 10px}.mfilter input{flex:1;min-width:0;margin:0}
details.site{margin:0 0 12px}details.site>summary{padding:6px 2px;cursor:pointer}.sitec{font-size:.85rem;margin-left:6px}.sitec .ok{color:var(--ok)}.sitec .bad{color:var(--bad)}.sitec .warn{color:var(--warn)}.sitec .plain{color:var(--muted)}
.tbl.mt{table-layout:fixed}.tbl.mt th.cm{width:17%}.tbl.mt th.cs{width:156px}.tbl.mt th.cr{width:160px}.tbl.mt th.cd{width:112px}.mini{display:inline-block;width:26px;height:8px;border-radius:3px;background:var(--line);overflow:hidden;margin-right:4px;vertical-align:middle}.tbl.mt th.cb{width:118px}.tbl.mt td{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.tbl.mt td,.tbl.mt th{padding-left:10px;padding-right:10px}.tbl.mt td.tick,.tbl.mt th.tick{width:30px;padding-right:0}.tbl.mt td{padding-top:8px;padding-bottom:8px}.tbl.mt .chip{margin-right:4px}.tbl.mt span.bad{color:var(--bad)}.tbl.mt span.warn{color:var(--warn)}
.mbar{position:sticky;bottom:10px;display:flex;flex-wrap:wrap;align-items:center;gap:8px;background:var(--card);border:1px solid var(--accent);border-radius:10px;padding:8px 12px;margin-top:8px}.mbar[hidden]{display:none}
.tbl.dt td{padding-top:7px;padding-bottom:7px}.tbl.dt td.acts{text-align:right;white-space:nowrap}.tbl.dt td.acts button{padding:4px 10px}
@media(max-width:1400px){.tbl.mt .w4{display:none}}@media(max-width:1000px){.tbl.mt .w3,.tbl.dt .w3{display:none}}@media(max-width:600px){.tbl.dt .w2{display:none}}@media(max-width:860px){.mbar{bottom:76px}}@media(max-width:600px){.tbl.mt .w2{display:none}.tbl.mt th.cm{width:auto}.tbl.mt th.cs{width:170px}}
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
.pic path{fill:none;stroke:var(--line2);stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.pic path.stub.up,.pic path.away.up{stroke:var(--ok);stroke-opacity:.8}.pic path.stub.off,.pic path.away.off{stroke:var(--off)}
.pic path.stub.off{stroke-dasharray:4 4}.pic path.away{stroke-dasharray:7 5}.pic path.bar,.pic path.drop{stroke-width:1.6}
.pic path.standby{stroke:var(--muted);stroke-dasharray:6 5;stroke-width:1.4}.pic rect.sb{stroke-dasharray:6 4}
.pic path.link{stroke:var(--fed);stroke-width:2.2;stroke-dasharray:9 3 2 3}.pic path.reach{stroke:var(--fed);stroke-width:1.5;stroke-dasharray:1 4}
.pic rect.ln{stroke:var(--fed);stroke-dasharray:2 3}.pic rect.pt{stroke:var(--fed);stroke-opacity:.6}
.pic path.fed{stroke:var(--fed);stroke-opacity:.7;stroke-width:1.6}.pic rect.zone.fz{stroke:var(--fed);stroke-opacity:.6}.pic circle.lk{fill:var(--fed)}.pic rect.zone.away{stroke-dasharray:7 5}
.pic rect.chip{fill:var(--fed);stroke:none}.pic text.chip{fill:#fff;font:600 9.5px system-ui,sans-serif}
.pic .x{transition:opacity .15s}.pic svg.focus .x:not(.hi){opacity:.15}.pic path.reach.hi{stroke-width:2.4}
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
{% if nav %}{% set sh = shell %}{% macro links(items, cls) %}{% set seen = namespace(group="") %}{% for href, label, pic, group in items %}
{% if cls == "nav" and group != seen.group %}{% set seen.group = group %}<span class="grp">{{ group }}</span>{% endif %}
<a class="{{ cls }}{% if nav == href %} on{% endif %}" href="{{ href }}">{{ icon(pic) }}<span>{{ label }}</span>{% if cls == "nav" and sh.counts.get(href) %}<span class="count">{{ sh.counts[href] }}</span>{% endif %}</a>{% endfor %}{% endmacro %}
{% macro theme() %}<button type="button" class="icon" title="Light or dark" aria-label="Light or dark" onclick="var d=document.documentElement,t=(d.dataset.theme||(matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light'))=='dark'?'light':'dark';d.dataset.theme=t;document.cookie='kiwi_theme='+t+';path=/;max-age=31536000;samesite=strict'">{{ icon("moon") }}</button>{% endmacro %}
{% macro brand() %}<a class="brand" href="{{ open_frame.home if open_frame else "/" }}">{{ icon("kiwi") }}<span>{% if open_frame %}{{ open_frame.name }}{% else %}Kiwi <small>{{ sh.domain }}</small>{% endif %}</span></a>{% endmacro %}
<div class="shell"><aside class="side">{{ brand() }}
{{ links(navitems, "nav") }}
<div class="foot">{% if open_frame %}<div class="row2">{{ theme() }}</div>{% elif sh.total %}<div class="health"><span class="t">Your machines</span>
<span><span class="dot ok"></span>fine</span><b>{{ sh.ok }}</b>
{% if sh.warn %}<span><span class="dot warn"></span>want a look</span><b>{{ sh.warn }}</b>{% endif %}
{% if sh.bad %}<span><span class="dot bad"></span>with a problem</span><b>{{ sh.bad }}</b>{% endif %}
{% if sh.down %}<span><span class="dot down"></span>not reachable</span><b>{{ sh.down }}</b>{% endif %}
<a class="t" href="/status">What everyone sees: the status page</a></div>{% endif %}
{% if not open_frame %}<div class="row2">{{ theme() }}<form class="inline" method="post" action="/logout" style="flex:3;display:flex">{{ tok }}<button>Log out</button></form></div>{% endif %}</div></aside>
<div><header class="top">{{ brand() }}
{% if sh.total and not open_frame %}<span class="pill {{ sh.level }}">{{ sh.word }}</span>{% endif %}{{ theme() }}</header>
<main>{% block main %}{% endblock %}</main></div>
<nav class="bar">{{ links(navitems[:3], "") }}{% if navitems|length > 3 or not open_frame %}<details><summary>{{ icon("more") }}<span>More</span></summary>
<div class="sheet">{{ links(navitems[3:], "nav") }}{% if not open_frame %}<form method="post" action="/logout">{{ tok }}<button>Log out</button></form>{% endif %}</div></details>{% endif %}</nav></div>
{% else %}<main>{{ self.main() }}</main>{% endif %}{% if live %}""" + LIVE + """{% endif %}</body></html>"""

STEP = """{% macro step(m, tok) %}<div class="card"><div class="row"><strong>{{ m.name }}</strong>
<span class="muted">{{ kind(m.role, m.standby_of) }}</span></div>
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
<label style="display:flex;gap:8px;align-items:flex-start;font-weight:400"><input type="checkbox" name="unsigned" value="1" style="width:auto;margin-top:3px">
<span>Open it without a signature. Only for a copy of yours made before 2.170.0: nobody can tell whether such a copy was changed.</span></label>
<p class="muted">When it is back, look at the machines' names on the first page: they should be yours.</p>
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
<script>setTimeout(function(){location.replace(location.pathname + '?auto=1')},6000)</script>
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
{% if emergencies %}<span id="emergencies"></span>{% endif %}{% for e in emergencies or [] %}<div class="card"><div class="row"><strong><span class="dot bad"></span>Emergency</strong>
{% if e.by %}<span class="muted small">{{ e.by }} has it</span>{% else %}<form class="inline" method="post" action="/ack">{{ tok }}
<input type="hidden" name="key" value="{{ e.key }}"><button class="primary">I have it</button></form>{% endif %}</div>
<p>{{ e.text }}</p><p class="muted small">{% if e.by %}Not told again.{% else %}Told on every way that is set, and again every 15 minutes until someone has it.{% endif %}</p></div>{% endfor %}
{% if changed %}<div class="card"><div class="row"><strong><span class="dot warn"></span>Changes not applied yet</strong>
<form class="inline" method="post" action="/run-many" onsubmit="return confirm('Apply {{ changed|join(", ") }} now? Services that changed restart for a moment.')">{{ tok }}
<input type="hidden" name="action" value="apply">{% for h in changed %}<input type="hidden" name="host" value="{{ h }}">{% endfor %}
<button class="primary">Apply {{ "it" if changed|length == 1 else "all " ~ changed|length }}</button></form></div>
<p>{{ changed|join(", ") }}: {{ "its" if changed|length == 1 else "their" }} settings changed, a new kiwi-server release changes what {{ "it gets" if changed|length == 1 else "they get" }}, or something elsewhere that reaches
{{ "it" if changed|length == 1 else "them" }} (a new name the DNS must know, a public name the master hands on). Until applied,
{{ "it keeps" if changed|length == 1 else "they keep" }} the old.</p></div>{% endif %}
{% set todo = machines|selectattr("problems")|list %}{% if todo %}<h2>Needs a look</h2>
{% for m in todo %}{% for p in m.problems %}<div class="card"><div class="row"><strong><span class="dot {{ p.level }}"></span>{{ m.name }}</strong>
<a class="btn" href="/hosts/{{ m.name }}">Open</a></div><p>{{ p.text }}</p>
{% set tip = p.hint or hint(p.text) %}{% if tip %}<p class="muted">{{ tip }}</p>{% endif %}
{% from "acts.html" import acts with context %}{{ acts(m, p, tok) }}
{% if p.level == "warn" and p.key in quietable %}<form class="inline" method="post" action="/hosts/{{ m.name }}/quiet">{{ tok }}
<input type="hidden" name="key" value="{{ p.key }}"><button title="It stays as a note on the machine's page; its settings bring it back">Leave it out</button></form>{% endif %}
</div>{% endfor %}{% endfor %}{% endif %}

{% from "step.html" import step with context %}
{% set fresh = machines|selectattr("phase", "in", ["enroll", "build", "install", "invalid", "bare"])|list %}
{% if fresh %}<h2>Still to set up</h2><p class="muted">One machine after the other{% if not alone %}: the master first{% endif %}.</p>
{% for m in fresh %}{{ step(m, tok) }}{% endfor %}{% endif %}

{% if wide %}<h2>How they are linked</h2><div class="card pic"><div class="wide">{{ wide }}</div><div class="narrow">{{ narrow }}</div>
<p class="muted small" style="margin:10px 0 0">Every machine holds a link to the master. A green line: the machine answers; a grey, broken one: it does not. Dashed: over the Internet, from another site. Violet dots: what a linked network may reach here; in violet below it, what it opens to you. Point at a machine or a network to see its lines.</p></div>{% endif %}
<h2>Go on</h2><div class="tiles">
<a class="tile" href="/machines"><b>Machines</b><span>How each one is, add one, update them all</span></a>
<a class="tile" href="/services"><b>Services</b><span>Open what runs on them, with its login</span></a>
<a class="tile" href="/devices"><b>Add a phone or laptop</b><span>{% if alone %}Without a master: your services at home{% else %}Put a device on your VPN with a QR code{% endif %}</span></a>
{% if not alone %}<a class="tile" href="/access"><b>Say who may reach what</b><span>Which devices and linked networks open which machine or service</span></a>{% endif %}</div>
{% endblock %}""",
    "machines.html": """{% extends "base.html" %}{% block main %}
{% set running = machines|rejectattr("phase", "in", ["enroll", "build", "install", "invalid", "bare"])|list %}
<h1>Machines</h1><div class="row"><span class="muted">{% if looked %}Checked {{ ago(looked) }} ago.{% endif %}</span>
<form class="inline" method="post" action="/refresh">{{ tok }}<input type="hidden" name="back" value="/machines"><button>Check now</button></form></div>
{% if not machines %}<p class="muted">No machines yet.</p>{% else %}
<div class="counts"><a class="count{{ " on" if not t.q }}" href="/machines"><b>{{ t.all }}</b> {{ "machine" if t.all == 1 else "machines" }}</a>
{% for c in t.counts %}<a class="count {{ c.level }}{{ " on" if ("state:" ~ c.key) in t.q.split() }}" href="/machines?q={{ toggled(t.q, "state", c.key)|urlencode }}"
title="Show only these"><b>{{ c.n }}</b> {{ c.words }}</a>{% endfor %}</div>
<form class="mfilter" method="get" action="/machines" role="search"><input type="search" name="q" value="{{ t.q }}" aria-label="Filter the machines"
placeholder="Filter: a name, state:down, site:{{ t.sites[0]|slug }}, part:dns, role:node"{% if t.q %} autofocus{% endif %}>
<button>Filter</button>{% if t.q %}<a class="btn" href="/machines">Show all</a>{% endif %}</form>
{% if t.filtered %}<p class="muted small" style="margin:0 0 8px">{{ t.shown }} of {{ t.all }} shown. This view has its own address: bookmark it.</p>{% endif %}
{% if not t.shown %}<p class="muted">No machine matches. A filter is words like <code>state:down</code>, <code>site:NAME</code>,
<code>part:NAME</code>, <code>role:node</code>, or a part of a machine's name; several narrow it down.</p>{% endif %}
<form method="post" action="/run-many" id="mt">{{ tok }}
{% for g in t.groups %}<details class="site" open><summary><b>{{ g.site }}</b> <span class="muted">({{ g.machines|length }})</span>
<span class="sitec">{% if g.fine %}<span class="ok">all fine</span>{% else %}{% for c in g.counts %}<span class="{{ c.level }}">{{ c.n }} {{ c.words }}</span>{{ " · " if not loop.last }}{% endfor %}{% endif %}</span></summary>
<div class="scroll"><table class="tbl mt"><thead><tr><th class="tick"><input type="checkbox" aria-label="Tick all of {{ g.site }}"
onclick="for(const b of this.closest('table').querySelectorAll('tbody input[type=checkbox]'))b.checked=this.checked;mtn()"></th>
<th class="cm">Machine</th><th class="cs">State</th><th class="w2 cr">Role</th><th class="w3">Parts</th><th class="w2 cd" title="CPU, memory and the fullest disk">Use</th><th class="w3 cb">Backup</th><th class="w4 cb" title="When it last got its role">Applied</th><th class="w4 cb">Seen</th></tr></thead><tbody>
{% for m in g.machines %}<tr><td class="tick">{% if m.phase not in fresh %}<input type="checkbox" name="host" value="{{ m.name }}" aria-label="Tick {{ m.name }}" onclick="mtn()">{% endif %}</td>
<td><a href="/hosts/{{ m.name }}"><b>{{ m.name }}</b></a>{% if g.masters and m.site and m.site != "home" %} <span class="sm">{{ m.site }}</span>{% endif %}{% for x in m.tags %} <span class="sm">{{ x }}</span>{% endfor %}{% if m.waiting %} <span class="sm warn" title="An apply did not reach it. It gets it by itself when it answers again.">apply waits</span>{% endif %}</td>
<td><span class="badge {{ level(m.st) }}{{ " busy" if looking }}"{% if m.story %} title="{{ m.story }}"{% endif %}>{{ stword(m.st) }}</span></td>
<td class="w2">{{ kind(m.role, m.standby_of) }}</td>
<td class="w3"{% if m.part_titles %} title="{{ m.part_titles|join(", ") }}"{% endif %}>{{ m.part_titles|join(", ") or "-" }}</td>
<td class="w2">{% for b in m.bars %}<span class="mini" title="{{ b.label }} {{ b.pct }} %: {{ b.more }}"><span class="fill {{ b.level }}" style="width:{{ b.pct }}%"></span></span>{% else %}<span class="sm">-</span>{% endfor %}</td>
<td class="w3">{% set bk = m.facts.backup %}{% if bk and bk.age is not none %}<span class="{{ "warn" if bk.age > 172800 }}">{{ age(bk.age) }} ago</span>{% elif bk %}<span class="bad">never</span>{% else %}<span class="sm">-</span>{% endif %}</td>
<td class="w4 sm">{% if m.applied %}{{ ago(m.applied) }} ago{% else %}-{% endif %}</td>
<td class="w4 sm">{% if m.seen %}{{ ago(m.seen) }} ago{% else %}-{% endif %}</td></tr>
{% endfor %}</tbody></table></div></details>{% endfor %}
<div class="mbar" id="mbar" hidden><span id="mn"></span><button name="action" value="status">Check</button><button name="action" value="plan">Preview</button>
<button class="primary" name="action" value="apply" onclick="return confirm('Apply to the ticked machines now? Services that changed restart for a moment.')">Apply</button>
<span class="muted small">One after the other; one that fails does not stop the others.</span></div></form>
<script>function mtn(){const n=document.querySelectorAll('#mt tbody input:checked').length,b=document.getElementById('mbar');b.hidden=!n;document.getElementById('mn').textContent=n+' ticked:'}mtn()</script>
{% endif %}
<details{% if take_error %} open{% endif %}><summary>Take in a machine</summary>
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
<details><summary>Add a machine</summary><form method="post" action="/machines/add" class="card">{{ tok }}
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
{% if alone %}<details{% if master_error %} open{% endif %}><summary>Add a master</summary><form method="post" action="/machines/add-master" class="card">{{ tok }}
<p>A master makes your services open from anywhere, through your own VPN, and gives your phones a place on it. It is
a small rented server (or a machine at home whose port your router passes on). Your machines at home go onto its VPN:
after it is set up, each one gets its place there and is set up again (Home lists the steps).</p>
{% for p, pname in hosters.items() %}<div class="lookup-m" data-provider="{{ p }}" data-name="{{ pname }}">
<label>A server at {{ pname }}</label><p class="muted" style="margin:0 0 4px">Sign in at {{ pname }} in your browser:
the page lists your servers, and the one you pick is installed for you later (everything on it is erased then).</p>
<input type="hidden" name="hoster" value=""><input type="hidden" name="provider_server" value="">
<div class="actions"><button type="button" class="hoster-go">Sign in at {{ pname }} and pick it</button></div>
<select class="hoster-list" style="display:none" aria-label="Your server at {{ pname }}"></select>
<p class="hoster-say muted" aria-live="polite"></p></div>{% endfor %}
<label for="maddr">Its address on the Internet</label><p class="muted" style="margin:0 0 4px">The IPv4 address your
hoster shows, like 203.0.113.7, or a dynamic DNS name for a master at home. Picked above: filled in.</p><input id="maddr" name="address">
<details><summary>More settings</summary><label for="mmname">Name</label><p class="muted" style="margin:0 0 4px">Empty: it is
named after a fruit dish.</p><input id="mmname" name="name"></details>
{% if master_error %}<p class="bad">{{ master_error }}</p>{% endif %}
<div class="actions"><button class="primary">Add the master</button></div></form></details>
<script>
document.querySelectorAll(".lookup-m").forEach(function (box) {
  var go = box.querySelector(".hoster-go"), say = box.querySelector(".hoster-say"), list = box.querySelector(".hoster-list");
  var csrf = box.closest("form").querySelector('[name="csrf"]').value, name = box.dataset.name;
  function failed(why) { say.textContent = "That did not work: " + why + ". Type the address instead."; go.disabled = false; }
  function take() { var r = JSON.parse(list.value); box.querySelector('[name="provider_server"]').value = r.id;
    document.getElementById("maddr").value = r.address; say.textContent = "Its address is " + r.address + "."; }
  list.addEventListener("change", take);
  function poll(handle, opened) {
    fetch("/machines/hoster/" + encodeURIComponent(handle), {cache: "no-store"}).then(function (r) { return r.json(); }).then(function (s) {
      if (s.state === "failed" || (s.error && !s.state)) return failed(s.error || "no answer");
      if (s.url && !opened) { opened = true; say.innerHTML = ""; var a = document.createElement("a"); a.href = s.url;
        a.target = "_blank"; a.rel = "noopener"; a.textContent = "Sign in to " + name; say.append(a, " in a new tab and confirm it there."); }
      if (s.state !== "listed") return setTimeout(function () { poll(handle, opened); }, 2000);
      list.innerHTML = ""; s.rows.forEach(function (r) { var o = document.createElement("option");
        o.value = JSON.stringify({id: r.id, address: r.address}); o.textContent = r.label; list.appendChild(o); });
      box.querySelector('[name="hoster"]').value = handle; list.style.display = ""; go.style.display = "none"; take();
    }).catch(function () { setTimeout(function () { poll(handle, opened); }, 4000); });
  }
  go.addEventListener("click", function () {
    go.disabled = true; say.textContent = "Asking " + name + "…";
    fetch("/machines/hoster", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({provider: box.dataset.provider, csrf: csrf})}).then(function (r) { return r.json(); })
      .then(function (d) { if (d.error) return failed(d.error); poll(d.handle, false); }).catch(function () { failed("this page did not answer"); });
  });
});
</script>{% endif %}

{% if running %}<h2>Updates</h2><div class="card"><p>Brings every machine to your network's current settings and the
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
{% if t.login %} · <a href="/hosts/{{ t.machine }}?tab=services#logins">login</a>{% endif %}</span></td>
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
<p class="muted">{{ kind(m.role, m.standby_of) }}{% for t in m.tags %} · {{ t }}{% endfor %} · {{ m.hostname }}{% if m.facts.os %} · {{ m.facts.os }}{% endif %}</p>
{% if chips %}<div class="chips">{% for c in chips %}<span class="chip {{ c.level }}">{{ c.text }}</span>{% endfor %}</div>{% endif %}
{% if m.waiting %}<p class="banner warn">An apply did not reach this machine. It gets it by itself as soon as it answers again; nothing to do here.</p>{% endif %}
<nav class="tabs">{% for t, label in (("overview", "Overview"), ("services", "Services"), ("history", "History"), ("backups", "Backups")) %}
<a href="/hosts/{{ m.name }}{{ '' if t == 'overview' else '?tab=' ~ t }}" class="{{ 'on' if tab == t }}">{{ label }}</a>{% endfor %}
<a href="/hosts/{{ m.name }}/settings">Settings</a></nav>
{% if tab == "overview" %}
{% if m.bars %}<div class="stats">{% if m.facts.uptime is number and m.facts.uptime %}<div class="stat"><span class="lbl">On for</span>
<b>{{ age(m.facts.uptime) }}</b><span class="cap">since it last started</span></div>{% endif %}
{% for b in m.bars %}<div class="stat"><span class="lbl">{{ b.label }}</span><b class="{{ b.level }}">{{ b.pct }}<small> %</small></b>
<span class="cap">{{ b.more }}</span><span class="track"><span class="fill {{ b.level }}" style="width:{{ b.pct }}%"></span></span></div>{% endfor %}</div>{% endif %}
{% if m.state == "asleep" %}<div class="card"><p>{{ (m.notes or ["Asleep"])[0] | capitalize }}.</p>
{% if m.wol %}<form method="post" action="/run">{{ tok }}<input type="hidden" name="action" value="wake">
<input type="hidden" name="host" value="{{ m.name }}"><button>Wake it now</button></form>{% endif %}</div>{% endif %}
{% for p in m.problems %}<div class="card"><p><span class="dot {{ p.level }}"></span>{{ p.text }}</p>
{% set tip = p.hint or hint(p.text) %}{% if tip %}<p class="muted">{{ tip }}</p>{% endif %}
{% from "acts.html" import acts with context %}{{ acts(m, p, tok) }}
{% if p.level == "warn" and p.key in quietable %}<form class="inline" method="post" action="/hosts/{{ m.name }}/quiet">{{ tok }}
<input type="hidden" name="key" value="{{ p.key }}"><button title="It stays as a note here; the settings bring it back">Leave it out</button></form>{% endif %}
{% if m.wol and p.key == "unreachable" %}<form method="post" action="/run">{{ tok }}<input type="hidden" name="action" value="wake">
<input type="hidden" name="host" value="{{ m.name }}"><button class="primary">Wake it</button></form>
<p class="muted small">Another machine of its LAN sends it the wake-up signal (Wake-on-LAN).</p>{% endif %}</div>{% endfor %}
{% from "step.html" import step with context %}
{% if m.phase and m.phase != "applied" %}<h2>Still to set up</h2>{{ step(m, tok) }}
{% elif not m.known %}<div class="card"><p>This machine has not answered yet.</p></div>{% endif %}

<h2>What you can do</h2><div class="card"><div class="what">
<form method="post" action="/run">{{ tok }}<input type="hidden" name="action" value="status"><input type="hidden" name="host" value="{{ m.name }}"><button>Check now</button></form>
<p>Asks the machine how it is. <span class="muted">Changes nothing.</span></p>
<form method="post" action="/run">{{ tok }}<input type="hidden" name="action" value="plan"><input type="hidden" name="host" value="{{ m.name }}"><button>Preview</button></form>
<p>Shows what <em>Apply</em> would change on it. <span class="muted">Changes nothing.</span></p>
{% if m.role == "node" %}<form method="post" action="/run">{{ tok }}<input type="hidden" name="action" value="mtu"><input type="hidden" name="host" value="{{ m.name }}"><button>Measure the MTU</button></form>
<p>How big a packet gets through its tunnel to the master, and through the path outside it: whether the tunnel's MTU fits, and how large it could be. <span class="muted">Changes nothing.</span></p>{% endif %}
<form method="post" action="/run" onsubmit="return confirm('Apply to {{ m.name }} now? Services that changed restart for a moment.')">{{ tok }}
<input type="hidden" name="action" value="apply"><input type="hidden" name="host" value="{{ m.name }}"><button class="primary">Apply</button></form>
<p>Sets the machine up the way your network's settings say: new settings, updates of its services, repairs.
<span class="muted">Services that changed restart for a moment. Safe to run again.</span></p>
<a class="btn" href="/hosts/{{ m.name }}/settings">Settings</a>
<p>What this machine does and how: its parts, its address, its updates. <span class="muted">Apply brings a change to it.</span></p>
{% set big = (" It is the master: the VPN and everything reached through it stop until it is back." if m.role == "master" else "")
   ~ (" It runs this page: the page goes away until it is back." if "control" in (m.modules or []) else "") %}
<form method="post" action="/run" onsubmit="return confirm('Restart {{ m.name }}? It is gone for a few minutes, then back by itself.{{ big }}')">{{ tok }}
<input type="hidden" name="action" value="reboot"><input type="hidden" name="host" value="{{ m.name }}"><button>Restart</button></form>
<p>Shuts it down and starts it again. <span class="muted">Its disk opens again the way it always does.</span></p>
<form method="post" action="/run" onsubmit="return confirm('Switch {{ m.name }} off? It stays off.{{ big }}{{ ' Wake-on-LAN starts it again.' if m.wol else ' Only someone at the machine, or its provider, can switch it on again.' }}')">{{ tok }}
<input type="hidden" name="action" value="poweroff"><input type="hidden" name="host" value="{{ m.name }}"><button>Switch off</button></form>
<p>Shuts it down. <span class="muted">{{ "Wake it starts it again." if m.wol else "It stays off until someone switches it on." }}</span></p></div></div>

{% endif %}{% if tab == "services" %}
{% if tiles %}<h2>Its services</h2><div class="tiles">{% for t in tiles %}<a class="tile" href="https://{{ t.site }}" target="_blank" rel="noopener">
<b>{{ t.label }}</b><span>{{ t.site }}</span></a>{% endfor %}</div>{% else %}<p class="muted">No services of its own: its parts are {{ (m.modules|parts) or "none" }}.</p>{% endif %}

{% include "logins.html" %}
{% if m.facts.services %}<h2 id="containers">Its containers</h2>
<p class="muted small">As the last look saw them. Restart one that hangs; <em>Log</em> shows its last lines. A stopped one stays
stopped until you start it or the machine restarts.</p>
<div class="scroll"><table class="tbl"><thead><tr><th>Container</th><th>Now</th><th></th></tr></thead><tbody>
{% for s in m.facts.services %}<tr><td><b>{{ s.name }}</b>{% if s.image %}<br><span class="sm">{{ s.image.split("/")[-1] }}</span>{% endif %}</td>
<td>{% if s.asleep %}<span class="badge" title="Nobody played for a while; the first player who joins wakes it">asleep</span>{% else %}
<span class="badge {{ 'ok' if s.running and s.health != 'unhealthy' else 'bad' }}">{{ s.health or s.state }}</span>{% endif %}
{% if not s.stack %}<br><span class="sm">not of its stack</span>{% endif %}</td>
<td>{% if s.stack %}{% for act, label in (("restart", "Restart"), ("stop", "Stop"), ("start", "Start"), ("logs", "Log")) %}
{% if not (act == "stop" and not s.running) and not (act == "start" and s.running) %}<form class="inline" method="post" action="/run"
{% if act == "stop" %}onsubmit="return confirm('Stop {{ s.name }} on {{ m.name }}? It stays stopped until you start it.')"{% endif %}>{{ tok }}
<input type="hidden" name="action" value="service"><input type="hidden" name="host" value="{{ m.name }}">
<input type="hidden" name="module" value="{{ s.name }}"><input type="hidden" name="at" value="{{ act }}"><button>{{ label }}</button></form>
{% endif %}{% endfor %}{% endif %}</td></tr>{% endfor %}</tbody></table></div>{% endif %}
{% set ku = m.facts.kiwi_units or [] %}{% if ku %}{% set bad = ku | selectattr("active", "equalto", "failed") | list %}
<details id="kiwi-units"{% if bad %} open{% endif %}><summary>Kiwi's own units: {{ ku | length }}{% if bad %},
<span class="badge bad">{{ bad | length }} failed</span>{% else %}, none failed{% endif %}</summary>
<p class="muted small">The update, the runbook, the role. Start runs one that has finished once more; Log shows its last lines.</p>
<div class="scroll"><table class="tbl"><tbody>{% for u in ku %}<tr><td><b>{{ u.name }}</b></td>
<td><span class="badge {{ 'bad' if u.active == 'failed' else 'ok' if u.active == 'active' else '' }}">{{ u.active }}</span>
<span class="sm">{{ u.sub }}</span></td><td>{% for act, label in (("restart", "Restart"), ("start", "Start"), ("logs", "Log")) %}
{% if not (act == "start" and u.active == "active") and not (act == "restart" and u.active != "active") %}<form class="inline" method="post" action="/run">{{ tok }}
<input type="hidden" name="action" value="service"><input type="hidden" name="host" value="{{ m.name }}">
<input type="hidden" name="module" value="{{ u.name }}"><input type="hidden" name="at" value="{{ act }}"><button>{{ label }}</button></form>
{% endif %}{% endfor %}</td></tr>{% endfor %}</tbody></table></div></details>{% endif %}
{% if m.stops %}<h2 id="when">When it stops</h2><p>{{ m.stops }}</p>
<details><summary class="muted small">Each part</summary><ul class="small">{% for r in m.parts_table %}<li>{{ r.sentence }}</li>{% endfor %}</ul>
<p class="muted small">How much the network needs a part: <code>defaults.parts</code> in the fleet file
(<code>need: must|normal|wait</code>).</p></details>
<p class="small">{% for r in m.runbook %}{{ r.sentence }} {% endfor %}{% if m.runbook %}Every other case: you are told,
nothing is done.{% else %}In every case you are told, and nothing is done.{% endif %}
<span class="muted">What happens in which case: <code>defaults.runbook</code> in the fleet file.</span></p>{% endif %}
{% if m.phase not in fresh and (targets or standby) %}<h2 id="move">Move a part, or keep it ready</h2>
<p class="muted small">A part moves to another machine with its data, its secrets and its name. A part that is kept ready has its
data copied to a second machine every minute: a switch moves it there while this machine runs, with nothing lost.
Nothing switches by itself; a machine that is gone gets its data back from its backups once it is installed anew.</p>
{% for l in standby %}<div class="card row"><div><b>{{ ptitle(l.part) }}</b> is kept ready on <a href="/hosts/{{ l.ready_on }}">{{ l.ready_on }}</a>:
{% if l.age is none %}no copy there yet{% elif l.age < 90 %}its copy is current{% else %}its copy is {{ age(l.age) }} old{% endif %}.</div>
<form class="inline" method="post" action="/hosts/{{ m.name }}/switch"
onsubmit="return confirm('Give {{ l.part }} to {{ l.ready_on }} now? It stops on {{ m.name }}, {{ l.ready_on }} fetches the last of its data and starts it.')">{{ tok }}
<input type="hidden" name="part" value="{{ l.part }}"><button class="primary">Switch to {{ l.ready_on }}</button></form></div>{% endfor %}
{% if targets %}<form method="get" action="/hosts/{{ m.name }}/move" class="card">
<label for="mv_part">Part</label><select id="mv_part" name="part">{% for p in m.modules %}<option value="{{ p }}">{{ ptitle(p) }}</option>{% endfor %}</select>
<label for="mv_to">To the machine</label><select id="mv_to" name="to">{% for t in targets %}<option value="{{ t }}">{{ t }}</option>{% endfor %}</select>
<div class="actions"><button>See what would happen</button></div></form>{% endif %}{% endif %}
{% endif %}{% if tab == "history" %}
<p class="muted">What was done to {{ m.name }} and what it did, newest first: from this page, the app or a terminal,
and what {{ m.name }} itself remembers of the last 30 days.</p>
<nav class="tabs small">{% for k, label in show_filters %}<a href="?tab=history&show={{ k }}"{% if k == show %} class="on"{% endif %}>{{ label }}</a>{% endfor %}</nav>
{% if own is none %}<p class="muted small">{{ m.name }} did not answer: what it remembers itself is not here.</p>{% endif %}
{% for day, entries in days %}<h2>{{ day }}</h2>{% for i in entries[:150] %}<div class="card row"><span>
{% if i.job %}<a href="/jobs/{{ i.job }}">{{ i.what }}</a>{% else %}{{ i.what }}{% endif %}{% if badge(i) %} <span class="state {{ i.state }}">{{ badge(i) }}</span>{% endif %}
{% if i.detail %}<br><span class="muted small">{{ i.detail }}</span>{% endif %}</span>
<span class="muted small">{{ clock(i) }}{% if i.who %} · {{ i.who }}{% endif %}</span></div>{% endfor %}
{% else %}<p class="muted">Nothing of this kind in the last 30 days.</p>{% endfor %}
{% endif %}{% if tab == "backups" %}{% set b = m.facts.backup %}
{% if "backup" in m.modules %}<div class="card"><div class="row"><strong>Its data</strong>
{% if b and b.last %}<span class="state {{ 'ok' if b.age < 129600 else 'bad' }}">saved {{ age(b.age) }} ago</span>{% else %}<span class="state warn">no backup seen yet</span>{% endif %}</div>
{% if b and b.copies %}<p class="muted">Kept on: {% for c in b.copies %}{{ c.server }}{{ ", " if not loop.last }}{% endfor %}</p>{% endif %}
<form class="inline" method="post" action="/run">{{ tok }}<input type="hidden" name="action" value="save"><input type="hidden" name="host" value="{{ m.name }}"><button>Save now</button></form>
<a class="btn" href="/backups/{{ m.name }}">Bring something back</a></div>
{% else %}<p class="muted">Nothing on {{ m.name }} is set to be saved. A machine with your data should have the <em>backup</em> part (Settings).</p>{% endif %}
{% if m.facts.backup_store %}<div class="card"><p>It keeps the backups of: {% for r in m.facts.backup_store.repos %}{{ r.client }} ({{ r.snapshots }} versions){{ ", " if not loop.last }}{% endfor %}
<span class="muted">· {{ size(m.facts.backup_store.free_kib) }} free</span></p></div>{% endif %}
<p class="muted small">Every machine's backups, and the network's own copy: <a href="/backups">Backups</a>.</p>
{% endif %}{% if tab == "overview" %}
{% if m.notes or m.facts %}<h2>Details</h2><div class="card"><dl>
{% if m.facts.running is defined and m.facts.expected is defined %}<dt>Services</dt><dd>{{ m.facts.running|length if m.facts.running is iterable and m.facts.running is not string else m.facts.running }} running</dd>{% endif %}
{% if m.facts.backup and m.facts.backup.age is not none %}<dt>Last backup</dt><dd>{{ age(m.facts.backup.age) }} ago</dd>{% endif %}
{% if m.facts.reboot %}<dt>Updates</dt><dd>installed, it restarts in its update window</dd>{% endif %}
{% if m.vpn_ip %}<dt>VPN address</dt><dd>{{ m.vpn_ip }}</dd>{% endif %}
<dt>Parts</dt><dd>{{ m.modules|parts }}</dd></dl>
{% if m.notes %}<ul class="muted">{% for n in m.notes %}<li>{{ n }}</li>{% endfor %}</ul>{% endif %}
{% if m.output %}<details><summary>What the machine said, word for word</summary><pre>{{ m.output }}</pre></details>{% endif %}</div>{% endif %}
<form method="post" action="/hosts/{{ m.name }}/script" target="_blank" class="muted small">{{ tok }}<button
style="padding:2px 8px;font-size:.85rem">The role script</button>: what <em>Apply</em> sends to {{ m.name }} and runs
there, to read. It holds the machine's passwords.</form>

{% include "rebuild.html" %}
<details style="margin-top:28px"><summary>Remove this machine</summary><div class="card"><p>{{ m.name }} leaves the network
for good: its place on the VPN, its keys and its entry in the settings go. <span class="muted">The machine itself is not
touched: wipe it, or switch it off. A machine that keeps the backups of others, or helps another one unlock its disk,
is named first.</span></p>
<form method="post" action="/run" onsubmit="return confirm('Remove {{ m.name }} from the network for good?')">{{ tok }}
<input type="hidden" name="action" value="remove"><input type="hidden" name="host" value="{{ m.name }}">
<button>Remove {{ m.name }}</button></form></div></details>
{% endif %}
{% endblock %}""",

    "settings.html": """{% extends "base.html" %}{% block main %}
{% macro field(f) %}{% set id = "f-" + f.name %}<div class="q">
{% if f.type == "modules" %}<label>{{ f.label }}</label><p class="muted" style="margin:0 0 4px">{{ f.help }}</p>
<input type="hidden" name="shown:{{ f.name }}" value="1">
{% for o in f.options %}<label class="check"><input type="checkbox" name="f:{{ f.name }}" value="{{ o.value }}" onchange="var g=document.querySelector('[data-module=&quot;{{ o.value }}&quot;]');if(g){g.hidden=!this.checked;if(this.checked)g.open=true}"
{%- if ((o.value in typed[f.name]) if f.name in typed else o.on) %} checked{% endif %}><span>{{ o.label }}
<span class="muted small">{{ o.help }}{% if o.needs %} (needs {{ o.needs|join(", ") }}){% endif %}</span></span></label>{% endfor %}
{% else %}<label for="{{ id }}">{{ f.label }}{% if host and f.set and not f.secret %} <span class="chip">set here</span>{% endif %}</label>
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
{% endif %}{% endif %}{% if host and f.set and not f.secret and f.type != "modules" %}<label class="check"><input type="checkbox"
name="reset:{{ f.name }}" value="1"><span>As for every machine{% if to_text(f.type, f.inherited) %} ({{ to_text(f.type, f.inherited)|truncate(60) }}){% endif %}</span></label>{% endif %}
{% if wrong.get(f.name) %}<p class="bad">{{ wrong[f.name] }}</p>{% endif %}</div>{% endmacro %}
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
{% if host %}<p class="small">{% if only %}<a href="/hosts/{{ host }}/settings">Every setting</a> · <b>Only what {{ host }} sets itself</b>
{% else %}<b>Every setting</b> · <a href="/hosts/{{ host }}/settings?only=set">Only what {{ host }} sets itself</a>{% endif %}</p>{% endif %}
<form method="post" action="{{ "/hosts/" + host + "/settings" if host else "/settings" }}" autocomplete="off">{{ tok }}
{% for g in groups if not only or g.fields|selectattr("set")|list %}{% set more = g.fields|selectattr("more")|selectattr("set" if only else "more")|list %}
{% if g.advanced %}<details class="card" id="s-{{ g.id|replace(':', '-') }}" style="scroll-margin-top:64px"{% if g.id in open %} open{% endif %}{% if g.module %} data-module="{{ g.module }}"{% endif %}{% if g.off %} hidden{% endif %}><summary>{{ g.title }}</summary>
{% else %}<div class="card" id="s-{{ g.id|replace(':', '-') }}" style="scroll-margin-top:64px"><h2 style="margin-top:0">{{ g.title }}</h2>{% endif %}
{% if g.help %}<p class="muted">{{ g.help }}</p>{% endif %}
{% for f in g.fields if not f.more and (not only or f.set) %}{{ field(f) }}{% else %}{% if not more %}<p class="muted">{{ "Nothing set here." if only else "Nothing to set: it is on or off." }}</p>{% endif %}{% endfor %}
{% if more %}<details{% if more|selectattr("name", "in", touched)|list %} open{% endif %}><summary>More settings</summary>
{% for f in more %}{{ field(f) }}{% endfor %}</details>{% endif %}
{{ "</details>"|safe if g.advanced else "</div>"|safe }}{% endfor %}
<div class="actions"><button name="then" value="">Save</button>
<button class="primary" name="then" value="apply" title="Save, then apply {{ host or "every machine" }} and every other machine the change reaches">Save &amp; apply</button></div></form>{% endif %}
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
<details {% if job.state == 'failed' or job.action == 'service' %}open{% endif %}><summary>What is happening, line by line</summary><pre id="log"></pre>
<p class="small"><a href="/api/jobs/{{ job.id }}/log?text=1" target="_blank" rel="noopener">Open as text</a> ·
<a href="/api/jobs/{{ job.id }}/log?text=1&amp;download=1">Download</a></p></details>
<script>
const say={{ say|tojson }};let off=0;const log=document.getElementById("log"),el=document.getElementById("say");
let away=0;async function poll(){let r;try{r=await fetch("/api/jobs/{{ job.id }}/log?offset="+off)}catch(e){r={ok:false,status:0}}
if(!r.ok){if(r.status==404&&away){el.textContent="This page restarted while the job ran (an apply to its own machine does that), and the job ended with it. Start it again: an apply that has nothing left to do ends at once.";el.className="banner warn";return}
away=1;el.textContent="This page is away for a moment (an apply to its own machine restarts it). Waiting for it…";el.className="banner warn";setTimeout(poll,3000);return}
let d;try{d=await r.json()}catch(e){el.textContent="This page restarted while the job ran, and the job ended with it. Log in again and start it once more: an apply that has nothing left to do ends at once.";el.className="banner warn";return}away=0;log.textContent+=d.text;off=d.offset;el.textContent=say[d.state];
el.className="banner "+(d.state=="ok"?"ok":d.state=="failed"?"bad":"warn");
if(d.state=="failed")log.parentElement.open=true;
const u=log.textContent.match(/https:\\/\\/[^\\s"']+/g),o=document.getElementById("open");
if(u&&d.state=="running"){const a=document.getElementById("openlink");a.href=a.textContent=u[u.length-1];o.style.display=""}else o.style.display="none";
if(d.state=="waiting"||d.state=="running")setTimeout(poll,1500)}
poll();
</script>{% endblock %}""",

    "move.html": """{% extends "base.html" %}{% block main %}
<p><a href="/hosts/{{ src }}?tab=services#move">← {{ src }}</a></p>
<h1>Move {{ ptitle(plan.part) }}</h1>
<p class="muted">From {{ src }} to {{ dst }}{% if plan.name %}. It stays <b>{{ plan.name }}</b>{% endif %}.</p>
{% if error %}<p class="banner bad">{{ error }}</p>{% endif %}
{% if plan.stops %}<div class="banner warn">It cannot be done as things are.</div>
<ul>{% for x in plan.stops %}<li>{{ x }}</li>{% endfor %}</ul>
{% else %}<h2>What happens</h2><ol>{% for x in plan.steps %}<li>{{ x }}</li>{% endfor %}</ol>
{% for n in plan.notes %}<p class="muted small">{{ n | capitalize }}.</p>{% endfor %}
<div class="actions"><form class="inline" method="post" action="/hosts/{{ src }}/move"
onsubmit="return confirm('Move {{ plan.part }} from {{ src }} to {{ dst }} now? It is away for the minutes the move takes.')">{{ tok }}
<input type="hidden" name="part" value="{{ plan.part }}"><input type="hidden" name="to" value="{{ dst }}">
<button class="primary">Move it</button></form>
<form class="inline" method="post" action="/hosts/{{ src }}/keep-ready">{{ tok }}
<input type="hidden" name="part" value="{{ plan.part }}"><input type="hidden" name="to" value="{{ dst }}">
<button title="It stays on {{ src }}; {{ dst }} fetches its data every minute and can take it over">Keep it ready on {{ dst }} instead</button></form></div>
{% endif %}{% endblock %}""",

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
{% if "0.0.0.0/0" not in config %}<p class="muted small">Only the network goes through this one. On a computer with NetworkManager,
after importing it: <code>nmcli connection modify {{ shown }} ipv4.dns-search {{ dns_domains }} ipv4.never-default yes</code>,
so that only the network's names are asked through it and the computer's own names still work.</p>{% endif %}
<form method="post" action="/devices/download">{{ tok }}<input type="hidden" name="name" value="{{ shown }}"><button>Download {{ shown }}.conf</button></form>
<p class="warn small">Whoever has this code or file is on your VPN as this device. Do not send it around.</p></div>{% endif %}
{% if error %}<p class="banner bad">{{ error }}</p>{% endif %}
<h2>Add a phone or laptop</h2><form method="post" action="/devices/add" class="card">{{ tok }}
<label for="n">Name</label><input id="n" name="name" placeholder="annas-phone" pattern="[a-z0-9][a-z0-9-]*" required>
<p class="muted small">Lowercase letters, digits and dashes.</p>
{% if groups %}<label for="g">Who is it for?</label><select id="g" name="group">
{% for g in groups %}<option value="{{ g }}" {% if g == default_group %}selected{% endif %}>{{ g }}</option>{% endfor %}</select>
<p class="muted small">The group decides what the device may reach. The admin pages only for a group that has them.</p>{% endif %}
<label class="check"><input type="checkbox" name="split" value="yes"><span>Only the network through the VPN
<span class="muted small">(a laptop at home, say): its own Internet stays as it is. Without it, everything goes through the VPN.</span></span></label>
<div class="actions"><button class="primary">Add it</button></div></form>
<h2>Devices on your VPN</h2>
{% if not t.all %}<p class="muted">None yet.</p>{% else %}
{% if t.groups|length > 1 %}<div class="counts"><a class="count{{ " on" if not t.q }}" href="/devices"><b>{{ t.all }}</b> {{ "device" if t.all == 1 else "devices" }}</a>
{% for g in t.groups %}<a class="count{{ " on" if ("group:" ~ g.group) in t.q.split() }}" href="/devices?q={{ toggled(t.q, "group", g.group)|urlencode }}"
title="Show only this group"><b>{{ g.n }}</b> {{ g.group }}</a>{% endfor %}</div>{% endif %}
{% if t.all > 8 or t.q %}<form class="mfilter" method="get" action="/devices" role="search"><input type="search" name="q" value="{{ t.q }}"
aria-label="Filter the devices" placeholder="Filter: a part of a name, an address, group:NAME, site:NAME"{% if t.q %} autofocus{% endif %}>
<button>Filter</button>{% if t.q %}<a class="btn" href="/devices">Show all</a>{% endif %}</form>{% endif %}
{% if t.filtered %}<p class="muted small" style="margin:0 0 8px">{{ t.shown }} of {{ t.all }} shown.</p>{% endif %}
{% if not t.shown %}<p class="muted">No device matches.</p>{% else %}
{% set by_site = t.site_names|length > 1 %}
{% if by_site %}<div class="counts">{% for x in t.site_names %}<a class="count{{ " on" if ("site:" ~ x) in t.q.split() }}"
href="/devices?q={{ toggled(t.q, "site", x)|urlencode }}" title="Show only this site">{{ x }}</a>{% endfor %}</div>{% endif %}
<form method="post" action="/devices/show">{{ tok }}{% for g in (t.sites if by_site else [{"site": "", "devices": t.devices}]) %}
{% if by_site %}<h3 id="site-{{ g.slug }}">{{ g.site or "Master not known" }} <span class="muted small">({{ g.devices|length }})</span></h3>{% endif %}
<div class="scroll"><table class="tbl dt"><thead><tr><th>Device</th><th class="w2">Group</th><th class="w2">Address</th><th class="w3">Through the VPN</th><th></th></tr></thead><tbody>
{% for d in g.devices %}<tr><td><b>{{ d.name }}</b></td><td class="w2">{{ d.group or "-" }}</td><td class="w2"><code class="sm">{{ d.address or "-" }}</code></td>
<td class="w3">{{ "Only the network" if d.split else "Everything" }}</td>
<td class="acts"><button name="name" value="{{ d.name }}" formaction="/devices/show">QR code</button>
<button name="name" value="{{ d.name }}" formaction="/devices/remove" onclick="return confirm('Take '+this.value+' off the VPN? Its connection stops working at once.')">Remove</button></td></tr>
{% endfor %}</tbody></table></div>{% endfor %}</form>{% endif %}{% endif %}
{% if removed %}<p class="banner ok">{{ removed }} is off the VPN. Its connection no longer works.</p>{% endif %}
<details><summary>Lost device</summary><p class="muted">Remove it here. From that moment it cannot reach
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
<p class="muted">Other networks this one is linked to. Each reaches here only what is shared with it.</p>
{% if said %}<div class="banner ok">{% for x in said %}{{ x }}{% if not loop.last %}<br>{% endif %}{% endfor %}</div>{% endif %}
<details{% if card_error %} open{% endif %}><summary>Link a new network</summary><div class="card">
<p>Two networks link by giving each other their card. A card holds nothing secret: the master's address, its public
link key, the network's addresses and the ending of its names.</p>
<ol><li>Give the other network this network's card: <a class="btn" href="/federation/card.yaml" download>Download this network's card</a></li>
<li>Take theirs: paste the text of their card here.</li>
<li>They take yours the same way (their control page, or <code>kiwi-server federate THEIR-FLEET --accept CARD</code>).</li></ol>
<form method="post" action="/federation/card">{{ tok }}
<label for="card">The other network's card</label>
<textarea id="card" name="card" rows="8" spellcheck="false" required placeholder="federation-card: 1 ..."></textarea>
{% if card_error %}<p class="bad">{{ card_error }}</p>{% endif %}
<div class="actions"><button class="primary">Take their card</button></div></form>
<p class="muted small">Nothing is shared by linking. Afterwards say here what each may reach, then apply the master.</p></div></details>
{% if saved %}<p class="banner ok">Saved. Apply this network's master, and the machines that share something, to bring it there.</p>{% endif %}
{% if error %}<p class="banner bad">{{ error }}</p>{% endif %}
{% if partners %}<form method="post" action="/federation">{{ tok }}
{% for name, p in partners.items() %}<div class="card"><div class="row"><strong>{{ name }}</strong>
<span class="muted">its names end in .{{ p.domain or "?" }} · {{ p.mesh }}</span></div>
<p>Shared with it: {{ p.share | join(", ") or "nothing" }}. <a href="/access">Change who may reach what</a></p>
{% set c = cas.get(name) %}{% if c and c.offered %}<p>Its certificates: <a href="/federation/{{ name }}/ca.pem" download>{{ name }}'s CA</a>
<span class="muted">· mark <code>{{ c.mark }}</code> · {{ c.why }}. Import it on your devices to open {{ name }}'s pages
without a warning.</span></p>{% elif c %}<p class="muted">Its certificates: not offered here, {{ c.why }}.</p>{% endif %}
<label for="f-{{ name }}">Who of it may come</label><p class="muted" style="margin:0 0 4px">Addresses or networks of
{{ name }}, like 10.10.3.0/24. Empty: all of {{ name }}.</p>
<input id="f-{{ name }}" name="from.{{ name }}" value="{{ p['from'] | join(' ') }}">
<label><input type="checkbox" name="exit.{{ name }}" style="width:auto"{% if p.exit %} checked{% endif %}>
Leave to the Internet through {{ name }}</label><p class="muted" style="margin:0">Your devices come out at its master.
It has to allow that on its side.</p>
<label for="eg-{{ name }}">Only these VPN groups</label><p class="muted" style="margin:0 0 4px">Like family phones: only
their devices leave through {{ name }}, the others by your own master. Empty: every device.</p>
<input id="eg-{{ name }}" name="exit_groups.{{ name }}" value="{{ p.exit | join(' ') if p.exit is not sameas true and p.exit else '' }}">
<label><input type="checkbox" name="internet.{{ name }}" style="width:auto"{% if p.internet %} checked{% endif %}>
{{ name }} may leave to the Internet through this network</label><p class="muted" style="margin:0">Its devices come
out at your master. To the Internet only, never to your machines.</p>
</div>{% endfor %}
<div class="actions"><button class="primary">Save</button></div></form>
{% else %}<p class="muted">This network is linked to no other.</p>{% endif %}
{% endblock %}""",
    "access.html": """{% extends "base.html" %}{% block main %}
<p><a href="/">← Home</a></p><h1>Access rules</h1>
<p class="muted">A device on your VPN belongs to a group: you choose it when you add the device. A linked network is
another Kiwi network. Tick what each may open. A machine: all of it. A service: only that page.
The master is always reachable: it answers the names.</p>
{% if saved %}<div class="banner ok">Saved: {{ saved }}. The machines get it with the next Apply.
<form class="inline" method="post" action="/run">{{ tok }}<input type="hidden" name="action" value="update">
<button>Update everything</button></form></div>{% endif %}
{% if error %}<p class="banner bad">{{ error }}</p>{% endif %}
<form method="post" action="/access">{{ tok }}
{% for w in who %}<div class="card"><div class="row"><strong>{{ w.name }}</strong>
<span class="muted">{{ {"group": "group", "lan": "group of the home network"}.get(w.kind, "linked network") }} · {{ w.range }}{% if w.devices %} ·
{{ w.devices | join(", ") }}{% endif %}</span></div>
<p class="muted" style="margin:4px 0">Now: {{ w.says }}.</p>
{% if w.kind in ("group", "lan") %}<label><input type="checkbox" name="all.{{ w.name }}" style="width:auto"{% if w.open.all %} checked{% endif %}>
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
<label for="p-{{ w.name }}">Single ports{{ ", addresses and linked networks" if w.kind == "group" else "" }}</label>
<input id="p-{{ w.name }}" name="p.{{ w.name }}" placeholder="{{ "lychee:22 kiwi2" if w.kind == "group" else "lychee:22" }}" value="{{ w.extra | join(' ') }}">
{% if w.kind == "group" %}<p class="muted small" style="margin:4px 0 0">A linked network by its name (kiwi2): what it opens to this network. Without it, or <em>Everything</em>, the group reaches nothing of it.</p>{% endif %}
{% if w.kind in ("group", "lan") %}<div style="margin-top:8px">
{% if w.kind == "group" %}<label style="display:inline"><input type="checkbox" name="peers.{{ w.name }}" style="width:auto"{% if w.peers %} checked{% endif %}> Other devices on the VPN</label>
<label style="display:inline;margin-left:12px"><input type="checkbox" name="internet.{{ w.name }}" style="width:auto"{% if w.internet %} checked{% endif %}> The Internet through the VPN</label>{% endif %}
<label style="display:inline;margin-left:12px"><input type="checkbox" name="admin.{{ w.name }}" style="width:auto"{% if w.admin %} checked{% endif %}> The admin pages</label>
<p class="muted" style="margin:4px 0 0">Admin pages are the network's controls: this control page, Portainer, Pi-hole, the
VPN's device page, backups. Without this tick a group does not open them, also not with "everything", unless one is
ticked above.</p></div>{% else %}<p class="muted" style="margin:6px 0 0">Who of {{ w.name }} may come, and the way out:
<a href="/federation">Linked networks</a>.</p>{% endif %}
</div>{% endfor %}
<div class="card"><label for="new">A new group</label><p class="muted" style="margin:0 0 4px">A name like kids or
work. A device joins it under <a href="/devices">Add a phone or laptop</a>.</p>
<input id="new" name="new" placeholder="kids" pattern="[a-z0-9][a-z0-9-]*">
<label for="newlan">At home: its subnet</label><p class="muted" style="margin:0 0 4px">Empty: a group of the VPN. A subnet
(192.168.20.0/24: a guest Wi-Fi, a VLAN) makes a group of the home network: each machine keeps it itself.</p>
<input id="newlan" name="new_lan" placeholder="192.168.20.0/24">
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
{% endfor %}<button class="small" name="new" value="{{ s }}.{{ m.name }}"
onclick="return confirm('A new address for {{ s }}.{{ m.name }}? The old one stops answering with its next apply, and whoever had it needs the new one.')">New address</button>{% endif %}</div>{% endfor %}</div>{% endif %}{% endfor %}
<p class="muted small">Listed are the sites that were seen to work through Tor. Others send a visitor to their name on the VPN,
or need it; <code>kiwi-server onion SERVICE.MACHINE --anyway</code> makes one all the same.</p>
<div class="actions"><button class="primary">Save hidden services</button></div></form>
{% endblock %}""",
    "sources.html": """{% extends "base.html" %}{% block main %}
<h1>Module sources</h1><p class="sub">Modules besides the built-in ones, from a git repository of your own or a community one.
Each is held at one version: nothing of it changes until you update it.</p>
{% if said %}<div class="banner ok" style="white-space:pre-line">{{ said }}</div>{% endif %}
{% if error %}<div class="banner bad" style="white-space:pre-line">{{ error }}</div>{% endif %}
{% for s in sources %}{% set new = news.get(s.name) %}<div class="card"><div class="row"><span><b>{{ s.name }}</b>
{% if s.url %}<span class="muted small"> · <a href="{{ s.url }}" rel="noopener" target="_blank">{{ s.url.split("//", 1)[1] }}</a></span>{% endif %}</span>
<span>{% if s.tag %}<span class="badge plain">{{ s.tag }}</span>{% elif s.commit %}<span class="badge plain">commit {{ s.commit[:12] }}</span>{% else %}<span class="badge plain">a folder</span>{% endif %}
{% if new and new.tag and new.commit != s.commit %}<span class="badge warn">{{ new.tag }} is out</span>{% elif new and new.error %}<span class="badge plain" title="{{ new.error }}">could not ask</span>{% elif new %}<span class="badge ok">the newest</span>{% endif %}</span></div>
<p class="muted small">{% if s.modules %}Its modules: {{ s.modules|join(", ") }}{% else %}No modules found in it.{% endif %}{% if not s.url %} A folder on the computer that added it: this
machine does not have it.{% endif %}</p>
<div class="actions">{% if s.url %}<form class="inline" method="post" action="/modules/update">{{ tok }}<input type="hidden" name="name" value="{{ s.name }}">
<button{% if new and new.tag and new.commit != s.commit %} class="primary"{% endif %}>Update{% if new and new.tag and new.commit != s.commit %} to {{ new.tag }}{% endif %}</button></form>{% endif %}
<form class="inline" method="post" action="/modules/remove" onsubmit="return confirm('Remove the module source {{ s.name }}?')">{{ tok }}
<input type="hidden" name="name" value="{{ s.name }}"><button>Remove</button></form></div></div>
{% else %}<div class="card"><p class="muted">None yet: only the built-in modules.</p></div>{% endfor %}
{% if sources %}<p class="small"><a href="/modules?check=1">Look for new versions</a>{% if checked %} · asked just now{% endif %}</p>{% endif %}
<h2>Add a module source</h2>
<form method="post" action="/modules/add" class="card">{{ tok }}
<label for="url">Its link</label><input id="url" name="url" required placeholder="https://github.com/someone/kiwi-modules-community" pattern="https://[A-Za-z0-9.:-]+/[A-Za-z0-9._~/-]+">
<label for="name">Its name here <span class="muted small">(empty: from the link)</span></label><input id="name" name="name" pattern="[a-z0-9][a-z0-9-]{0,39}">
<label for="ref">Version <span class="muted small">(empty: its newest version tag, or its main branch as it is now)</span></label><input id="ref" name="ref" placeholder="v0.1.0" pattern="[A-Za-z0-9][A-Za-z0-9._/-]{0,99}">
<p class="muted small">Its modules' templates and scripts run as root on the machines that use them: add only sources you trust.
Then a machine takes its modules in its settings, like the built-in ones.</p>
<button class="primary">Add it</button></form>
<p class="muted small">In a terminal: <code>kiwi-server sources FLEET</code>. A new source to start from: <code>kiwi-server sources new FOLDER</code>
(<a href="https://kiwi-network.eu/docs/server/modules/" rel="noopener" target="_blank">Modules</a>).</p>
{% endblock %}""",

    "controllers.html": """{% extends "base.html" %}{% block main %}
{% if changed %}<p class="banner ok">The password is changed. Use the new one from now on.</p>{% endif %}
{% if removed %}<p class="banner ok">{{ removed }} is removed: it gets nothing new. If it was lost or stolen, it still knows
the keys it saw: replace them under <a href="#lost">If a computer was lost or stolen</a>.</p>{% endif %}
<h1>Controllers</h1><p class="muted">The computers that can manage this network with the Kiwi Server app. Each has its own key.</p>
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
<h2 id="lost">If a computer was lost or stolen</h2>
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
{% if rot.rings %}<p style="margin-top:18px"><strong>3. The keys that open the disks at boot</strong><br><span class="muted small">
Each ring makes new keys; every disk it opens is bound to them again, in place, and the old keys go when the last
machine has changed. A machine that is off meanwhile still opens with the old ones: switch it on, then press again.
Nothing restarts.</span></p>
{% for r in rot.rings %}<div style="margin:6px 0"><strong>{{ r.name }}</strong> <span class="muted small">·
{% if r.pending %}under way: {{ r.pending|join(", ") }} still on the old keys{% elif r.last %}last replaced {{ r.last[:10] }}{% else %}never replaced{% endif %}</span>
<form class="inline" method="post" action="/run" onsubmit="return confirm('New keys for the ring {{ r.name }}, and every disk it opens bound to them again?')">{{ tok }}
<input type="hidden" name="action" value="rotate-ring"><input type="hidden" name="module" value="{{ r.name }}"><button class="small">Replace</button></form>
{% for n in r.pending %}<form class="inline" method="post" action="/run"
onsubmit="return confirm('Leave {{ n }} out? It opens with the old keys until they go, then only with its key at the console.')">{{ tok }}
<input type="hidden" name="action" value="rotate-ring"><input type="hidden" name="module" value="{{ r.name }}"><input type="hidden" name="host" value="{{ n }}">
<button class="small">Leave {{ n }} out</button></form>{% endfor %}</div>{% endfor %}{% endif %}
<p style="margin-top:18px"><strong>{{ 4 if rot.rings else 3 }}. The password of this page</strong></p>
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
<h1>Activity</h1><p class="muted">What was done here, by whom, newest first. A machine's own History is on its page.</p>
<nav class="tabs small">{% for k, label in show_filters %}<a href="?show={{ k }}"{% if k == show %} class="on"{% endif %}>{{ label }}</a>{% endfor %}</nav>
{% for day, items in days %}<h2>{{ day }}</h2>{% for i in items[:150] %}<div class="card row"><span>
{% if i.job %}<a href="/jobs/{{ i.job }}">{{ i.what }}</a>{% else %}{{ i.what }}{% endif %} {{ i.hosts|join(", ") }}{% if badge(i) %} <span class="state {{ i.state }}">{{ badge(i) }}</span>{% endif %}</span>
<span class="muted small">{{ clock(i) }}{% if i.who %} · {{ i.who }}{% endif %}</span></div>{% endfor %}
{% else %}<p class="muted">Nothing of this kind since this page was last unlocked.</p>{% endfor %}
<div class="actions"><form class="inline" method="post" action="/lock"
onsubmit="return confirm('Lock the network\\'s keys? Nothing is checked or done by itself until the next login.')">{{ tok }}<button>Lock the keys now</button></form></div>
{% endblock %}""",

    "joined.html": """{% extends "base.html" %}{% block main %}
<h1>This machine joins {{ network }}</h1>
<div class="banner ok">The network's control page has taken it in and sets it up now.</div>
<p>Nothing more to do here. In a few minutes this page goes away, and the machine shows up on the control page of
your network, under Machines.</p>
{% endblock %}""",
    # the network's website for strangers (site.py): its pages from the pinned repository, the services
    # and the sign-up from the network itself
    "site.html": """{% extends "base.html" %}{% block title %}{{ site.name or brand_name or "Kiwi" }}{% if pg and pg.slug != home %}: {{ pg.title }}{% endif %}{% endblock %}{% block main %}
<style>{% if site.accent %}:root{--accent:{{ site.accent }}}{% endif %}
.prose{line-height:1.65}.prose>:first-child{margin-top:0}.prose pre{overflow:auto;background:var(--hover);padding:12px;border-radius:10px}
.svcs{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px;margin:10px 0 18px}.svcs .card{margin:0;display:flex;flex-direction:column;gap:6px}
.svcs .card p{margin:0;flex:1}</style>
{% if pg and pg.slug == home %}
<div class="row" style="margin-top:8px"><h1 style="margin:0">{{ site.tagline or pg.title }}</h1></div>
<div class="actions" style="margin:14px 0 18px">{% if join_url %}<a class="btn primary" href="{{ join_url }}">Get an account</a>{% endif %}
<a class="btn" href="/services">The services</a><a class="btn" href="{{ status_url }}">Is everything working?</a></div>
<div class="card prose">{{ pg.html|safe }}</div>
{% elif pg %}<div class="card prose" style="margin-top:8px">{{ pg.html|safe }}</div>{% endif %}
{% if not pg or pg.slug == home %}<h2>Services</h2><p class="sub">What is open to everyone, and whether it works right now.</p>
{% if h.services %}<div class="svcs">{% for s in h.services %}<div class="card"><div class="row"><b>{{ s.label }}</b><span class="badge {{ "ok" if s.ok else "bad" }}">{{ "Up" if s.ok else "Down" }}</span></div>
{% if s.about %}<p class="muted small">{{ s.about }}</p>{% else %}<p></p>{% endif %}<a class="small" href="https://{{ s.site }}" rel="noopener">{{ s.site }}</a></div>{% endfor %}</div>
{% else %}<p class="muted">No service is open to the Internet yet.</p>{% endif %}
{% if join_url %}<div class="card"><div class="row"><div><b>Want an account?</b><p class="muted" style="margin:4px 0 0">One account for every service that needs one.</p></div>
<a class="btn primary" href="{{ join_url }}">Get an account</a></div></div>{% endif %}{% endif %}
<p class="muted small" style="margin-top:24px">{% if contact %}Contact: {{ contact }} · {% endif %}{% if cdn %}Images come through {{ cdn }}, which sees your address for them. · {% endif %}Made with Kiwi Network</p>
{% endblock %}""",
    "status.html": """{% extends "base.html" %}{% block title %}{{ (brand_name if h.strangers else "") or "Kiwi" }}: {{ one.name if one else "status" }}{% endblock %}{% block main %}
<meta http-equiv="refresh" content="120;url=?auto=1"><style>main{max-width:1120px}.tbl td:first-child{min-width:190px}
.cta{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.about{color:var(--muted);font-size:.86rem}</style>
<div class="row" style="margin-top:8px"><h1 style="display:flex;align-items:center;gap:10px;margin:0"><span style="width:26px;height:26px;display:inline-flex">{{ icon("kiwi") }}</span>{% if one %}{{ one.name }}{% else %}{{ (brand_name if h.strangers else "") or "Kiwi" }}{% endif %}</h1>
<div class="cta">{% if one %}<a class="btn" href="/">The whole network</a>{% endif %}{% if play_host %}<a class="btn" href="https://{{ play_host }}/">Game servers</a>{% endif %}
{% if h.join and (not h.strangers or h.join_public) %}<a class="btn primary" href="{{ h.join }}">Get an account</a>{% endif %}</div></div>
<p class="sub">{% if one %}{% set w = {"ok": "This machine answers.", "warn": "This machine answers, and something on it wants a look.", "bad": "This machine answers, and something on it does not work.", "down": "This machine does not answer."} %}{{ w.get(one.state, "This machine has not been asked yet.") }}
Its services, asked from the network itself.{% else %}{% if h.strangers and about_text %}{{ about_text }} {% endif %}{{ "The services that are open to the Internet, asked the way you reach them." if h.strangers else "Every service of the network, asked from the network itself." }}{% endif %}{% if h.checked %}
<span class="muted small">Checked {{ age(now - h.checked) }} ago, every ten minutes.</span>{% endif %}</p>
{% set down = h.services|rejectattr("ok")|list %}{% set off = (h.machines.of or 0) - (h.machines.on or 0) %}
{% if not h.checked %}<div class="banner warn">Nothing has been checked yet.</div>
{% elif stale %}<div class="banner warn">Not checked for {{ age(now - h.checked) }}. The machine that checks is off, or waits for
the people who run this network to unlock it.</div>
{% elif down %}<div class="banner bad">{{ down|length }} {{ "service does" if down|length == 1 else "services do" }} not answer</div>
{% elif off > 0 and not one %}<div class="banner warn">The services answer. {{ off }} {{ "machine does" if off == 1 else "machines do" }} not.</div>
{% else %}<div class="banner ok">Everything works</div>{% endif %}
{% if h.services %}{% set known = h.services|selectattr("uptime", "ne", none)|map(attribute="uptime")|list %}
<div class="stats"><div class="stat ok"><span class="lbl">Up</span><b>{{ h.services|selectattr("ok")|list|length }}</b></div>
<div class="stat {{ "bad" if down else "" }}"><span class="lbl">Down</span><b>{{ down|length }}</b></div>
<div class="stat"><span class="lbl">Worked, 90 days</span><b>{% if known %}{{ "%.2f"|format(known|sum / known|length) }}<small> %</small>{% else %}–{% endif %}</b></div>
{% if h.machines.of and not one %}<div class="stat {{ "warn" if off > 0 else "" }}"><span class="lbl">Machines</span><b>{{ h.machines.on }}<small> of {{ h.machines.of }}</small></b></div>{% endif %}</div>
<table class="tbl"><thead><tr><th>Service</th><th>Now</th><th class="wide">The last 90 days</th><th>Worked</th><th class="wide">Answers in</th></tr></thead><tbody>
{% for s in h.services %}<tr><td><b>{{ s.label }}</b>{% if s.about %}<br><span class="about">{{ s.about }}</span>{% endif %}<br><a class="sm" href="https://{{ s.site }}" rel="noopener">{{ s.site }}</a></td>
<td><span class="badge {{ "ok" if s.ok else "bad" }}">{{ "Up" if s.ok else "Down" }}</span><br><span class="sm">for {{ age(now - s.since) }}</span></td>
<td class="wide"><div class="spark" role="img" aria-label="{{ s.label }}: each of the last 90 days">{% for b in s.bars %}<span class="{% if b.pct is none %}none{% elif b.pct >= 99.9 %}up{% elif b.pct >= 99 %}dip{% elif b.pct >= 90 %}part{% else %}down{% endif %}" style="height:{{ 100 if b.pct is none or b.pct >= 99.9 else [b.pct, 25]|max|int }}%" title="{{ b.day }}: {% if b.pct is none %}not checked{% else %}{{ "%.1f"|format(b.pct) }} % worked{% if b.ms is not none %}, {{ b.ms }} ms{% endif %}{% endif %}"></span>{% endfor %}</div></td>
<td>{% if s.uptime is none %}<span class="sm">not yet</span>{% else %}<span class="badge plain {{ "ok" if s.uptime >= 99 else "warn" if s.uptime >= 90 else "bad" }}">{{ "%.2f"|format(s.uptime) if s.uptime < 100 else "100" }} %</span>{% endif %}</td>
<td class="wide">{% if s.ms is not none %}{{ s.ms }} <span class="sm">ms</span>{% else %}<span class="sm">–</span>{% endif %}</td></tr>{% endfor %}</tbody></table>
<p class="muted small">90 days ago on the left, today on the right: green worked all day, yellow had gaps, red was down most of the day.</p>{% endif %}
{% if games %}<h2 id="games">Game servers</h2>
<div class="scroll"><table class="tbl"><thead><tr><th>Server</th><th>Now</th><th class="wide">Where to connect</th></tr></thead><tbody>
{% for g in games %}<tr><td><b>{{ g.title or g.name }}</b>{% if g.title %}<br><span class="sm">{{ g.name }}</span>{% endif %}</td>
<td>{% if g.state == "running" %}<span class="badge ok">Up</span>{% elif g.state == "asleep" %}<span class="badge plain">Asleep</span><br><span class="sm">wakes when you join</span>{% elif g.state == "stopped" %}<span class="badge bad">Stopped</span>{% else %}<span class="badge plain">not known yet</span>{% endif %}</td>
<td class="wide">{% if g.address %}<code class="sm">{{ g.address }}</code>{% else %}<span class="sm">–</span>{% endif %}</td></tr>{% endfor %}</tbody></table></div>
{% if play_host %}<p class="muted small">Start, stop and read the logs on <a href="https://{{ play_host }}/">the game page</a>, with your account.</p>{% endif %}{% endif %}
{% set hidden = h.services|selectattr("onion")|list %}{% if hidden %}<h2 id="tor">Over Tor</h2>
<p class="muted small">Each hidden service is asked through Tor every ten minutes, by the machine it runs on: the way Tor Browser
finds it.{% for site, a in h.onion.items() if site.startswith("status.") %} This page itself: <code>{{ a }}</code>.{% endfor %}</p>
<div class="scroll"><table class="tbl"><thead><tr><th>Service</th><th class="wide">Its Tor address</th><th>Through Tor</th></tr></thead><tbody>
{% for s in hidden %}{% set t = tor.get(s.onion) %}<tr><td><b>{{ s.label }}</b><br><code class="sm" style="overflow-wrap:anywhere">{{ s.onion }}</code></td>
<td class="wide"><a class="sm" href="#" onclick="navigator.clipboard.writeText('http://{{ s.onion }}');this.textContent='copied';return false">copy the address</a></td>
<td>{% if not t %}<span class="badge plain">not asked yet</span>{% elif t.ok %}<span class="badge ok">Answers</span>{% else %}<span class="badge bad">No answer</span>{% endif %}
{% if t and t.at %}<br><span class="sm">{{ age(now - t.at) }} ago</span>{% endif %}</td></tr>{% endfor %}</tbody></table></div>{% endif %}
{% if h.strangers and h.services %}<div class="grid3">
<div class="card" style="margin:0"><b>Run by people, not by a company</b><p class="muted small">No tracking, no ads, no selling of what you do here.
The services are free software.</p></div>
{% if h.onion %}<div class="card" style="margin:0"><b>Over Tor too</b><p class="muted small">Every service with a Tor address opens in Tor Browser,
without anyone seeing who you are or where you are.</p></div>{% endif %}
{% if h.join and h.join_public %}<div class="card" style="margin:0"><b>One account for everything</b><p class="muted small">With an invite code,
or by asking. No email address is needed. <a href="{{ h.join }}">Get one</a>.</p></div>{% endif %}</div>
<style>.grid3{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;margin-top:22px}</style>{% endif %}
{% if h.checked %}<h2>What was wrong, the last seven days</h2>
{% for i in h.incidents %}<div class="card"><div class="row"><b>{{ i.text }}</b><span class="badge {{ "ok" if i.to else "bad" }}">{{ "fine again" if i.to else "still" }}</span></div>
<span class="muted small">{{ i.when }} · {% if i.to %}for {{ age(i.to - i.from) }}{% else %}for {{ age(now - i.from) }} so far{% endif %}</span></div>
{% else %}<p class="muted">Nothing was wrong.</p>{% endfor %}{% endif %}
{% if h.window %}<p class="muted small">When updates need it, the machines restart: {{ h.window }}.</p>{% endif %}
{% if not h.strangers %}<p class="muted small">On a phone or laptop that is not on the VPN yet, these addresses do not open: ask the people
who run this network to add it{% if h.onion %}, or use the Tor addresses with Tor Browser{% endif %}.</p>{% endif %}
<p class="muted small" style="margin-top:28px">{% if h.strangers %}This page shows the services that are open to the Internet,
asked the way you reach them, and nothing else.{% else %}This page needs no login. It shows which services work, and no admin pages or
settings. The people who run this network see more on their control page.{% if mark %} The network's mark: <code>{{ mark }}</code>,
the same as an app shows for it.{% endif %}{% endif %}{% if contact %} Contact: {{ contact }}.{% endif %}</p>
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
{% if people %}<div class="scroll"><table class="tbl"><thead><tr><th>Name</th><th class="wide">Shown as</th><th class="wide">Jabber</th>{% if play_group %}<th>Game page</th>{% endif %}<th title="An admin of Forgejo, Nextcloud and Immich, from the next sign-in there">Admin</th><th></th></tr></thead><tbody>
{% for p in people %}<tr><td><b>{{ p.name }}</b></td><td class="wide">{{ p.display }}</td><td class="wide">{{ "yes" if p.jabber else "–" }}</td>
{% if play_group %}<td><form class="inline" method="post" action="/accounts/games">{{ tok }}<input type="hidden" name="name" value="{{ p.name }}">
<input type="hidden" name="note" value="{{ 'off' if p.games else 'on' }}"><button title="Members of {{ play_group }} start and stop the game servers at the game page">{{ "In · leave out" if p.games else "Let in" }}</button></form></td>{% endif %}
<td><form class="inline" method="post" action="/accounts/admins"{% if not p.admin %} onsubmit="return confirm('Make {{ p.name }} an admin of Forgejo, Nextcloud and Immich? It takes effect at the next sign-in there.')"{% endif %}>{{ tok }}
<input type="hidden" name="name" value="{{ p.name }}"><input type="hidden" name="note" value="{{ 'off' if p.admin else 'on' }}">
<button title="An admin of Forgejo, Nextcloud and Immich, from the next sign-in there">{{ "Admin · take back" if p.admin else "Make admin" }}</button></form></td>
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

    "play.html": """{% extends "base.html" %}{% block title %}Kiwi: game servers{% endblock %}{% block main %}
<style>main{max-width:980px}.who{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}
.who form{display:inline}.acts form{display:inline}.acts button{margin:2px 4px 2px 0}pre{white-space:pre-wrap;max-height:70vh;overflow:auto}</style>
{% if refresh %}<meta http-equiv="refresh" content="{{ refresh }}">{% endif %}
<div class="who"><h1>Game servers</h1>{% if person %}<span class="muted">{{ person }} ·
<form method="post" action="out">{{ tok }}<button>Sign out</button></form></span>{% endif %}</div>
{% if error %}<div class="banner bad">{{ error }}</div>{% endif %}
{% if not ready %}<p>{{ why }}</p>
{% elif not person %}<p>Start a game server when you want to play, stop it when you are done, and read what it says. With your
account of the network{% if group %}, for the people in the group <b>{{ group }}</b>{% endif %}.</p>
<p><a class="btn primary" href="signin">Sign in</a></p>
{% elif log is not none %}<p><a href="./">← Game servers</a></p>
<h2>{{ title }}</h2><p class="muted small">{{ "still running" if running else "done" }}</p><pre>{{ log }}</pre>
{% else %}<p class="muted">An asleep server wakes for the first player who knocks, or with <em>Start</em>; one stopped here stays
stopped until someone starts it. Your clicks are written down with your name.</p>
{% if rows %}<div class="scroll"><table class="tbl"><thead><tr><th>Server</th><th>Where to connect</th><th>Now</th><th></th></tr></thead><tbody>
{% for r in rows %}<tr><td><b>{{ r.name }}</b>{% if r.title %}<br><span class="sm">{{ r.title }} · {{ r.machine }}</span>{% else %}<br><span class="sm">{{ r.machine }}</span>{% endif %}</td>
<td>{% if r.address %}<code>{{ r.address }}</code>{% else %}<span class="sm">–</span>{% endif %}</td>
<td><span class="badge {{ {'running': 'ok', 'stopped': 'bad'}.get(r.state, '') }}">{{ r.state }}</span></td>
<td class="acts">{% for act, label in (("start", "Start"), ("stop", "Stop"), ("restart", "Restart"), ("logs", "Log")) %}
{% if (act == "start" and r.state != "running") or (act in ("stop", "restart") and r.state == "running") or act == "logs" %}
<form method="post" action="do"{% if act == "stop" %} onsubmit="return confirm('Stop {{ r.name }}? Whoever plays on it is thrown out.')"{% endif %}>{{ tok }}
<input type="hidden" name="machine" value="{{ r.machine }}"><input type="hidden" name="container" value="{{ r.container }}">
<input type="hidden" name="action" value="{{ act }}"><button{% if act == "start" %} class="primary"{% endif %}>{{ label }}</button></form>{% endif %}{% endfor %}</td></tr>
{% endfor %}</tbody></table></div>{% else %}<p class="muted">No game servers yet: they come with the games part of a machine.</p>{% endif %}
{% if did %}<p class="muted small">Asked. <a href="log?job={{ did }}">What it said</a>. The state here follows within a few minutes.</p>{% endif %}
{% endif %}{% endblock %}""",

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
<dt>…bring something back that broke?</dt><dd><a href="/recovery">Recovery</a> says what happens by
itself and what to press: for a service's data, a lost machine, the master, a lost phone or computer. It also says
now whether you could.</dd>
<dt>…add a machine?</dt><dd>On <a href="/">Home</a>, under <em>Your machines</em>: <em>Add a machine</em>.</dd>
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
comes back from the copy the fleet keeps of it; <a href="/recovery">Recovery</a> says whether there is one.
{% else %}It has no backups: after the set-up it starts empty.{% endif %}</p>
{% if "backup" in m.modules and m.state != "down" %}<div class="card"><p><strong>First: save its data.</strong>
{{ m.name }} still answers. Installed anew, it gets back what its last backup holds{% if m.facts.backup and m.facts.backup.age is not none %},
and that one is {{ age(m.facts.backup.age) }} old{% endif %}. What changed since then would be gone.</p>
<form class="inline" method="post" action="/run">{{ tok }}<input type="hidden" name="action" value="save">
<input type="hidden" name="host" value="{{ m.name }}"><button class="primary">Save its data now</button></form></div>{% endif %}
{{ step(dict(m, phase="build" if m.provider or not m.media else "install"), tok) }}</details>{% endif %}"""

# if something breaks: the report of `kiwi-server recovery`, its checks and its cases
TEMPLATES["breaks.html"] = """{% extends "base.html" %}{% block main %}
<p><a href="/">← Home</a></p><h1>Recovery</h1>
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


# what a person can do about a problem (facts.ACTIONS): a job for this machine as a button, a page as a link
TEMPLATES["acts.html"] = """{% macro acts(m, p, tok) %}{% set list = p.actions if p.actions else
([{"label": "Unlock it", "run": "unlock"}] if "disk is locked" in p.text|lower else []) %}{% if list %}<div class="actions">
{% for a in list %}{% if a.run %}<form class="inline" method="post" action="/run"{% if a.run == "apply" %}
onsubmit="return confirm('Apply to {{ m.name }} now? Services that changed restart for a moment.')"{% elif a.run == "switch" %}
onsubmit="return confirm('{{ a.label }} now? It goes on with the copy there; when {{ m.name }} is back it gives the part up.')"{% endif %}>{{ tok }}
<input type="hidden" name="action" value="{{ a.run }}"><input type="hidden" name="host" value="{{ m.name }}">
{% if a.part %}<input type="hidden" name="module" value="{{ a.part }}">{% endif %}
<button{% if loop.first %} class="primary"{% endif %}>{{ a.label }}</button></form>
{% elif a.page and a.page.startswith("/") and not a.page.startswith("//") %}<a class="btn" href="{{ a.page }}">{{ a.label }}</a>{% endif %}{% endfor %}
</div>{% endif %}{% endmacro %}"""

TEMPLATES["unlock.html"] = """{% extends "base.html" %}{% block main %}
<p><a href="/">← Home</a></p><h1>Disk unlock</h1>
<p class="muted">Who opens whom: how each machine's disk opens at boot, with which rings, and whether it comes
back by itself after a power cut.</p>
{% if error %}<p class="banner bad">{{ error }}</p>{% endif %}
{% for m in machines %}<div class="card"><p><strong>{{ m.name }}</strong> · {{ m.way }}{% if m.rings %}, with {{ m.threshold }} of
{{ m.rings|length }} {{ "ring" if m.rings|length == 1 else "rings" }}{% endif %}</p>
{% if m.rings %}<form class="inline" method="post" action="/unlock/{{ m.name }}/opens">{{ tok }}<input type="hidden"
name="opens" value="{{ 'by-a-person' if m.auto else 'by-itself' }}"><button>{{ "Switch auto-unlock off" if m.auto else
"Switch auto-unlock on" }}</button></form> <span class="muted small">an apply of {{ m.name }}: its rings are
{{ "unbound; it waits for its key or Unlock" if m.auto else "bound again" }}</span>
<ul class="small">{% for r in m.rings %}<li>{{ r.ring }} on {{ r.server or "nobody" }}{% if r.path != "the LAN" %}:
{{ r.path }}{% endif %}</li>{% endfor %}</ul>
<p class="small"><span class="dot {{ 'warn' if m.power_cut == 'waits' or m.needs else 'ok' }}"></span>After a power cut:
{% if m.power_cut == "waits" %}it waits for a person.{% elif m.needs %}it opens, as long as {{ m.needs|join(" and ") }} {{ "starts" if m.needs|length == 1 else "start" }} too.
{% else %}it opens, also with one of its rings' machines off.{% endif %}</p>{% endif %}</div>{% endfor %}
<p class="muted small">A ring answers anyone who reaches it: for a machine that is off or stolen, switching
auto-unlock off is no protection; <em>New keys</em> for its rings is (kiwi-server rotate ring). Change a machine's rings
in its Settings (Disk unlock). In a terminal:
<code>kiwi-server unlock &lt;fleet&gt;</code>.</p>
{% endblock %}"""


# what the pages call things
KIND = {"master": "Master", "node": "Home server (node)", "node-gw": "Home server and gateway",
        "node-cloud": "Home server", "bare": "Bare machine"}
WORDS = {"ok": "fine", "warn": "wants a look", "bad": "has a problem", "down": "not reachable", "unknown": "not checked yet"}
JOBWORD = {"waiting": "waiting", "running": "running", "ok": "done", "failed": "did not work"}
FRESH = ("enroll", "build", "install", "invalid", "bare")      # a machine's phases before it is set up
VERB = timeline.DOING        # what a task does, while it runs (timeline: also what it did)
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
    return (util.count(s, "second") if s < 90 else util.count(s // 60, "minute") if s < 5400
            else util.count(s // 3600, "hour") if s < 172800 else util.count(s // 86400, "day"))


def ago(t):
    return age(time.time() - t)


# the menu: (address, words, picture); the first three are the phone's bar, the rest its "More"
# (address, label, picture, group): the first three stand alone and are the phone's bar; the
# rest come under the word of their group
NAV = (("/", "Home", "home", ""), ("/machines", "Machines", "server", ""), ("/services", "Services", "grid", ""),
       ("/devices", "Devices", "phone", "Access"), ("/accounts", "Accounts", "people", "Access"),
       ("/access", "Access rules", "shield", "Access"),
       ("/federation", "Linked networks", "link", "Access"),
       ("/backups", "Backups", "box", "Safety"), ("/recovery", "Recovery", "buoy", "Safety"),
       ("/unlock", "Disk unlock", "lock", "Safety"),
       ("/controllers", "Controllers", "screen", "Safety"),
       ("/activity", "Activity", "pulse", "More"), ("/settings", "Settings", "sliders", "More"),
       ("/modules", "Module sources", "stack", "More"), ("/help", "Help", "help", "More"))
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
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
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


def machine_chips(m, mine):
    """One look at a machine, part by part (after Foreman's sub-statuses): set up or not, how its
    last apply ended, its backup, its fullest disk. The app shows the same (its machine_chips)."""
    out = []
    if m.get("phase") and m["phase"] != "applied":
        out.append({"text": "not set up yet", "level": "warn"})
    last = next((e for e in mine if e.get("result") and e.get("what") in ("apply", "update", "setup", "restore")), None)
    if last:
        when = ""
        try:
            when = " %s ago" % age(time.time() - time.mktime(time.strptime(last["time"][:19], "%Y-%m-%dT%H:%M:%S")))
        except (ValueError, KeyError):
            pass
        out.append({"text": "last %s: %s%s" % ({"update": "apply"}.get(last["what"], last["what"]),
                                                JOBWORD.get(last["result"], last["result"]), when),
                    "level": "ok" if last["result"] == "ok" else "bad"})
    b = (m.get("facts") or {}).get("backup")
    if "backup" in (m.get("modules") or []):
        if b and b.get("last") and b.get("age") is not None:
            out.append({"text": "backup %s ago" % age(b["age"]), "level": "ok" if b["age"] < 129600 else "bad"})
        else:
            out.append({"text": "no backup seen yet", "level": "warn"})
    disks = [x for x in m.get("bars") or [] if "disk" in str(x.get("label", "")).lower()]
    if disks:
        d = max(disks, key=lambda x: x.get("pct") or 0)
        out.append({"text": "disk %s %%" % d.get("pct"), "level": d.get("level") or "ok"})
    return out


def tags(modules, facts=None):
    """What a machine is for the others, from its parts; and the site's moving address while it holds it."""
    out = [word for part, word in (("control", "control node"), ("gateway", "gateway"), ("backup-server", "keeps backups"))
           if part in (modules or [])]
    vip = (facts or {}).get("vip") or {}
    if vip.get("holds"):
        out.append("holds %s" % vip.get("address"))
    return out


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


def picture(machines, devices=0, per_row=4, linked=()):
    """The network as a family tree (SVG; the app draws the same, both from picture.layout): the
    masters on top, this network's with its standbys and the networks linked to it, the machines
    under it in a zone per site, a dotted line from each linked network to what it may reach here.
    Pointing at a machine or a linked network shows its lines and dims the rest. `per_row=1` is
    the narrow one for a phone: a list with a branch to each machine."""
    from . import picture as picmod
    masters = [m for m in machines if m["role"] == "master" and not m.get("standby_of")]
    rest = [m for m in machines if m["role"] and m["role"] != "master"]
    if not masters or not rest:
        return ""
    esc = markupsafe.escape
    master = masters[0]
    cap = lambda m: ", ".join(tags(m["modules"])[:2]) or ("master" if m["role"] == "master" else "node")   # noqa: E731
    mcap = "master" + (" · %d %s on its VPN" % (devices, "device" if devices == 1 else "devices") if devices else "")
    standbys = [dict(name=m["name"], state=m["state"], cap="standby: takes over when %s is gone" % master["name"])
                for m in machines if m.get("standby_of") == master["name"]]
    said = (master.get("facts") or {}).get("opened") or {}        # what each opens to us, as its DNS told the master
    masters_of = (master.get("facts") or {}).get("partner_masters") or {}
    nets = [dict(name=n["name"], state="", reach=list(n.get("reach") or []), theirs=said.get(n["name"]),
                 cap="linked network · %s%s%s" % (masters_of.get(n["name"]) or n.get("mesh") or "", " · its way out" if n.get("exit") else "",
                                                  " · opens nothing to you" if said.get(n["name"]) == [] else ""))
            for n in linked or []]
    others = [m for m in masters[1:]] + rest
    rows = [dict(name=m["name"], state=m["state"], cap=cap(m), site=m.get("site") or "home", up=m["state"] in ("ok", "warn", "bad"))
            for m in others]
    lay = picmod.layout(dict(name=master["name"], state=master["state"], cap=mcap), rows, standbys, nets,
                        width=340 if per_row == 1 else picmod.fit_width(rows, nets), narrow=per_row == 1)
    allhosts = " ".join(b["name"] for b in lay["boxes"] if b["kind"] != "linked")
    allnets = " ".join(n["name"] for n in nets)
    shares = {n["name"]: n["reach"] for n in nets}
    out = []
    for z in lay["zones"]:
        out.append('<rect class="zone%s" x="%d" y="%d" width="%d" height="%d" rx="14"/><text class="zl" x="%d" y="%d">%s</text>'
                   % (" away" if z["away"] else " fz" if z.get("partner") else "", z["x"], z["y"], z["w"], z["h"], z.get("lx") or z["x"] + 14,
                      z["y"] + 18, esc(z["label"])))
    for ln in lay["lines"]:
        d = " ".join(p[0] + " ".join("%.1f" % v for v in p[1:]) for p in ln["d"])
        cls = ln["kind"] + ("" if ln["up"] is None else " up" if ln["up"] else " off")
        out.append('<path class="x %s" data-h="%s" data-n="%s" d="%s"/>' % (cls, esc(" ".join(ln["hosts"])), esc(" ".join(ln["nets"])), d))
    for b in lay["boxes"]:
        kind = b["kind"]
        cls = {"master": "m", "standby": "sb", "linked": "ln", "partner": "pt"}.get(kind, "")
        href = "/federation" if kind in ("linked", "partner") else "/hosts/%s" % esc(b["name"])
        cy = b["y"] + b["h"] // 2
        dot = ('<circle class="ring %s" cx="%d" cy="%d" r="9"/><circle class="%s" cx="%d" cy="%d" r="5"/>'
               % (esc(b["state"]), b["x"] + 20, cy, esc(b["state"]), b["x"] + 20, cy)
               if kind not in ("linked", "partner") else '<circle class="lk" cx="%d" cy="%d" r="5"/>' % (b["x"] + 20, cy))
        if kind == "partner":
            title = "%s, in %s — opened to you: %s" % (b["name"], b["net"], b["cap"])
        elif kind == "linked":
            what = shares.get(b["name"]) or []
            title = "%s — %s. May reach here: %s" % (b["name"], b["cap"], ", ".join(what) if what else "nothing")
        else:
            title = "%s — %s%s" % (b["name"], b["cap"], ". Reached from: " + ", ".join(b["reached"]) if b["reached"] else "")
        chips, cx = [], b["x"] + b["w"] - 8
        for net in b["reached"] if kind not in ("linked", "partner") else []:
            cw = 10 + 6 * len(net)
            cx -= cw
            chips.append('<rect class="chip" x="%d" y="%d" width="%d" height="15" rx="7.5"/><text class="chip" x="%d" y="%d">%s</text>'
                         % (cx, b["y"] - 7, cw, cx + 5, b["y"] + 4, esc(net)))
            cx -= 4
        h = allhosts if kind == "master" else " ".join(b["hosts"])
        n = b["name"] if kind == "linked" else b["net"] if kind == "partner" else allnets if kind == "master" else " ".join(b["reached"])
        out.append('<g class="x" data-k="%s" data-h="%s" data-n="%s"><a href="%s"><title>%s</title><rect %sx="%d" y="%d" width="%d" '
                   'height="%d" rx="12"/>%s<text x="%d" y="%d">%s</text><text class="c" x="%d" y="%d">%s</text>%s</a></g>'
                   % (kind, esc(h), esc(n), href, esc(title), 'class="%s" ' % cls if cls else "", b["x"], b["y"], b["w"], b["h"], dot,
                      b["x"] + 36, cy - 4, esc(b["name"]), b["x"] + 36, cy + 13, esc(b["cap"]), "".join(chips)))
    return markupsafe.Markup('<svg viewBox="0 0 %d %d" style="max-width:%dpx" role="img" aria-label="Your network: the masters on top, '
                             'the machines under them, and what the linked networks may reach">%s</svg>%s'
                             % (lay["w"], lay["h"], lay["w"], "".join(out), PICTURE_JS))


# pointing at a machine or a linked network: its lines stand out, the rest steps back
PICTURE_JS = """<script>(function(){document.querySelectorAll('.pic svg').forEach(function(s){if(s.dataset.hov)return;s.dataset.hov=1;
var xs=s.querySelectorAll('.x');function sp(e,a){return (e.getAttribute(a)||'').split(' ').filter(Boolean)}
function any(a,b){return a.some(function(v){return b.indexOf(v)>=0})}
function on(g){var k=g.getAttribute('data-k'),h=sp(g,'data-h'),n=sp(g,'data-n');s.classList.add('focus');
xs.forEach(function(o){var r=(k==='linked'||k==='partner')?any(sp(o,'data-n'),n):(any(sp(o,'data-h'),h)||(o.getAttribute('data-k')==='linked'&&any(sp(o,'data-n'),n)));
o.classList.toggle('hi',r)})}
function off(){s.classList.remove('focus');xs.forEach(function(o){o.classList.remove('hi')})}
s.querySelectorAll('g.x').forEach(function(g){g.addEventListener('mouseenter',function(){on(g)});g.addEventListener('mouseleave',off);
g.addEventListener('focusin',function(){on(g)});g.addEventListener('focusout',off)})})})()</script>"""

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


# the Android app that belongs to a service, where there is one (free software, on F-Droid where it
# is there): an app on the phone opens that instead of the web page. By module; later each module
# can name its own
ANDROID_APPS = {"cloud": "com.nextcloud.client", "vault": "com.x8bit.bitwarden", "immich": "app.alextran.immich",
                "xmpp": "eu.siacs.conversations", "paperless": "de.astubenbord.paperless_mobile",
                "authenticator": "", "git": "", "paste": ""}


def ca_mark(pem):
    """The CA's mark: the first eight hex digits of its certificate's SHA-256, as an invite link ends
    with. A person compares it with what an app shows before trusting that app's network."""
    return hashlib.sha256(pem.encode()).hexdigest()[:8] if pem else ""


def tiles(machines):
    out = []
    for m in machines:
        for site in m["sites"]:
            first = site.split(".", 1)[0]
            out.append({"site": site, "label": healthmod.label(site), "machine": m["name"],
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
    from .facts import QUIETABLE
    env.globals.update(quietable=QUIETABLE)

    def parts(names):
        """A machine's parts by one name each (docs/design/ui.md): the module's title first, its
        technical name small after it: "Ad blocker (Pi-hole) dns"."""
        try:
            titles = {m["name"]: m.get("title") for m in (control.schema() or {}).get("modules") or []}
        except (KiwiError, OSError, ValueError, KeyError):
            titles = {}
        esc = markupsafe.escape
        return markupsafe.Markup(", ".join(
            '%s <code class="sm">%s</code>' % (esc(titles[n]), esc(n)) if titles.get(n) and titles[n] != n else str(esc(n))
            for n in names or []))
    env.filters["parts"] = parts

    def ptitle(name):
        """A part by its title, as plain words: "Passwords (Vaultwarden)" for vault."""
        try:
            return next((m.get("title") or name for m in (control.schema() or {}).get("modules") or [] if m["name"] == name), name)
        except (KiwiError, OSError, ValueError, KeyError):
            return name
    env.globals.update(ptitle=ptitle)
    env.filters["slug"] = machtable.slug
    env.globals.update(toggled=machtable.toggled, stword=lambda k: machtable.WORDS.get(k, k), level=lambda k: machtable.LEVEL.get(k, "plain"))
    env.globals.update(brand_name=os.environ.get("KIWI_BRAND", ""), brand_accent=re.sub(r"[^#0-9a-fA-F]", "", os.environ.get("KIWI_ACCENT", ""))[:7],
                       about_text=os.environ.get("KIWI_ABOUT", ""), contact=os.environ.get("KIWI_CONTACT", ""))
    env.globals.update(version=VERSION, ago=ago, age=age, hint=hint, words=lambda s: WORDS.get(s, s),
                       kind=lambda r, standby_of="": ("Standby of %s" % standby_of) if standby_of else KIND.get(r, r), verb=lambda a: VERB.get(a, a), jobword=lambda s: JOBWORD.get(s, s),
                       to_text=formmod.to_text, icon=icon, size=size, navitems=NAV, yesno=lambda v: "" if v is None else "yes" if v else "no")
    app = FastAPI(title="Kiwi control", version=VERSION, docs_url=None, redoc_url=None)
    PASSIVE = ("/setup/hoster/", "/machines/hoster/", "/static/", "/favicon", "/api/v1/events")
    V1_OWN = ("state", "ack", "events")      # /api/v1/ routes with no older name

    @app.middleware("http")
    async def api_v1(request: Request, call_next):
        """Every /api/NAME answers at /api/v1/NAME too (ui.md step 5): the versioned names for the
        app and the phone, without a second copy of each route."""
        path = request.scope.get("path") or ""
        if path.startswith("/api/v1/") and path[8:].split("/", 1)[0] not in V1_OWN:
            request.scope["path"] = "/api/" + path[8:]
            request.scope["raw_path"] = request.scope["path"].encode()
        return await call_next(request)

    @app.middleware("http")
    async def keep_session(request: Request, call_next):
        """A page used: the login lasts another SESSION_IDLE_MINUTES. The page's own reloads
        (?auto=1) and polls do not count, or a tab left open would never end its login."""
        response = await call_next(request)
        tok = request.cookies.get(COOKIE)
        if tok and "auto" not in request.query_params and not request.url.path.startswith(PASSIVE) \
                and COOKIE not in (response.headers.get("set-cookie") or ""):
            new = control.refresh(tok)
            if new and new != tok:
                secure = request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https"
                response.set_cookie(COOKIE, new, httponly=True, samesite="strict", secure=secure, max_age=12 * 3600)
        return response

    def page(template, request=None, nav=None, **kw):
        # tok: the hidden field every form of a logged-in page carries (see posted())
        tok = ""
        if request is not None:
            tok = markupsafe.Markup('<input type="hidden" name="csrf" value="%s">'
                                    % control.csrf(request.cookies.get(COOKIE)))
        kw.setdefault("alone", alone())
        if kw.get("live"):
            kw["mark"] = control.changes()
        frame = ({"counts": {}, "total": 0, "domain": ""} if kw.get("open_frame") else shell_state()) if nav else None
        return HTMLResponse(env.get_template(template).render(tok=tok, nav=nav, shell=frame, **kw))

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
        return util.client_address(request)

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
        waits = control.waiting()
        out = []
        for h in info.get("hosts") or []:
            s = said.get(h["name"])
            out.append({"name": h["name"], "hostname": h["hostname"], "role": h["role"], "standby_of": h.get("standby_of") or "",
                        "site": h.get("site") or "home", "modules": h.get("modules") or [],
                        "sites": h.get("sites") or [], "vpn_ip": h.get("vpn_ip") or "", "next": h.get("next") or "",
                        "phase": h.get("phase") or "", "provider": h.get("provider") or "", "address": h.get("address") or "",
                        "here": bool(h.get("here")), "wol": bool(h.get("wol")),
                        "locked": bool((s or {}).get("locked")), "stage": (s or {}).get("stage") or "",
                        "waiting": h["name"] in waits,
                        "keep_ready": h.get("keep_ready") or {}, "applied": h.get("applied") or 0,
                        # what its stop does to its parts (policy.py): one sentence, and one per part
                        "stops": h.get("stops") or "", "parts_table": h.get("parts_table") or [],
                        "runbook": h.get("runbook") or [],
                        "story": (s or {}).get("story") or "", "seen": (s or {}).get("seen") or (st.get("time") if s else 0) or 0,
                        "media": (control.media(h["name"]) or (None, 0))[1], "on_done": h.get("on_done") or "poweroff",
                        "known": bool(s), "state": (s or {}).get("state") or "unknown",
                        "problems": (s or {}).get("problems") or [], "notes": (s or {}).get("notes") or [],
                        "facts": (s or {}).get("facts") or {}, "output": (s or {}).get("output") or ""})
        ready = all(m["phase"] == "applied" for m in out if m["role"] == "master")
        for m in out:
            m["master_ready"] = ready
            m["bars"], m["tags"] = bars(m["facts"]), tags(m["modules"], m["facts"])
            m["chips"] = [SERVICES.get(x.split(".", 1)[0], x.split(".", 1)[0].capitalize()) for x in m["sites"]]
        for name, s in said.items():            # a look at a machine the fleet file no longer names
            if name not in [m["name"] for m in out]:
                out.append({"name": name, "hostname": name, "role": "", "standby_of": "", "modules": [], "sites": [], "vpn_ip": "",
                            "next": "", "phase": "", "provider": "", "address": "", "media": 0, "on_done": "", "master_ready": True, "here": False, "known": True, "bars": bars(s.get("facts") or {}), "tags": [], "chips": [], "state": s.get("state") or "unknown",
                            "problems": s.get("problems") or [], "notes": s.get("notes") or [],
                            "facts": s.get("facts") or {}, "output": s.get("output") or ""})
        return out, st, info

    def tor_checks():
        """The hidden services as each machine's onion part last asked them through Tor: address -> answer."""
        out = {}
        for h in (control.status() or {}).get("hosts") or []:
            for o in (h.get("facts") or {}).get("onion") or []:
                out[o["address"]] = o
        return out

    def games():
        """The network's game servers as their machines last said: for the status page on the VPN."""
        return [g for g in playmod.servers(control.fleet_info(), control.status()) if g["game"]]

    @app.get("/status")
    @app.get("/start")
    def status_page(machine: str = ""):
        """How the network is, for everyone on it: no login, every service people use, what it is for and
        whether it works, the game servers, and how to get an account. status.<host> answers with it.
        `?machine=NAME`: one machine's part of it, where its own name (https://NAME.<domain>) leads."""
        now = time.time()
        h = healthmod.public(control.health(), now)
        h["services"] = [s for s in h["services"] if not s["site"].startswith("status.")]
        found, one = games(), None
        if machine and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", machine):
            mine = {s["site"] for s in h["services"] if s.get("host") == machine}
            h["services"] = [s for s in h["services"] if s["site"] in mine]
            h["incidents"] = [i for i in h["incidents"] if i.get("key") in ["machine:" + machine] + ["service:" + x for x in mine]]
            said = next((x for x in (control.status() or {}).get("hosts") or [] if x.get("host") == machine), None)
            one = {"name": machine, "state": (said or {}).get("state") or ("down" if said and not said.get("ok") else "unknown")}
            found = [g for g in found if g["machine"] == machine]
        return page("status.html", h=h, now=now, tor=tor_checks(), games=found, play_host=PLAY_HOST, one=one,
                    mark=ca_mark(control.ca_pem() or ""), stale=bool(h["checked"]) and now - h["checked"] > 45 * 60,
                    **status_frame(h, play=PLAY_HOST, games=bool(found)))

    def registry(request, strangers=False, machine=""):
        """The network as a person on it uses it, as data for an app (kiwi-app's user side): its name,
        every service with what it is for, where it is and whether it works, the game servers, how to
        get an account, and where the status page and the network's CA are. No login, no admin page,
        no setting: what the status page shows. `machine`: one machine's part."""
        now = time.time()
        h = healthmod.public(control.health(), now, strangers=strangers)
        found = [] if strangers else games()
        one = None
        if machine and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", machine) and not strangers:
            said = next((x for x in (control.status() or {}).get("hosts") or [] if x.get("host") == machine), None)
            one = {"name": machine, "state": (said or {}).get("state") or "unknown"}
            h["services"] = [x for x in h["services"] if x.get("host") == machine]
            found = [g for g in found if g["machine"] == machine]
        info = control.fleet_info() or {}
        base = "https://%s/" % (request.headers.get("host") or "").split(",")[0].strip()
        return {"kind": "kiwi-registry", "version": 1, "network": (os.environ.get("KIWI_BRAND") if strangers else "") or "Kiwi",
                "domain": "" if strangers else info.get("domain") or "", "checked": h.get("checked") or 0,
                "status": base, "machine": one,
                "join": h.get("join") if (not strangers or h.get("join_public")) else "",
                "play": ("https://%s/" % PLAY_HOST) if PLAY_HOST and not strangers else "",
                "ca": "" if strangers else base + "api/ca",
                "ca_mark": "" if strangers else ca_mark(control.ca_pem() or ""),
                "services": [{"label": x["label"], "about": x.get("about") or "", "url": "https://%s/" % x["site"], "ok": bool(x["ok"]),
                              "uptime": x.get("uptime"), "onion": x.get("onion") or "",
                              "android": ANDROID_APPS.get(SITE_MODULE.get(x["site"].split(".", 1)[0], ""), "")}
                             for x in h["services"] if not x["site"].startswith("status.")],
                "games": [{"name": g["name"], "title": g["title"], "address": g["address"], "state": g["state"]} for g in found]}

    @app.get("/api/registry")
    def api_registry(request: Request, machine: str = ""):
        return registry(request, machine=machine)

    @app.get("/api/registry/public")
    def api_registry_public(request: Request):
        r = registry(request, strangers=True)
        h = healthmod.public(control.health(), time.time(), strangers=True)
        join = h.get("join") or ""
        r["join"] = join if join and not join.split("/")[2].endswith("." + str((control.fleet_info() or {}).get("domain") or "home")) else ""
        return r

    def status_frame(h, strangers=False, play="", games=False):
        """The status page in the control page's frame: itself, the website, the sign-up, the game page."""
        items = [("/", "Status", "pulse", "")]
        if SITE_HOST and strangers:
            items.append(("https://%s/" % SITE_HOST, "Website", "home", ""))
        if h.get("join") and (not strangers or h.get("join_public")):
            items.append((h["join"], "Get an account", "people", ""))
        if play and games:
            items.append(("https://%s/" % play, "Game servers", "grid", ""))
        name = (os.environ.get("KIWI_BRAND") if strangers else "") or "Kiwi"
        return {"nav": "/", "navitems": items, "open_frame": {"name": name, "home": "/"}}

    @app.get("/status/public")
    @app.get("/start/public")
    def status_public():
        """The same for the Internet (the control module's public_status_hostname): the services that are
        open to it, whether they work, what was wrong with them, and how to get an account. Nothing else."""
        now = time.time()
        h = healthmod.public(control.health(), now, strangers=True)
        h["services"] = [s for s in h["services"] if not s["site"].startswith("status.")]
        h["join_public"] = bool(h.get("join")) and not h["join"].split("/")[2].endswith("." + str((control.fleet_info() or {}).get("domain") or "home"))
        return page("status.html", h=h, now=now, tor=tor_checks(), games=[], play_host="",
                    stale=bool(h["checked"]) and now - h["checked"] > 45 * 60, **status_frame(h, strangers=True))

    # ---- the website (site.py): at its name and www., where only the site's pages answer ------------
    SITE_HOST, SITE_REPO, SITE_COMMIT = sitemod.settings()
    SITE_CDN = sitemod.cdn() if SITE_HOST else ""
    SITE = {}

    def the_site():
        """The site at its pin: fetched once, kept; None while it cannot be had (said in the log)."""
        if "got" not in SITE or SITE_COMMIT == "dir":       # a folder being written: read anew each time
            try:
                SITE["got"] = sitemod.fetch(SITE_REPO, SITE_COMMIT, os.path.join(control.run, "site"),   # fetched again after a restart
                                            sitemod.assets_base(SITE_CDN))
            except (KiwiError, OSError) as e:
                util.warn("the website %s at %s: %s" % (SITE_REPO, SITE_COMMIT[:12], e))
                return None
        return SITE["got"]

    SITE_ICONS = {"home": "home", "join": "people", "terms": "shield", "rules": "shield", "privacy": "shield", "about": "help"}

    def site_nav(site, home, status_url):
        """The website's menu in the control page's frame: its pages, the services, the status page."""
        items = [("/" if p["slug"] == home else "/" + p["slug"], p["title"], SITE_ICONS.get(p["slug"], "link"), "") for p in site["pages"]]
        return items[:1] + [("/services", "Services", "grid", "")] + items[1:] + [(status_url, "Status", "pulse", "")]

    def site_answer(request):
        path = request.url.path
        if path == "/status":
            h = healthmod.public(control.health(), time.time(), strangers=True)
            there = next(("https://%s/" % x["site"] for x in h["services"] if x["site"].startswith("status.")), "")
            return RedirectResponse(there, status_code=302) if there else status_public()
        if path in ("/api/registry", "/.well-known/kiwi"):
            return JSONResponse(registry(request, True))
        site = the_site()
        if site is None:
            return status_public()          # the front page there is, until the site can be had
        if path.startswith("/assets/"):
            found = sitemod.asset(site, path[len("/assets/"):])
            if not found:
                return Response("Not here.", status_code=404, media_type="text/plain")
            with open(found[0], "rb") as fh:       # a pinned commit's file never changes: kept a year when asked with ?v=
                keep = "public, max-age=31536000, immutable" if request.query_params.get("v") else "public, max-age=86400"
                return Response(fh.read(), media_type=found[1], headers={"Cache-Control": keep})
        home = site["pages"][0]["slug"] if site["pages"] else ""
        slug = path.strip("/") or home
        pg = next((p for p in site["pages"] if p["slug"] == slug), None)
        if pg is None and slug != "services":
            return Response("Not here.", status_code=404, media_type="text/plain")
        now = time.time()
        h = healthmod.public(control.health(), now, strangers=True)
        status_url = next(("https://%s/" % x["site"] for x in h["services"] if x["site"].startswith("status.")), "/status")
        h["services"] = [x for x in h["services"] if not x["site"].startswith("status.") and x["site"] not in sitemod.hosts(SITE_HOST)]
        h["join_public"] = bool(h.get("join")) and not h["join"].split("/")[2].endswith(
            "." + str((control.fleet_info() or {}).get("domain") or "home"))
        join_url = h["join"] if h.get("join") and h["join_public"] else ("/join" if any(p["slug"] == "join" for p in site["pages"]) else "")
        return page("site.html", nav="/" + ("" if not pg or pg["slug"] == home else pg["slug"]) if pg else "/services",
                    navitems=site_nav(site, home, status_url), open_frame={"name": site["name"] or os.environ.get("KIWI_BRAND") or "Kiwi",
                                                                         "home": "/"},
                    site=site, pg=pg, home=home, h=h, status_url=status_url, join_url=join_url, contact=os.environ.get("KIWI_CONTACT", ""),
                    cdn=SITE_CDN if any("<img " in p["html"] for p in site["pages"]) else "")

    @app.middleware("http")
    async def site_only(request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0].lower()
        if SITE_CDN and host == SITE_CDN:               # a CDN's name: the site's files and nothing else
            if request.method not in ("GET", "HEAD") or not request.url.path.startswith("/assets/") or "/.." in request.url.path:
                return Response("Not here.", status_code=404, media_type="text/plain")
            return site_answer(request)
        if SITE_HOST and host in sitemod.hosts(SITE_HOST):
            if "/.." in request.url.path or "//" in request.url.path or request.method not in ("GET", "HEAD"):
                return Response("Not here.", status_code=404, media_type="text/plain")
            if host == "www." + SITE_HOST:        # one address for the site: www. leads to the bare name
                return RedirectResponse("https://%s%s" % (SITE_HOST, request.url.path), status_code=301)
            return site_answer(request)
        return await call_next(request)

    # ---- the game page (play.py): at play.<this node>, where nothing but /play/ answers ----------
    PLAY_HOST, PLAY_GROUP, PLAY_EXTRA = playmod.settings()

    @app.middleware("http")
    async def play_only(request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0].lower()
        path = request.url.path
        if PLAY_HOST and host == PLAY_HOST.lower() and (not path.startswith("/play/") or "/.." in path or "//" in path):
            return Response("Not here.", status_code=404, media_type="text/plain")
        return await call_next(request)

    def play_person(request):
        return playmod.who(control._session_key, request.cookies.get(playmod.COOKIE))

    def play_page(request, **kw):
        tok = request.cookies.get(playmod.COOKIE) or ""
        kw.setdefault("person", play_person(request))
        kw.setdefault("group", PLAY_GROUP)
        ready, why = True, ""
        if not PLAY_HOST:
            ready, why = False, "The game page is off (the control part's setting play_page)."
        elif control.locked():
            ready, why = False, "The network's control node waits for its admin after a restart. Try again later."
        elif not control.play_client():
            ready, why = False, ("The game page is not connected to the network's accounts yet: kiwi-server accounts "
                                 "<fleet> connect %s control" % (os.environ.get("KIWI_CONTROL_HOST") or "<this node>"))
        kw.update(ready=ready, why=why, log=kw.get("log"))
        if ready and kw["person"] and "rows" not in kw and kw["log"] is None:
            kw["rows"] = playmod.servers(control.fleet_info(), control.status(), PLAY_EXTRA)
        form = markupsafe.Markup('<input type="hidden" name="csrf" value="%s">' % playmod.csrf(control._session_key, tok))
        # in the control page's frame, for the people of the network's accounts: the game servers only
        r = HTMLResponse(env.get_template("play.html").render(
            tok=form, nav="/play/", navitems=[("/play/", "Game servers", "grid", "")],
            open_frame={"name": os.environ.get("KIWI_BRAND") or "Kiwi", "home": "/play/"},
            shell={"counts": {}, "total": 0, "domain": ""}, alone="", **kw))
        r.headers["Cache-Control"] = "no-store"
        return r

    def play_back():
        return "https://%s/back" % PLAY_HOST

    def play_secure(request):
        return request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https"

    @app.get("/play/")
    def play_home(request: Request, did: str = ""):
        return play_page(request, did=did if controlmod_job.match(did or "") else "")

    @app.get("/play/signin")
    def play_signin(request: Request):
        c = control.play_client()
        if not PLAY_HOST or not c:
            return play_page(request)
        try:
            where, state = playmod.begin(control._session_key, c["client"], control.play_conf(c["issuer"]), play_back())
        except KiwiError as e:
            return play_page(request, error=str(e))
        r = RedirectResponse(where, status_code=303)
        r.set_cookie(playmod.STATE_COOKIE, state, httponly=True, samesite="lax", secure=play_secure(request), max_age=600)
        return r

    @app.get("/play/back")
    def play_return(request: Request, code: str = "", state: str = "", error: str = ""):
        c = control.play_client()
        if not PLAY_HOST or not c:
            return play_page(request)
        verifier = playmod.verifier_of(control._session_key, request.cookies.get(playmod.STATE_COOKIE), state)
        if error or not code or not verifier:
            return play_page(request, error="The sign-in did not finish%s. Try again." % (" (%s)" % error[:80] if error else ""))
        try:
            person = playmod.finish(control.play_conf(c["issuer"]), c["client"], c["secret"], code, verifier, play_back(),
                                    control.play_ca(), PLAY_GROUP)
        except KiwiError as e:
            return play_page(request, error=str(e))
        control.audit("play:" + person, "signed in on the game page", address=address(request))
        r = RedirectResponse("./", status_code=303)
        r.set_cookie(playmod.COOKIE, playmod.token(control._session_key, person), httponly=True, samesite="lax",
                     secure=play_secure(request), max_age=playmod.HOURS * 3600)
        r.delete_cookie(playmod.STATE_COOKIE)
        return r

    @app.post("/play/do")
    def play_do(request: Request, machine: str = Form(""), container: str = Form(""), action: str = Form(""), csrf: str = Form("")):
        person = play_person(request)
        tok = request.cookies.get(playmod.COOKIE) or ""
        if not person or not hmac.compare_digest(playmod.csrf(control._session_key, tok), csrf or ""):
            return RedirectResponse("./", status_code=303)
        try:
            jid = control.play_start(person, machine, container, action, PLAY_EXTRA)
        except KiwiError as e:
            return play_page(request, error=str(e))
        return RedirectResponse(("log?job=" if action == "logs" else "./?did=") + jid, status_code=303)

    @app.get("/play/log")
    def play_log(request: Request, job: str = ""):
        person = play_person(request)
        j = control.job(job) if person and controlmod_job.match(job or "") else None
        if not j or j.get("action") != "service" or not str(j.get("user") or "").startswith("play:"):
            return RedirectResponse("./", status_code=303)
        text, _off = control.log(job)
        running = j.get("state") in ("waiting", "running")
        return play_page(request, log=text, running=running, refresh=2 if running else 0,
                         title="%s, asked by %s" % ((j.get("hosts") or ["?"])[0], j["user"][5:]))

    @app.post("/play/out")
    def play_out(request: Request, csrf: str = Form("")):
        r = RedirectResponse("./", status_code=303)
        r.delete_cookie(playmod.COOKIE)
        return r

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
            m.pop("download_dir", None)         # an old answer: a gateway has no downloads since 2.306.2
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
            await asyncio.sleep(1)
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
        """A starter, taken into a network that exists: its control node proves it has the code from this
        machine's screen (without sending it) and sends the key it will come in with (Control.join_as_starter)."""
        try:
            body = await request.json()
        except ValueError:
            body = {}
        try:
            out = await run_in_threadpool(control.join_as_starter, str(body.get("nonce") or ""), str(body.get("mac") or ""),
                                          str(body.get("key") or ""),
                                          str(body.get("network") or ""), address(request))
        except KiwiError as e:
            await asyncio.sleep(1)
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
            await asyncio.sleep(1)
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
                                            address(request), fields.get("unsigned") == "1")
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
        return page("index.html", request, "/", live=True, machines=ms, tiles=with_logins(tiles(ms), _info), looked=st.get("time"),
                    changed=[n for n in (_info or {}).get("changed") or [] if n != os.environ.get("KIWI_CONTROL_HOST")]
                            + [n for n in (_info or {}).get("changed") or [] if n == os.environ.get("KIWI_CONTROL_HOST")],
                    stats=stats(ms, devs), wide=picture(ms, len(devs), 4, (_info or {}).get("linked")),
                    narrow=picture(ms, len(devs), 1, (_info or {}).get("linked")), silent=silent, now=time.time(),
                    error=st.get("error"), looking=control.looking, presets=setupmod.PRESETS, gone=control.gone(),
                    add_error=request.query_params.get("add_error"), emergencies=control.emergencies())

    @app.get("/ack")
    def ack_page(request: Request, key: str = ""):
        """The link in an emergency message: Home, where the emergency has its "I have it" button."""
        return RedirectResponse("/#emergencies", status_code=303)

    @app.post("/ack")
    def ack_do(request: Request, key: str = Form(""), csrf: str = Form("")):
        who = posted(request, csrf)
        if not who:
            return RedirectResponse("/", status_code=303)
        try:
            control.ack(key, who)
        except KiwiError:
            pass
        return RedirectResponse("/", status_code=303)

    @app.post("/api/v1/ack")
    async def api_ack(request: Request):
        """A computer or the app has it (kiwi-server control ack)."""
        who = bearer(request)
        if not who:
            return JSONResponse({"error": "who?"}, status_code=401)
        try:
            body = await request.json()
            await run_in_threadpool(control.ack, str(body.get("key") or ""), who)
        except (ValueError, AttributeError, KiwiError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return {"ok": True}

    @app.get("/machines")
    def machines_page(request: Request):
        if not user(request):
            return login_page()
        ms, st, _info = machines()
        from . import setup as setupmod
        try:
            titles = {m["name"]: m.get("title") for m in (control.schema() or {}).get("modules") or []}
        except (KiwiError, OSError, ValueError, KeyError):
            titles = {}
        for m in ms:
            m["kind"] = KIND.get(m["role"], m["role"])
            m["part_titles"] = [titles.get(n) or n for n in m["modules"]]
        t = machtable.table(ms, request.query_params.get("q") or "", titles)
        return page("machines.html", request, "/machines", live=True, machines=ms, t=t, fresh=machtable.FRESH,
                    looked=st.get("time"), looking=control.looking,
                    presets=setupmod.PRESETS, add_error=request.query_params.get("add_error"),
                    take_error=request.query_params.get("take_error"), master_error=request.query_params.get("master_error"),
                    hosters={p.id: p.name for p in providersmod.PROVIDERS.values()})

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
        return page("services.html", request, "/services", live=True, by_machine=list(by.items()), everything=everything, now=now,
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

    @app.post("/machines/hoster")
    async def machines_hoster(request: Request):
        """"Add a master" at a hoster: the device login, for someone logged in (hoster_start)."""
        try:
            body = await request.json()
        except ValueError:
            body = {}
        u = posted(request, str(body.get("csrf") or ""))
        if not u:
            return JSONResponse({"error": "log in"}, status_code=401)
        try:
            handle = await run_in_threadpool(control.hoster_start, str(body.get("provider") or ""), "", address(request), u)
        except KiwiError as e:
            return JSONResponse({"error": str(e)}, status_code=403)
        return JSONResponse({"handle": handle}, headers={"Cache-Control": "no-store"})

    @app.get("/machines/hoster/{handle}")
    def machines_hoster_state(request: Request, handle: str):
        s = control.hoster_state(handle) if user(request) else None
        if s is None:
            return JSONResponse({"error": "no such sign-in"}, status_code=404)
        return JSONResponse(s, headers={"Cache-Control": "no-store"})

    @app.post("/machines/add-master")
    def machines_add_master(request: Request, address: str = Form(""), name: str = Form(""), csrf: str = Form(""),
                            hoster: str = Form(""), provider_server: str = Form("")):
        u = posted(request, csrf)
        if not u:
            return login_page()
        try:
            made, _moved = control.add_master(address, name, user=u, hoster=hoster, server=provider_server)
        except KiwiError as e:
            return RedirectResponse("/machines?" + urllib.parse.urlencode({"master_error": str(e)}), status_code=303)
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
        tab = request.query_params.get("tab", "overview")
        tab = tab if tab in ("overview", "services", "history", "backups") else "overview"
        mine = [e for e in control.audit_tail(600) if name in (e.get("hosts") or [])]
        jobs = [j for j in control.jobs(40) if name in (j.get("hosts") or [])][:10]
        standby = [l for l in control.standby() if l.get("on") == name] if tab == "services" and m["keep_ready"] else []
        own = control.own_history(name) if tab == "history" else None     # what the machine itself remembers
        show = request.query_params.get("show", "all")
        show = show if show in dict(timeline.FILTERS) else "all"
        days = timeline.by_day(timeline.keep(timeline.merge(own=own, jobs=jobs, log=mine), show)) if tab == "history" else []
        return page("host.html", request, "/", live=True, m=m, tiles=tiles([m]), logins=logins_of(_info, name), tab=tab,
                    chips=machine_chips(m, mine), history=mine[:60], jobs=jobs, standby=standby, own=own,
                    days=days, show=show, show_filters=timeline.FILTERS, badge=timeline.badge, clock=timeline.clock,
                    targets=same_kind(m, ms) if tab == "services" else [], fresh=machtable.FRESH)

    def same_kind(m, ms):
        """The machines a part of this one can go to: set up, of its kind, not a standby master."""
        return [x["name"] for x in ms if x["name"] != m["name"] and x["role"] == m["role"] and not x["standby_of"]
                and x["phase"] not in machtable.FRESH]

    @app.get("/hosts/{name}/move")
    def move_plan_page(request: Request, name: str, part: str = "", to: str = ""):
        """What moving a part of this machine to another would do, before anything is done."""
        if not user(request):
            return login_page()
        try:
            plan = control.move_plan(part, name, to)
        except KiwiError as e:
            plan = {"part": part, "name": "", "steps": [], "notes": [], "stops": [str(e)]}
        return page("move.html", request, "/machines", plan=plan, src=name, dst=to, error=request.query_params.get("error"))

    @app.post("/hosts/{name}/move")
    def move_do(request: Request, name: str, part: str = Form(""), to: str = Form(""), csrf: str = Form("")):
        u = posted(request, csrf)
        if not u:
            return login_page()
        try:
            jid = control.start("move", [name, to], user=u, module=part)
        except KiwiError as e:
            return RedirectResponse("/hosts/%s/move?part=%s&to=%s&error=%s" % (name, urllib.parse.quote(part), urllib.parse.quote(to),
                                                                                urllib.parse.quote(str(e))), status_code=303)
        return RedirectResponse("/jobs/" + jid, status_code=303)

    @app.post("/hosts/{name}/keep-ready")
    def keep_ready_do(request: Request, name: str, part: str = Form(""), to: str = Form(""), csrf: str = Form("")):
        """Another machine keeps a part of this one ready: into the fleet file, then both are applied."""
        u = posted(request, csrf)
        if not u:
            return login_page()
        ms, _st, _info = machines()
        m = next((x for x in ms if x["name"] == name), None)
        try:
            if not m or to not in same_kind(m, ms) or part not in m["modules"]:
                raise KiwiError("which part of %s, kept ready on which machine?" % name)
            control.save_settings(name, {"keep_ready": dict(m["keep_ready"], **{part: to})}, user=u)
            jid = control.start("apply", [name, to], user=u)
        except KiwiError as e:
            return RedirectResponse("/hosts/%s/move?part=%s&to=%s&error=%s" % (name, urllib.parse.quote(part), urllib.parse.quote(to),
                                                                                urllib.parse.quote(str(e))), status_code=303)
        return RedirectResponse("/jobs/" + jid, status_code=303)

    @app.post("/hosts/{name}/switch")
    def switch_do(request: Request, name: str, part: str = Form(""), csrf: str = Form("")):
        u = posted(request, csrf)
        if not u:
            return login_page()
        try:
            jid = control.start("switch", [name], user=u, module=part)
        except KiwiError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return RedirectResponse("/jobs/" + jid, status_code=303)

    @app.get("/api/hosts/{name}/logins")
    def api_logins(request: Request, name: str):
        """A machine's logins with their passwords, for "Show password" on its page: the one
        place a page hands a password out. The audit says that it did, with the machine's
        name and nothing of what was shown. A controller (kiwi-app, with its token) may ask too: the
        audit then names the controller."""
        u = user(request) or bearer(request)
        if not u:
            return JSONResponse({"error": "log in"}, status_code=401)
        try:
            found = control.logins(name)
        except KiwiError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        control.audit(u, "logins shown", hosts=[name])
        return JSONResponse({"host": name, "logins": found}, headers={"Cache-Control": "no-store"})

    @app.post("/hosts/{name}/script")
    async def host_script(request: Request, name: str):
        """The role script, as plain text: like "Show password", in the audit by name only. A POST:
        making it may make the machine's missing secrets (certificates and the like)."""
        form = await request.form()
        u = posted(request, str(form.get("csrf") or ""))
        if not u:
            return login_page()
        try:
            text = await run_in_threadpool(control.role_script, name)
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
                    only=bool(host) and request.query_params.get("only") == "set",
                    open=[g["id"] for g in groups if any(f["name"] in touched for f in g["fields"])],
                    same="as inherited" if host else "the usual")

    def settings_change(host, groups, typed, secrets, u, apply=False):
        """Values against the form's fields (form.changes): only what changed is written; with `apply`,
        this machine (or, for the defaults, every one) and every machine the change reaches are applied
        as a job. For the page's Save and Save & apply, and for a controller. -> {saved, wrong, error,
        job, touched}."""
        changes, wrong = formmod.changes(groups, dict(typed, **secrets))
        done = {"saved": 0, "wrong": wrong, "error": "", "job": "",
                "touched": set(wrong) | {".".join(path) for path in changes}}
        if wrong:
            return done
        try:
            done["saved"] = control.save_settings(host, changes, u)
        except KiwiError as e:
            done["error"] = "Not saved: %s" % e
            return done
        if apply:
            # this machine (or, for the defaults, every one) and every other machine the change
            # reaches: those whose role script is another now (`list`'s changed)
            info = control.fleet_info(fresh=True) or {}
            names = [h["name"] for h in info.get("hosts") or [] if h.get("phase") == "applied"]
            todo = ([host] if host else names) + [n for n in info.get("changed") or [] if n != host]
            todo = [n for n in dict.fromkeys(todo) if n in names]
            if todo:
                try:
                    done["job"] = control.start("apply", todo, user=u)
                except KiwiError as e:
                    done["error"] = "Saved, not applied: %s" % e
        return done

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
                elif sent.get("reset:" + name) and f["set"]:     # back to what every machine has
                    typed[name] = ""
                elif "f:" + name in sent:
                    typed[name] = str(sent.get("f:" + name)).replace("\r\n", "\n")
        done = settings_change(host, groups, typed, secrets, u, apply=str(sent.get("then") or "") == "apply")
        if done["wrong"]:
            return settings_page(request, host, typed, done["wrong"], "Not saved: %s" % next(iter(done["wrong"].values())), done["touched"])
        if done["error"]:
            return settings_page(request, host, typed, None, done["error"], done["touched"])
        if done["job"]:
            return RedirectResponse("/jobs/" + done["job"], status_code=303)
        keys = done["saved"]
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

    def api_fields(groups):
        """The form's groups for a controller: a secret field says only whether it is set."""
        out = []
        for g in groups:
            g = dict(g)
            g["fields"] = [dict(f, value=None, inherited=None) if f.get("secret") else f for f in g.get("fields") or []]
            out.append(g)
        return out

    @app.get("/api/settings")
    def api_settings(request: Request, host: str = ""):
        """The settings form of one machine (?host=NAME) or of the defaults: its groups and fields, as the
        page shows them (name, type, label, value, inherited, set, options, help …). Secrets never."""
        if not bearer(request) and not user(request):
            return JSONResponse({"error": "who?"}, status_code=401)
        try:
            got = control.settings(host or None)
        except KiwiError as e:
            return JSONResponse({"error": str(e)}, status_code=404 if "no such" in str(e) or "not a host" in str(e) else 409)
        return JSONResponse({"host": got["host"], "groups": api_fields(got["groups"])}, headers={"Cache-Control": "no-store"})

    @app.post("/api/settings")
    async def api_settings_do(request: Request):
        """{host, values: {field: value}, clear: [secret fields], apply} -> {saved, job}, or 400 with
        {error, wrong: {field: why}}. A value of "" puts a field back to what every machine has; a
        module list is a list of names. Only what changed is written, in the controller's name."""
        who = bearer(request)
        if not who:
            return JSONResponse({"error": "who?"}, status_code=401)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("values") or {}, dict):
            return JSONResponse({"error": "a JSON object: {host, values, clear, apply}"}, status_code=400)
        host = str(body.get("host") or "") or None
        try:
            groups = control.settings(host)["groups"]
        except KiwiError as e:
            return JSONResponse({"error": str(e)}, status_code=404 if "no such" in str(e) or "not a host" in str(e) else 409)
        fields = {f["name"]: f for g in groups for f in g["fields"]}
        values = body.get("values") or {}
        unknown = [k for k in list(values) + list(body.get("clear") or []) if k not in fields]
        if unknown:
            return JSONResponse({"error": "no such setting: %s" % unknown[0], "wrong": {k: "no such setting" for k in unknown}},
                                status_code=400)
        typed, secrets = {}, {}
        for k, v in values.items():
            f = fields[k]
            if f["type"] == "modules":
                typed[k] = [str(x) for x in v] if isinstance(v, list) else None
                if typed[k] is None:
                    return JSONResponse({"error": "%s: a list of module names" % k, "wrong": {k: "a list"}}, status_code=400)
            elif f["secret"]:
                if str(v or "").strip():
                    secrets[k] = str(v)
            elif isinstance(v, bool):
                typed[k] = "yes" if v else "no"
            else:
                typed[k] = "" if v is None else str(v)
        for k in body.get("clear") or []:
            if fields[k]["secret"]:
                secrets[k] = None
        done = await run_in_threadpool(settings_change, host, groups, typed, secrets, who, body.get("apply") is True)
        if done["wrong"] or (done["error"] and not done["saved"]):
            return JSONResponse({"error": done["error"] or "Not saved: %s" % next(iter(done["wrong"].values())),
                                 "wrong": done["wrong"]}, status_code=400)
        return {"saved": done["saved"], "job": done["job"] or None, "error": done["error"] or None}

    @app.post("/hosts/{name}/quiet")
    def host_quiet(request: Request, name: str, key: str = Form(""), csrf: str = Form("")):
        """A warning this machine leaves out from now on (its setting `quiet`): it stays as a note."""
        u = posted(request, csrf)
        if not u:
            return login_page()
        from .facts import QUIETABLE
        if key not in QUIETABLE:
            return RedirectResponse("/hosts/%s" % name, status_code=303)
        try:
            doc = formmod.Doc.parse(util.read_text(os.path.realpath(control.fleet_path)))
            now = list(doc.get(name, ("quiet",)) or [])
            if key not in now:
                control.save_settings(name, {("quiet",): now + [key]}, u)
            # gone from the pages at once: the next look says the same by itself
            st = control.status() or {}
            for h in st.get("hosts") or []:
                if h.get("host") == name:
                    gone = [p for p in h.get("problems") or [] if p.get("key") == key and p.get("level") == "warn"]
                    h["problems"] = [p for p in h.get("problems") or [] if p not in gone]
                    h["notes"] = list(h.get("notes") or []) + ["left out on purpose: " + p["text"] for p in gone]
                    levels = {p.get("level") for p in h["problems"]}
                    h["state"] = "bad" if "bad" in levels else "warn" if "warn" in levels else "ok"
            if st:
                control._put(os.path.join(control.run, "status.json"), json.dumps(st))
        except KiwiError:
            pass
        return RedirectResponse("/hosts/%s" % name, status_code=303)

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
            jid = control.start(action, [host] if host else [], user=u, at=at if action in ("setup", "restore", "service") else "",
                                module=module if action in ("restore", "service", "rotate-ring", "switch") else "")
        except KiwiError as e:
            if action == "backup":              # said where the button is
                ms, _st, _info = machines()
                return page("backups.html", request, "/backups", machines=ms, error="Nothing was saved: %s" % e)
            return JSONResponse({"error": str(e)}, status_code=400)
        return RedirectResponse("/jobs/" + jid, status_code=303)

    @app.post("/run-many")
    async def run_many(request: Request):
        """One action on the ticked machines (after Foreman): check, preview or apply."""
        form = await request.form()
        u = posted(request, str(form.get("csrf") or ""))
        if not u:
            return login_page()
        action = str(form.get("action") or "")
        hosts = list(dict.fromkeys(str(h) for h in form.getlist("host")))[:1000]
        if action not in ("status", "plan", "apply") or not hosts:
            return RedirectResponse("/machines", status_code=303)
        try:
            jid = control.start(action, hosts, user=u)
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
        title = "%s %s" % (VERB.get(j["action"], j["action"]), what if j["action"] in ("status", "plan", "apply", "save", "setup", "build", "restore", "unlock", "wake", "remove", "start-new", "prune", "service", "reboot", "poweroff") else "")
        say = {"waiting": "Waiting for another task to finish…", "running": "Working… this can take a few minutes.",
               "ok": "Done.", "failed": "That did not work. The lines below say why."}
        if j["action"] == "plan":
            say["ok"] = "Done. Nothing was changed; the lines below say what Apply would do."
        if j["action"] == "build":
            say["ok"] = "Done. Go back: the machine's page has the install media and says what to do with them."
        if j["action"] == "setup":
            say["ok"] = "Done. Go back for the next step."
        if j["action"] in ("rotate", "rotate-seed", "rotate-ring"):      # a machine that is off is no failure: it waits
            say["failed"] = ("Not every machine could be changed. The ones that wait are listed under Computers; "
                             "the lines below say why.")
        back = ("/" if j["action"] == "remove" else "/backups" if j["action"] in ("save", "backup", "restore", "start-new", "prune") else "/controllers" if j["action"] in ("rotate", "rotate-seed", "rotate-ring")
                else "/help" if j["action"] == "doctor"
                else "/hosts/" + j["hosts"][0] if j["hosts"] else "/")
        return page("job.html", request, "/activity", job=j, title=title.strip(), say=say, back=back)

    @app.post("/api/jobs")
    async def api_jobs(request: Request):
        """An action as a job, for a computer that manages the network (its key's token): the same
        actions as the page's buttons, so that the app on a computer sends its applies here instead
        of running them itself. {action, hosts, at, module} -> {job}; its log at /api/jobs/{job}/log."""
        who = bearer(request)
        if not who:
            return JSONResponse({"error": "who?"}, status_code=401)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return JSONResponse({"error": "a JSON object: {action, hosts}"}, status_code=400)
        action = str(body.get("action") or "")
        hosts = [str(h) for h in body.get("hosts") or []][:50]
        if action not in ACTIONS:
            return JSONResponse({"error": "unknown action"}, status_code=400)
        at, module = str(body.get("at") or ""), str(body.get("module") or "")
        try:
            jid = control.start(action, hosts, user=who, at=at if action in ("setup", "restore", "service") else "",
                                module=module if action in ("restore", "service", "rotate-ring", "move", "switch") else "")
        except KiwiError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return {"job": jid}

    @app.post("/api/report")
    async def agent_report(request: Request):
        """A machine's agent says how the machine is (machineagent.py). No login: the report is signed
        with the machine's own key, which only the seed gives."""
        from . import machineagent as agentmod
        try:
            size = int(request.headers.get("content-length") or 0)
        except ValueError:
            size = 0
        if size > agentmod.LIMIT:
            return JSONResponse({"error": "too big"}, status_code=413)
        body = await request.body()
        h = request.headers
        why = control.report(h.get("x-kiwi-host") or "", h.get("x-kiwi-time") or "", h.get("x-kiwi-boot") or "",
                             h.get("x-kiwi-sign") or "", body)
        if why:
            return JSONResponse({"error": why}, status_code=503 if "locked" in why else 403)
        return JSONResponse({"ok": True})

    @app.get("/api/jobs/{jid}/log")
    def job_log(request: Request, jid: str, offset: int = 0, text: int = 0, download: int = 0):
        """A job's log: in pieces for the page that follows it, or whole as text (text=1), to read
        at any size or to keep (download=1)."""
        if not user(request) and not bearer(request):
            return JSONResponse({"error": "log in"}, status_code=401)
        j = control.job(jid)
        if not j:
            return JSONResponse({"error": "no such job"}, status_code=404)
        if text:
            whole, _off = control.log(jid, 0)
            head = {"Content-Disposition": 'attachment; filename="kiwi-%s.log"' % jid} if download else {}
            return PlainTextResponse(whole, headers=head)
        text, off = control.log(jid, offset)
        return {"text": text, "offset": off, "state": control.job(jid)["state"]}

    @app.get("/api/status")
    @app.get("/api/v1/status")
    def api_status(request: Request):
        """The last look at the machines: for the page's login and for a controller (the Android app).
        Only reads, and is asked at every refresh: no line in the activity."""
        if not (user(request) or bearer(request)):
            return JSONResponse({"error": "log in"}, status_code=401)
        # with the emergencies nobody has yet: the app shows them with "I have it"
        return dict(control.status() or {}, emergencies=control.emergencies())

    @app.get("/api/machines")
    @app.get("/api/v1/machines")
    def api_machines(request: Request):
        """What the fleet file says of the machines (`list --porcelain`), without the paths on this
        machine that a phone has no use for. {} while the fleet is locked. Only reads, as /api/status."""
        if not (user(request) or bearer(request)):
            return JSONResponse({"error": "log in"}, status_code=401)
        info = control.fleet_info()
        if info is None:
            return {}
        out = {k: v for k, v in info.items() if k not in ("fleet", "output_dir")}
        out["hosts"] = [{k: v for k, v in h.items() if k not in ("dir", "iso")} for h in info.get("hosts") or []]
        if "module_sources" in out:
            out["module_sources"] = [{k: v for k, v in x.items() if k != "path"} for x in out["module_sources"] or []]
        return out

    # ---- devices ------------------------------------------------------------------------
    def devices_page(request, **kw):
        info = control.fleet_info() or {}
        q = request.query_params.get("q") or "" if request.method == "GET" else ""
        return page("devices.html", request, "/devices", devices=control.devices(), t=machtable.device_table(control.device_rows(), q),
                    groups=sorted(info.get("groups") or {}), default_group=info.get("default_group"), **kw)

    @app.get("/devices")
    def devices(request: Request):
        if not user(request):
            return login_page()
        return devices_page(request)

    def accounts_view():
        """What the Accounts page shows, for the page and for a controller: the people, the invite
        codes (never a code itself: shown once when made), the requests, Jabber's; `error` when the
        accounts machine did not answer."""
        available, people, invites, requests, error = control.has_accounts(), [], [], [], None
        if available:
            try:
                for p in control.accounts_read("list") or []:
                    name = (p.get("name") or [""])[0]
                    people.append({"name": name, "display": (p.get("displayname") or [name])[0],
                                   "jabber": any(g.startswith("jabber@") for g in p.get("memberof") or []),
                                   "games": bool(PLAY_HOST) and any(g.startswith(PLAY_GROUP + "@") for g in p.get("memberof") or []),
                                   "admin": any(g.startswith("admins@") for g in p.get("memberof") or [])})
                invites = control.accounts_read("invites") or []
                requests = control.accounts_read("requests") or []
            except KiwiError as e:
                error = str(e)
        jabber = None
        if control.has_jabber_join():
            try:
                jr = control.accounts_read("requests", "--jabber") or []
                jabber = {"waiting": [r for r in jr if r.get("state") == "waiting"], "invites": control.accounts_read("invites", "--jabber") or []}
            except KiwiError as e:
                error = error or str(e)
        return {"available": available, "people": sorted(people, key=lambda p: p["name"]), "invites": invites,
                "waiting": [r for r in requests if r.get("state") == "waiting"], "jabber": jabber, "error": error}

    def accounts_page(request, **kw):
        v = accounts_view()
        error = kw.pop("error", None) or v["error"]
        info = control.fleet_info() or {}
        holder = next((h for h in info.get("hosts") or [] if "accounts" in (h.get("modules") or [])), None) or {}
        id_host = next((s for s, label in (holder.get("site_labels") or {}).items() if label == "Accounts"),
                       "id.%s" % holder.get("hostname", ""))
        now = time.time()
        connectable = [{"host": h["name"], "module": m, "label": label,
                        "connected": os.path.isfile(os.path.join(control.fleet_dir, "secrets", "accounts", "%s-%s.json" % (h["name"], m)))}
                       for h in info.get("hosts") or [] for m, label in (("git", "Forgejo (git)"), ("cloud", "Nextcloud (files)"), ("paperless", "Paperless (documents)"), ("vault", "Vaultwarden (passwords)"), ("immich", "Immich (photos)"), ("control", "Game page (play)"))
                       if m in (h.get("modules") or [])]
        return page("accounts.html", request, "/accounts", available=v["available"], connectable=connectable, jabber=v["jabber"],
                    people=v["people"], play_group=PLAY_GROUP if PLAY_HOST else "",
                    invites=v["invites"], live=[i for i in v["invites"] if i.get("live")], waiting=v["waiting"],
                    id_host=id_host, now=now, error=error, **kw)

    @app.get("/accounts")
    def accounts_get(request: Request):
        if not user(request):
            return login_page()
        return accounts_page(request)

    def accounts_act(what, u, hint="", name="", note="", uses=1, days=14):
        """One of the Accounts page's buttons, for the page and for a controller: {} when done, or what
        to show once (a code, a link: title, value, text, note). KiwiError says why not."""
        if what.startswith("jabber-"):
            op = what[7:]
            if op == "invite":
                code = control.accounts("invite", "--jabber", "--uses", max(1, min(50, uses)), "--days", max(1, min(90, days)),
                                        "--note", note.strip()[:80], user=u)["code"]
                return {"title": "A Jabber invite code", "value": code,
                        "text": "Give it to %s. It makes %d Jabber address%s, for %d days:" % (
                            note.strip() or "the person", uses, "" if uses == 1 else "es", days),
                        "note": "Shown this once. They enter it at /join on Jabber's page, with a name and a password."}
            if op in ("accept", "deny", "revoke"):
                control.accounts(op, hint.strip(), "--jabber", user=u)
                return {}
        if what == "invite":
            code = control.accounts("invite", "--uses", max(1, min(50, uses)), "--days", max(1, min(90, days)),
                                    "--note", note.strip()[:80], user=u)["code"]
            return {"title": "An invite code", "value": code,
                    "text": "Give it to %s. It makes %d account%s, for %d days:" % (
                        note.strip() or "the person", uses, "" if uses == 1 else "s", days),
                    "note": "It is shown this once. They enter it at the sign-up page with a name of their choice."}
        if what in ("accept", "deny", "revoke"):
            control.accounts(what, hint.strip(), user=u)
            return {}
        if what in ("create", "link"):
            answer = control.accounts(what, name.strip().lower(), user=u)
            return {"title": "The account %s" % answer["name"] if what == "create" else "A new password link for %s" % answer["name"],
                    "value": answer["link"], "text": "Hand this link to its person: it sets the password or a passkey.",
                    "note": "It works for a day, once. Nobody else should see it."}
        if what == "delete":
            control.accounts("delete", name.strip().lower(), user=u)
            return {}
        if what == "admins":                        # the network's admins: Forgejo, Nextcloud, Immich
            control.accounts("member", "admins", name.strip().lower(), "off" if note.strip() == "off" else "on", user=u)
            return {}
        if what == "games" and PLAY_HOST:          # who may use the game page: its group
            control.accounts("member", PLAY_GROUP, name.strip().lower(), "off" if note.strip() == "off" else "on", user=u)
            return {}
        if what == "connect":
            labels = {"git": "Forgejo", "cloud": "Nextcloud", "paperless": "Paperless", "vault": "Vaultwarden", "immich": "Immich",
                      "control": "the game page"}
            module = note.strip() if note.strip() in labels else "git"
            label = labels[module]
            control.accounts("connect", name.strip(), module, user=u)
            return {"title": "%s on %s is connected" % (label, name.strip()), "value": "kiwi-server apply %s" % name.strip(),
                    "text": "Apply that machine now (Machines, %s, Apply): then its %s signs people in with these accounts." % (name.strip(), label),
                    "note": "Nothing changes for people who already have a Forgejo user of the same name: they are linked on their first sign-in."}
        raise KiwiError("not a thing to do here")

    @app.post("/accounts/{what}")
    def accounts_do(request: Request, what: str, hint: str = Form(""), name: str = Form(""), note: str = Form(""),
                    uses: int = Form(1), days: int = Form(14), csrf: str = Form("")):
        u = posted(request, csrf)
        if not u:
            return login_page()
        try:
            shown = accounts_act(what, u, hint, name, note, uses, days)
        except KiwiError as e:
            return accounts_page(request, error=str(e))
        return accounts_page(request, shown=shown) if shown else accounts_page(request)

    @app.get("/api/accounts")
    def api_accounts(request: Request):
        """The Accounts page for a controller (the Android app): people, invite codes, requests."""
        if not bearer(request) and not user(request):
            return JSONResponse({"error": "who?"}, status_code=401)
        return accounts_view()

    @app.post("/api/accounts")
    async def api_accounts_do(request: Request):
        """{op, name, hint, note, uses, days} -> {done, shown}: the Accounts page's buttons (invite, accept,
        deny, revoke, create, link, delete, admins, games, connect; jabber-invite, jabber-accept …).
        `shown` is a code or a password link, given this once."""
        who = bearer(request)
        if not who:
            return JSONResponse({"error": "who?"}, status_code=401)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("op"), str):
            return JSONResponse({"error": "a JSON object: {op, name, hint, note, uses, days}"}, status_code=400)
        try:
            uses, days = int(body.get("uses") or 1), int(body.get("days") or 14)
        except (TypeError, ValueError):
            return JSONResponse({"error": "uses and days are numbers"}, status_code=400)
        try:
            shown = accounts_act(body["op"], who, str(body.get("hint") or ""), str(body.get("name") or ""),
                                 str(body.get("note") or ""), uses, days)
        except KiwiError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"done": body["op"], "shown": shown or None}, headers={"Cache-Control": "no-store"})

    @app.post("/devices/{what}")
    def devices_do(request: Request, what: str, name: str = Form(""), group: str = Form(""), csrf: str = Form(""),
                   split: str = Form("")):
        u = posted(request, csrf)
        if not u:
            return login_page()
        name = name.strip().lower()
        try:
            if what == "remove":
                control.remove_device(name, u)
                return devices_page(request, removed=name)
            config = control.add_device(name, group, u, split=split == "yes") if what == "add" else control.device_config(name)
        except KiwiError as e:
            return devices_page(request, error=str(e))
        if not config:
            return devices_page(request, error="There is no device called %s." % name)
        if what == "download":          # the file itself, as a download: works in every browser, no script
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or "device"
            return Response(config, media_type="text/plain; charset=utf-8", headers={
                "Content-Disposition": 'attachment; filename="%s.conf"' % safe, "Cache-Control": "no-store"})
        return devices_page(request, config=config, shown=name, qr=markupsafe.Markup(control.qr_svg(config)),
                            dns_domains=split_domains())

    def split_domains():
        """The names a split config's computer asks the network for: its domain and the linked networks'
        (NetworkManager's ipv4.dns-search, `~` = only these)."""
        info = control.fleet_info() or {}
        domains = ["~" + str(info.get("domain") or "home")] + \
            ["~" + str(n["domain"]) for n in (info.get("linked") or []) if n.get("domain")]
        return ",".join(dict.fromkeys(domains))

    # the same for a computer that manages the network (its key's token): the Android app adds the phone
    # it runs on, the desktop app a laptop; one function with the page (control.add_device / remove_device)
    @app.get("/api/devices")
    def api_devices(request: Request):
        """The phones and laptops on the VPN, and the groups a new one may go into. Names only."""
        if not bearer(request) and not user(request):
            return JSONResponse({"error": "who?"}, status_code=401)
        info = control.fleet_info() or {}
        return {"devices": control.devices(), "groups": sorted(info.get("groups") or {}),
                "default_group": info.get("default_group") or ""}

    @app.post("/api/devices")
    async def api_device_add(request: Request):
        """{name, group, split} -> {name, config, qr, dns_search}: the new device's WireGuard config (its
        private key inside: shown once to the one who asked, never logged), its QR code as SVG, and for a
        split config the NetworkManager line's domains."""
        who = bearer(request)
        if not who:
            return JSONResponse({"error": "who?"}, status_code=401)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return JSONResponse({"error": "a JSON object: {name, group, split}"}, status_code=400)
        name = str(body.get("name") or "").strip().lower()
        split = body.get("split") is True
        try:
            config = control.add_device(name, str(body.get("group") or ""), who, split=split)
        except KiwiError as e:
            return JSONResponse({"error": str(e)}, status_code=409 if "exists already" in str(e) else 400)
        if not config:
            return JSONResponse({"error": "the master made no config for %s" % name}, status_code=502)
        return JSONResponse({"name": name, "config": config, "qr": control.qr_svg(config),
                             "dns_search": split_domains() if split else ""}, headers={"Cache-Control": "no-store"})

    @app.delete("/api/devices/{name}")
    def api_device_remove(request: Request, name: str):
        """A device off the VPN (a lost phone): the master forgets its key at once."""
        who = bearer(request)
        if not who:
            return JSONResponse({"error": "who?"}, status_code=401)
        try:
            control.remove_device(name.strip().lower(), who)
        except KiwiError as e:
            return JSONResponse({"error": str(e)}, status_code=404 if "no device called" in str(e) else 400)
        return {"removed": name.strip().lower()}

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
        return page("backups.html", request, "/backups", live=True, machines=ms)

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
            partners, cas = control.federation(), control.partner_cas()
        except KiwiError as e:
            partners, cas, kw = {}, {}, dict(kw, error=kw.get("error") or str(e))
        return page("federation.html", request, "/federation", partners=partners, cas=cas, **kw)

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
            if form.get("new"):                         # its next address: the old one stops with the next apply
                what = str(form["new"])
                address = control.onion_new(what, user=u)
                return RedirectResponse("/access?tor=" + urllib.parse.quote("%s, at its new address http://%s" % (what, address))
                                        + "#tor", status_code=303)
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
                if "all." + n in form and w["kind"] in ("group", "lan"):
                    reach = ["all"]
                else:
                    reach = ["apps"] if "apps." + n in form and w["kind"] in ("group", "lan") else []
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
                                       new_lan=str(form.get("new_lan", "")).strip(),
                                       new_as=str(form.get("new_as", "nothing")), user=u)
        except KiwiError as e:
            return access_page(request, error=str(e))
        return RedirectResponse("/access?saved=" + urllib.parse.quote(", ".join(done)) if done else "/access", status_code=303)

    async def api_body(request):
        try:
            body = await request.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    @app.get("/api/access")
    def api_access(request: Request):
        """Who may reach what, as the page shows it: {master, who, machines} (`access --porcelain`)."""
        if not bearer(request) and not user(request):
            return JSONResponse({"error": "who?"}, status_code=401)
        try:
            return control.access()
        except KiwiError as e:
            return JSONResponse({"error": str(e)}, status_code=409)

    @app.post("/api/access")
    async def api_access_do(request: Request):
        """{who: {name: {reach, peers, internet, admin}}, new, new_as, new_lan} -> {saved: [names]}. What a
        name leaves out stays as it is; `reach` is a list (machines, services like cloud.papaya,
        ports like papaya:22, "apps", "all"); `new` makes a group starting with `new_as` (nothing,
        apps, all); with `new_lan` (a subnet at home) it is a group of the home network."""
        who = bearer(request)
        if not who:
            return JSONResponse({"error": "who?"}, status_code=401)
        body = await api_body(request)
        if body is None or not isinstance(body.get("who") or {}, dict):
            return JSONResponse({"error": "a JSON object: {who: {name: {reach, peers, internet, admin}}, new, new_as}"},
                                status_code=400)
        try:
            now = {x["name"]: x for x in control.access()["who"]}
            changes = {}
            for name, c in (body.get("who") or {}).items():
                if name not in now:
                    raise KiwiError("no group or linked network called %s" % name)
                if not isinstance(c, dict) or not isinstance(c.get("reach", []), list):
                    raise KiwiError("%s: {reach: [...], peers, internet, admin}" % name)
                changes[name] = dict({"reach": [str(e) for e in c["reach"]] if "reach" in c else now[name]["reach"]},
                                     **{k: (c[k] is True) if k in c else bool(now[name].get(k)) for k in ("peers", "internet", "admin")})
            done = await run_in_threadpool(control.save_access, changes, str(body.get("new") or "").strip().lower(),
                                           str(body.get("new_as") or "nothing"), who, str(body.get("new_lan") or "").strip())
        except KiwiError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return {"saved": list(done or [])}

    FED_KEYS = ("domain", "mesh", "share", "from", "exit", "internet")

    @app.get("/api/federation")
    def api_federation(request: Request):
        """The linked networks: {name: {domain, mesh, share, from, exit, internet}}. Linking one needs
        both fleets: that stays with `kiwi-server federate`."""
        if not bearer(request) and not user(request):
            return JSONResponse({"error": "who?"}, status_code=401)
        try:
            cas = control.partner_cas()
            # its CA (PEM) only when this network's devices may import it; else why not
            return {n: dict({k: e.get(k) for k in FED_KEYS}, ca=cas[n]["pem"], ca_mark=cas[n]["mark"], ca_why=cas[n]["why"])
                    for n, e in control.federation().items()}
        except KiwiError as e:
            return JSONResponse({"error": str(e)}, status_code=409)

    @app.post("/api/federation")
    async def api_federation_do(request: Request):
        """{name: {share, from, exit, internet}} -> {saved: true}: what this network opens to a linked
        one, which of its people this network lets in, and whether it leaves to the Internet through it. Left out: as now."""
        who = bearer(request)
        if not who:
            return JSONResponse({"error": "who?"}, status_code=401)
        body = await api_body(request)
        if body is None:
            return JSONResponse({"error": "a JSON object: {name: {share, from, exit, internet}}"}, status_code=400)
        try:
            had = control.federation()
            changes = {}
            for name, c in body.items():
                if name not in had:
                    raise KiwiError("no linked network called %s" % name)
                if not isinstance(c, dict) or any(not isinstance(c.get(k, []), list) for k in ("share", "from")):
                    raise KiwiError("%s: {share: [...], from: [...], exit, internet}" % name)
                changes[name] = {"share": [str(x) for x in c["share"]] if "share" in c else had[name].get("share") or [],
                                 "from": [str(x) for x in c["from"]] if "from" in c else had[name].get("from") or [],
                                 "exit": (([str(x) for x in c["exit"]] or False) if isinstance(c["exit"], list) else c["exit"] is True)
                                         if "exit" in c else (had[name].get("exit") or False),
                                 "internet": (c["internet"] is True) if "internet" in c else bool(had[name].get("internet"))}
            await run_in_threadpool(control.save_federation, changes, who)
        except KiwiError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return {"saved": True}

    @app.get("/federation/{name}/ca.pem")
    def federation_ca(request: Request, name: str):
        """A linked network's CA, for a device to import; only one that can sign its own names only."""
        if not user(request):
            return login_page()
        try:
            c = control.partner_cas().get(name)
        except KiwiError as e:
            return PlainTextResponse(str(e), status_code=409)
        if not c or not c["offered"]:
            return PlainTextResponse("not offered: %s" % (c["why"] if c else "no linked network called " + name), status_code=404)
        return Response(c["pem"], media_type="application/x-pem-file",
                        headers={"Content-Disposition": 'attachment; filename="kiwiCA-%s.pem"' % re.sub(r"[^a-z0-9-]", "", name)})

    @app.get("/federation")
    def federation(request: Request):
        if not user(request):
            return login_page()
        return federation_page(request, saved=request.query_params.get("saved") == "1")

    @app.get("/federation/card.yaml")
    def federation_card(request: Request):
        """This network's card, to give to a network someone else runs."""
        if not user(request):
            return login_page()
        try:
            text = control.federation_card()
        except KiwiError as e:
            return federation_page(request, card_error=str(e))
        return PlainTextResponse(text, headers={"Content-Disposition": 'attachment; filename="network.card.yaml"'})

    @app.post("/federation/card")
    def federation_take(request: Request, card: str = Form(""), csrf: str = Form("")):
        u = posted(request, csrf)
        if not u:
            return login_page()
        try:
            said = control.take_card(card, user=u)
        except KiwiError as e:
            return federation_page(request, card_error=str(e))
        return federation_page(request, said=said)

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
                           "exit": (str(form.get("exit_groups." + n, "")).split() or True) if "exit." + n in form else False,
                           "internet": "internet." + n in form} for n in names}
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
        """Recovery: could the network be brought back, and what to do (recovery.py)."""
        if not user(request):
            return login_page()
        try:
            rep, error = control.recovery(), None
        except (KiwiError, OSError) as e:
            rep, error = None, "The report could not be made: %s" % e
        return page("breaks.html", request, "/recovery", rep=rep, error=error)

    @app.get("/unlock")
    def unlock_page(request: Request):
        """Who opens whom (docs/design/unlock.md): read only, from the fleet."""
        if not user(request):
            return login_page()
        try:
            machines, error = control.unlock_picture(), None
        except (KiwiError, OSError) as e:
            machines, error = [], "The picture could not be made: %s" % e
        return page("unlock.html", request, "/unlock", machines=machines, error=error)

    @app.post("/unlock/{name}/opens")
    def unlock_opens(request: Request, name: str, opens: str = Form(""), csrf: str = Form("")):
        """Auto-unlock on or off for one machine (unlock.opens): into the fleet file, then an apply binds
        or unbinds its rings."""
        u = posted(request, csrf)
        if not u:
            return login_page()
        try:
            if opens not in ("by-itself", "by-a-person"):
                raise KiwiError("auto-unlock on or off?")
            control.save_settings(name, {("unlock", "opens"): opens}, user=u)
            jid = control.start("apply", [name], user=u)
        except KiwiError as e:
            return page("unlock.html", request, "/unlock", machines=control.unlock_picture(), error=str(e))
        return RedirectResponse("/jobs/" + jid, status_code=303)

    @app.get("/api/unlock")
    def api_unlock(request: Request):
        """The same rows for a controller (the Android app, the app on a computer)."""
        if not (user(request) or bearer(request)):
            return JSONResponse({"error": "log in"}, status_code=401)
        try:
            return {"machines": control.unlock_picture()}
        except (KiwiError, OSError) as e:
            return JSONResponse({"error": str(e)}, status_code=409)

    @app.get("/activity")
    def activity(request: Request):
        if not user(request):
            return login_page()
        show = request.query_params.get("show", "all")
        show = show if show in dict(timeline.FILTERS) else "all"
        days = timeline.by_day(timeline.keep(timeline.merge(jobs=control.jobs(40), log=control.audit_tail(200)), show))
        return page("activity.html", request, "/activity", live=True, days=days, show=show, show_filters=timeline.FILTERS,
                    badge=timeline.badge, clock=timeline.clock)

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

    def sources_found(check=False):
        """The fleet's module sources (without paths on this machine); with `check`, each repository
        asked for its newest version tag: (sources, news)."""
        found = [{k: v for k, v in x.items() if k != "path"} for x in ((control.fleet_info() or {}).get("module_sources")) or []]
        news = {}
        if check:                               # each repository asked for its newest version tag
            from . import sources as sourcesmod
            for x in found:
                if x.get("url"):
                    try:
                        got = sourcesmod.latest(x["url"])
                        news[x["name"]] = {"tag": got[0], "commit": got[1]} if got else {"tag": "", "commit": x.get("commit")}
                    except KiwiError as e:
                        news[x["name"]] = {"error": str(e)}
        return found, news

    def sources_change(what, url, name, ref, u):
        """Add, update or remove a module source, for the page and a controller: what it said.
        KiwiError says why not."""
        from . import sources as sourcesmod
        url, name, ref = url.strip().rstrip("/"), name.strip(), ref.strip()
        # nothing typed here may pass for an option of the command
        if (name and not sourcesmod.NAME.match(name)) or (ref and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}", ref)):
            raise KiwiError("A name is lowercase letters, digits and dashes; a version is a tag or a branch.")
        if what == "add":
            if not url.startswith("https://") or not sourcesmod.URL.match(url):
                raise KiwiError("The link is the https address of a git repository.")
            args = ["add", url] + (["--name", name] if name else []) + (["--ref", ref] if ref else [])
        elif what in ("update", "remove") and name:
            args = [what, name] + (["--ref", ref] if ref and what == "update" else [])
        else:
            raise KiwiError("Which module source?")
        ok, said = control.sources_run(args, u)
        if not ok:
            raise KiwiError(said)
        if what in ("add", "update") and " already" not in said:
            said += "\nThe machines that use its modules take the change with their next apply (Home: Changes not applied yet)."
        return said

    @app.get("/modules")
    def sources_page(request: Request, check: int = 0, said: str = "", error: str = ""):
        """The module sources of the fleet: which, at what version, their modules; add, update, remove."""
        if not user(request):
            return login_page()
        found, news = sources_found(bool(check))
        return page("sources.html", request, "/modules", sources=found, news=news, checked=bool(check), said=said, error=error)

    @app.post("/modules/{what}")
    def sources_do(request: Request, what: str, csrf: str = Form(""), url: str = Form(""), name: str = Form(""),
                   ref: str = Form("")):
        u = posted(request, csrf)
        if not u:
            return login_page()
        try:
            return sources_page(request, said=sources_change(what, url, name, ref, u))
        except KiwiError as e:
            return sources_page(request, error=str(e))

    @app.get("/api/modules")
    def api_sources(request: Request, check: int = 0):
        """The module sources: {sources, news}; ?check=1 asks each repository for its newest tag."""
        if not bearer(request) and not user(request):
            return JSONResponse({"error": "who?"}, status_code=401)
        found, news = sources_found(bool(check))
        return {"sources": found, "news": news}

    @app.post("/api/modules")
    async def api_sources_do(request: Request):
        """{op: add|update|remove, url, name, ref} -> {said}: a module source, as on the page."""
        who = bearer(request)
        if not who:
            return JSONResponse({"error": "who?"}, status_code=401)
        body = await api_body(request)
        if body is None:
            return JSONResponse({"error": "a JSON object: {op, url, name, ref}"}, status_code=400)
        try:
            said = await run_in_threadpool(sources_change, str(body.get("op") or ""), str(body.get("url") or ""),
                                           str(body.get("name") or ""), str(body.get("ref") or ""), who)
        except KiwiError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return {"said": said}

    @app.get("/controllers")
    def controllers(request: Request):
        if not user(request):
            return login_page()
        removed = request.query_params.get("removed") or ""
        return controllers_page(request, removed=removed if re.match(r"^[A-Za-z0-9._-]{1,64}$", removed) else "")

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
                return RedirectResponse("/controllers?removed=%s" % urllib.parse.quote(name), status_code=303)
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

    JOB_FIELDS = ("id", "action", "hosts", "user", "state", "started", "ended", "rc")

    @app.get("/api/jobs")
    def api_jobs(request: Request):
        """The newest tasks (/api/v1/jobs): what each did, on which machines, how it ended. No output."""
        if not (user(request) or bearer(request)):
            return JSONResponse({"error": "log in"}, status_code=401)
        return {"jobs": [{k: j.get(k) for k in JOB_FIELDS} for j in control.jobs(50)]}

    @app.get("/api/jobs/{jid}")
    def api_job(request: Request, jid: str):
        if not (user(request) or bearer(request)):
            return JSONResponse({"error": "log in"}, status_code=401)
        j = control.job(jid)
        if not j:
            return JSONResponse({"error": "no such job"}, status_code=404)
        return {k: j.get(k) for k in JOB_FIELDS}

    @app.get("/api/machines/{name}")
    def api_machine(request: Request, name: str):
        """One machine (/api/v1/machines/NAME): what the fleet file says of it and how the last look saw it."""
        if not (user(request) or bearer(request)):
            return JSONResponse({"error": "log in"}, status_code=401)
        info = control.fleet_info() or {}
        row = next((h for h in info.get("hosts") or [] if h.get("name") == name), None)
        if row is None:
            return JSONResponse({"error": "no such machine"}, status_code=404)
        seen = next((h for h in (control.status() or {}).get("hosts") or [] if h.get("host") == name), None)
        return {"machine": {k: v for k, v in row.items() if k not in ("dir", "iso")}, "status": seen}

    @app.get("/api/activity")
    @app.get("/api/v1/activity")
    def api_activity(request: Request):
        """The Activity page for a computer that manages the network (`control activity`, the app's
        Activity…): the newest tasks and the log of who did what. Names and times; no output, no secret."""
        if not bearer(request):
            return JSONResponse({"error": "who?"}, status_code=401)
        keep = ("id", "action", "hosts", "user", "state", "started", "ended", "rc")
        return {"jobs": [{k: j.get(k) for k in keep} for j in control.jobs(15)], "entries": control.audit_tail(40)}

    EVENT_QUIET = 20            # seconds: a comment then, so that no proxy closes a quiet stream

    @app.get("/api/v1/events")
    async def api_events(request: Request):
        """Pushed changes (server-sent events): `change` with a mark at once and whenever something a
        page shows changed (control.changes); the page, the app and the phone then ask for what they
        show. `gone` when the login ended. Does not keep a login alive."""
        if not (user(request) or bearer(request)):
            return JSONResponse({"error": "log in"}, status_code=401)
        from fastapi.responses import StreamingResponse

        async def stream():
            last, quiet = None, 0
            while not await request.is_disconnected():
                if quiet >= EVENT_QUIET:
                    if not (user(request) or bearer(request)):
                        yield "event: gone\ndata: {}\n\n"
                        return
                    yield ": still here\n\n"
                    quiet = 0
                mark = await run_in_threadpool(control.changes)
                if mark != last:
                    yield "event: change\ndata: %s\n\n" % json.dumps({"mark": mark})
                    last, quiet = mark, 0
                await asyncio.sleep(1)
                quiet += 1
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

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

    @app.post("/api/v1/state")
    async def api_state(request: Request):
        """What the network remembers (fleetstate.py): a computer sends the changes it made, and
        gets the whole state back. This node keeps it; the steps carry names and times, no secret."""
        who = bearer(request)
        if not who:
            return JSONResponse({"error": "who?"}, status_code=401)
        try:
            body = await request.json()
            steps = body.get("steps") or []
            if not isinstance(steps, list) or len(steps) > 5000:
                raise ValueError("steps: a list")
        except (ValueError, AttributeError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        state = await run_in_threadpool(control.take_state, steps)
        return {"state": state}

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

    if getattr(control, "public_only", False):
        # the public pages' own container (publicpages.py): the website, the status page for the
        # Internet and the public registry, and nothing else, whatever name is asked
        PUBLIC_PATHS = ("/start/public", "/status/public", "/api/registry/public", "/healthz")

        @app.middleware("http")
        async def public_only(request: Request, call_next):
            host = (request.headers.get("host") or "").split(":")[0].lower()
            path = request.url.path
            if request.method not in ("GET", "HEAD") or "/.." in path or "//" in path:
                return Response("Not here.", status_code=404, media_type="text/plain")
            if (SITE_HOST and host in sitemod.hosts(SITE_HOST)) or (SITE_CDN and host == SITE_CDN) or path in PUBLIC_PATHS:
                return await call_next(request)
            return Response("Not here.", status_code=404, media_type="text/plain")

    return app


def self_signed(folder):
    """A certificate of the page's own (key and certificate in `folder`, which is in RAM), for a page that
    has no proxy in front: the starter's first run, on the home network. Returns (key, cert, the
    SHA-256 fingerprint as the browser shows it)."""
    import subprocess
    os.makedirs(folder, mode=0o700, exist_ok=True)
    key, cert = os.path.join(folder, "page.key"), os.path.join(folder, "page.crt")
    r = subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes",
                        "-days", "90", "-subj", "/CN=kiwi.local", "-addext", "subjectAltName=DNS:kiwi.local",
                        "-keyout", key, "-out", cert], capture_output=True, text=True)
    if r.returncode != 0:
        raise KiwiError("no certificate for the page: %s" % (r.stderr.strip().splitlines() or ["openssl failed"])[-1])
    os.chmod(key, 0o600)
    fp = subprocess.run(["openssl", "x509", "-in", cert, "-noout", "-fingerprint", "-sha256"], capture_output=True, text=True)
    return key, cert, fp.stdout.strip().split("=", 1)[-1]


def redirect_app(https_port):
    """Plain HTTP that only sends the browser to the HTTPS page: nothing typed there travels unencrypted."""
    from fastapi import FastAPI
    from fastapi.responses import RedirectResponse as Redirect
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.api_route("/{path:path}", methods=["GET", "HEAD", "POST"])
    def anywhere(request: Request, path: str):
        host = (request.headers.get("host") or "kiwi.local").strip()
        name, _, port = host.rpartition(":") if host.count(":") == 1 else (host, "", "")
        name = name or host
        # asked on port 80 (no port in the name): HTTPS on 443, which leads to the page; else its own port
        target = "https://%s%s/%s" % (name, ":%d" % https_port if port else "", path)
        return Redirect(target, status_code=308)
    return app


def serve_public(folder, listen="0.0.0.0:8741"):
    """The public pages from FOLDER (publicpages.py): no loop, no fleet, nothing to look after."""
    import uvicorn
    from . import publicpages
    host, port = listen.rsplit(":", 1)
    uvicorn.run(make_app(publicpages.PublicControl(folder)), host=host.strip("[]"), port=int(port), log_level="warning",
                proxy_headers=True, forwarded_allow_ips=util.forwarded_allow())


STOP_WAIT = 3       # seconds a stopping page waits for open requests: a page's event stream never ends by itself


def serve(control: Control, listen="127.0.0.1:8740", https=None, tls_dir=None):
    """The pages on `listen`. With `https` (ADDR:PORT): the pages there, with a certificate of their own,
    and `listen` only sends the browser on to them."""
    import uvicorn
    host, port = listen.rsplit(":", 1)
    stop = threading.Event()
    threading.Thread(target=control.loop, args=(stop,), daemon=True).start()
    try:
        if https:
            shost, sport = https.rsplit(":", 1)
            tls_dir = tls_dir or os.path.join(control.run, "tls")
            key, cert = os.path.join(tls_dir, "page.key"), os.path.join(tls_dir, "page.crt")
            if not (os.path.isfile(key) and os.path.isfile(cert)):     # made by the caller already, who said its fingerprint
                key, cert, _fp = self_signed(tls_dir)
            plain = uvicorn.Server(uvicorn.Config(redirect_app(int(sport)), host=host.strip("[]"), port=int(port),
                                                  log_level="warning"))
            threading.Thread(target=plain.run, daemon=True).start()
            uvicorn.run(make_app(control), host=shost.strip("[]"), port=int(sport), log_level="warning",
                        ssl_keyfile=key, ssl_certfile=cert, proxy_headers=True, forwarded_allow_ips=util.forwarded_allow(),
                        timeout_graceful_shutdown=STOP_WAIT)
        else:
            uvicorn.run(make_app(control), host=host.strip("[]"), port=int(port), log_level="warning",
                        proxy_headers=True, forwarded_allow_ips=util.forwarded_allow(), timeout_graceful_shutdown=STOP_WAIT)
    finally:
        stop.set()
