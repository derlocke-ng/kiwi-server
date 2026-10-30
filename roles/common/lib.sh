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
KS_FAILED=$KS_STATE_DIR/role.failed
KS_LOG=/var/log/kiwi-server-role.log
KS_OS=other
KS_APT_UPDATED=0

KS_STEP=starting
ks_say()  { KS_STEP=$*; printf ':: %s\n' "$*"; }
ks_warn() { printf 'warning: %s\n' "$*" >&2; }
ks_die()  { printf 'error: %s\n' "$*" >&2; exit 1; }
ks_fail() { # EXIT LINE COMMAND — the ERR trap: say where it stopped and leave a marker status can show
    local rc=$1 line=$2 cmd=$3 fn=${FUNCNAME[1]:-main}
    # KS_STEP is the last thing ks_say announced, which may be an earlier step; the
    # function the command sits in says where it really stopped
    {
        echo "failed: $(date -u +%FT%TZ)"
        echo "step: $KS_STEP"
        echo "function: $fn"
        echo "command: $cmd (line $line, exit $rc)"
        echo "log: $KS_LOG — last lines:"
        tail -n 20 "$KS_LOG" 2>/dev/null | sed 's/^/  /'
    } > "$KS_FAILED" 2>/dev/null || true
    printf 'error: the role stopped during "%s", in %s: %s (line %s, exit %s) — see %s and %s\n' \
        "$KS_STEP" "$fn" "$cmd" "$line" "$rc" "$KS_LOG" "$KS_FAILED" >&2
    exit "$rc"
}

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
# Files reach the machine either embedded in the role script (KS_FILES, base64)
# or as a bundle directory (KS_BUNDLE_DIR: what `kiwi-modules render` writes and
# `kiwi-stack apply DIR` reads) — the same keys name the same files.
declare -p KS_FILES >/dev/null 2>&1 || declare -gA KS_FILES=()
ks_bundle_path() { # KEY -> path inside $KS_BUNDLE_DIR
    local key=$1
    case $key in
        stack/*)        echo "$KS_BUNDLE_DIR/${key#stack/}" ;;
        unit/*)         echo "$KS_BUNDLE_DIR/host-units/${key#unit/}" ;;
        quadlet/*)      echo "$KS_BUNDLE_DIR/quadlets/${key#quadlet/}" ;;
        ca_cert)        echo "$KS_BUNDLE_DIR/kiwi/ca/kiwiCA.crt" ;;
        kiwi-stack)     echo "${KS_STACK_SELF:-/usr/local/bin/kiwi-stack}" ;;
        kiwi-stack-lib) echo "${KS_STACK_LIB_SELF:-/usr/local/lib/kiwi-stack/lib.sh}" ;;
        *)              echo "$KS_BUNDLE_DIR/$key" ;;
    esac
}
ks_has_file() { # KEY
    [[ -n ${KS_FILES[$1]:-} ]] && return 0
    [[ -n ${KS_BUNDLE_DIR:-} && -f $(ks_bundle_path "$1") ]]
}
ks_file() { # KEY DEST [MODE] [OWNER]   — write a file the generator provided
    local key=$1 dest=$2 mode=${3:-0600} owner=${4:-root:root} src
    ks_parent_dir "$dest"
    if [[ -n ${KS_FILES[$key]:-} ]]; then
        printf '%s' "${KS_FILES[$key]}" | base64 -d > "$dest.tmp"
    elif [[ -n ${KS_BUNDLE_DIR:-} ]] && src=$(ks_bundle_path "$key") && [[ -f $src ]]; then
        [[ $src -ef $dest ]] && { rm -f "$dest.tmp" 2>/dev/null; return 0; }   # installing itself over itself
        cp "$src" "$dest.tmp"
    else
        ks_die "no file for '$key' — neither embedded nor in ${KS_BUNDLE_DIR:-a bundle directory}"
    fi
    chmod "$mode" "$dest.tmp"
    chown "$owner" "$dest.tmp"
    mv -f "$dest.tmp" "$dest"
}

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
ks_ensure_podman() {
    if ! command -v podman >/dev/null 2>&1; then
        case $KS_OS in
            debian) ks_apt podman netavark aardvark-dns catatonit curl ;;
            ostree) ks_die "this image has no podman — every Fedora CoreOS and uCore image ships it; is this one stripped?" ;;
            *)      ks_die "podman is not installed and this is not Debian — install it first" ;;
        esac
    fi
    # the modules' `init: true` needs catatonit (a Recommends on Debian, not a Depends)
    if [[ $KS_OS == debian ]] && ! ls /usr/libexec/podman/catatonit /usr/bin/catatonit >/dev/null 2>&1; then
        ks_apt catatonit
    fi
    local pv
    pv=$(podman --version 2>/dev/null | awk '{print $NF}')
    if [[ -n $pv ]] && [[ $(printf '%s\n4.4\n' "$pv" | sort -V | head -1) != 4.4 ]]; then
        ks_die "podman $pv is too old for quadlets (4.4 or newer) — Debian trixie, or runtime: docker in the role block"
    fi
    # the API socket: what Portainer (and anything else that speaks docker) talks to
    systemctl enable --now podman.socket 2>/dev/null || true
    install -d -m 0755 /etc/containers/systemd
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
    ks_write "${KS_SYSTEMD_DIR:-/etc/systemd/system}/$1" 0644
    systemctl daemon-reload
}
ks_firewall_open() { # PORT/PROTO...
    if systemctl is-active -q firewalld 2>/dev/null; then
        local p
        for p in "$@"; do firewall-cmd -q --permanent --add-port="$p"; done
        firewall-cmd -q --reload
    elif command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
        local p
        for p in "$@"; do ufw allow "${p/-/:}" >/dev/null; done   # ufw spells a range a:b
    fi
}

ks_firewall_trust_mesh() { # the mesh is as trusted as the stack network itself
    [[ -n ${KS_STACK_MESH_SUBNET:-} && -n ${KS_STACK_MESH_VIA:-} ]] || return 0
    systemctl is-active -q firewalld 2>/dev/null || return 0
    # A mesh client's connection to a service here is DNAT'd inside the VPN client
    # and goes to the container straight over the bridge; the container answers
    # through its gateway, the host, which routes the mesh back into the VPN
    # client. The host's conntrack saw only the reply, and firewalld dropped it as
    # invalid (found with the first device, problem 57). So the host tracks the
    # bridged traffic too, and the mesh is a trusted source like the stack network
    # netavark already put there.
    modprobe br_netfilter 2>/dev/null || true
    install -d -m 0755 /etc/modules-load.d /etc/sysctl.d
    echo br_netfilter > /etc/modules-load.d/kiwi-br_netfilter.conf
    printf '%s\n' '# kiwi-server stack: the host tracks bridged container traffic (problem 57)' \
        'net.bridge.bridge-nf-call-iptables = 1' > /etc/sysctl.d/91-kiwi-mesh.conf
    sysctl -q -w net.bridge.bridge-nf-call-iptables=1 2>/dev/null || true
    if ! firewall-cmd -q --zone=trusted --query-source="$KS_STACK_MESH_SUBNET" 2>/dev/null; then
        ks_say "firewalld: the mesh $KS_STACK_MESH_SUBNET is trusted like the stack network"
        firewall-cmd -q --permanent --zone=trusted --add-source="$KS_STACK_MESH_SUBNET"
        firewall-cmd -q --zone=trusted --add-source="$KS_STACK_MESH_SUBNET"
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
KS_SYSTEMD_DIR=/etc/systemd/system

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
    if [[ $KS_STACK_RUNTIME == podman && -n ${KS_STACK_DNS_IP:-} ]]; then
        # podman's own resolver (aardvark-dns) listens on the stack network's gateway,
        # port 53 — and a container publishing 53 on every address (Pi-hole) then fails
        # with "address already in use". containers.conf moves aardvark to another port;
        # podman redirects the containers' lookups there itself. Read at network start.
        install -d -m 0755 /etc/containers/containers.conf.d
        printf '%s\n' '# kiwi-server stack: podman'"'"'s own DNS off port 53, so the dns module may publish it' \
            '[network]' 'dns_bind_port = 5533' > /etc/containers/containers.conf.d/50-kiwi-dns.conf
    fi
    # Pi-hole wants port 53 on the host; systemd-resolved's stub listener (Fedora
    # CoreOS, uCore) sits on 127.0.0.53:53 and would block it. Debian's default
    # install has no systemd-resolved, so nothing happens there.
    if systemctl is-active -q systemd-resolved 2>/dev/null && { (( KS_STACK_NO_RESOLVED_STUB )) || [[ -n ${KS_STACK_DNS_IP:-} ]]; }; then
        # the stub listener on 127.0.0.53:53 would block the dns module's port 53; and the
        # host itself asks this stack's Pi-hole (Quad9 behind it while the stack is down),
        # so the fleet's names and the blocklist hold for the host, not only its containers
        if [[ -n ${KS_STACK_DNS_IP:-} ]]; then
            ks_say "systemd-resolved: the host resolves through this stack's Pi-hole at $KS_STACK_DNS_IP (Quad9 while it is down); stub listener off"
        else
            ks_say "turning off systemd-resolved's stub listener (port 53 goes to the dns module)"
        fi
        install -d -m 0755 /etc/systemd/resolved.conf.d
        {
            echo '[Resolve]'
            (( KS_STACK_NO_RESOLVED_STUB )) && echo 'DNSStubListener=no'
            if [[ -n ${KS_STACK_DNS_IP:-} ]]; then echo "DNS=$KS_STACK_DNS_IP 9.9.9.9 149.112.112.112"; echo 'Domains=~.'; fi
        } > /etc/systemd/resolved.conf.d/90-kiwi-stack.conf
        (( KS_STACK_NO_RESOLVED_STUB )) && ln -sf /run/systemd/resolve/resolv.conf /etc/resolv.conf
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

ks_stack_hosts() {
    # The fleet's names in /etc/hosts: the host resolves every node and service
    # without a Pi-hole (backups, certificate renewal, kiwi-stack itself). Its own
    # names point at itself. The block is replaced on every run.
    local begin='# >>> kiwi-server fleet names >>>' end='# <<< kiwi-server fleet names <<<' rest=''
    [[ -f /etc/hosts ]] && rest=$(sed "/^${begin}\$/,/^${end}\$/d" /etc/hosts)
    {
        printf '%s\n' "$rest"
        if [[ ${#KS_STACK_HOSTS[@]} -gt 0 ]]; then
            echo "$begin"
            printf '%s\n' "${KS_STACK_HOSTS[@]}"
            echo "$end"
        fi
    } | ks_write /etc/hosts 0644
}

ks_stack_drop_units() {
    # A host unit the last apply installed and this one does not (a module or a
    # setting such as dns.mullvad_socks turned off) is stopped and removed:
    # kiwi-stack no longer knows it, so nothing else would ever stop it.
    local old='' unit u keep
    [[ -f $KS_STACK_ENV ]] && old=$(sed -n 's/^STACK_UNITS="\(.*\)"$/\1/p' "$KS_STACK_ENV")
    for unit in $old; do
        [[ $unit =~ ^[A-Za-z0-9@._-]+\.(service|timer|path|socket)$ ]] || continue
        keep=0
        for u in "${KS_STACK_UNITS[@]}"; do [[ $u == "$unit" ]] && keep=1; done
        (( keep )) && continue
        ks_say "removing the host unit $unit — no module installs it any more"
        systemctl disable --now "$unit" 2>/dev/null || true
        rm -f "${KS_SYSTEMD_DIR:?}/$unit"
    done
    systemctl daemon-reload 2>/dev/null || true
}

ks_stack_unpack() {
    # Everything the generator rendered (compose file, module configs, gw.sh)
    # is root's: root runs the stack and the host units execute some of these
    # files. Data directories — where containers write — belong to the service
    # user, whose uid the containers that drop privileges (PUID, sftp) use.
    # An absolute directory that already exists (a mounted data disk) is left
    # exactly as it is.
    local dd=$KS_STACK_DIR u=$KS_STACK_USER d entry rel mode unit old_rt='' old_dir='' old_services='' svc keep
    if [[ -f $KS_STACK_ENV ]]; then
        local old_seed
        old_seed=$(sed -n 's/^STACK_SEED_ID=//p' "$KS_STACK_ENV")
        if [[ -n $old_seed && -n ${KS_SEED_ID:-} && $old_seed != "$KS_SEED_ID" ]]; then
            ks_die "the fleet's seed changed ($old_seed -> $KS_SEED_ID): the generated secrets differ from the ones the" \
                   "running databases were set up with. Restore secrets/seed from the fleet backup; to start over with" \
                   "new secrets on an empty stack, remove $KS_STACK_ENV and the data directories first"
        fi
        old_rt=$(sed -n 's/^STACK_RUNTIME=//p' "$KS_STACK_ENV")
        old_dir=$(sed -n 's/^STACK_DIR=//p' "$KS_STACK_ENV")
        old_services=$(sed -n 's/^STACK_SERVICES="\(.*\)"$/\1/p' "$KS_STACK_ENV")
    fi
    if [[ -n $old_rt && ( $old_rt != "$KS_STACK_RUNTIME" || ( -n $old_dir && $old_dir != "$dd" ) ) ]] && [[ -x /usr/local/bin/kiwi-stack ]]; then
        # the installed kiwi-stack still speaks the old runtime and directory: take that stack
        # down before stack.env describes the new one, or both fight for the same ports
        ks_say "the stack moves from $old_rt in $old_dir to $KS_STACK_RUNTIME in $dd — stopping the old stack first"
        /usr/local/bin/kiwi-stack stop || ks_warn "the old stack did not stop cleanly — check with podman ps / docker ps"
        old_services=''
    fi
    if [[ $KS_STACK_RUNTIME == podman && -n $old_services ]]; then
        # a container whose module left the stack: its quadlet goes below, the running
        # unit would stay (a target does not stop what merely wants it)
        for svc in $old_services; do
            keep=0
            for u in "${KS_STACK_SERVICES[@]}"; do [[ $u == "$svc" ]] && keep=1; done
            (( keep )) && continue
            [[ $svc =~ ^[A-Za-z0-9_.-]+$ ]] || continue
            ks_say "stopping $svc — no module runs it any more"
            systemctl stop "$svc.service" 2>/dev/null || true
            podman rm -f "$svc" >/dev/null 2>&1 || true
        done
    fi
    u=$KS_STACK_USER
    install -d -m 0755 "$dd"
    chown root:root "$dd"
    for d in "${KS_STACK_DIRS[@]}"; do
        if [[ $d == /* ]]; then
            [[ -d $d ]] || install -d -m 0755 -o "$u" -g "$u" "$d"
        elif [[ -d $dd/$d ]]; then
            if ks_stack_dir_is_data "$d"; then
                chown "$u:$u" "$dd/$d"
            else   # rendered files live here: root's, writable by nobody else (the Mullvad refresh insists)
                chown root:root "$dd/$d"; chmod go-w "$dd/$d"
            fi
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
    ks_stack_drop_units
    for unit in "${KS_STACK_UNITS[@]}"; do
        ks_file "unit/$unit" "$KS_SYSTEMD_DIR/$unit" 0644
    done
    if [[ $KS_STACK_RUNTIME == podman ]]; then
        # the quadlets: every unit this tool wrote before goes first, so a module
        # that left the stack takes its container with it
        local q netname
        declare -A old_net=()
        install -d -m 0755 /etc/containers/systemd
        # a .network quadlet only creates its network when it is missing: remember what
        # the installed ones said, so a changed one gets its network recreated below
        for q in /etc/containers/systemd/*.network; do
            [[ -f $q ]] && old_net[$(basename "$q")]=$(sha256sum < "$q")
        done
        # one file at a time: on a fresh machine the globs match nothing, and a grep
        # over the literal patterns exits 2, which pipefail turned into a failed apply
        for q in /etc/containers/systemd/*.container /etc/containers/systemd/*.network; do
            if [[ -f $q ]] && grep -qsF '# kiwi-server stack' "$q"; then rm -f "$q"; fi
        done
        for q in "${KS_STACK_QUADLETS[@]}"; do
            case $q in
                *.target) ks_file "quadlet/$q" "$KS_SYSTEMD_DIR/$q" 0644 ;;
                *)        ks_file "quadlet/$q" "/etc/containers/systemd/$q" 0600 ;;
            esac
        done
        for q in "${KS_STACK_QUADLETS[@]}"; do
            [[ $q == *.network ]] || continue
            netname=$(sed -n 's/^NetworkName=//p' "/etc/containers/systemd/$q")
            [[ -n $netname ]] || continue
            if [[ -n ${old_net[$q]:-} && ${old_net[$q]} != "$(sha256sum < "/etc/containers/systemd/$q")" ]] \
               && podman network exists "$netname" 2>/dev/null; then
                ks_say "the stack network $netname changed — stopping the stack and recreating it"
                systemctl stop kiwi-stack.service 2>/dev/null || true
                podman network rm -f "$netname" >/dev/null || ks_warn "could not remove network $netname — podman network rm -f $netname by hand"
            fi
            # the quadlet's generated oneshot (knet-node-network.service) stays "active (exited)"
            # after the network is gone and would not run again: stop it, so the start recreates
            # the network before the containers ask for it
            if ! podman network exists "$netname" 2>/dev/null; then
                systemctl stop "${q%.network}-network.service" 2>/dev/null || true
            fi
        done
    fi
    install -d -m 0755 /etc/kiwi-server
    {
        echo "# written by kiwi-server $KS_VERSION — read by /usr/local/bin/kiwi-stack"
        echo "STACK_DIR=$dd"
        echo "STACK_USER=$u"
        echo "STACK_RUNTIME=$KS_STACK_RUNTIME"
        echo "STACK_SERVICES=\"${KS_STACK_SERVICES[*]}\""
        echo "STACK_PREFIX=$KS_STACK_PREFIX"
        echo "STACK_VPN_CONTAINER=$KS_STACK_VPN_CONTAINER"
        echo "STACK_VPN_DEPENDENTS=\"${KS_STACK_VPN_DEPENDENTS[*]}\""
        echo "STACK_MESH_SUBNET=$KS_STACK_MESH_SUBNET"
        echo "STACK_MESH_VIA=$KS_STACK_MESH_VIA"
        echo "STACK_UNITS=\"${KS_STACK_UNITS[*]}\""
        echo "STACK_MODULES=\"${KS_STACK_MODULES[*]}\""
        echo "STACK_SEED_ID=${KS_SEED_ID:-}"
        echo "STACK_AUTO_UPDATE=${KS_STACK_AUTO_UPDATE:-registry}"
    } | ks_write "$KS_STACK_ENV" 0644
}

ks_stack_pull() {
    # Every image before the first start, one after the other with retries and a
    # visible name: a target that starts a dozen cold pulls at once on a home
    # uplink times units out and the stack looks up while nothing runs.
    local img n ok
    [[ ${#KS_STACK_IMAGES[@]} -gt 0 ]] || return 0
    ks_say "pulling ${#KS_STACK_IMAGES[@]} images (the first run of a stack; later runs are quick)"
    for img in "${KS_STACK_IMAGES[@]}"; do
        ok=0
        for n in 1 2 3; do
            if [[ $KS_STACK_RUNTIME == podman ]]; then
                podman pull -q "$img" >/dev/null && ok=1 && break
            else
                docker pull -q "$img" >/dev/null && ok=1 && break
            fi
            ks_warn "pull of $img failed (try $n of 3)"; sleep $((n * 10))
        done
        (( ok )) || ks_die "cannot pull $img — is the Internet reachable from this host?"
        ks_say "  $img"
    done
}

ks_stack_install_cli() {
    # the kiwi-stack CLI and this library itself, so `kiwi-stack apply DIR` can run a
    # bundle rendered elsewhere (kiwi-modules) without a role script
    ks_file kiwi-stack /usr/local/bin/kiwi-stack 0755
    ks_file kiwi-stack-lib /usr/local/lib/kiwi-stack/lib.sh 0644
}

ks_stack_units() { # the service that brings the stack up, and the two maintenance timers
    local vpn_restart=${KS_ROLE_DAILY_VPN_RESTART:-} weekly=${KS_ROLE_WEEKLY_UPDATE:-} rt_unit="" rt_requires=""
    if [[ $KS_STACK_RUNTIME == docker ]]; then rt_unit="docker.service"; rt_requires="Requires=docker.service"; fi
    ks_unit kiwi-stack.service <<UNIT
[Unit]
Description=Kiwi Server stack ($KS_ROLE: ${KS_STACK_MODULES[*]}, $KS_STACK_RUNTIME)
Documentation=https://github.com/derlocke-ng/kiwi-server
After=$rt_unit network-online.target
Wants=network-online.target
$rt_requires

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
After=$rt_unit kiwi-stack.service
$rt_requires

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
After=$rt_unit kiwi-stack.service
$rt_requires

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
    if [[ $KS_STACK_RUNTIME == podman ]]; then
        ks_ensure_podman
    else
        ks_ensure_docker "${KS_ROLE_DOCKER_SOURCE:-ce}"
        ks_add_to_group "$u" docker
        ks_add_to_group "$KS_ADMIN_USER" docker
    fi
    ks_stack_host_prep
    ks_stack_hosts
    ks_stack_unpack
    ks_stack_install_cli
    ks_firewall_trust_mesh
    if [[ ${#KS_STACK_PORTS[@]} -gt 0 ]]; then
        ks_firewall_open "${KS_STACK_PORTS[@]}"
    fi
    systemctl daemon-reload
    ks_stack_pull
    ks_say "starting the stack (${KS_STACK_MODULES[*]})"
    ks_stack_units
    # restart, not `enable --now`: on a re-run the service is already active and
    # the containers must pick up the replaced files (a bind mount keeps the old inode)
    systemctl enable -q kiwi-stack.service
    systemctl restart kiwi-stack.service || ks_die "kiwi-stack.service did not start — journalctl -u kiwi-stack"
    ks_say "checking that every container is running"
    /usr/local/bin/kiwi-stack verify || ks_die "the stack is not up — the containers above are not running (kiwi-stack logs <name>)"
    for unit in "${KS_STACK_UNITS[@]}"; do
        systemctl enable -q "$unit" 2>/dev/null || true   # a service its timer owns has no [Install]
        if [[ $unit == *.service && " ${KS_STACK_UNITS[*]} " == *" ${unit%.service}.timer "* ]]; then
            # a timer's service: run it now, but not on the apply's critical path (it may fetch from the Internet)
            systemctl restart --no-block "$unit" || ks_warn "$unit did not start — journalctl -u $unit"
        else
            systemctl restart "$unit" || ks_warn "$unit did not start — journalctl -u $unit"
        fi
    done
    ks_say "stack is up in $KS_STACK_DIR — manage it with: sudo kiwi-stack start|stop|update|status|verify|logs"
    for url in "${KS_STACK_URLS[@]}"; do ks_say "  $url"; done
}

ks_stack_standalone() { # DIR   — `kiwi-stack apply DIR`: a bundle without a role script
    local dir=$1
    [[ $EUID -eq 0 ]] || ks_die "this must run as root"
    [[ -f $dir/stack.sh ]] || ks_die "$dir is not a stack bundle (no stack.sh) — kiwi-modules render writes one"
    KS_BUNDLE_DIR=$(readlink -f "$dir")
    # shellcheck disable=SC1091
    . "$KS_BUNDLE_DIR/stack.sh"
    KS_OS=$(ks_detect_os)
    install -d -m 0700 "$KS_STATE_DIR"
    exec > >(tee -a "$KS_LOG") 2>&1
    ks_say "kiwi-stack $KS_VERSION — stack ${KS_STACK_MODULES[*]} on $KS_HOSTNAME from $KS_BUNDLE_DIR ($KS_OS)"
    trap 'ks_fail $? $LINENO "$BASH_COMMAND"' ERR
    set -E
    ks_trust_ca
    ks_stack_apply
    trap - ERR
    rm -f "$KS_FAILED"
    date -u +%FT%TZ > "$KS_STATE_DIR/stack.applied"
    ks_say "stack applied — log: $KS_LOG"
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

# network-online.target is satisfied before the resolver is (a first boot on
# Debian saw apt fail with "Temporary failure resolving" and go on without
# updates). Wait for names to resolve before the first apt or pull, two
# minutes at most; a timeout is a warning, the steps that follow retry anyway.
ks_wait_dns() {
    local i name
    for ((i = 0; i < 60; i++)); do
        for name in deb.debian.org quay.io registry.fedoraproject.org; do
            getent hosts "$name" >/dev/null 2>&1 && return 0
        done
        [[ $i -eq 0 ]] && ks_say "waiting for name resolution"
        sleep 2
    done
    ks_warn "names do not resolve after 120 s — going on; apt and pulls retry on their own"
}

ks_main() {
    [[ $EUID -eq 0 ]] || ks_die "this script must run as root (sudo bash $0)"
    KS_OS=$(ks_detect_os)
    install -d -m 0700 "$KS_STATE_DIR" "$KS_STATE_DIR/backups"   # backups/: kiwi-server backup
    exec > >(tee -a "$KS_LOG") 2>&1
    ks_say "kiwi-server $KS_VERSION — role $KS_ROLE on $KS_HOSTNAME ($KS_OS, target $KS_TARGET)"
    if [[ -f $KS_MARKER && ${1:-} != --force ]]; then
        ks_say "already applied on $(cat "$KS_MARKER") — run with --force to apply again"
        exit 0
    fi
    # a failed step names itself and leaves role.failed; a success removes it
    trap 'ks_fail $? $LINENO "$BASH_COMMAND"' ERR
    set -E
    ks_wait_dns
    ks_base
    ks_role_apply
    ks_post_script
    trap - ERR
    rm -f "$KS_FAILED"
    date -u +%FT%TZ > "$KS_MARKER"
    ks_say "role $KS_ROLE applied — log: $KS_LOG"
    # the script carries every secret of this host: it stays for `kiwi-stack` re-runs
    # (apply puts a fresh copy), readable by root alone
    chmod 0600 "$0" 2>/dev/null || true
}
