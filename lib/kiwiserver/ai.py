"""The local AI (the ai module, docs/design/ai.md): its API key in the store, opencode's provider
block, and a benchmark of the models it has.

    kiwi-server ai key FLEET MACHINE        the key made on its page (Settings, API), from stdin
                                            (Unsloth Studio; llama.cpp's key is made by kiwi-server)
    kiwi-server ai connect FLEET MACHINE    opencode's provider block (opencode.json)
    kiwi-server ai bench FLEET MACHINE [MODEL...]

The API is OpenAI's (/v1/models, /v1/chat/completions) at https://ai.<hostname>, with the fleet's
CA. The benchmark asks each model the same things: the time to its first token, its tokens per
second, and a few tasks whose answers are known. Its results are kept in ai-bench/<machine>.jsonl
beside the fleet file, to compare models and machines over time.
"""
import json
import os
import re
import ssl
import time
import urllib.error
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


def _call(fleet, host, path, body=None, timeout=300, stream=False):
    key = api_key(fleet, host)
    if not key:
        raise KiwiError("%s: no API key on record: make one on https://ai.%s (Settings, API), then "
                        "kiwi-server ai key FLEET %s" % (host.name, host.hostname, host.name))
    req = urllib.request.Request(base_url(host) + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                                 method="POST" if body is not None else "GET")
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=_ctx(fleet))
    except urllib.error.HTTPError as e:
        raise KiwiError("%s: the AI said %d to %s" % (host.name, e.code, path))
    except (urllib.error.URLError, OSError) as e:
        raise KiwiError("%s: the AI does not answer at %s (%s): is the machine on?" % (host.name, base_url(host), e))


def models(fleet, host):
    with _call(fleet, host, "/v1/models", timeout=30) as r:
        return [m["id"] for m in (json.load(r).get("data") or []) if m.get("id")]


def ask(fleet, host, model, prompt, max_tokens=300):
    """One streamed answer: (text, seconds to the first token, tokens written, seconds writing)."""
    body = {"model": model, "stream": True, "max_tokens": max_tokens, "temperature": 0,
            "messages": [{"role": "user", "content": prompt}], "stream_options": {"include_usage": True}}
    t0 = time.monotonic()
    first, text, tokens, chunks = None, [], None, 0
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
                piece = (c.get("delta") or {}).get("content") or ""
                if piece:
                    if first is None:
                        first = time.monotonic() - t0
                    text.append(piece)
                    chunks += 1
            if d.get("usage"):
                tokens = d["usage"].get("completion_tokens")
    end = time.monotonic() - t0
    return "".join(text), first or end, tokens or chunks, max(0.001, end - (first or end))


def bench(fleet, host, names=None, say=util.say):
    """Each model: speed (short and long context) and the tasks. Returns the rows, kept in the fleet folder."""
    names = names or models(fleet, host)
    if not names:
        raise KiwiError("%s: no model is loaded or downloaded yet (%s)" % (
            host.name, "ai.models names them" if lean(host) else "its page: choose one"))
    rows = []
    for m in names:
        say("%s: %s" % (host.name, m))
        _t, ttft, n, writing = ask(fleet, host, m, SPEED_PROMPT, 400)
        _t2, ttft_long, _n2, _w2 = ask(fleet, host, m, LONG_CONTEXT + "\n\nHow many sentences were above? A number.", 20)
        right = []
        for name, prompt, pattern in TASKS:
            answer, _a, _b, _c = ask(fleet, host, m, prompt, 200)
            if re.search(pattern, answer, re.S):
                right.append(name)
        row = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "machine": host.name, "model": m,
               "first_token_s": round(ttft, 2), "tokens_per_s": round(n / writing, 1),
               "first_token_long_s": round(ttft_long, 2), "tasks": "%d/%d" % (len(right), len(TASKS)), "right": right}
        rows.append(row)
        say("  first token %.2f s, %.1f tokens/s, with 4000 tokens to read %.2f s, tasks %s"
            % (row["first_token_s"], row["tokens_per_s"], row["first_token_long_s"], row["tasks"]))
    path = os.path.join(fleet.dir, "ai-bench", "%s.jsonl" % host.name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return rows


def opencode_block(fleet, host, names):
    """opencode's provider for this machine's AI: its models, the key in an environment variable."""
    env = "KIWI_AI_%s_KEY" % re.sub(r"[^A-Z0-9]", "_", host.name.upper())
    return env, {"$schema": "https://opencode.ai/config.json", "provider": {
        "kiwi-" + host.name: {"npm": "@ai-sdk/openai-compatible", "name": "Local AI on %s" % host.name,
                              "options": {"baseURL": base_url(host) + "/v1", "apiKey": "{env:%s}" % env},
                              "models": {m: {"name": m} for m in names}}}}
