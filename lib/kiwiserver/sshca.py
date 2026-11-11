"""The fleet's SSH certificate authority, `secrets/ssh_ca` beside the TLS CA.

Machines show a host certificate signed by it, so a machine is known at any of its
addresses without trusting the first key it shows; and they accept user certificates
signed by it, so a key can be let in for a while without touching every machine's
authorized_keys. admin.ssh_keys keep working beside it.
"""
import os
import subprocess
import tempfile

from . import remote, util
from .util import KiwiError

CA = "secrets/ssh_ca"
HOST_DAYS = 730             # signed anew at every apply
USER_DAYS = 30
USER_CA = "/etc/ssh/kiwi-user-ca.pub"
HOST_CERT = "/etc/ssh/ssh_host_ed25519_key-cert.pub"
DROP_IN = "/etc/ssh/sshd_config.d/40-kiwi-ca.conf"


def path(fleet):
    return fleet.resolve_path(CA)


def exists(fleet):
    return os.path.isfile(path(fleet))


def public(fleet):
    return util.read_text(path(fleet) + ".pub").strip()


def _keygen(*argv):
    try:
        r = subprocess.run(["ssh-keygen"] + list(argv), capture_output=True, text=True)
    except FileNotFoundError:
        raise KiwiError("ssh-keygen is not installed")
    if r.returncode != 0:
        raise KiwiError("ssh-keygen: %s" % (r.stderr.strip() or r.stdout.strip())[:300])
    return r.stdout


def init(fleet):
    if exists(fleet):
        raise KiwiError("%s exists already" % path(fleet))
    os.makedirs(os.path.dirname(path(fleet)), exist_ok=True)
    domain = (fleet.defaults or {}).get("domain") or "home"
    _keygen("-q", "-t", "ed25519", "-N", "", "-C", "kiwi ssh ca (%s)" % domain, "-f", path(fleet))
    os.chmod(path(fleet), 0o600)
    trust(fleet, fleet.resolve_path("secrets/known_hosts"))
    return public(fleet)


def fingerprint(fleet):
    return _keygen("-l", "-f", path(fleet) + ".pub").split()[1]


def sign(fleet, pubkey, identity, principals, days, host=False):
    """The certificate (text) for an OpenSSH public key (text)."""
    if not exists(fleet):
        raise KiwiError("the fleet has no SSH CA: kiwi-server sshca %s init" % os.path.basename(fleet.path))
    with tempfile.TemporaryDirectory() as d:
        key = os.path.join(d, "key.pub")
        util.write_text(key, pubkey.strip() + "\n", 0o600)
        _keygen("-q", "-s", path(fleet), "-I", identity, *(["-h"] if host else []),
                "-n", ",".join(principals), "-V", "-5m:+%dd" % days, key)
        return util.read_text(os.path.join(d, "key-cert.pub"))


def patterns(fleet):
    """Where the fleet's CA speaks for a machine: its domain and each machine's names and addresses."""
    domain = (fleet.defaults or {}).get("domain") or "home"
    found = ["*.%s" % domain]
    for h in fleet.hosts.values():
        for a in [h.name] + _addresses(h):
            if a not in found:
                found.append(a)
    return found


def _addresses(h):
    try:
        return remote.addresses(h)
    except (AttributeError, KeyError, TypeError):     # settings not resolved: the names alone
        return [h.hostname]


def trust(fleet, known_hosts):
    """The @cert-authority line in a known_hosts file, made anew when the fleet's names changed."""
    line = "@cert-authority %s %s" % (",".join(patterns(fleet)), public(fleet))
    ca = public(fleet).split()[1]
    old = util.read_text(known_hosts).splitlines() if os.path.isfile(known_hosts) else []
    keep = [x for x in old if not (x.startswith("@cert-authority ") and ca in x)]
    if line in old and len(keep) == len(old) - 1:
        return False
    os.makedirs(os.path.dirname(os.path.abspath(known_hosts)), exist_ok=True)
    util.write_text(known_hosts, "\n".join(keep + [line]) + "\n", 0o600)
    return True


def install(fleet, h, tgt):
    """A host certificate for the machine and its sshd trusting the fleet's user certificates."""
    hostkey = remote.run(tgt, "cat /etc/ssh/ssh_host_ed25519_key.pub").decode().strip()
    cert = sign(fleet, hostkey, "%s host" % h.hostname, [h.name] + _addresses(h), HOST_DAYS, host=True)
    conf = "HostCertificate %s\nTrustedUserCAKeys %s\n" % (HOST_CERT, USER_CA)
    script = (
        "set -e\n"
        "umask 022\n"
        "printf '%%s\\n' %s > %s.new\n" % (_q(cert.strip()), HOST_CERT) +
        "printf '%%s\\n' %s > %s.new\n" % (_q(public(fleet)), USER_CA) +
        "printf '%%s' %s > %s.new\n" % (_q(conf), DROP_IN) +
        "mv %s.new %s; mv %s.new %s; mv %s.new %s\n" % (HOST_CERT, HOST_CERT, USER_CA, USER_CA, DROP_IN, DROP_IN) +
        "if ! sshd -t; then rm -f %s; echo 'sshd refused the CA settings: left out' >&2; exit 1; fi\n" % DROP_IN +
        "systemctl reload sshd 2>/dev/null || systemctl reload ssh\n")
    remote.run(tgt, script)
    trust(fleet, fleet.resolve_path("secrets/known_hosts"))


def _q(text):
    return "'" + text.replace("'", "'\\''") + "'"
