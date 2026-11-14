"""This computer on the network: a WireGuard connection of its own in NetworkManager, with
only the network's addresses through the tunnel and only the network's names asked of its
DNS. Everything else on the computer stays as it was: its Internet, its other names.

The ordinary way, a config imported by hand, gets one thing wrong on a desktop: the config's
DNS line becomes the computer's DNS for everything, or for nothing. NetworkManager and
systemd-resolved can route one name ending to one connection (`~home`), which is what a
second network next to the first needs.
"""
import glob
import os
import shutil
import subprocess
import tempfile

from . import util
from .util import KiwiError


def connection_name(domain):
    """The connection's and the interface's name: 15 characters at most for an interface."""
    return ("kiwi-" + "".join(c for c in domain.split(".")[0].lower() if c.isalnum()))[:15]


def _nm(*argv, check=True, runner=subprocess.run):
    r = runner(["nmcli"] + list(argv), capture_output=True, text=True)
    if check and r.returncode != 0:
        said = ((r.stderr or r.stdout).strip().splitlines() or ["nmcli failed"])[-1]
        if "nsufficient privileges" in said:        # a login over SSH, or a session that is not at the screen
            said += (". NetworkManager lets the person at the computer add a connection: run this in a terminal on "
                     "the computer itself, not over SSH")
        raise KiwiError("NetworkManager: %s" % said)
    return r


def have_networkmanager():
    return bool(shutil.which("nmcli")) and subprocess.run(["nmcli", "-t", "-f", "RUNNING", "general"],
                                                          capture_output=True, text=True).stdout.strip() == "running"


def exists(domain, runner=subprocess.run):
    """Does this computer have the network's connection already?"""
    r = _nm("-t", "-f", "NAME", "connection", "show", check=False, runner=runner)
    return connection_name(domain) in (r.stdout or "").splitlines()


def up(conf_path, domain, runner=subprocess.run):
    """The config as a NetworkManager connection that is up and comes up by itself. Returns its name."""
    name = connection_name(domain)
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    tmp = tempfile.mkdtemp(prefix="kiwi-connect-", dir=base)        # nmcli names the connection after the file
    try:
        path = os.path.join(tmp, name + ".conf")
        util.write_bytes(path, util.read_bytes(conf_path), 0o600)
        _nm("connection", "delete", name, check=False, runner=runner)       # an older one of this network
        _nm("connection", "import", "type", "wireguard", "file", path, runner=runner)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # only this network's names go to its DNS, and it is never the way to the Internet
    _nm("connection", "modify", name, "ipv4.dns-search", "~" + domain, "ipv4.never-default", "yes",
        "ipv6.method", "disabled", "connection.autoconnect", "yes", runner=runner)
    _nm("connection", "up", name, runner=runner)
    return name


def down(domain, runner=subprocess.run):
    return _nm("connection", "delete", connection_name(domain), check=False, runner=runner).returncode == 0


def browser_stores(home=None):
    """The certificate stores of this user's browsers (NSS): Chromium and its kin share one,
    each Firefox profile has its own; Flatpak browsers keep theirs under ~/.var/app."""
    home = home or os.path.expanduser("~")
    found = [os.path.join(home, ".pki", "nssdb")]
    for pattern in (".mozilla/firefox/*", ".var/app/org.mozilla.firefox/.mozilla/firefox/*",
                    ".var/app/*/.pki/nssdb", "snap/firefox/common/.mozilla/firefox/*", ".librewolf/*",
                    ".var/app/io.gitlab.librewolf-community/.librewolf/*"):
        found += glob.glob(os.path.join(home, pattern))
    return [d for d in found if os.path.isfile(os.path.join(d, "cert9.db"))]


def trust(ca_pem, domain, tc, home=None):
    """The network's certificate authority into this user's browsers, no administrator
    needed. `tc` runs certutil, from the toolchain image where the computer has none (an
    ostree desktop). Returns (the stores that took it, why none could when there is no
    certutil at all)."""
    stores = browser_stores(home)
    if not stores:
        return [], ""
    how, why = tc.how("certutil")
    if how is None:
        return [], why
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    tmp = tempfile.mkdtemp(prefix="kiwi-ca-", dir=base)     # the certificate alone: its key lies beside the original
    took = []
    try:
        pem = os.path.join(tmp, "ca.pem")
        util.write_bytes(pem, util.read_bytes(ca_pem), 0o644)
        for store in stores:
            r = tc.run(["certutil", "-d", "sql:" + store, "-A", "-t", "C,,", "-n", "Kiwi network (%s)" % domain, "-i", pem],
                       mounts=[store, tmp], capture=True, check=False, relabel=False)
            if r.returncode == 0:
                took.append(store)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return took, ""


def system_trust_command(ca_pem, domain):
    """The one command an administrator runs so every program on this computer trusts the network."""
    if os.path.isdir("/etc/pki/ca-trust"):
        return "sudo trust anchor --store %s" % ca_pem
    return ("sudo cp %s /usr/local/share/ca-certificates/kiwi-%s.crt && sudo update-ca-certificates"
            % (ca_pem, domain.split(".")[0]))
