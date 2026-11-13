"""A machine that already runs Debian becomes a fleet host without a reinstall: a board that
boots its maker's image from SD or eMMC (Armbian, the Odroid or Banana Pi Debian, Raspberry Pi
OS), or any Debian someone installed by hand.

Once, over SSH with the board's own login (a password works): the admin user with the fleet's
keys and passwordless sudo, the sshd and update settings — what the installer's late command
puts on a fresh install. From then on it is a host like the others and `apply` gives it its
role. Its disk, partitions and network stay as they are.
"""
import io
import shlex
import subprocess
import tarfile
import tempfile

from . import remote, util
from .targets import debian
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
        raise KiwiError("%s: adopt is for target: debian or armbian (its target is %s)" % (host.name, host.target))
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
