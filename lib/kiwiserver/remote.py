"""Talking to a machine the fleet built: SSH as the admin user, sudo on the
other side. Everything that reaches a node goes through here — the fleet
backup, `apply`, `status`, the tunnel `enroll` uses to reach the master's
wg-easy — so one place knows how a host is addressed and how a failure reads.
"""
import os
import shlex
import socket
import subprocess
import time

from .util import KiwiError

# ControlMaster=no: an operator's ~/.ssh/config often multiplexes sessions, and a
# channel-less `ssh -N -L` through an existing mux master ended with "Broken pipe"
# and exit 0 — the enroll tunnel died silently (found on hardware). Every
# connection kiwi-server opens is its own.
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ControlMaster=no", "-o", "ControlPath=none"]
KNOWN_HOSTS = ""   # the fleet's own known_hosts (secrets/known_hosts): set by configure()
IDENTITIES = []    # private keys of the fleet's admin.ssh_keys that exist next to their .pub


def configure(known_hosts, identities=()):
    """The fleet keeps the machines' host keys next to its other secrets: a
    fresh machine is accepted on first contact, a changed key (a reinstall)
    is refused with a message that says what to do. The machines trust the
    keys in admin.ssh_keys; when the private key of one lies next to its .pub
    (a key made for the fleet, under its directory), ssh uses those keys and
    no other — the operator's agent may hold keys the machines never saw."""
    global KNOWN_HOSTS, IDENTITIES
    KNOWN_HOSTS = known_hosts or ""
    IDENTITIES = [str(i) for i in identities]


def ssh_opts(identities=True):
    opts = list(SSH_OPTS)
    if KNOWN_HOSTS:
        opts += ["-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=%s" % KNOWN_HOSTS]
    if IDENTITIES and identities:
        opts += ["-o", "IdentitiesOnly=yes"]
        for i in IDENTITIES:
            opts += ["-i", i]
    return opts


def target(host, override=None):
    """user@address: the admin user at the hostname, unless told otherwise."""
    return override or "%s@%s" % (host.admin["user"], host.hostname)


def _on_record(address):
    """Has this fleet met the machine at that address? ssh-keygen also reads hashed entries."""
    if not KNOWN_HOSTS or not os.path.isfile(KNOWN_HOSTS):
        return False
    try:
        r = subprocess.run(["ssh-keygen", "-F", address, "-f", KNOWN_HOSTS], capture_output=True)
    except FileNotFoundError:
        return False
    return r.returncode == 0 and bool(r.stdout.strip())


def _answers(address, port=22, timeout=2):
    try:
        socket.create_connection((address, port), timeout=timeout).close()
        return True
    except OSError:
        return False


def addresses(host):
    """Where this machine may answer, its name first."""
    found = [host.hostname]
    net = host.cfg.get("network") or {}
    if not net.get("dhcp", True) and net.get("address"):
        found.append(str(net["address"]).split("/", 1)[0])
    found.append((host.role_settings_raw.get("vpn-server") or {}).get("wg_host"))
    found.append((host.role_settings or {}).get("vpn_ip"))
    return [a for i, a in enumerate(found) if a and a not in found[:i]]


def reach(host, override=None):
    """user@address, for an address that answers from where we are now.

    `.home` names only resolve on the mesh, so the fleet's other addresses for
    the machine are tried too, but only ones whose host key is already on
    record: an address on someone else's LAN must never get a role script
    because the first key it showed was accepted. Nothing on record: the name.
    """
    if override:
        return override
    for address in addresses(host):
        if _on_record(address) and _answers(address):
            return "%s@%s" % (host.admin["user"], address)
    return target(host)


def _fail(where, r):
    text = (r.stderr or b"").decode(errors="replace")
    lines = text.strip().splitlines() or ["ssh failed"]
    if "REMOTE HOST IDENTIFICATION HAS CHANGED" in text or "Host key verification failed" in text:
        host = where.rsplit("@", 1)[-1]
        raise KiwiError("%s: the machine's SSH host key is not the one on record (reinstalled?) — when that is expected: "
                        "ssh-keygen -R %s -f %s" % (where, host, KNOWN_HOSTS or "~/.ssh/known_hosts"))
    raise KiwiError("%s: %s" % (where, lines[-1]))


def run(tgt, script, data=None, runner=subprocess.run, options=()):
    """A shell script as root on the machine; stdout as bytes. `options` are more for ssh."""
    argv = ["ssh"] + ssh_opts() + list(options) + [tgt, "sudo sh -c " + shlex.quote(script)]
    try:
        r = runner(argv, input=data, capture_output=True)
    except FileNotFoundError:
        raise KiwiError("ssh is not installed")
    if r.returncode != 0:
        _fail(tgt, r)
    return r.stdout


def run_stream(tgt, script, runner=subprocess.run):
    """Like run(), but the output goes straight to the terminal (a role
    script takes minutes and talks)."""
    argv = ["ssh"] + ssh_opts() + [tgt, "sudo sh -c " + shlex.quote(script)]
    try:
        r = runner(argv, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise KiwiError("ssh is not installed")
    return r.returncode


def put(tgt, data, path, mode="0600", runner=subprocess.run):
    """A file onto the machine, written as root, atomically."""
    q = shlex.quote(path)
    script = ("set -e; install -d -m 0700 \"$(dirname %s)\"; cat > %s.tmp; chmod %s %s.tmp; mv -f %s.tmp %s"
              % (q, q, shlex.quote(mode), q, q, q))
    run(tgt, script, data, runner)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Tunnel:
    """`with Tunnel(target, 51821) as url:` — a local port forwarded to a
    port on the machine's loopback, for the web APIs the stacks publish on
    127.0.0.1 only (wg-easy)."""

    def __init__(self, tgt, remote_port, remote_host="127.0.0.1"):
        self.tgt, self.remote_port, self.remote_host = tgt, remote_port, remote_host
        self.port = free_port()
        self.proc = None

    def __enter__(self):
        argv = ["ssh"] + ssh_opts() + ["-o", "ExitOnForwardFailure=yes", "-N",
                                     "-L", "%d:%s:%d" % (self.port, self.remote_host, self.remote_port), self.tgt]
        try:
            self.proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE)
        except FileNotFoundError:
            raise KiwiError("ssh is not installed")
        deadline = time.time() + 20
        while time.time() < deadline:
            if self.proc.poll() is not None:
                err = (self.proc.stderr.read() or b"").decode(errors="replace").strip().splitlines()
                self.proc.stderr.close()
                raise KiwiError("%s: the tunnel to port %d failed: %s" % (self.tgt, self.remote_port, (err or ["ssh exited"])[-1]))
            try:
                s = socket.create_connection(("127.0.0.1", self.port), timeout=1)
                s.close()
                return "http://127.0.0.1:%d" % self.port
            except OSError:
                time.sleep(0.3)
        self.__exit__(None, None, None)
        raise KiwiError("%s: the tunnel to port %d did not come up" % (self.tgt, self.remote_port))

    def __exit__(self, *_exc):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.proc and self.proc.stderr:
            self.proc.stderr.close()
        return False
