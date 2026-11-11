"""coreos-ram: a master that lives in RAM only (docs/design/ram-master.md).

The Fedora CoreOS live ISO with the host's live Ignition: every boot starts from the image,
everything written stays in RAM, no disk is touched. The image holds no role. It holds one
secret, the machine's SSH host key (signed by the fleet's SSH CA when there is one), so home
knows it is talking to this master. After every boot the master waits with SSH only, and home
pushes: `kiwi-server watch` sees a blank master and runs apply, which sends the role and puts
back what the fleet keeps of the master (the WireGuard identity, the Let's Encrypt state).
"""
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

from .. import remote, sshca, util
from ..util import KiwiError
from . import ROLE_DIR, ROLE_MARKER, ROLE_SCRIPT
from . import coreos as coreos_t

FROMRAM = "coreos.liveiso.fromram"
HOST_KEY = "/etc/ssh/ssh_host_ed25519_key"
SSHD_DROP_IN = "/etc/ssh/sshd_config.d/30-kiwi-ram.conf"
DEFAULT_STACK_DIR = "/home/user/docker"
# what the fleet keeps of a RAM master beside its WireGuard identity: a public site's
# certificates and ACME account (asking again is limited to a few a week)
KEPT = ("km-nginx/letsencrypt", "km-nginx/acme")
KEEP_EVERY = 3600       # watch refreshes the fleet's copies of a running master this often


def settings(host):
    return {"copy_to_ram": bool((host.cfg.get("ram") or {}).get("copy_to_ram"))}


# ---- the host key: the image's one secret ---------------------------------------------

def key_dir(host):
    return host.fleet.resolve_path("secrets/ram/%s" % host.name)


def host_key(host):
    """(private, public) of the machine's SSH host key, made once and kept in the fleet's
    secrets. A new one (delete the directory, build again) revokes every image built before."""
    d = key_dir(host)
    key = os.path.join(d, "ssh_host_ed25519_key")
    if not os.path.isfile(key):
        os.makedirs(d, mode=0o700, exist_ok=True)
        try:
            r = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "%s host" % host.hostname, "-f", key],
                               capture_output=True, text=True)
        except FileNotFoundError:
            raise KiwiError("ssh-keygen is not installed")
        if r.returncode != 0:
            raise KiwiError("ssh-keygen: %s" % r.stderr.strip()[:300])
        util.say("%s: made its SSH host key in %s; every image of it carries the key" % (host.name, d))
    return util.read_text(key), util.read_text(key + ".pub").strip()


def pin(host, public, known_hosts):
    """The key on record for the machine's names and addresses: home knows the image at first contact."""
    names = remote.addresses(host)
    line = "%s %s" % (",".join(names), " ".join(public.split()[:2]))
    old = util.read_text(known_hosts).splitlines() if os.path.isfile(known_hosts) else []
    if line in old:
        return False
    if os.path.isfile(known_hosts):
        for n in names:     # whatever key an earlier install of the machine showed
            subprocess.run(["ssh-keygen", "-R", n, "-f", known_hosts], capture_output=True)
        old = util.read_text(known_hosts).splitlines()
        if os.path.isfile(known_hosts + ".old"):
            os.remove(known_hosts + ".old")
    os.makedirs(os.path.dirname(os.path.abspath(known_hosts)), exist_ok=True)
    util.write_text(known_hosts, "\n".join(old + [line]) + "\n", 0o600)
    return True


# ---- the image ------------------------------------------------------------------------

ISSUE = ("This master lives in RAM. After every boot it waits for its role:\n"
         "kiwi-server watch (or apply) at home sends it.\n\n")


def build_butane(host):
    """The live system's Ignition: the base system and the host key, nothing of the role."""
    co = host.cfg["coreos"]
    files, dirs, links, units = [], [], [], []
    f = coreos_t._file
    user = coreos_t.admin_user(host)
    coreos_t.base_system(host, files, links)
    private, public = host_key(host)
    files += [f(HOST_KEY, private, mode=0o600), f(HOST_KEY + ".pub", public + "\n"),
              # this key and no other: the ones the image would make at every boot would be new each time
              f(SSHD_DROP_IN, "HostKey %s\n" % HOST_KEY)]
    if sshca.exists(host.fleet):
        cert = sshca.sign(host.fleet, public, "%s host" % host.hostname, [host.name] + sshca._addresses(host),
                          sshca.HOST_DAYS, host=True)
        files += [f(sshca.HOST_CERT, cert.strip() + "\n"), f(sshca.USER_CA, sshca.public(host.fleet) + "\n"),
                  f(sshca.DROP_IN, "HostCertificate %s\nTrustedUserCAKeys %s\n" % (sshca.HOST_CERT, sshca.USER_CA))]
    # logs in RAM, and not much of it: gone at every reboot
    files.append(f("/etc/systemd/journald.conf.d/50-kiwi-ram.conf", "[Journal]\nStorage=volatile\nRuntimeMaxUse=64M\n"))
    files.append(f("/etc/issue.d/50-kiwi-ram.issue", ISSUE))
    dirs.append({"path": ROLE_DIR, "mode": 0o700})
    coreos_t.passthrough(co, files, dirs, units)
    return coreos_t.assemble(user, files, dirs, links, units)


def render(host):
    pin(host, host_key(host)[1], host.fleet.resolve_path("secrets/known_hosts"))
    return coreos_t.to_yaml(build_butane(host))


def live_kargs(host):
    co = host.cfg["coreos"]
    kargs = [FROMRAM] if settings(host)["copy_to_ram"] else []
    kargs += [str(k) for k in (co.get("kernel_arguments") or [])]
    if co.get("console"):
        kargs.append("console=%s" % co["console"])
    return kargs


def iso(tc, host, ign_path, out_iso, cache_dir):
    """The stock live ISO with this host's live Ignition: the whole system, no installer."""
    from .. import iso as isomod
    base = isomod.fcos_base_iso(tc, host.cfg["coreos"]["stream"], cache_dir)
    argv = ["coreos-installer", "iso", "customize", "--live-ignition", ign_path]
    for k in live_kargs(host):
        argv += ["--live-karg-append", k]
    argv += ["-o", out_iso, base]
    if os.path.exists(out_iso):
        os.unlink(out_iso)   # coreos-installer will not replace an existing output file
    tc.run(argv, mounts=[util.parent(ign_path), util.parent(out_iso), cache_dir])
    if os.path.isfile(out_iso):
        os.chmod(out_iso, 0o600)   # it carries the host key
    return out_iso


# ---- what the fleet keeps of a running master --------------------------------------------

def stack_dir(host):
    return str((host.role_settings or {}).get("docker_dir") or DEFAULT_STACK_DIR)


def _kept_copy(host):
    return os.path.join(key_dir(host), "kept.tar")


def keep(host, tgt, runner=subprocess.run):
    """KEPT, as one tar, into the fleet's secrets when it changed. True when it did."""
    d = shlex.quote(stack_dir(host))
    script = ('cd %s 2>/dev/null || exit 0\nset --\nfor p in %s; do [ -d "$p" ] && set -- "$@" "$p"; done\n'
              '[ $# -gt 0 ] && tar -cf - --sort=name "$@"\nexit 0' % (d, " ".join(KEPT)))
    out = remote.run(tgt, script, runner=runner)
    path = _kept_copy(host)
    if not out or (os.path.isfile(path) and util.read_bytes(path) == out):
        return False
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    util.write_bytes(path, out, 0o600)
    return True


def put_back(host, tgt, runner=subprocess.run):
    """The fleet's copy onto a master that booted without it. True when it was put back."""
    path = _kept_copy(host)
    if not os.path.isfile(path):
        return False
    d = shlex.quote(stack_dir(host))
    script = ('[ -e %s/%s ] && { echo present; exit 0; }\nmkdir -p %s && cd %s && tar -xf - && echo restored'
              % (d, KEPT[0], d, d))
    out = remote.run(tgt, script, util.read_bytes(path), runner=runner)
    return b"restored" in out


# ---- home pushes -------------------------------------------------------------------------

# [/]: the pattern must not find this probe's own command line
STATE_PROBE = ("if [ -f %s ]; then echo ready; elif pgrep -f '[/]%s' >/dev/null; then echo busy; else echo blank; fi"
               % (ROLE_MARKER, ROLE_SCRIPT.lstrip("/")))


def state(tgt, runner=subprocess.run):
    """ready, busy (a role is running), blank (booted, nothing sent yet) or unreachable."""
    try:
        out = remote.run(tgt, STATE_PROBE, runner=runner).decode(errors="replace").strip()
    except KiwiError:
        return "unreachable"
    return out if out in ("ready", "busy", "blank") else "unreachable"


def _watch_record(fleet):
    return fleet.resolve_path("output/watch.json")


def _record(fleet):
    try:
        return json.loads(util.read_text(_watch_record(fleet)))
    except (OSError, ValueError):
        return {}


def keep_due(fleet, host, now=None):
    return (now if now is not None else time.time()) - _record(fleet).get(host, 0) >= KEEP_EVERY


def kept_now(fleet, host, now=None):
    rec = _record(fleet)
    rec[host] = int(now if now is not None else time.time())
    os.makedirs(os.path.dirname(_watch_record(fleet)), exist_ok=True)
    util.write_text(_watch_record(fleet), json.dumps(rec, sort_keys=True), 0o600)


def _unit(fleet_path):
    return "kiwi-watch-%s" % hashlib.sha256(fleet_path.encode()).hexdigest()[:10]


def watch_timer(fleet_path, on, systemctl=None):
    """A timer on this computer that runs `watch --once` every minute, or none."""
    from .. import backup
    systemctl = systemctl or backup._systemctl_user
    unit, d = _unit(fleet_path), backup._user_units()
    if not on:
        systemctl("disable", "--now", unit + ".timer")
        for ext in (".service", ".timer"):
            if os.path.isfile(os.path.join(d, unit + ext)):
                os.remove(os.path.join(d, unit + ext))
        systemctl("daemon-reload")
        util.say("nothing watches the RAM masters of %s any more" % fleet_path)
        return
    cli = shutil.which("kiwi-server") or os.path.abspath(sys.argv[0])
    os.makedirs(d, exist_ok=True)
    util.write_bytes(os.path.join(d, unit + ".service"), (
        "[Unit]\nDescription=Kiwi Network: the role to a RAM master of %s that booted\n\n"
        "[Service]\nType=oneshot\nExecStart=%s watch %s --once\n" % (fleet_path, shlex.quote(cli), shlex.quote(fleet_path))).encode(), 0o644)
    util.write_bytes(os.path.join(d, unit + ".timer"), (
        "[Unit]\nDescription=Kiwi Network: watch the RAM masters of %s\n\n"
        "[Timer]\nOnBootSec=1min\nOnUnitInactiveSec=1min\n\n[Install]\nWantedBy=timers.target\n" % fleet_path).encode(), 0o644)
    systemctl("daemon-reload")
    r = systemctl("enable", "--now", unit + ".timer")
    if r.returncode != 0:
        raise KiwiError("the timer did not start: %s" % (r.stderr.strip().splitlines() or ["systemctl failed"])[-1])
    util.say("%s.timer looks at the RAM masters of %s every minute while this computer is on; "
             "journalctl --user -u %s" % (unit, fleet_path, unit))


def problems(host):
    """What cannot work on this target, as validate errors."""
    c = host.cfg
    errs = []
    if not isinstance((c.get("ram") or {}).get("copy_to_ram"), bool):
        errs.append("ram.copy_to_ram must be true or false")
    if (c.get("unlock") or {}).get("rings"):
        errs.append("unlock.rings: a coreos-ram master has no disk to unlock")
    for k in ("luks", "boot_device"):
        if c["coreos"].get(k):
            errs.append("coreos.%s: a coreos-ram master touches no disk" % k)
    return errs
