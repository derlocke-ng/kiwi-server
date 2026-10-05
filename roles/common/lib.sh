# roles/common/lib.sh — the library every rendered role script carries.
#
# Not a program: kiwi-server pastes this between the generated settings and
# the role's apply.sh. Everything here may rely on the KS_* variables the
# generator defines (KS_HOSTNAME, KS_ADMIN_USER, KS_FILES, KS_ROLE_* ...).
#
# shellcheck shell=bash
# shellcheck disable=SC2034,SC2154

KS_STATE_DIR=/var/lib/kiwi-server
KS_MARKER=$KS_STATE_DIR/role.done
KS_LOG=/var/log/kiwi-server-role.log
KS_OS=other
KS_APT_UPDATED=0

ks_say()  { printf ':: %s\n' "$*"; }
ks_warn() { printf 'warning: %s\n' "$*" >&2; }
ks_die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

ks_detect_os() { # debian | ostree | other
    if [[ -f /run/ostree-booted ]]; then echo ostree
    elif [[ -f /etc/debian_version ]]; then echo debian
    else echo other; fi
}
ks_is_debian() { [[ $KS_OS == debian ]]; }
ks_is_ostree() { [[ $KS_OS == ostree ]]; }

# ---- packages (Debian only; an ostree host gets what its image ships) --------
ks_apt_update() {
    ks_is_debian || return 0
    (( KS_APT_UPDATED )) && return 0
    apt-get -o DPkg::Lock::Timeout=600 -q update
    KS_APT_UPDATED=1
}
ks_apt() { # package...
    ks_is_debian || { ks_warn "not Debian — cannot install: $*"; return 0; }
    ks_apt_update
    # the lock timeout matters: unattended-upgrades may be running on first boot
    apt-get -o DPkg::Lock::Timeout=600 -q -y --no-install-recommends install "$@"
}

# ---- files ----------------------------------------------------------------------
ks_parent_dir() { # PATH   — the parent exists afterwards; an existing one keeps its mode
    local d
    d=$(dirname "$1")
    [[ -d $d ]] || install -d -m 0755 "$d"
}
ks_file() { # KEY DEST [MODE] [OWNER]   — write a file embedded by the generator
    local key=$1 dest=$2 mode=${3:-0600} owner=${4:-root:root}
    [[ -n ${KS_FILES[$key]:-} ]] || ks_die "no embedded file for setting '$key'"
    ks_parent_dir "$dest"
    printf '%s' "${KS_FILES[$key]}" | base64 -d > "$dest.tmp"
    chmod "$mode" "$dest.tmp"
    chown "$owner" "$dest.tmp"
    mv -f "$dest.tmp" "$dest"
}
ks_has_file() { [[ -n ${KS_FILES[$1]:-} ]]; }

ks_write() { # DEST [MODE] [OWNER]   — content on stdin
    local dest=$1 mode=${2:-0644} owner=${3:-root:root}
    ks_parent_dir "$dest"
    cat > "$dest.tmp"
    chmod "$mode" "$dest.tmp"
    chown "$owner" "$dest.tmp"
    mv -f "$dest.tmp" "$dest"
}

ks_subst() { # SRC DEST VAR...   — ${VAR} and $VAR from the current shell, nothing else
    # A stand-in for envsubst (gettext), which an ostree host may not have.
    local src=$1 dest=$2 expr='' v val
    shift 2
    for v in "$@"; do
        val=${!v:-}
        val=${val//\\/\\\\}; val=${val//|/\\|}; val=${val//&/\\&}
        expr+="s|\\\${$v}|${val}|g;s|\\\$$v\\b|${val}|g;"
    done
    sed -e "$expr" "$src" > "$dest.tmp" && mv -f "$dest.tmp" "$dest"
}

# ---- users -------------------------------------------------------------------------
ks_ensure_user() { # NAME [UID]
    local u=$1 uid=${2:-}
    id "$u" &>/dev/null && return 0
    ks_say "creating user $u"
    if [[ -n $uid ]] && ! getent passwd "$uid" >/dev/null; then
        useradd -m -u "$uid" -s /bin/bash "$u"
    else
        useradd -m -s /bin/bash "$u"
    fi
}
ks_add_to_group() { # USER GROUP
    getent group "$2" >/dev/null || return 0
    # Fedora CoreOS keeps the docker group in /usr/lib/group; getent sees it
    # (nss altfiles) but usermod only reads /etc/group — copy the line over.
    if ! grep -q "^$2:" /etc/group && grep -q "^$2:" /usr/lib/group 2>/dev/null; then
        grep "^$2:" /usr/lib/group >> /etc/group
    fi
    id -nG "$1" | tr ' ' '\n' | grep -qx "$2" && return 0
    usermod -aG "$2" "$1"
}

# ---- docker --------------------------------------------------------------------------
ks_ensure_docker() { # [ce|distro]
    local source=${1:-ce}
    if ! command -v docker >/dev/null 2>&1; then
        case $KS_OS in
            debian)
                if [[ $source == distro ]]; then
                    ks_apt docker.io docker-compose-v2 || ks_apt docker.io
                else
                    ks_docker_ce
                fi ;;
            ostree) ks_die "this image has no docker — use a uCore image (ucore-minimal and up ship docker)" ;;
            *)      ks_die "docker is not installed and this is not Debian — install it first" ;;
        esac
    fi
    # uCore ships docker but leaves the socket disabled
    systemctl enable --now docker.socket 2>/dev/null || true
    systemctl enable --now docker.service
    docker compose version >/dev/null 2>&1 || ks_compose_plugin
}
ks_docker_ce() {
    ks_say "installing Docker CE from download.docker.com"
    ks_apt ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    local codename
    codename=$(. /etc/os-release && echo "$VERSION_CODENAME")
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $codename stable" \
        > /etc/apt/sources.list.d/docker.list
    KS_APT_UPDATED=0
    ks_apt docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}
ks_compose_plugin() {
    local arch dir=/usr/local/lib/docker/cli-plugins
    arch=$(uname -m)
    ks_say "installing the docker compose plugin into $dir"
    install -d -m 0755 "$dir"
    curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$arch" \
        -o "$dir/docker-compose.tmp"
    chmod 0755 "$dir/docker-compose.tmp"
    mv -f "$dir/docker-compose.tmp" "$dir/docker-compose"
    docker compose version >/dev/null
}

# ---- git -------------------------------------------------------------------------------
ks_git_clone() { # URL REF DIR   — clone, or bring an existing clone to REF
    local url=$1 ref=$2 dir=$3
    if ! command -v git >/dev/null 2>&1; then
        ks_is_debian && ks_apt git
    fi
    if command -v git >/dev/null 2>&1; then
        if [[ -d $dir/.git ]]; then
            git -C "$dir" fetch -q origin
            git -C "$dir" checkout -q "$ref"
            git -C "$dir" pull -q --ff-only origin "$ref" 2>/dev/null || true
        else
            git clone -q --branch "$ref" -- "$url" "$dir"
        fi
        return 0
    fi
    # no git (plain Fedora CoreOS): a GitHub tarball is as good as a clone here
    case $url in
        https://github.com/*)
            local base=${url%.git} tmp
            tmp=$(mktemp -d)
            ks_say "no git — fetching ${base}/archive/${ref}.tar.gz"
            curl -fsSL "${base}/archive/${ref}.tar.gz" | tar -xz -C "$tmp"
            install -d "$dir"
            cp -a "$tmp"/*/. "$dir/"
            rm -rf "$tmp" ;;
        *) ks_die "git is not installed and $url is not on GitHub" ;;
    esac
}

# ---- services / firewall --------------------------------------------------------------
ks_unit() { # NAME   — unit file on stdin, then daemon-reload
    ks_write "/etc/systemd/system/$1" 0644
    systemctl daemon-reload
}
ks_firewall_open() { # PORT/PROTO...
    if systemctl is-active -q firewalld 2>/dev/null; then
        local p
        for p in "$@"; do firewall-cmd -q --permanent --add-port="$p"; done
        firewall-cmd -q --reload
    elif command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
        local p
        for p in "$@"; do ufw allow "$p" >/dev/null; done
    fi
}

ks_self_signed_cert() { # CERT KEY CN   — a stop-gap until real certificates exist
    local cert=$1 key=$2 cn=$3
    ks_say "no TLS certificate given — generating a self-signed one for $cn and *.$cn"
    install -d -m 0755 "$(dirname "$cert")"
    openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
        -keyout "$key" -out "$cert" -subj "/CN=$cn" \
        -addext "subjectAltName=DNS:$cn,DNS:*.$cn" >/dev/null 2>&1
    chmod 0600 "$key"
}

# ---- stacks: module roles ------------------------------------------------------------
# The generator renders the modules into a bundle (docker-compose.yml, per-module
# configs, host units) and embeds it as KS_FILES[stack/...] / KS_FILES[unit/...]
# with KS_STACK_* describing directories, ports, sysctls and kernel modules.
KS_STACK_ENV=/etc/kiwi-server/stack.env

ks_stack_host_prep() {
    local m kv
    for m in "${KS_STACK_KMODS[@]}"; do
        modprobe "$m" 2>/dev/null || ks_warn "kernel module $m did not load"
        install -d -m 0755 /etc/modules-load.d
        echo "$m" > "/etc/modules-load.d/kiwi-$m.conf"
    done
    if [[ ${#KS_STACK_SYSCTL[@]} -gt 0 ]]; then
        install -d -m 0755 /etc/sysctl.d
        printf '%s\n' "# kiwi-server stack" "${KS_STACK_SYSCTL[@]}" > /etc/sysctl.d/90-kiwi-stack.conf
        for kv in "${KS_STACK_SYSCTL[@]}"; do sysctl -q -w "$kv" || true; done
    fi
    # Pi-hole wants port 53 on the host; systemd-resolved's stub listener (Fedora
    # CoreOS, uCore) sits on 127.0.0.53:53 and would block it. Debian's default
    # install has no systemd-resolved, so nothing happens there.
    if (( KS_STACK_NO_RESOLVED_STUB )) && systemctl is-active -q systemd-resolved 2>/dev/null; then
        ks_say "turning off systemd-resolved's stub listener (port 53 goes to the dns module)"
        install -d -m 0755 /etc/systemd/resolved.conf.d
        printf '[Resolve]\nDNSStubListener=no\n' > /etc/systemd/resolved.conf.d/90-kiwi-stack.conf
        ln -sf /run/systemd/resolve/resolv.conf /etc/resolv.conf
        systemctl restart systemd-resolved
    fi
}

ks_stack_dir_is_data() { # DIR   — true when no rendered file lives under it
    local entry
    for entry in "${KS_STACK_FILES[@]}"; do
        [[ ${entry%:*} == "$1"/* ]] && return 1
    done
    return 0
}

ks_stack_unpack() {
    # Everything the generator rendered (compose file, module configs, gw.sh)
    # is root's: root runs the stack and the host units execute some of these
    # files. Data directories — where containers write — belong to the service
    # user, whose uid the containers that drop privileges (PUID, sftp) use.
    # An absolute directory that already exists (a mounted data disk) is left
    # exactly as it is.
    local dd=$KS_STACK_DIR u=$KS_STACK_USER d entry rel mode unit
    install -d -m 0755 "$dd"
    chown root:root "$dd"
    for d in "${KS_STACK_DIRS[@]}"; do
        if [[ $d == /* ]]; then
            [[ -d $d ]] || install -d -m 0755 -o "$u" -g "$u" "$d"
        elif [[ -d $dd/$d ]]; then
            ks_stack_dir_is_data "$d" && chown "$u:$u" "$dd/$d"
        elif ks_stack_dir_is_data "$d"; then
            install -d -m 0755 -o "$u" -g "$u" "$dd/$d"
        else
            install -d -m 0755 "$dd/$d"
        fi
    done
    for entry in "${KS_STACK_FILES[@]}"; do
        rel=${entry%:*}; mode=${entry##*:}
        ks_file "stack/$rel" "$dd/$rel" "$mode" root:root
    done
    for unit in "${KS_STACK_UNITS[@]}"; do
        ks_file "unit/$unit" "/etc/systemd/system/$unit" 0644
    done
    install -d -m 0755 /etc/kiwi-server
    {
        echo "# written by kiwi-server $KS_VERSION — read by /usr/local/bin/kiwi-stack"
        echo "STACK_DIR=$dd"
        echo "STACK_USER=$u"
        echo "STACK_PREFIX=$KS_STACK_PREFIX"
        echo "STACK_VPN_CONTAINER=$KS_STACK_VPN_CONTAINER"
        echo "STACK_VPN_DEPENDENTS=\"${KS_STACK_VPN_DEPENDENTS[*]}\""
        echo "STACK_UNITS=\"${KS_STACK_UNITS[*]}\""
        echo "STACK_MODULES=\"${KS_STACK_MODULES[*]}\""
    } | ks_write "$KS_STACK_ENV" 0644
}

ks_stack_install_cli() {
    ks_write /usr/local/bin/kiwi-stack 0755 <<'KIWI_STACK'
#!/usr/bin/env bash
# kiwi-stack — run the stack kiwi-server put on this machine.
#   kiwi-stack start|stop|restart|update|status|logs [service]|vpn-restart
set -euo pipefail
# shellcheck disable=SC1091
. /etc/kiwi-server/stack.env
cd "$STACK_DIR"
compose() { docker compose --file "$STACK_DIR/docker-compose.yml" "$@"; }
# The host units (kn-gateway.service …) are ordered After=kiwi-stack.service,
# and `start` is kiwi-stack.service's own ExecStart: a blocking restart from
# here would wait for itself. --no-block queues the restart; systemd runs it
# once this service is up.
host_units() { local verb=$1 u; shift; for u in $STACK_UNITS; do systemctl "$verb" "$@" "$u" 2>/dev/null || true; done; }
vpn_restart() {
    [[ -n ${STACK_VPN_CONTAINER:-} ]] || return 0
    # containers that share the VPN client's network namespace must follow it,
    # or they are left in a namespace that no longer exists
    # shellcheck disable=SC2086
    docker restart "$STACK_VPN_CONTAINER" ${STACK_VPN_DEPENDENTS:-}
}
case "${1:-}" in
    start)   compose up --detach --remove-orphans; host_units restart --no-block ;;
    stop)    host_units stop; compose down ;;
    restart) "$0" stop; "$0" start ;;
    update)  compose pull; compose up --detach --remove-orphans; docker image prune -f >/dev/null; host_units restart --no-block ;;
    status)  compose ps; host_units status --no-pager ;;
    logs)    compose logs --follow "${@:2}" ;;
    vpn-restart) vpn_restart ;;
    *) echo "usage: kiwi-stack start|stop|restart|update|status|logs [service]|vpn-restart" >&2; exit 1 ;;
esac
KIWI_STACK
}

ks_stack_units() { # the service that brings the stack up, and the two maintenance timers
    local vpn_restart=${KS_ROLE_DAILY_VPN_RESTART:-} weekly=${KS_ROLE_WEEKLY_UPDATE:-}
    ks_unit kiwi-stack.service <<UNIT
[Unit]
Description=Kiwi Server stack ($KS_ROLE: ${KS_STACK_MODULES[*]})
Documentation=https://github.com/derlocke-ng/kiwi-server
After=docker.service network-online.target
Wants=network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/kiwi-stack start
ExecStop=/usr/local/bin/kiwi-stack stop
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
UNIT
    if [[ -n $vpn_restart && -n $KS_STACK_VPN_CONTAINER ]]; then
        ks_unit kiwi-stack-vpn-restart.service <<UNIT
[Unit]
Description=Kiwi Server: restart the VPN client (new exit address)
After=docker.service kiwi-stack.service
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/kiwi-stack vpn-restart
UNIT
        ks_unit kiwi-stack-vpn-restart.timer <<UNIT
[Unit]
Description=Kiwi Server: daily VPN client restart

[Timer]
OnCalendar=*-*-* $vpn_restart:00
Persistent=false

[Install]
WantedBy=timers.target
UNIT
        systemctl enable --now kiwi-stack-vpn-restart.timer
    fi
    if [[ -n $weekly ]]; then
        ks_unit kiwi-stack-update.service <<UNIT
[Unit]
Description=Kiwi Server: pull new images and restart the stack
After=docker.service kiwi-stack.service
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/kiwi-stack update
UNIT
        ks_unit kiwi-stack-update.timer <<UNIT
[Unit]
Description=Kiwi Server: weekly stack update

[Timer]
OnCalendar=$weekly:00
Persistent=true
RandomizedDelaySec=10min

[Install]
WantedBy=timers.target
UNIT
        systemctl enable --now kiwi-stack-update.timer
    fi
}

ks_stack_apply() {
    (( KS_STACK )) || ks_die "ks_stack_apply called for a role without modules"
    local u=$KS_STACK_USER unit url
    ks_ensure_user "$u" 1000
    ks_ensure_docker "${KS_ROLE_DOCKER_SOURCE:-ce}"
    ks_add_to_group "$u" docker
    ks_add_to_group "$KS_ADMIN_USER" docker
    ks_stack_host_prep
    ks_stack_unpack
    ks_stack_install_cli
    if [[ ${#KS_STACK_PORTS[@]} -gt 0 ]]; then
        ks_firewall_open "${KS_STACK_PORTS[@]}"
    fi
    systemctl daemon-reload
    ks_say "starting the stack (${KS_STACK_MODULES[*]}) — the first start pulls every image"
    ks_stack_units
    # restart, not `enable --now`: on a re-run the service is already active and
    # the containers must pick up the replaced files (a bind mount keeps the old inode)
    systemctl enable -q kiwi-stack.service
    systemctl restart kiwi-stack.service
    for unit in "${KS_STACK_UNITS[@]}"; do
        systemctl enable -q "$unit"
        systemctl restart "$unit" || ks_warn "$unit did not start — journalctl -u $unit"
    done
    ks_say "stack is up in $KS_STACK_DIR — manage it with: sudo kiwi-stack start|stop|update|status|logs"
    for url in "${KS_STACK_URLS[@]}"; do ks_say "  $url"; done
}

# ---- base -----------------------------------------------------------------------------
ks_trust_ca() { # install the fleet CA (KS_FILES[ca_cert]) into the system trust store
    ks_has_file ca_cert || return 0
    if [[ -d /etc/pki/ca-trust/source/anchors ]]; then
        ks_file ca_cert /etc/pki/ca-trust/source/anchors/kiwiCA.pem 0644
        update-ca-trust 2>/dev/null || true
    elif [[ -d /usr/local/share/ca-certificates ]] || ks_is_debian; then
        ks_apt ca-certificates
        ks_file ca_cert /usr/local/share/ca-certificates/kiwiCA.crt 0644
        update-ca-certificates >/dev/null 2>&1 || true
    fi
    ks_say "fleet CA trusted (kiwiCA)"
}

ks_base() {
    if [[ $(hostname) != "$KS_HOSTNAME" ]]; then
        hostnamectl set-hostname "$KS_HOSTNAME" 2>/dev/null || echo "$KS_HOSTNAME" > /etc/hostname
    fi
    timedatectl set-timezone "$KS_TIMEZONE" 2>/dev/null || true
    ks_apt_update
    ks_trust_ca
}

ks_main() {
    [[ $EUID -eq 0 ]] || ks_die "this script must run as root (sudo bash $0)"
    KS_OS=$(ks_detect_os)
    install -d -m 0700 "$KS_STATE_DIR"
    exec > >(tee -a "$KS_LOG") 2>&1
    ks_say "kiwi-server $KS_VERSION — role $KS_ROLE on $KS_HOSTNAME ($KS_OS, target $KS_TARGET)"
    if [[ -f $KS_MARKER && ${1:-} != --force ]]; then
        ks_say "already applied on $(cat "$KS_MARKER") — run with --force to apply again"
        exit 0
    fi
    ks_base
    ks_role_apply
    ks_post_script
    date -u +%FT%TZ > "$KS_MARKER"
    ks_say "role $KS_ROLE applied — log: $KS_LOG"
}
