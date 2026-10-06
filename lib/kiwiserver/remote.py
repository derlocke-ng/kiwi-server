"""Talking to a machine the fleet built: SSH as the admin user, sudo on the
other side. Everything that reaches a node goes through here — the fleet
backup, `apply`, `status`, the tunnel `enroll` uses to reach the master's
wg-easy — so one place knows how a host is addressed and how a failure reads.
"""
import shlex
import socket
import subprocess
import time

from .util import KiwiError

SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]


def target(host, override=None):
    """user@address: the admin user at the hostname, unless told otherwise."""
    return override or "%s@%s" % (host.admin["user"], host.hostname)


def _fail(where, r):
    lines = (r.stderr or b"").decode(errors="replace").strip().splitlines() or ["ssh failed"]
    raise KiwiError("%s: %s" % (where, lines[-1]))


def run(tgt, script, data=None, runner=subprocess.run):
    """A shell script as root on the machine; stdout as bytes."""
    argv = ["ssh"] + SSH_OPTS + [tgt, "sudo sh -c " + shlex.quote(script)]
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
    argv = ["ssh"] + SSH_OPTS + [tgt, "sudo sh -c " + shlex.quote(script)]
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
        argv = ["ssh"] + SSH_OPTS + ["-o", "ExitOnForwardFailure=yes", "-N",
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
