#!/usr/bin/env bash
# A lean local AI answers: the ai module with engine: llama (llama.cpp's server alone,
# docs/design/ai.md), through the machine's reverse proxy, with the key kiwi-server made.
#
#   tests/drills/ai-lean.sh FLEET MACHINE [ADDRESS]
#
# MACHINE: one that runs ai with engine: llama and is applied. ADDRESS: its address, for the time
# the network's DNS does not know ai.<machine> yet (the DNS machines not applied since).
# Checks: the page, the API refusing a request without the key, the models the fleet names, one
# answer from the first model, and, when ai.idle_minutes is 1 to 3, that the model leaves the
# memory when idle and comes back with the next request. One PASS or FAIL line per check.
# Nothing on the machine changes. The key is never printed.
set -u
fleet=${1:?the fleet file}; m=${2:?a machine}; addr=${3:-}
ks=${KIWI_SERVER_LIB:-$(cd "$(dirname "$0")/../.." && pwd)/lib}
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
FLEET=$fleet MACHINE=$m ADDRESS=$addr KS=$ks python3 - <<'EOF'
import argparse, json, os, socket, ssl, sys, time, urllib.error, urllib.request
sys.path.insert(0, os.environ["KS"])
from kiwiserver import ai, cli, roles as rolesmod, util

fails = []


def check(ok, what):
    print("%s %s %s" % (time.strftime("%H:%M:%S"), "PASS" if ok else "FAIL", what), flush=True)
    if not ok:
        fails.append(what)


util.QUIET = True
fleet, roles, ms = cli.load(argparse.Namespace(fleet=os.environ["FLEET"], roles=None, modules=None))
h = fleet.select([os.environ["MACHINE"]])[0]
rolesmod.resolve_settings(roles[h.role], h, ms)
s = (h.module_settings or {}).get("ai") or {}
if "ai" not in (h.modules or []) or not ai.lean(h):
    sys.exit("%s does not run the ai module with engine: llama: nothing to drill" % h.name)
name = "ai." + h.hostname
if os.environ["ADDRESS"]:
    real = socket.getaddrinfo
    socket.getaddrinfo = lambda host, *a, **k: real(os.environ["ADDRESS"] if host == name else host, *a, **k)
ca = fleet.resolve_path("secrets/ca/kiwiCA.pem")
ctx = ssl.create_default_context(cafile=ca if os.path.isfile(ca) else None)


def code(path, headers=None):
    req = urllib.request.Request("https://%s%s" % (name, path), headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError) as e:
        return str(e)


def status():     # by every name a model answers to: its id and its aliases (the fleet's name, see ai.lean_id)
    with ai._call(fleet, h, "/v1/models", timeout=20) as r:
        return {n: (x.get("status") or {}).get("value") for x in json.load(r)["data"]
                for n in [x["id"]] + list(x.get("aliases") or [])}


check(code("/", {"Accept-Encoding": "gzip"}) == 200, "its chat page opens (https://%s)" % name)
check(code("/health") == 200, "llama.cpp says it is healthy")
check(code("/v1/models") == 401, "the API refuses a request without the key")
check(code("/v1/models", {"Authorization": "Bearer not-the-key-0000000000"}) == 401, "and one with a wrong key")
try:
    have = status()
except Exception as e:      # noqa: BLE001 — a drill reports, whatever went wrong
    have = {}
    check(False, "the API answers with the fleet's key (%s)" % e)
want = [str(x) for x in s.get("models") or []]
check(bool(have) and all(x in have for x in want), "it offers the models the fleet names (%s)" % ", ".join(want))
if have:
    first = want[0] if want and want[0] in have else sorted(have)[0]
    try:
        got = ai.ask(fleet, h, first, "Say hello in five words.", 40)        # a dict since 2.310.0
        check(bool(got["text"].strip()), "%s answers (first token after %.1f s, %.1f tokens/s)"
              % (first, got["first_s"], got["tokens"] / got["writing_s"]))
    except Exception as e:  # noqa: BLE001
        check(False, "%s answers (%s)" % (first, e))
    idle = int(s.get("idle_minutes") or 0)
    if 1 <= idle <= 3:
        wait = idle * 60 + 20
        print("%s waiting %d s without a request" % (time.strftime("%H:%M:%S"), wait), flush=True)
        time.sleep(wait)
        check(status().get(first) in ("sleeping", "unloaded"), "idle for %d minute(s): %s left the memory" % (idle, first))
        try:
            got = ai.ask(fleet, h, first, "Say hello in five words.", 40)
            check(bool(got["text"].strip()) and status().get(first) == "loaded",
                  "the next request brings it back (first token after %.1f s)" % got["first_s"])
        except Exception as e:  # noqa: BLE001
            check(False, "the next request brings it back (%s)" % e)
    else:
        print("%s ai.idle_minutes is %d: the sleep check runs with 1 to 3" % (time.strftime("%H:%M:%S"), idle))
print("%s %s" % (time.strftime("%H:%M:%S"), "all passed" if not fails else "%d failed" % len(fails)))
sys.exit(len(fails))
EOF
