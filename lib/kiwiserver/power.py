"""Switch a machine off or restart it (the owner, 19 Nov 2026: "we should have a shutdown button"),
from the terminal, the control page and the app; `wake` is the way back on. Before, it says in
words what that means for this machine: the master takes the VPN with it, a disk without rings
waits for its key, a machine without Wake-on-LAN comes back only by hand.
"""
import os

from . import remote, unlock, util
from .util import KiwiError

ACTIONS = ("off", "restart")


def what(host, roles, action):
    """[sentences]: what switching this machine off or restarting it means."""
    if action not in ACTIONS:
        raise KiwiError("off or restart")
    role = roles.get(host.role)
    master = role is not None and role.node_type == "master"
    control = "control" in (host.modules or []) or os.environ.get("KIWI_CONTROL_HOST") == host.name
    out = []
    if action == "off":
        out.append("%s shuts down and stays off." % host.name)
    else:
        out.append("%s restarts: gone for a few minutes, then back by itself." % host.name)
    if master:
        out.append("It is the master: the VPN and everything reached through it stop until it is back.")
    if control:
        out.append("It runs the control page: the page goes away until it is back.")
    way = unlock._way(host)
    if way == "by a person":
        out.append("Its disk opens only with its key: at boot it waits (kiwi-server unlock … --manual, or Unlock on its page).")
    elif way == "by itself" and action == "restart":
        out.append("Its disk opens by itself at boot if enough of its rings answer.")
    if action == "off":
        if (host.cfg.get("network") or {}).get("wol"):
            out.append("Wake-on-LAN starts it again (Wake it, or kiwi-server wake).")
        elif master or (host.cfg.get("vps") or {}).get("provider"):
            out.append("Start it again at its provider.")
        else:
            out.append("Only someone at the machine can switch it on again.")
    return out


SCRIPT = {"off": "systemctl poweroff --no-block", "restart": "systemctl reboot --no-block"}


def run(host, action, ssh=None, runner=None):
    """Tell the machine; the connection may end with it."""
    go = runner or remote.run
    tgt = remote.reach(host, ssh)
    try:
        go(tgt, SCRIPT[action] + "; echo ok")
    except KiwiError as e:
        if "255" not in str(e) and "closed" not in str(e).lower():
            raise
    util.say("%s: %s" % (host.name, "switching off" if action == "off" else "restarting"))
