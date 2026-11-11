"""The port sshd listens on, `admin.ssh_port` (22 unless set). Fewer bots knock on another
port; fail2ban and a rate limit (the fail2ban module) deal with the rest.

Moving the port never locks the fleet out: sshd listens on 22 and the new port at first, the
host's firewall and SELinux are told, `sshd -t` checks the result; only once kiwi-server has
logged in over the new port is 22 dropped. Back to 22: `ssh_port: 22` and one apply with
`--ssh ssh://user@address:PORT`, the port it listens on now.
"""
from . import remote, util
from .util import KiwiError

DROP_IN = "/etc/ssh/sshd_config.d/30-kiwi-port.conf"

_SCRIPT = r"""
set -e
port=%(port)d
keep22=%(keep22)d
conf=%(conf)s
svc=$(systemctl is-active -q sshd.service 2>/dev/null && echo sshd || echo ssh)
if [ "$port" = 22 ]; then
    [ -f "$conf" ] || exit 0
    rm -f "$conf"
    if command -v semodule >/dev/null && semodule -l 2>/dev/null | grep -qx kiwi-ssh-port; then semodule -r kiwi-ssh-port; fi
else
    if command -v getenforce >/dev/null && [ "$(getenforce)" != Disabled ]; then
        if command -v semanage >/dev/null; then
            semanage port -l | grep -qE "^ssh_port_t .*[ ,]$port(,|$)" || semanage port -a -t ssh_port_t -p tcp "$port" \
                || semanage port -m -t ssh_port_t -p tcp "$port"
        elif command -v semodule >/dev/null; then
            # Fedora CoreOS has no semanage: the port's label as a policy module of its own
            printf '(portcon tcp %%s (system_u object_r ssh_port_t ((s0)(s0))))\n' "$port" > /etc/selinux/kiwi-ssh-port.cil
            semodule -i /etc/selinux/kiwi-ssh-port.cil
        else
            echo "SELinux is on and neither semanage nor semodule is here: port $port left out" >&2; exit 3
        fi
    fi
    if systemctl is-active -q firewalld 2>/dev/null; then
        firewall-cmd -q --query-port="$port/tcp" || { firewall-cmd -q --permanent --add-port="$port/tcp"; firewall-cmd -q --add-port="$port/tcp"; }
    elif command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then
        ufw allow "$port/tcp" >/dev/null
    fi
    { [ "$keep22" = 1 ] && echo "Port 22"; echo "Port $port"; } > "$conf.new"
    mv "$conf.new" "$conf"
fi
if ! sshd -t; then
    rm -f "$conf"; echo "sshd refused the port setting: left out" >&2; exit 1
fi
systemctl reload "$svc"
"""


def _run(tgt, port, keep22):
    remote.run(tgt, _SCRIPT % {"port": port, "keep22": 1 if keep22 else 0, "conf": DROP_IN})


def ensure(h, tgt):
    """Bring sshd to admin.ssh_port over the connection `tgt` (which may still be port 22).
    Returns the target to use from now on."""
    port = int(h.admin.get("ssh_port") or 22)
    if port == 22:
        if remote.port_of(tgt) == 22:
            return tgt                  # nothing moved; a fleet on 22 pays no extra call
        _run(tgt, 22, False)            # back to 22, over the port it is on now (apply --ssh ssh://…:PORT)
        return remote.with_port(tgt, 22)
    if remote.port_of(tgt) == port:
        _run(tgt, port, False)          # there already: keeps the firewall and SELinux told
        return tgt
    _run(tgt, port, True)               # both, until the new one is proven
    new = remote.with_port(tgt, port)
    if remote.run_ok(new):
        _run(new, port, False)
        util.say("%s: sshd listens on port %d now, 22 is closed" % (h.name, port))
        return new
    util.warn("%s: port %d does not answer from here (a firewall in front?): sshd keeps 22 too" % (h.name, port))
    return tgt
