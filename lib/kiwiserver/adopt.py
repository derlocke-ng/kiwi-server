"""A machine that already runs Debian becomes a fleet host without a reinstall: a board that
boots its maker's image from SD or eMMC (Armbian, the Odroid or Banana Pi Debian, Raspberry Pi
OS), or any Debian someone installed by hand.

Once, over SSH with the board's own login (a password works): the admin user with the fleet's
keys and passwordless sudo, the sshd and update settings — what the installer's late command
puts on a fresh install. From then on it is a host like the others and `apply` gives it its
role. Its disk, partitions and network stay as they are.

A machine that runs Fedora CoreOS already (the one the starter stick installed) is taken over
the same way: what Ignition would have written at an install (files, units, the admin user's
keys) is put in place over SSH, with a key instead of a password. What only an install can do
(an encrypted disk, kernel arguments) is refused.
"""
import io
import shlex
import subprocess
import tarfile
import tempfile

from . import remote, unlock, util
from .targets import coreos, debian
from .util import KiwiError

# the files of a fresh install that matter on a running system
_FILES = ("kiwi-reboot-if-required.service", "kiwi-reboot-if-required.timer", "sshd-kiwi.conf",
          "sudoers-kiwi", "authorized_keys", "motd", "20auto-upgrades", "52kiwi-updates")

_SCRIPT = r"""
set -e
D=$(dirname "$0")
U=%(user)s
[ -f /etc/debian_version ] || { echo "not a Debian system: adopt takes Debian and its derivatives" >&2; exit 3; }
export DEBIAN_FRONTEND=noninteractive
hostnamectl set-hostname %(short)s 2>/dev/null || echo %(short)s > /etc/hostname
if grep -q '^127\.0\.1\.1' /etc/hosts; then
    sed -i 's/^127\.0\.1\.1.*/127.0.1.1\t%(fqdn)s %(short)s/' /etc/hosts
else
    printf '127.0.1.1\t%%s %%s\n' %(fqdn)s %(short)s >> /etc/hosts
fi
id -u "$U" >/dev/null 2>&1 || useradd -m -s /bin/bash "$U"
usermod -aG sudo "$U"
for g in %(groups)s; do getent group "$g" >/dev/null && usermod -aG "$g" "$U"; done
%(password)s
H=$(getent passwd "$U" | cut -d: -f6)
install -d -m 0700 -o "$U" -g "$U" "$H/.ssh"
install -m 0600 -o "$U" -g "$U" "$D/authorized_keys" "$H/.ssh/authorized_keys"
command -v sudo >/dev/null || apt-get -y -q install sudo
install -m 0440 "$D/sudoers-kiwi" /etc/sudoers.d/kiwi-admin
visudo -cq
install -d /etc/ssh/sshd_config.d
grep -qi '^Include /etc/ssh/sshd_config.d/' /etc/ssh/sshd_config || sed -i '1i Include /etc/ssh/sshd_config.d/*.conf' /etc/ssh/sshd_config
install -m 0644 "$D/sshd-kiwi.conf" /etc/ssh/sshd_config.d/50-kiwi.conf
[ -s "$D/motd" ] && install -m 0644 "$D/motd" /etc/motd
install -m 0644 "$D/kiwi-reboot-if-required.service" "$D/kiwi-reboot-if-required.timer" /etc/systemd/system/
install -d -m 0700 %(role_dir)s
%(updates)s
%(lock_root)s
systemctl daemon-reload
sshd -t
systemctl reload ssh 2>/dev/null || systemctl reload sshd
echo "adopted: $U has the fleet's keys"
"""

_UPDATES_ON = """apt-get -y -q update
apt-get -y -q install unattended-upgrades
install -m 0644 "$D/20auto-upgrades" "$D/52kiwi-updates" /etc/apt/apt.conf.d/
systemctl enable kiwi-reboot-if-required.timer"""


def script(host, lock_root=False):
    a = host.admin
    pw = host.password_hash()
    return _SCRIPT % {
        "user": shlex.quote(a["user"]),
        "short": shlex.quote(host.hostname.split(".", 1)[0]),
        "fqdn": shlex.quote(host.hostname),
        "groups": " ".join(shlex.quote(str(g)) for g in (a.get("groups") or []) if str(g) not in ("sudo", "docker")),
        "password": ("usermod -p %s \"$U\"" % shlex.quote(pw)) if pw else "",
        "role_dir": debian.ROLE_DIR,
        "updates": _UPDATES_ON if host.updates()["enabled"] else "",
        # an image's root has the maker's well-known password (Armbian: 1234); a running board's is the owner's
        "lock_root": ("passwd -l root >/dev/null\n"
                      "grep -ls -- '--autologin root' /etc/systemd/system/*getty@.service.d/*.conf | xargs -r rm -f")
                     if lock_root else "",
    }


def bundle_files(host, lock_root=False):
    """The script and its files: name -> (text, mode)."""
    files = debian.iso_files(host, "")
    out = {"adopt.sh": (script(host, lock_root), 0o700)}
    out.update((f, files[f]) for f in _FILES)
    return out


def bundle(host):
    """A tar.gz of the script and its files, as bytes."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, (text, mode) in bundle_files(host).items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


_COREOS_HEAD = r"""
set -eu
D=$(cd "$(dirname "$0")" && pwd)
[ -e /run/ostree-booted ] || { echo "not a CoreOS system: this takes over a machine the starter stick installed" >&2; exit 3; }
put() {     # put <file of the bundle> <mode> <path> [keep: not over a file that is there]
    if [ "${4:-}" = keep ] && [ -e "$3" ]; then return 0; fi
    install -D -m "$2" "$D/$1" "$3"
    restorecon -F "$3" 2>/dev/null || true
}
"""

_COREOS_USER = r"""
U=%(user)s
id -u "$U" >/dev/null 2>&1 || useradd -m "$U"
for g in %(groups)s; do getent group "$g" >/dev/null && usermod -aG "$g" "$U"; done
%(password)s
H=$(getent passwd "$U" | cut -d: -f6)
install -d -m 0700 -o "$U" -g "$U" "$H/.ssh" "$H/.ssh/authorized_keys.d"
# where Ignition puts the keys of an install: the machine is then like an installed one
install -m 0600 -o "$U" -g "$U" "$D/authorized_keys" "$H/.ssh/authorized_keys.d/ignition"
restorecon -RF "$H/.ssh" 2>/dev/null || true
hostnamectl set-hostname "$(cat /etc/hostname)" 2>/dev/null || true
"""


def coreos_files(host, role_script):
    """What Ignition gives a CoreOS machine at its install, for one that runs already: a script
    and the files it puts in place, name -> (text, mode)."""
    if host.target != "coreos":
        raise KiwiError("%s: a machine that runs CoreOS is taken over as target: coreos (its target is %s)"
                        % (host.name, host.target))
    encrypted = unlock.wanted(host)
    bu = None if encrypted else coreos.build_butane(host, role_script)
    storage = (bu or {}).get("storage") or {}
    if encrypted or bu.get("boot_device") or storage.get("luks") or storage.get("disks") or storage.get("filesystems"):
        raise KiwiError("%s: a disk that is in use cannot be encrypted. Take `unlock` out of its settings, or install the "
                        "machine from its own install media" % host.name)
    if bu.get("kernel_arguments"):
        raise KiwiError("%s: kernel arguments come with an install, not with a machine that runs" % host.name)
    out, lines = {}, [_COREOS_HEAD]
    for d in storage.get("directories") or []:
        lines.append("install -d -m %04o %s" % (int(d.get("mode", 0o755)), shlex.quote(d["path"])))
    for i, f in enumerate(storage.get("files") or []):
        contents = f.get("contents") or {}
        if set(contents) - {"inline"} or set(f) - {"path", "mode", "overwrite", "contents"}:
            raise KiwiError("%s: %s cannot be put on a machine that runs (only plain files with their text are)"
                            % (host.name, f.get("path")))
        out["f%d" % i] = (contents.get("inline", ""), 0o600)
        lines.append("put f%d %04o %s%s" % (i, int(f.get("mode", 0o644)), shlex.quote(f["path"]),
                                              "" if f.get("overwrite", True) else " keep"))
    for ln in storage.get("links") or []:
        lines.append("ln -sfn %s %s" % (shlex.quote(ln["target"]), shlex.quote(ln["path"])))
    user = bu["passwd"]["users"][0]
    out["authorized_keys"] = ("".join(k.rstrip("\n") + "\n" for k in user.get("ssh_authorized_keys") or []), 0o600)
    lines.append(_COREOS_USER % {
        "user": shlex.quote(user["name"]),
        "groups": " ".join(shlex.quote(str(g)) for g in user.get("groups") or []),
        "password": ("usermod -p %s \"$U\"" % shlex.quote(user["password_hash"])) if user.get("password_hash") else ""})
    now, later = [], []
    for i, u in enumerate((bu.get("systemd") or {}).get("units") or []):
        name = shlex.quote(u["name"])
        if u.get("contents"):
            out["u%d" % i] = (u["contents"], 0o600)
            lines.append("put u%d 0644 /etc/systemd/system/%s" % (i, name))
        for j, drop in enumerate(u.get("dropins") or []):
            out["u%d-%d" % (i, j)] = (drop.get("contents", ""), 0o600)
            lines.append("put u%d-%d 0644 /etc/systemd/system/%s.d/%s" % (i, j, name, shlex.quote(drop["name"])))
        if u.get("mask"):
            later.append("systemctl mask %s" % name)
        elif u.get("enabled") is False:
            later.append("systemctl disable --now %s 2>/dev/null || true" % name)
        elif u.get("enabled"):
            # a service of the system, or a timer, starts as it would have at the first boot. The first-boot
            # units (the role) wait: `apply` runs the role right after this
            (now if not u.get("contents") or u["name"].endswith(".timer") else later).append(
                "systemctl enable %s%s" % ("--now " if not u.get("contents") or u["name"].endswith(".timer") else "", name))
    lines += ["systemctl daemon-reload"] + later + now
    lines += ["sshd -t", "systemctl reload sshd", 'echo "adopted: $U has the fleet\'s keys"', ""]
    out["adopt.sh"] = ("\n".join(lines), 0o700)
    return out


def run_coreos(host, login, role_script, key=None, runner=subprocess.run):
    """Take over a machine that runs CoreOS: copy the files and run the script, as `login`
    (the user the starter made, with sudo) with `key`. Returns the admin target."""
    files = coreos_files(host, role_script)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, (text, mode) in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            tar.addfile(info, io.BytesIO(data))
    opts = ["-o", "ConnectTimeout=15", "-o", "BatchMode=yes"]
    if key:
        opts += ["-i", key, "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none"]
    if remote.KNOWN_HOSTS:
        opts += ["-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=%s" % remote.KNOWN_HOSTS]
    url = login if login.startswith("ssh://") else "ssh://" + login
    who = url[len("ssh://"):].rsplit("@", 1)
    if len(who) != 2:
        raise KiwiError("--as wants the machine's login, user@address (as in: core@192.168.1.30)")
    ssh = ["ssh"] + opts + ["-p", str(remote.port_of(url)), "%s@%s" % (who[0], remote.address_of(url))]
    dest = "/var/tmp/kiwi-adopt"       # not /tmp: the files hold the role script, and /tmp is everyone's
    r = runner(ssh + ["sudo rm -rf %s && sudo install -d -m 0700 %s && sudo tar -xz -C %s" % (dest, dest, dest)],
               input=buf.getvalue())
    if r.returncode != 0:
        raise KiwiError("%s: could not copy the files to %s" % (host.name, login))
    util.say("%s: taking it over" % host.name)
    r = runner(ssh + ["sudo bash %s/adopt.sh; rc=$?; sudo rm -rf %s; exit $rc" % (dest, dest)])
    if r.returncode != 0:
        raise KiwiError("%s: the adopt script exited with %d" % (host.name, r.returncode))
    tgt = admin_target(host, login)
    if not remote.run_ok(tgt):
        raise KiwiError("%s: adopted, but %s does not let the fleet's key in" % (host.name, tgt))
    return tgt


def admin_target(host, login):
    """The admin user at the address the board's login used, on the same port."""
    address = login[len("ssh://"):] if login.startswith("ssh://") else login
    address = address.rsplit("@", 1)[-1]
    if address.count(":") == 1:
        address = address.rsplit(":", 1)[0]
    return remote.with_port("%s@%s" % (host.admin["user"], address), remote.port_of(login))


def forget_host_key(login, runner=subprocess.run):
    """A board flashed again has a new host key: the fleet forgets the old one of that address."""
    if not remote.KNOWN_HOSTS:
        return
    address = remote.address_of(login if login.startswith("ssh://") else "ssh://" + login)
    port = remote.port_of(login)
    name = address if port == 22 else "[%s]:%d" % (address, port)
    runner(["ssh-keygen", "-R", name, "-f", remote.KNOWN_HOSTS], capture_output=True)


def run(host, login, runner=subprocess.run, new_host_key=False):
    """Copy the bundle and run it, asking for the login's password and sudo's on this
    terminal if they are needed. Returns the admin target."""
    if host.target not in ("debian", "armbian"):
        raise KiwiError("%s: adopt is for target: debian or armbian, or coreos for a machine the starter stick installed "
                        "(its target is %s)" % (host.name, host.target))
    user = (login[len("ssh://"):] if login.startswith("ssh://") else login).rsplit("@", 1)
    if len(user) != 2:
        raise KiwiError("--as wants the board's login, user@address (as in: odroid@192.168.2.40)")
    sudo = "" if user[0] == "root" else "sudo "
    if new_host_key:
        forget_host_key(login, runner)
    with tempfile.TemporaryDirectory(prefix="kiwi-adopt-") as tmp:
        # one connection, its password asked once; the host key goes to the fleet's known_hosts
        opts = ["-o", "ConnectTimeout=15", "-o", "ControlMaster=auto", "-o", "ControlPath=%s/c" % tmp,
                "-o", "ControlPersist=120"]
        if remote.KNOWN_HOSTS:
            opts += ["-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=%s" % remote.KNOWN_HOSTS]
        dest = "/tmp/kiwi-adopt"
        r = runner(["ssh"] + opts + [login, "rm -rf %s && mkdir -m 0700 %s && tar -xz -C %s" % (dest, dest, dest)],
                   input=bundle(host))
        if r.returncode != 0:
            raise KiwiError("%s: could not copy the adopt files to %s (a board flashed again shows a new "
                            "host key: --new-host-key forgets the old one)" % (host.name, login))
        util.say("%s: taking it over (sudo may ask for the password of %s)" % (host.name, user[0]))
        r = runner(["ssh", "-t"] + opts + [login, "%ssh %s/adopt.sh; rc=$?; %srm -rf %s; exit $rc" % (sudo, dest, sudo, dest)])
        runner(["ssh"] + opts + ["-O", "exit", login], capture_output=True)
        if r.returncode != 0:
            raise KiwiError("%s: the adopt script exited with %d" % (host.name, r.returncode))
    tgt = admin_target(host, login)
    if not remote.run_ok(tgt):
        raise KiwiError("%s: adopted, but %s does not let the fleet's key in" % (host.name, tgt))
    return tgt
