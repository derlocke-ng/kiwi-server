"""The local AI (the ai module, docs/design/ai.md): its API key in the store, the model it has
loaded, OpenCode's settings on this computer, and a benchmark of the models it has.

    kiwi-server ai key FLEET MACHINE        the key made on its page (Settings, API), from stdin
                                            (Unsloth Studio; llama.cpp's key is made by kiwi-server)
    kiwi-server ai models FLEET MACHINE     the models it has, and which one is loaded
    kiwi-server ai load FLEET MACHINE [MODEL]
    kiwi-server ai connect FLEET MACHINE [--apply]
    kiwi-server ai bench FLEET MACHINE [MODEL...]

The API is OpenAI's (/v1/models, /v1/chat/completions) at https://ai.<hostname>, with the fleet's
CA, and Studio's own /v1/status and /v1/load beside it. After a start Studio has no model loaded
and answers 400 to every chat: `load` and `connect --apply` load one, with the context asked for.
llama.cpp's server alone (ai.engine: llama) differs: its models and their context are settings of
the fleet, and it loads a model by itself with the request that names it. `load` only does that
ahead of time.
The benchmark asks each model the same things: the time to its first token, its tokens per second
when writing and when reading, and a few tasks whose answers are known. Its results are kept in
ai-bench/<machine>.jsonl beside the fleet file, to compare models and machines over time.
"""
import json
import os
import re
import shutil
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from . import util
from .util import KiwiError

# tasks with one right answer, checked by a pattern: a small sign of quality next to the speed
TASKS = (
    ("arithmetic", "What is 17 multiplied by 23? Answer with the number only.", r"\b391\b"),
    ("facts", "What is the capital of Australia? Answer with one word.", r"(?i)\bcanberra\b"),
    ("logic", "If all bloops are razzies and all razzies are lazzies, are all bloops lazzies? Answer yes or no.",
     r"(?i)\byes\b"),
    ("german", "Translate to German, the sentence only: The cat sleeps on the sofa.", r"(?i)katze\b.*\bschl[aä]ft\b"),
    ("code", "Write a Python function is_even(n) that returns True for even numbers. Only the code.",
     r"def is_even\(n\).*\n?.*%\s*2"),
)
SPEED_PROMPT = ("Write a short paragraph (about 150 words) on why backups should be kept in more than one place.")
LONG_CONTEXT = " ".join(["The quick brown fox jumps over the lazy dog."] * 400)     # about 4000 tokens to read

CONTEXT = 131072        # tokens, when nothing was asked for, nothing is loaded and nothing was chosen before
# The context cache at 8 bits: half the memory of the card, so the model itself stays on the card
# (35B-A3B at 262k: 2.7 GB instead of 5.1, 125 instead of 68 tokens per second; lemonaide, 25 Nov 2026).
CACHE = "q8_0"
# Memory (MiB) for chats that are not in use at the moment (llama-server's --cache-ram, 8 GB by itself:
# two or three big chats). A chat that is still there answers at once; a dropped one is read again,
# minutes at 200k tokens.
CHAT_CACHE_MB = 16384
# Sent with every request. Without it Studio lets a model it has no preset for choose among all its
# words (top_k 0), a third slower (91 against 125 tokens per second).
TOP_K = 20
OUTPUT = 32768          # tokens of one answer, at most (and never more than a quarter of the context)
HOUR_MS = 3600000
FLATPAK = "ai.opencode.opencode"


def key_path(fleet, host):
    return fleet.resolve_path("secrets/ai/%s.key" % host.name)


def base_url(host):
    return "https://ai.%s" % host.hostname


def _ctx(fleet):
    ca = fleet.resolve_path("secrets/ca/kiwiCA.pem")
    ctx = ssl.create_default_context(cafile=ca if os.path.isfile(ca) else None)
    return ctx


def lean(host):
    """Does this machine run llama.cpp's server alone (ai.engine: llama), not Unsloth Studio?"""
    return ((getattr(host, "module_settings", None) or {}).get("ai") or {}).get("engine") == "llama"


def api_key(fleet, host):
    """The key its API asks for. llama.cpp's is a setting of the module, made from the fleet's seed
    (the machine's settings resolved); Studio's is made on its page and put in the store by `ai key`."""
    if lean(host):
        return str(host.module_settings["ai"].get("api_key") or "")
    return util.read_text(key_path(fleet, host)).strip() if os.path.isfile(key_path(fleet, host)) else ""


def _key(fleet, host):
    key = api_key(fleet, host)
    if not key:
        raise KiwiError("%s: no API key on record: make one on https://ai.%s (Settings, API), then "
                        "kiwi-server ai key FLEET %s" % (host.name, host.hostname, host.name))
    return key


def _said(e):
    """What Studio wrote into an error answer, in one short line."""
    with e:
        text = e.read().decode(errors="replace")
    try:
        d = json.loads(text)
        err = d.get("error") if isinstance(d, dict) else None
        text = (err.get("message") if isinstance(err, dict) else err) or (d.get("detail") if isinstance(d, dict) else None) or text
    except ValueError:
        pass
    return " ".join(str(text).split())[:200]


def _call(fleet, host, path, body=None, timeout=300):
    req = urllib.request.Request(base_url(host) + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + _key(fleet, host), "Content-Type": "application/json"},
                                 method="POST" if body is not None else "GET")
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=_ctx(fleet))
    except urllib.error.HTTPError as e:
        said = _said(e)
        if e.code in (401, 403):
            raise KiwiError("%s: the AI refused the key, or this computer may not reach it (%d %s)" % (host.name, e.code, said))
        if e.code == 400 and "no model loaded" in said.lower():
            raise KiwiError("%s: no model is loaded: kiwi-server ai load FLEET %s" % (host.name, host.name))
        raise KiwiError("%s: the AI said %d to %s: %s" % (host.name, e.code, path, said))
    except (urllib.error.URLError, OSError) as e:
        raise KiwiError("%s: the AI does not answer at %s (%s): is the machine on?" % (host.name, base_url(host), e))


def _json(fleet, host, path, body=None, timeout=60):
    with _call(fleet, host, path, body, timeout=timeout) as r:
        try:
            d = json.loads(r.read() or b"{}")
        except ValueError:
            raise KiwiError("%s: the AI answered %s with something that is not JSON" % (host.name, path))
    return d if isinstance(d, dict) else {}


# ---------------------------------------------------------------- the models and the loaded one

def listing(data):
    """Studio's /v1/models as one entry per model: id, the quants it has of it, the quant in use
    (the loaded one, else the first), loaded, and the loaded one's context. Studio lists a model
    with several quants as "id" and once more as "id:QUANT" for each."""
    found = {}
    for m in data or []:
        mid, quant = str(m.get("id") or ""), str(m.get("quant") or "")
        if not mid:
            continue
        repo = mid[:-len(quant) - 1] if quant and mid.endswith(":" + quant) else mid
        e = found.setdefault(repo, {"id": repo, "quants": [], "quant": "", "loaded": False, "context": None})
        if quant and quant not in e["quants"]:
            e["quants"].append(quant)
        if m.get("loaded"):
            e["loaded"] = True
            e["quant"] = quant or e["quant"]
            e["context"] = m.get("context_length") or e["context"]
    for e in found.values():
        e["quant"] = e["quant"] or (e["quants"][0] if e["quants"] else "")
    return list(found.values())


def lean_listing(data, context=None):
    """llama.cpp's /v1/models in the same shape. There a model is one name with its quant in it, and
    it is loaded, asleep (out of the memory until the next request, which brings it back) or not
    loaded. Loaded and asleep both count as loaded here: neither needs a load. `state` says which;
    `aliases`, the other names it answers to (the fleet's own, see lean_id)."""
    out = []
    for m in data or []:
        mid, state = str(m.get("id") or ""), str((m.get("status") or {}).get("value") or "")
        if mid:
            out.append({"id": mid, "quants": [], "quant": "", "loaded": state in ("loaded", "sleeping"),
                        "state": state, "context": context, "aliases": [str(a) for a in m.get("aliases") or []]})
    return out


def lean_id(name):
    """The name llama.cpp's server lists a model under that the fleet names user/model:QUANT. It
    takes the quant as the end of a file's name, upper case: UD-Q4_K_S is listed as Q4_K_S, q8_0 as
    Q8_0 (its canonical_tag; lemonaide, 28 Nov 2026: the fleet's model was not found, and the first
    model in the list was loaded instead). The ai module gives such a model the fleet's name as an
    alias, since kiwi-modules 0.142.0; this finds it in a list without one too."""
    head, colon, tag = str(name).rpartition(":")
    if not colon:
        return str(name)
    end = re.split(r"[-.]", tag)[-1]
    return "%s:%s" % (head, (end if re.fullmatch(r"[A-Za-z0-9_]+", end or "") else tag).upper())


def lean_context(fleet, host, model=None):
    """The context llama.cpp gives its models: the fleet's ai.context, or with 0 there (as much as a
    model was trained for) what the loaded model says. None when nothing says it."""
    n = int((host.module_settings.get("ai") or {}).get("context") or 0)
    if n or not model:
        return n or None
    try:
        props = _json(fleet, host, "/props?model=" + urllib.parse.quote(model, safe=""), timeout=30)
    except KiwiError:
        return None
    return int((props.get("default_generation_settings") or {}).get("n_ctx") or 0) or None


def models(fleet, host):
    data = _json(fleet, host, "/v1/models", timeout=30).get("data")
    return lean_listing(data, lean_context(fleet, host)) if lean(host) else listing(data)


def status(fleet, host):
    """What is loaded and how: model, quant, context, the context cache, whether it reads images."""
    if lean(host):
        e = next((x for x in models(fleet, host) if x["loaded"]), None) or {}
        return {"model": e.get("id") or "", "quant": "", "context": lean_context(fleet, host, e.get("id")), "cache": "",
                "images": False, "chat_cache_mb": None, "state": e.get("state") or ""}
    d = _json(fleet, host, "/v1/status", timeout=30)
    return {"model": d.get("active_model") or d.get("model_identifier") or "", "quant": d.get("gguf_variant") or "",
            "context": d.get("context_length"), "cache": d.get("cache_type_kv") or "",
            "images": not d.get("disable_vision", True), "chat_cache_mb": d.get("requested_cache_ram")}


def short(model):
    return model.split("/")[-1]


def pick(entries, name=None, quant=None, last=None):
    """The model and the quant that are meant. By name: its id, "id:QUANT", or a part of the id
    that only one model has. Without a name: the loaded one, else the one chosen last, else the
    only one. Never a guess among several."""
    if not entries:
        raise KiwiError("the AI has no model yet: download one on its page")
    have = ", ".join(e["id"] for e in entries)

    def named(n):
        e = next((x for x in entries if x["id"] == n), None)
        if e is None:
            near = [x for x in entries if n.lower() in x["id"].lower()]
            e = near[0] if len(near) == 1 else None
        return e
    last = last or {}
    if name:
        e = named(name)
        if e is None and ":" in name:               # id:QUANT, as Studio lists it
            head, tail = name.rsplit(":", 1)
            e, quant = named(head), quant or tail
        if e is None:
            raise KiwiError("the AI has no model %s. It has: %s" % (name, have))
    else:
        e = (next((x for x in entries if x["loaded"]), None)
             or next((x for x in entries if x["id"] == last.get("model")), None)
             or (entries[0] if len(entries) == 1 else None))
        if e is None:
            raise KiwiError("which model? Name one (--model). It has: %s" % have)
    q = quant or ("" if e["loaded"] else last.get("quant") if last.get("model") == e["id"] else "") or e["quant"]
    if e["quants"] and q not in e["quants"]:
        if quant:
            raise KiwiError("the AI has no %s of %s. It has: %s" % (quant, e["id"], ", ".join(e["quants"])))
        q = e["quant"]
    return e, q


def load_body(model, quant, context, cache=CACHE, images=False, chat_cache_mb=CHAT_CACHE_MB):
    """What POST /v1/load takes."""
    body = {"model_path": model, "max_seq_length": int(context), "disable_vision": not images}
    if quant:
        body["gguf_variant"] = quant
    if cache and cache != "f16":
        body["cache_type_kv"] = cache
    if chat_cache_mb:
        body["cache_ram"] = int(chat_cache_mb)
    return body


def load(fleet, host, model, quant, context, cache=CACHE, images=False, chat_cache_mb=CHAT_CACHE_MB):
    """Studio loads this model (and drops the one it had). Returns the context it got."""
    d = _json(fleet, host, "/v1/load", load_body(model, quant, context, cache, images, chat_cache_mb), timeout=1800)
    if d.get("status") != "loaded":
        raise KiwiError("%s: Studio did not load %s: %s" % (host.name, model, json.dumps(d)[:200]))
    return int(d.get("context_length") or context)


def lean_load(fleet, host, model, say=util.say, wait=1800):
    """llama.cpp loads this model now (and drops the one it had), instead of with the first request
    that names it. A model it does not have yet is downloaded first."""
    _json(fleet, host, "/models/load", {"model": model}, timeout=60)
    end, told = time.monotonic() + wait, False
    while time.monotonic() < end:
        e = next((x for x in models(fleet, host) if x["id"] == model), None) or {}
        if e.get("state") == "loaded":
            return
        if e.get("state") == "downloading" and not told:
            told = True
            say("%s: downloading %s first" % (host.name, short(model)))
        time.sleep(2)
    raise KiwiError("%s: %s is not loaded after %d minutes: kiwi-stack logs on the machine say why" % (host.name, model, wait // 60))


PLACED = r"""c=$(podman ps --format '{{.Names}}' | grep -E '^k[mn]-ai$' | head -n 1)
[ -n "$c" ] && podman logs --since %ds "$c" 2>&1 | grep -o 'offloaded [0-9]*/[0-9]* layers to GPU' | tail -n 1"""


def placed(host, since, run=None):
    """(layers on the card, layers in all) of the model llama.cpp loaded in the last SINCE seconds,
    from its log, or None when the log does not say (a machine without a card says nothing). A
    model that does not fit beside what ai.card_free_mb leaves goes to the processor without an
    error (28 Nov 2026, lemonaide beside Studio)."""
    from . import remote
    try:                # only a remark: whatever keeps it from the log (no login there), nothing is said
        out = (run or remote.run)(remote.reach(host, None), PLACED % max(int(since), 1)).decode(errors="replace")
    except (KiwiError, OSError, AttributeError, KeyError, TypeError):
        return None
    m = re.search(r"offloaded (\d+)/(\d+) layers", out)
    return (int(m.group(1)), int(m.group(2))) if m else None


def placement_said(host, model, got):
    """What `ai load` says about where the model went: nothing when the log did not say."""
    if not got:
        return None
    on, total = got
    if total and on >= total:
        return "%s: %s is on the graphics card (all %d layers)" % (host.name, short(model), total)
    if on == 0:
        return ("%s: %s runs on the processor, not on the card: slower. The card has too little room beside what "
                "ai.card_free_mb leaves to other programs (less, like 256, lets a small model on; or a smaller model)"
                % (host.name, short(model)))
    return ("%s: %s is only partly on the card (%d of %d layers), the rest on the processor: slower. ai.card_free_mb, "
            "or a smaller model, puts it all on the card" % (host.name, short(model), on, total))


def lean_ready(fleet, host, name=None, context=None, say=util.say, run=None):
    """ready() for llama.cpp's server alone: the model named, else the one in memory, else the
    fleet's first. Its context is the fleet's ai.context, not one asked for per load."""
    entries = models(fleet, host)
    if not entries:
        raise KiwiError("%s: llama.cpp offers no model: ai.models in the fleet names them" % host.name)
    if name:
        e, _q = pick(entries, lean_id(name))
    else:       # the one in memory, else the fleet's first; the list's first only when the fleet names none it has
        first = [lean_id(x) for x in (host.module_settings.get("ai") or {}).get("models") or []][:1]
        e = (next((x for x in entries if x["loaded"]), None) or next((x for x in entries if x["id"] in first), None)
             or entries[0])
    there = e["state"] == "loaded"
    if there:
        say("%s: %s is loaded" % (host.name, short(e["id"])))
    elif e["state"] == "sleeping":
        say("%s: %s is asleep and comes back with the next request" % (host.name, short(e["id"])))
    else:
        say("%s: loading %s" % (host.name, short(e["id"])))
        began = time.time()
        lean_load(fleet, host, e["id"], say)
        where = placement_said(host, e["id"], placed(host, time.time() - began + 30, run))
        if where:
            say(where)
    have = lean_context(fleet, host, e["id"])
    if context and have and int(context) != have:
        say("%s: its context is %d tokens, the fleet's ai.context: change it there, not per load" % (host.name, have))
    if not have:
        raise KiwiError("%s: how much context %s has is not known: set ai.context in the fleet" % (host.name, e["id"]))
    return {"model": e["id"], "quant": "", "context": have, "images": False, "loaded": e["state"] not in ("loaded", "sleeping")}


def _choice_path(fleet, host):
    return os.path.join(fleet.dir, "ai", "%s.json" % host.name)


def chosen(fleet, host):
    """The model loaded last through kiwi-server (model, quant, context, images): what `load`
    loads again after the machine started, when nothing is named."""
    try:
        d = json.loads(util.read_text(_choice_path(fleet, host)))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def ready(fleet, host, name=None, quant=None, context=None, images=None, say=util.say, keep=True):
    """The model is loaded as asked. One that is loaded already stays as it is unless another
    context (or the image reader) is asked for: a load takes a minute and forgets the chats
    Studio had in memory. Returns model, quant, context (what Studio gave), images, and loaded:
    whether it was loaded now. `keep`: remember it as the one chosen."""
    if lean(host):
        return lean_ready(fleet, host, name + (":" + quant if quant else "") if name else None, context, say)
    last = chosen(fleet, host)
    e, q = pick(models(fleet, host), name, quant, last)
    label = "%s%s" % (short(e["id"]), " " + q if q else "")
    again = last if last.get("model") == e["id"] and (last.get("quant") or "") == q else {}
    got = None
    if e["loaded"] and (not q or e["quant"] == q):
        st = status(fleet, host)
        if st["context"] and context in (None, st["context"]) and images in (None, st["images"]):
            say("%s: %s is loaded, %d tokens of context" % (host.name, label, st["context"]))
            got = context = st["context"]
            images = st["images"]
        else:
            context, images = context or st["context"], st["images"] if images is None else images
    there = got is not None
    if not there:
        context = int(context or again.get("context") or CONTEXT)
        images = bool(again.get("images")) if images is None else images
        say("%s: loading %s with %d tokens of context (a minute or so)" % (host.name, label, context))
        got = load(fleet, host, e["id"], q, context, images=images)
        if got < context:
            say("%s: Studio gave it %d tokens, not %d: more did not fit" % (host.name, got, context))
    if keep:
        util.write_text(_choice_path(fleet, host), json.dumps(
            {"model": e["id"], "quant": q, "context": int(context), "images": bool(images)}, indent=1) + "\n")
    return {"model": e["id"], "quant": q, "context": int(got), "images": bool(images), "loaded": not there}


# ---------------------------------------------------------------- asking, the benchmark

def ask(fleet, host, model, prompt, max_tokens=300, top_k=TOP_K):
    """One streamed answer: text, first_s (seconds to its first token, thinking included),
    tokens written, writing_s, and llama-server's own timings when Studio passes them on."""
    body = {"model": model, "stream": True, "max_tokens": max_tokens, "temperature": 0, "top_k": int(top_k),
            "messages": [{"role": "user", "content": prompt}], "stream_options": {"include_usage": True}}
    t0 = time.monotonic()
    first, text, tokens, chunks, timings = None, [], None, 0, {}
    with _call(fleet, host, "/v1/chat/completions", body, timeout=600) as r:
        for raw in r:
            line = raw.decode(errors="replace").strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            try:
                d = json.loads(line[5:])
            except ValueError:
                continue
            for c in d.get("choices") or []:
                delta = c.get("delta") or {}
                piece = delta.get("content") or ""
                if piece or delta.get("reasoning_content"):
                    if first is None:
                        first = time.monotonic() - t0
                    chunks += 1
                if piece:
                    text.append(piece)
            if d.get("usage"):
                tokens = d["usage"].get("completion_tokens")
            if isinstance(d.get("timings"), dict):
                timings = d["timings"]
    end = time.monotonic() - t0
    return {"text": "".join(text), "first_s": first or end, "tokens": tokens or chunks,
            "writing_s": max(0.001, end - (first or end)), "timings": timings}


def bench(fleet, host, names=None, say=util.say, context=None, top_k=TOP_K):
    """Each model: speed (short and long context) and the tasks. Studio answers from the loaded
    model only, so each one is loaded in turn (the loaded one is not loaded again), and the one
    that was loaded before is put back at the end. Without names: every model, one quant of each
    (the loaded one, else the first). Returns the rows, kept in the fleet folder."""
    entries = models(fleet, host)
    if not entries:
        raise KiwiError("%s: no model is downloaded yet (%s)" % (
            host.name, "ai.models in the fleet names them" if lean(host) else "its page: choose one"))
    before = status(fleet, host) if any(e["loaded"] for e in entries) else None
    # every model at one context, the one in use: the numbers are for comparing
    last = chosen(fleet, host)
    context = None if lean(host) else context or (before or {}).get("context") or last.get("context") or CONTEXT
    want = [pick(entries, n, None, last) for n in names] if names else [(e, e["quant"]) for e in entries]
    rows = []
    try:
        for e, q in want:
            now = ready(fleet, host, e["id"] + (":" + q if q else ""), None, context, say=say, keep=False)
            m = e["id"]
            fast = ask(fleet, host, m, SPEED_PROMPT, 400, top_k)
            # a first line of its own: Studio keeps what it read, and a text it knows is not read again
            slow = ask(fleet, host, m, "Run %s.\n%s\n\nHow many sentences were above? A number."
                       % (time.strftime("%Y-%m-%d %H:%M:%S"), LONG_CONTEXT), 20, top_k)
            right = []
            for name, prompt, pattern in TASKS:
                if re.search(pattern, ask(fleet, host, m, prompt, 200, top_k)["text"], re.S):
                    right.append(name)
            row = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "machine": host.name, "model": m, "quant": q,
                   "context": now["context"], "top_k": int(top_k),
                   "first_token_s": round(fast["first_s"], 2), "tokens_per_s": round(fast["tokens"] / fast["writing_s"], 1),
                   "first_token_long_s": round(slow["first_s"], 2), "tasks": "%d/%d" % (len(right), len(TASKS)), "right": right}
            read = slow["timings"]
            if (read.get("prompt_n") or 0) >= 1000 and read.get("prompt_per_second"):      # read, not taken from memory
                row["read_tokens"], row["read_tokens_per_s"] = int(read["prompt_n"]), round(read["prompt_per_second"])
            rows.append(row)
            say("  first token %.2f s, writes %.1f tokens/s, %s, tasks %s"
                % (row["first_token_s"], row["tokens_per_s"],
                   "reads %d tokens/s" % row["read_tokens_per_s"] if "read_tokens_per_s" in row
                   else "with 4000 tokens to read %.2f s" % row["first_token_long_s"], row["tasks"]))
    finally:                    # also when a model failed: the rows there are, and the model that was loaded
        path = os.path.join(fleet.dir, "ai-bench", "%s.jsonl" % host.name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        if before:
            try:
                cur = status(fleet, host)
            except KiwiError:
                cur = {}
            if lean(host):      # the one that was in memory before; asleep or awake, the next request finds it
                if before["model"] and cur.get("model") != before["model"]:
                    say("%s: loading %s again, as it was" % (host.name, short(before["model"])))
                    lean_load(fleet, host, before["model"], say)
            elif [cur.get(k) for k in ("model", "quant", "context")] != [before[k] for k in ("model", "quant", "context")]:
                say("%s: loading %s again, as it was" % (host.name, short(before["model"])))
                load(fleet, host, before["model"], before["quant"], before["context"], before["cache"] or "f16",
                     before["images"], before["chat_cache_mb"])
    return rows


def check(fleet, host, model, top_k=TOP_K):
    """One short answer and one tool call from the loaded model: (tokens per second, the tool was called).
    A model that does not call tools cannot edit files in OpenCode."""
    a = ask(fleet, host, model, "Explain in about 200 words how a heat pump works.", 300, top_k)
    d = _json(fleet, host, "/v1/chat/completions", {"model": model, "max_tokens": 400, "temperature": 0, "top_k": int(top_k),
              "messages": [{"role": "user", "content": "Read the file notes.txt with the tool and say what is in it."}],
              "tools": [{"type": "function", "function": {"name": "read", "description": "Read a file", "parameters": {
                  "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]}, timeout=600)
    calls = ((d.get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or []
    return a["tokens"] / a["writing_s"], any((c.get("function") or {}).get("name") == "read" for c in calls)


# ---------------------------------------------------------------- OpenCode on this computer

def key_env(host):
    return "KIWI_AI_%s_KEY" % re.sub(r"[^A-Z0-9]", "_", host.name.upper())


def opencode_block(host, model, quant, context, key_file=None, top_k=TOP_K, local_only=False):
    """OpenCode's settings for this machine's AI: the provider with the one model that is loaded,
    and that model as the one OpenCode uses. `context` is what Studio reports for it: OpenCode
    sums a chat up in time only when it knows the limit. The key comes from `key_file`, else from
    the environment (key_env). `local_only` hides OpenCode's own cloud models."""
    pid = "kiwi-" + host.name
    block = {"$schema": "https://opencode.ai/config.json", "provider": {pid: {
        "npm": "@ai-sdk/openai-compatible", "name": "Local AI on %s" % host.name,
        # An hour of patience: a full context takes minutes to read before the first word comes.
        # Numbers only for the two below: OpenCode 2.1 drops the whole provider without a word on
        # "chunkTimeout": false, and then answers from its own cloud model.
        "options": {"baseURL": base_url(host) + "/v1",
                    "apiKey": "{file:%s}" % key_file if key_file else "{env:%s}" % key_env(host),
                    "timeout": False, "headerTimeout": HOUR_MS, "chunkTimeout": HOUR_MS},
        "models": {model: {"name": "%s%s on %s" % (short(model), " " + quant if quant else "", host.name),
                           "tool_call": True, "options": {"top_k": int(top_k)},
                           "limit": {"context": int(context), "output": min(OUTPUT, int(context) // 4)}}}}},
        "model": "%s/%s" % (pid, model)}
    block["small_model"] = block["model"]           # titles from the same model: nothing leaves the house
    if local_only:
        block["disabled_providers"] = ["opencode"]
    return block


def merged(conf, block):
    """OpenCode's config with our block in it, everything else kept."""
    conf = dict(conf)
    conf.setdefault("$schema", block["$schema"])
    conf["provider"] = dict(conf.get("provider") or {}, **block["provider"])
    conf["model"], conf["small_model"] = block["model"], block["small_model"]
    for p in block.get("disabled_providers") or []:
        if p not in (conf.get("disabled_providers") or []):
            conf["disabled_providers"] = list(conf.get("disabled_providers") or []) + [p]
    return conf


def write_opencode(path, block):
    """Our block into this OpenCode config. Returns "same" when it was there already, else the
    path of the copy of the old file ("" when there was none). A file that is not plain JSON is
    not touched."""
    try:
        text = util.read_text(path)
    except OSError:
        text = ""
    old = None
    if text.strip():
        try:
            old = json.loads(text)
        except ValueError:
            old = None
        if not isinstance(old, dict):
            raise KiwiError("%s is not plain JSON (comments?) and was not touched: take the comments out, "
                            "or put the block in by hand (connect without --apply prints it)" % util.shown_path(path))
    new = merged(old or {}, block)
    if new == old:
        return "same"
    backup = ""
    if old is not None:
        backup = path + ".before-kiwi"
        shutil.copy2(path, backup)
    util.write_text(path, json.dumps(new, indent=2) + "\n", 0o644 if old is None else os.stat(path).st_mode & 0o777)
    return backup


def opencodes(home=None):
    """Where an OpenCode lives on this computer: (key, what it is called, its config file)."""
    home = home or os.path.expanduser("~")
    found = []
    if os.path.isdir(os.path.join(home, ".var/app", FLATPAK)):
        found.append(("flatpak", "OpenCode (Flatpak)", os.path.join(home, ".var/app", FLATPAK, "config/opencode/opencode.json")))
    if os.path.isdir(os.path.join(home, ".config/opencode")):
        found.append(("cli", "opencode in the terminal", os.path.join(home, ".config/opencode/opencode.json")))
    return found


def ca_copy(fleet, host, home=None):
    """The network's CA where OpenCode finds it also while the secret store is locked: a copy of
    the certificate (no secret) under ~/.local/share/kiwi. None when there is neither."""
    home = home or os.path.expanduser("~")
    src, dst = fleet.resolve_path("secrets/ca/kiwiCA.pem"), os.path.join(home, ".local/share/kiwi/kiwiCA.pem")
    if not os.path.isfile(src):
        return dst if os.path.isfile(dst) else None
    pem = util.read_text(src)
    if os.path.isfile(dst):
        if pem.strip() in util.read_text(dst):
            return dst
        dst = os.path.join(home, ".local/share/kiwi/kiwiCA-%s.pem" % host.name)      # that one is another network's
        if os.path.isfile(dst) and pem.strip() in util.read_text(dst):
            return dst
    util.write_text(dst, pem)
    return dst


def flatpak_ca(ca_file, run=subprocess.run):
    """The Flatpak trusts the network's CA: NODE_EXTRA_CA_CERTS as an override of the user's.
    "set" when it was missing and is set now, "there", or the other file it names already (left)."""
    if not shutil.which("flatpak"):
        return "there"
    shown = run(["flatpak", "override", "--user", "--show", FLATPAK], capture_output=True, text=True).stdout or ""
    m = re.search(r"^NODE_EXTRA_CA_CERTS=(.*)$", shown, re.M)
    if m:
        return "there" if m.group(1).strip() == ca_file else m.group(1).strip()
    run(["flatpak", "override", "--user", "--env=NODE_EXTRA_CA_CERTS=%s" % ca_file, FLATPAK], check=True)
    return "set"


def key_copy(fleet, host, home=None):
    """The API key in a file of this computer's user, for OpenCode: it reads the key whenever it
    starts, also while the secret store is locked. Returns (path, written now)."""
    home = home or os.path.expanduser("~")
    path, key = os.path.join(home, ".config/kiwi/%s-ai.key" % host.name), _key(fleet, host)
    if os.path.isfile(path) and util.read_text(path).strip() == key:
        return path, False
    util.write_text(path, key + "\n", 0o600)
    return path, True


def connect(fleet, host, now, top_k=TOP_K, local_only=False, home=None, say=util.say, run=subprocess.run):
    """Every OpenCode on this computer set up for the loaded model (`now`, from ready()). Returns
    what was written."""
    found = opencodes(home)
    if not found:
        say("no OpenCode found on this computer: nothing written (connect without --apply prints the settings)")
        return []
    key_file, new_key = key_copy(fleet, host, home)
    if new_key:
        say("the API key: a copy for OpenCode in %s, outside the secret store" % util.shown_path(key_file))
    ca = ca_copy(fleet, host, home)
    block = opencode_block(host, now["model"], now["quant"], now["context"], key_file, top_k, local_only)
    wrote = []
    for kind, label, path in found:
        backup = write_opencode(path, block)
        say("%s: %s" % (label, "already set up" if backup == "same" else
                        "set up" + (" (the old file: %s)" % os.path.basename(backup) if backup else "")))
        if ca and kind == "flatpak":
            got = flatpak_ca(ca, run)
            if got == "set":
                say("%s now trusts the network's CA" % label)
            elif got != "there":
                say("%s takes its CAs from %s: the network's CA (%s) has to be in that file" % (label, got, util.shown_path(ca)))
        elif ca and os.environ.get("NODE_EXTRA_CA_CERTS") != ca:
            say("%s: start it with NODE_EXTRA_CA_CERTS=%s, the network's CA" % (label, ca))
        if backup != "same":
            wrote.append(label)
    return wrote
