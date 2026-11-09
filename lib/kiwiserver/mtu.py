"""How big a packet really gets through a tunnel, measured instead of guessed
(ROADMAP, problem 40).

Don't-fragment pings inside the tunnel, from the namespace that holds it: a
node pings the master from its VPN client, because the client's firewall drops
pings that come in; a device is pinged from the WireGuard server. WireGuard's
outer packets over IPv4 are not marked don't-fragment, so a router on the way
may split one and the ping still comes back. The reassembly counters on this
end are read around every try: a reply that had to be put together again does
not count as fitting. A packet split only on the way out, and put together by
the far end, cannot be seen from here.
"""
import re
import shlex

from . import remote
from .util import KiwiError

STEP = 16        # WireGuard pads to a multiple of 16, so only those are worth using
LOWEST = 1280    # the least IPv6 allows, and what validate accepts
SMALL = 84       # an ordinary ping: is anybody there at all?

_SCRIPT = r'''
set -u
rt=podman; command -v podman >/dev/null 2>&1 || rt=docker
pid=$($rt inspect -f '{{.State.Pid}}' %(container)s) || exit 3
reasm() { awk '/^Ip:/{ if(!h){for(i=1;i<=NF;i++)n[$i]=i;h=1}else print $n["ReasmOKs"]}'; }
count() { a=$(reasm </proc/net/snmp); b=$(nsenter -t "$pid" -n cat /proc/net/snmp | reasm); echo $((a+b)); }
before=$(count)
out=$(nsenter -t "$pid" -n ping -M "do" -c 3 -i 0.2 -W 2 -s %(payload)d %(addr)s 2>&1)
after=$(count)
echo "received=$(printf '%%s' "$out" | sed -n 's/.* \([0-9]*\) received.*/\1/p') reassembled=$((after-before))"
'''


def send(tgt, container, addr, size):
    """Three pings of `size` bytes of IP packet: (replies, packets reassembled meanwhile)."""
    script = _SCRIPT % {"container": shlex.quote(container), "addr": shlex.quote(addr), "payload": size - 28}
    out = remote.run(tgt, script).decode(errors="replace")
    m = re.search(r"received=(\d*) reassembled=(-?\d+)", out)
    if not m:
        raise KiwiError("%s: could not read the measurement: %s" % (tgt, out.strip()[:120]))
    return int(m.group(1) or 0), int(m.group(2))


def fits(result):
    received, reassembled = result
    return received >= 2 and reassembled < received     # other traffic may be reassembled too: one per reply is ours


def search(probe, cap, say):
    """The largest multiple of 16 up to `cap` that gets through whole.
    Returns (size or None, what was tried). `probe(size)` measures one size."""
    if probe(SMALL)[0] == 0:
        raise KiwiError("no answer to an ordinary ping through the tunnel: is it connected, and does it answer pings?")
    sizes = list(range(LOWEST, cap + 1, STEP))
    if not sizes:
        raise KiwiError("the tunnel's MTU is %d, below the %d this can measure" % (cap, LOWEST))
    tried = []

    def good(size):
        r = probe(size)
        ok = fits(r)
        tried.append({"size": size, "received": r[0], "reassembled": r[1], "fits": ok})
        say("  %d bytes: %s" % (size, "fits" if ok else "reassembled on the way" if r[0] else "lost"))
        return ok
    if good(sizes[-1]):
        return sizes[-1], tried
    if len(sizes) == 1 or not good(sizes[0]):
        return None, tried
    low, high = 0, len(sizes) - 1                        # sizes[low] fits, sizes[high] does not
    while high - low > 1:
        mid = (low + high) // 2
        if good(sizes[mid]):
            low = mid
        else:
            high = mid
    return sizes[low], tried


def set_wg_mtu(text, host, block, value):
    """The fleet file with `wg_mtu: value` in a host's role block, everything
    else as it was. None when the file is not laid out so that one line can do it."""
    lines = text.split("\n")
    start = next((i for i, l in enumerate(lines) if re.match(r"  %s:\s*(#.*)?$" % re.escape(host), l)), None)
    if start is None:
        return None
    stop = next((i for i in range(start + 1, len(lines))
                 if lines[i].strip() and not lines[i].lstrip().startswith("#") and len(lines[i]) - len(lines[i].lstrip()) <= 2), len(lines))
    head = next((i for i in range(start + 1, stop) if re.match(r"    %s:\s*(#.*)?$" % re.escape(block), lines[i])), None)
    if head is None:
        return None
    end = next((i for i in range(head + 1, stop)
                if lines[i].strip() and not lines[i].lstrip().startswith("#") and len(lines[i]) - len(lines[i].lstrip()) <= 4), stop)
    for i in range(head + 1, end):
        m = re.match(r"(\s+wg_mtu:\s*)[^#\s]+(.*)$", lines[i])
        if m and len(lines[i]) - len(lines[i].lstrip()) == 6:
            lines[i] = "%s%d%s" % (m.group(1), value, m.group(2))
            return "\n".join(lines)
    lines.insert(head + 1, "      wg_mtu: %d" % value)
    return "\n".join(lines)
