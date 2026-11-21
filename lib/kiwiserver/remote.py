"""Talking to a machine the fleet built: SSH as the admin user, sudo on the
other side. Everything that reaches a node goes through here — the fleet
backup, `apply`, `status`, the tunnel `enroll` uses to reach the master's
wg-easy — so one place knows how a host is addressed and how a failure reads.
"""
import os
import re
import shlex
import socket
import subprocess
import threading
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


def ssh_port(host):
    return int((host.admin or {}).get("ssh_port") or 22)


def with_port(tgt, port):
    """user@address on a port: ssh's own URI form, which every ssh call here takes."""
    user_host = tgt[len("ssh://"):] if tgt.startswith("ssh://") else tgt
    if user_host.count(":") == 1 and "[" not in user_host:
        user_host = user_host.rsplit(":", 1)[0]
    return user_host if int(port) == 22 else "ssh://%s:%d" % (user_host, int(port))


def port_of(tgt):
    if tgt.startswith("ssh://") and tgt.count(":") >= 2:
        return int(tgt.rsplit(":", 1)[1])
    return 22


def address_of(tgt):
    rest = tgt[len("ssh://"):] if tgt.startswith("ssh://") else tgt
    rest = rest.rsplit("@", 1)[-1]
    return rest.rsplit(":", 1)[0] if tgt.startswith("ssh://") and rest.count(":") == 1 else rest


def target(host, override=None):
    """user@address: the admin user at the hostname, unless told otherwise; on admin.ssh_port."""
    if override:
        return override if override.startswith("ssh://") else with_port(override, ssh_port(host))
    return with_port("%s@%s" % (host.admin["user"], host.hostname), ssh_port(host))


def run_ok(tgt, timeout=30):
    """Does a login there work, the host key checked as everywhere?"""
    argv = ["ssh"] + ssh_opts() + ["-o", "ConnectTimeout=10", tgt, "true"]
    try:
        return subprocess.run(argv, capture_output=True, timeout=timeout).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _on_record(address, port=22):
    """Has this fleet met the machine at that address? ssh-keygen also reads hashed entries."""
    if not KNOWN_HOSTS or not os.path.isfile(KNOWN_HOSTS):
        return False
    # as ssh does: on another port, a key on record for the address itself counts too
    names = [address] if int(port) == 22 else ["[%s]:%d" % (address, int(port)), address]
    for name in names:
        try:
            r = subprocess.run(["ssh-keygen", "-F", name, "-f", KNOWN_HOSTS], capture_output=True)
        except FileNotFoundError:
            return False
        if r.returncode == 0 and r.stdout.strip():
            return True
    return False


def _answers(address, port=22, timeout=2):
    try:
        socket.create_connection((address, port), timeout=timeout).close()
        return True
    except OSError:
        return False


def speaks_ssh(address, port=22, timeout=4):
    """Does an SSH server answer there (its greeting, not only an open port)? A forwarding in
    between (QEMU's, a NAT) can take the connection although nothing listens behind it."""
    try:
        with socket.create_connection((address, port), timeout=timeout) as s:
            s.settimeout(timeout)
            return s.recv(64).startswith(b"SSH-")
    except OSError:
        return False


def addresses(host):
    """Where this machine may answer, its name first."""
    found = [host.hostname]
    net = host.cfg.get("network") or {}
    if not net.get("dhcp", True) and net.get("address"):
        found.append(str(net["address"]).split("/", 1)[0])
    found.append((host.role_settings_raw.get("vpn-server") or {}).get("wg_host"))
    found.append((host.role_settings or {}).get("vpn_ip") or _mesh_address(host))
    return [a for i, a in enumerate(found) if a and a not in found[:i]]


def _mesh_address(host):
    """A node's mesh address before its settings are resolved: the one written, else the Address
    of the WireGuard config the master gave it. A machine at another site is reached there before
    the home's DNS knows its name (`status` right after its first apply)."""
    raw = host.role_settings_raw or {}
    if raw.get("vpn_ip"):
        return str(raw["vpn_ip"])
    if "vpn-server" in raw or "vpn-server" in (raw.get("modules") or []) or not getattr(host, "fleet", None):
        return None         # a master's own client config is another network's (multi-hop)
    conf = (raw.get("vpn-client") or {}).get("wireguard_config") or "secrets/%s.conf" % host.hostname
    try:
        with open(host.fleet.resolve_path(conf), encoding="utf-8") as fh:
            for line in fh:
                key, _, value = line.partition("=")
                if key.strip() == "Address":
                    return value.strip().split(",")[0].split("/")[0].strip() or None
    except OSError:
        return None
    return None


def reach(host, override=None):
    """user@address, for an address that answers from where we are now.

    `.home` names only resolve on the mesh, so the fleet's other addresses for
    the machine are tried too, but only ones whose host key is already on
    record: an address on someone else's LAN must never get a role script
    because the first key it showed was accepted. Nothing on record: the name.
    """
    if override:
        return target(host, override) if remote_port_answers(override, ssh_port(host)) else override
    user, port = host.admin["user"], ssh_port(host)
    for p in ([port, 22] if port != 22 else [22]):      # 22 still, while a move to another port is under way
        for address in addresses(host):
            if _on_record(address, p) and _answers(address, p):
                return with_port("%s@%s" % (user, address), p)
        for address in addresses(host)[1:]:     # met nowhere yet: one whose certificate the fleet's CA signed
            if _ca_known() and _answers(address, p) and _certified(with_port("%s@%s" % (user, address), p)):
                return with_port("%s@%s" % (user, address), p)
    return target(host)


def remote_port_answers(override, port):
    """Does the address of a given user@address answer on the port? Then that is where to go."""
    return int(port) != 22 and _answers(override.rsplit("@", 1)[-1], port)


def _ca_known():
    if not KNOWN_HOSTS or not os.path.isfile(KNOWN_HOSTS):
        return False
    with open(KNOWN_HOSTS) as fh:
        return "@cert-authority " in fh.read()


def _certified(tgt):
    """Does the machine there show a host certificate of the fleet's CA? Strict, so a key
    nobody vouches for is refused rather than recorded."""
    opts = ["StrictHostKeyChecking=yes" if o == "StrictHostKeyChecking=accept-new" else o for o in ssh_opts()]
    argv = ["ssh"] + opts + ["-o", "ConnectTimeout=8", tgt, "true"]
    try:
        return subprocess.run(argv, capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


_KIND = {"ssh-ed25519": "ed25519", "ssh-rsa": "rsa", "ecdsa-sha2-nistp256": "ecdsa", "ecdsa-sha2-nistp384": "ecdsa",
         "ecdsa-sha2-nistp521": "ecdsa"}       # a known_hosts key type: what ssh-keyscan -t calls it


def _key_blobs(lines):
    return {f[2] for f in (line.split() for line in lines) if len(f) >= 3 and not f[0].startswith("#")}


def shows_key_from(address, port, known_hosts, runner=subprocess.run):
    """Does the SSH server there show a key from that known_hosts file? Nothing is logged into."""
    if not os.path.isfile(known_hosts):
        return False
    with open(known_hosts) as f:
        lines = f.read().splitlines()
    pinned = _key_blobs(lines)
    # only the kinds of key on record: one connection each. Every kind asked for meant six, some of
    # them a kind the server does not have, and fail2ban there (aggressive) banned whoever asked often
    kinds = sorted({_KIND.get(f[1], "") for f in (x.split() for x in lines) if len(f) >= 3 and not f[0].startswith("#")} - {""})
    try:
        r = runner(["ssh-keyscan", "-T", "4"] + (["-t", ",".join(kinds)] if kinds else []) + ["-p", str(port), address],
                   capture_output=True) if pinned else None
    except FileNotFoundError:
        return False
    return bool(r and pinned & _key_blobs((r.stdout or b"").decode(errors="replace").splitlines()))


def _fail(where, r):
    text = (r.stderr or b"").decode(errors="replace")
    # ssh itself fails with 255; anything else is the command there, which may stop without a word
    lines = text.strip().splitlines() or ["ssh failed" if r.returncode == 255 else
                                          "the command there stopped (exit status %d) without saying why" % r.returncode]
    if re.search(r"sudo: (a terminal is required|a password is required|no tty present)", text):
        # the admin user's sudo wants a password: kiwi-server runs everything there as root, without one
        user = where.rsplit("@", 1)[0] if "@" in where else "the admin user"
        raise KiwiError("%s: sudo there wants a password for %s. kiwi-server needs sudo without one for its admin user: "
                        "on the machine, echo '%s ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/kiwi-server"
                        % (where, user, user))
    if "REMOTE HOST IDENTIFICATION HAS CHANGED" in text or "Host key verification failed" in text:
        host = where.rsplit("@", 1)[-1]
        # CoreOS: the initramfs answers on the system's port, with a key of its own
        if KNOWN_HOSTS and shows_key_from(host, 22, KNOWN_HOSTS + ".initramfs"):
            raise KiwiError("%s: the machine is waiting for its disk key, not reinstalled: kiwi-server unlock" % where)
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


_PRINT = threading.Lock()


def run_stream(tgt, script, runner=subprocess.run, prefix=""):
    """Like run(), but the output goes straight to the terminal (a role
    script takes minutes and talks). `prefix`: each line under the machine's name, for
    several machines at once."""
    argv = ["ssh"] + ssh_opts() + [tgt, "sudo sh -c " + shlex.quote(script)]
    if prefix:
        try:
            p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, errors="replace")
        except FileNotFoundError:
            raise KiwiError("ssh is not installed")
        for line in p.stdout:
            with _PRINT:
                print("%s | %s" % (prefix, line.rstrip("\n")), flush=True)
        return p.wait()
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
