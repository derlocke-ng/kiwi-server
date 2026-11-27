"""A careful apply: `kiwi-server apply FLEET [MACHINES | --changed] --careful` (notes/TODO.md 15b).

A plain apply of several machines is made to be quick: the masters first, then everything that
answers for names at once, then the rest at once, and a machine that failed does not stop the
others. That is right after a change that is known to be good. A careful apply is for a change
that is not: a new release, a setting nobody tried yet.

- One machine after the other, the least important first: the machines nothing else leans on, then
  the ones that answer for names or open disks, then the masters, and the control node that runs
  this last. Of two machines that share a moving address, the one that waits goes before the one
  that holds it, so the address has a machine with its services up at every moment.
- After each machine its own sites are asked, from the machine itself, until they answer or two
  minutes have passed.
- The first machine that fails, or whose sites do not answer, stops the apply: the machines after it
  are not touched, and it says which.
- A machine that does not answer at all is left out and said at the end: nothing was changed there.
"""
import shlex
import time

from . import remote, util
from .util import KiwiError

WAIT = 120          # seconds a machine's sites get to answer after its apply

# stdin: nothing. NAMES: the machine's own sites. Asked on the machine, where its names lead to its own
# reverse proxy. A redirect, a login or a refusal is an answer; 5xx and no answer are not. The
# certificate is not judged here (kiwi-server check does that from where people are).
VERIFY_SCRIPT = r'''
bad=
for n in $NAMES; do
    code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 "https://$n/" 2>/dev/null)
    case ${code:-000} in 2??|3??|4??) ;; *) bad="$bad $n:${code:-000}" ;; esac
done
echo "bad$bad"
'''

HOLDS_SCRIPT = "ip -4 -o addr show 2>/dev/null | grep -q ' %s/' && echo holds || echo waits"


def names_of(renderer, host):
    """The machine's own sites under the fleet's domain, as its reverse proxy answers them."""
    renderer.scan()
    domain = "." + str(host.domain or "home")
    return sorted(n for n in (renderer._names.get(host.hostname) or [])
                  if n.endswith(domain) and n != host.hostname and "*" not in n and n not in renderer.aliases)


def holds(host, address, run=remote.run, reach=remote.reach):
    """Does this machine hold the site's moving address now? None when it does not say."""
    try:
        out = run(reach(host), HOLDS_SCRIPT % address).decode(errors="replace").strip()
    except (KiwiError, OSError):
        return None
    return out == "holds" if out in ("holds", "waits") else None


def order(fleet, roles, hosts, renderer, holds=holds, me=None):
    """The machines one after the other, the least important first: leaves, then the ones that answer
    for names or open disks (of a pair with a moving address the one that waits first), then standby
    masters, the masters, and the machine this runs on (`me`, the control node) last."""
    def master(h):
        return h.role in roles and roles[h.role].node_type == "master"
    mine = [h for h in hosts if h.name == me]
    rest = [h for h in hosts if h.name != me]
    masters = [h for h in rest if master(h) and not h.standby_of]
    standbys = [h for h in rest if h.standby_of]
    nodes = [h for h in rest if h not in masters and h not in standbys]
    core = [h for h in nodes if {"dns", "tang", "vip", "gateway"} & set(h.modules or [])]
    leaves = [h for h in nodes if h not in core]
    # of the machines that share a moving address: the one that holds it now after the others
    holding = {}
    for h in core:
        pair = renderer.vip_pair(h) if "vip" in (h.modules or []) else []
        if len(pair) > 1:
            address = str(((h.role_settings_raw or {}).get("vip") or {}).get("address") or "").split("/")[0]
            holding[h.name] = bool(holds(h, address)) if address else False
    core.sort(key=lambda h: (1 if holding.get(h.name) else 0, h.name))
    return sorted(leaves, key=lambda h: h.name) + core + standbys + masters + mine


def answers(host, tgt, names, run=remote.run, wait=WAIT, sleep=time.sleep, clock=time.monotonic):
    """The machine's sites that do not answer after its apply: [] when all do. Asked on the machine
    every few seconds until all answer or `wait` seconds have passed (a container that restarted
    takes a moment)."""
    if not names:
        return []
    script = "NAMES=%s\n%s" % (shlex.quote(" ".join(names)), VERIFY_SCRIPT)
    end, bad = clock() + wait, []
    while True:
        try:
            out = run(tgt, script).decode(errors="replace")
        except (KiwiError, OSError) as e:
            bad = ["%s (%s)" % (host.name, str(e).splitlines()[-1][:80])]
        else:
            line = next((ln for ln in out.splitlines() if ln.startswith("bad")), "bad ?")
            bad = line[3:].split()
        if not bad or clock() >= end:
            return bad
        sleep(5)
