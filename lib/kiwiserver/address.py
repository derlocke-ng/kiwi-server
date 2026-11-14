"""A machine's fixed address at home, after it was installed.

An installer writes the address into a machine installed from media. A board starts from an
image and asks the router, and an address changed in the fleet file reached no machine at
all. After an apply the machine is asked what it has; when that is not what the fleet file
says, `kiwi-stack address` on the machine changes it (where systemd-networkd runs the
network: Armbian) and puts the old one back when the new one does not work. It runs
detached, since the connection it was started over goes away with the old address.
"""
import os
import shlex
import subprocess
import time

from . import remote, util
from .util import KiwiError

WAIT = 90       # seconds for the machine to answer at its new address, or again at the old one


def wanted(host):
    """(address/prefix, gateway, [dns]) the fleet file gives the machine, or None (the router decides)."""
    net = host.cfg.get("network") or {}
    if net.get("dhcp", True) or not net.get("address"):
        return None
    address = str(net["address"])
    if "/" not in address:
        address += "/24"
    return address, str(net.get("gateway") or ""), [str(d) for d in net.get("dns") or []]


def _known_as(old, new, port):
    """The key on record for the machine at `old` is its key at `new` too: the same machine,
    told a moment ago to take that address."""
    known = remote.KNOWN_HOSTS
    if not known or not os.path.isfile(known):
        return
    name = lambda a: a if int(port) == 22 else "[%s]:%d" % (a, int(port))     # noqa: E731
    found = subprocess.run(["ssh-keygen", "-F", name(old), "-f", known], capture_output=True, text=True).stdout
    keys = [" ".join(x.split()[1:3]) for x in found.splitlines() if x and not x.startswith("#")]
    if not keys:        # on record for the plain address, as ssh keeps it after a port moved
        found = subprocess.run(["ssh-keygen", "-F", old, "-f", known], capture_output=True, text=True).stdout
        keys = [" ".join(x.split()[1:3]) for x in found.splitlines() if x and not x.startswith("#")]
    subprocess.run(["ssh-keygen", "-R", name(new), "-f", known], capture_output=True)
    if os.path.isfile(known + ".old"):
        os.remove(known + ".old")
    with open(known, "a") as fh:
        for key in keys:
            fh.write("%s %s\n" % (name(new), key))


def settle(host, tgt, say=util.say, warn=util.warn, wait=WAIT, sleep=time.sleep):
    """The machine at the address the fleet file gives it. Returns where it answers from now on."""
    want = wanted(host)
    if not want:
        return tgt
    address, gateway, dns = want
    ip = address.split("/")[0]
    try:
        has = remote.run(tgt, "ip -o -4 addr show scope global | awk '{print $4}'").decode().split()
    except KiwiError:
        return tgt
    if any(a.split("/")[0] == ip for a in has):
        return tgt
    was = remote.address_of(tgt)
    if not gateway:
        warn("%s has %s and the fleet file says %s: it needs network.gateway (your router's address) to change it"
             % (host.name, ", ".join(has) or "no address", address))
        return tgt
    try:
        remote.run(tgt, "/usr/local/bin/kiwi-stack address")       # can this machine change it?
    except KiwiError as e:
        why = str(e).splitlines()[-1].replace("kiwi-stack: ", "")
        warn("%s has %s and the fleet file says %s. %s" % (host.name, ", ".join(has) or "no address", address,
                                                             why[0].upper() + why[1:] if why else ""))
        return tgt
    say("%s: its address at home becomes %s (it has %s)" % (host.name, address, ", ".join(has)))
    cmd = "/usr/local/bin/kiwi-stack address %s" % " ".join(shlex.quote(x) for x in [address, gateway] + dns)
    remote.run(tgt, "systemctl reset-failed kiwi-address 2>/dev/null; systemd-run --collect --quiet --unit=kiwi-address %s" % cmd)
    port = remote.port_of(tgt)
    new = remote.with_port("%s@%s" % (host.admin["user"], ip), port)
    end = time.time() + wait
    while time.time() < end:
        sleep(3)
        if remote._answers(ip, port):
            _known_as(was, ip, port)
            if remote.run_ok(new):
                say("%s: it answers at %s now" % (host.name, ip))
                return new
    # not there: the machine put back what it had, and says why
    why = ""
    try:
        out = remote.run(tgt, "journalctl -u kiwi-address -n 5 --no-pager -o cat 2>/dev/null").decode()
        why = next((x.replace("kiwi-stack: ", "") for x in reversed(out.splitlines()) if "kiwi-stack:" in x), "")
    except KiwiError:
        pass
    warn("%s did not take %s%s" % (host.name, address, ": " + why if why else
                                   " and does not answer at %s either: look at it" % was))
    return tgt
