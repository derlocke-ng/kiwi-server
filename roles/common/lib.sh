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
ks_file() { # KEY DEST [MODE] [OWNER]   — write a file embedded by the generator
    local key=$1 dest=$2 mode=${3:-0600} owner=${4:-root:root}
    [[ -n ${KS_FILES[$key]:-} ]] || ks_die "no embedded file for setting '$key'"
    install -d -m 0755 "$(dirname "$dest")"
    printf '%s' "${KS_FILES[$key]}" | base64 -d > "$dest.tmp"
    chmod "$mode" "$dest.tmp"
    chown "$owner" "$dest.tmp"
    mv -f "$dest.tmp" "$dest"
}
ks_has_file() { [[ -n ${KS_FILES[$1]:-} ]]; }

ks_write() { # DEST [MODE] [OWNER]   — content on stdin
    local dest=$1 mode=${2:-0644} owner=${3:-root:root}
    install -d -m 0755 "$(dirname "$dest")"
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

# ---- base -----------------------------------------------------------------------------
ks_base() {
    if [[ $(hostname) != "$KS_HOSTNAME" ]]; then
        hostnamectl set-hostname "$KS_HOSTNAME" 2>/dev/null || echo "$KS_HOSTNAME" > /etc/hostname
    fi
    timedatectl set-timezone "$KS_TIMEZONE" 2>/dev/null || true
    ks_apt_update
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
