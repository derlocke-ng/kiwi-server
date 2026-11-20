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
    # A fresh Debian fetches its own package lists right after its first boot and holds their
    # lock; apt's lock timeout does not cover that one. Wait for it instead of failing (found:
    # a setup started a minute after the install stopped here).
    local n
    for n in $(seq 90); do
        if apt-get -o DPkg::Lock::Timeout=600 -q update; then
            KS_APT_UPDATED=1
            return 0
        fi
        pgrep -x apt-get >/dev/null || pgrep -x apt >/dev/null || pgrep -f unattended-upgrade >/dev/null || break
        (( n == 1 )) && ks_say "the system is fetching its own package lists: waiting for it"
        sleep 10
    done
    ks_die "apt-get update did not work — is the Internet reachable from this machine?"
}
ks_apt() { # package...
    ks_is_debian || { ks_warn "not Debian — cannot install: $*"; return 0; }
    local p missing=0
    for p in "$@"; do
        dpkg-query -W -f '${Status}' "$p" 2>/dev/null | grep -q "ok installed" || missing=1
    done
    (( missing )) || return 0       # all there: no lists fetched on every apply (newer ones: the OS's own updates)
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
ks_replace() { # TMP DEST   — TMP takes DEST's place, on the disk first: a power cut in the middle of
    # an apply leaves the old file or the new one, never an empty one (XFS keeps no data back for a rename)
    sync -- "$1" 2>/dev/null || true
    mv -f -- "$1" "$2"
    sync -- "$(dirname -- "$2")" 2>/dev/null || true
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
    ks_replace "$dest.tmp" "$dest"
}

ks_write() { # DEST [MODE] [OWNER]   — content on stdin
    local dest=$1 mode=${2:-0644} owner=${3:-root:root}
    ks_parent_dir "$dest"
    cat > "$dest.tmp"
    chmod "$mode" "$dest.tmp"
    chown "$owner" "$dest.tmp"
    ks_replace "$dest.tmp" "$dest"
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
    sed -e "$expr" "$src" > "$dest.tmp" && ks_replace "$dest.tmp" "$dest"
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
            debian) ks_apt podman netavark aardvark-dns catatonit nftables curl ;;
            ostree) ks_die "this image has no podman — every Fedora CoreOS and uCore image ships it; is this one stripped?" ;;
            *)      ks_die "podman is not installed and this is not Debian — install it first" ;;
        esac
    fi
    # the modules' `init: true` needs catatonit (a Recommends on Debian, not a Depends)
    if [[ $KS_OS == debian ]] && ! ls /usr/libexec/podman/catatonit /usr/bin/catatonit >/dev/null 2>&1; then
        ks_apt catatonit
    fi
    # netavark sets up the networks with nft; a minimal Debian (a cloud image, Armbian minimal) lacks it
    if [[ $KS_OS == debian ]] && ! command -v nft >/dev/null 2>&1; then
        ks_apt nftables
    fi
    # the modules' host scripts (the gateway's routing, the VPN client's rules) speak iptables;
    # Armbian's image has nft only, and the gateway then routed nothing ("iptables: command not found")
    if [[ $KS_OS == debian ]] && ! command -v iptables >/dev/null 2>&1; then
        ks_apt iptables
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
# Docker turns iptables' FORWARD policy to DROP when it starts, and what podman publishes goes
# through that chain too. Left over from a time this machine had runtime: docker, it cuts every
# published port off from the network at the next boot, with the containers all looking fine.
ks_retire_docker() {
    systemctl -q is-active docker.service 2>/dev/null || systemctl -q is-enabled docker.service 2>/dev/null || return 0
    if systemctl -q is-active docker.service && [[ -n $(docker ps -q 2>/dev/null) ]]; then
        ks_warn "docker runs containers here beside the podman stack: its FORWARD policy keeps the stack's published ports from the network"
        return 0
    fi
    ks_say "docker runs nothing here: switching it off, its firewall policy would hide the published ports"
    systemctl disable --now docker.socket docker.service 2>/dev/null || true
    iptables -P FORWARD ACCEPT 2>/dev/null || true
}
ks_docker_ce() {
    ks_say "installing Docker CE from download.docker.com"
    ks_apt ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    local codename distro
    distro=$(. /etc/os-release && { [[ $ID == ubuntu || ${ID_LIKE:-} == *ubuntu* ]] && echo ubuntu || echo debian; })  # Armbian comes as either
    curl -fsSL "https://download.docker.com/linux/$distro/gpg" -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    codename=$(. /etc/os-release && echo "$VERSION_CODENAME")
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$distro $codename stable" \
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
    ks_replace "$dir/docker-compose.tmp" "$dir/docker-compose"
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
    [[ -n ${KS_STACK_MESH_SUBNET:-} && -n ${KS_STACK_MESH_VIA:-} && $KS_STACK_RUNTIME == podman ]] || return 0
    # A mesh client's connection to a service here is DNAT'd inside the VPN client
    # and goes to the container straight over the bridge; the container answers
    # through its gateway, the host, which routes the mesh back into the VPN
    # client. The host's conntrack saw only the reply and dropped it as invalid —
    # netavark's own forward chain does that on every podman host, firewalld on
    # top where it runs (found with the first device, problem 57). So the host
    # tracks the bridged traffic too, and where firewalld is, the mesh is a
    # trusted source like the stack network netavark already put there.
    modprobe br_netfilter 2>/dev/null || true
    install -d -m 0755 /etc/modules-load.d /etc/sysctl.d
    echo br_netfilter > /etc/modules-load.d/kiwi-br_netfilter.conf
    printf '%s\n' '# kiwi-server stack: the host tracks bridged container traffic (problem 57)' \
        'net.bridge.bridge-nf-call-iptables = 1' > /etc/sysctl.d/91-kiwi-mesh.conf
    sysctl -q -w net.bridge.bridge-nf-call-iptables=1 2>/dev/null || true
    systemctl is-active -q firewalld 2>/dev/null || return 0
    if ! firewall-cmd -q --zone=trusted --query-source="$KS_STACK_MESH_SUBNET" 2>/dev/null; then
        ks_say "firewalld: the mesh $KS_STACK_MESH_SUBNET is trusted like the stack network"
        firewall-cmd -q --permanent --zone=trusted --add-source="$KS_STACK_MESH_SUBNET"
        firewall-cmd -q --zone=trusted --add-source="$KS_STACK_MESH_SUBNET"
    fi
}

# The groups of the home network (kiwi-server docs/design/lans.md): what each may open on this
# machine. KS_STACK_LAN_RULES: "SUBNET all|none|tcp:443,udp:51820,…" per group. Only what is
# addressed to this machine is looked at (published container ports too: before their DNAT);
# DNS, DHCP and ping stay open to everyone, and answers to what this machine started come back.
ks_lan_nft() { # the nftables ruleset on stdout: replaces the table kiwi_lan in one transaction
    local r net ports p
    printf '%s\n' 'table inet kiwi_lan' 'delete table inet kiwi_lan' 'table inet kiwi_lan {' '  chain pre {' \
        '    type filter hook prerouting priority -160; policy accept;' \
        '    fib daddr type != local accept' \
        '    ct state established,related accept' \
        '    meta l4proto { icmp, ipv6-icmp } accept' \
        '    udp dport { 53, 67, 68, 123 } accept' \
        '    tcp dport 53 accept'
    for r in "${KS_STACK_LAN_RULES[@]}"; do
        net=${r%% *}; ports=${r#* }
        [[ $net =~ ^[0-9.]+/[0-9]+$ ]] || { ks_warn "lan rule $r: not a subnet, left out"; continue; }
        case $ports in
            all) printf '    ip saddr %s accept\n' "$net" ;;
            none) printf '    ip saddr %s drop\n' "$net" ;;
            *)  for p in ${ports//,/ }; do
                    [[ $p =~ ^(tcp|udp):[0-9]+(-[0-9]+)?$ ]] || { ks_warn "lan rule $r: $p left out"; continue; }
                    printf '    ip saddr %s %s dport %s accept\n' "$net" "${p%%:*}" "${p#*:}"
                done
                printf '    ip saddr %s drop\n' "$net" ;;
        esac
    done
    printf '%s\n' '  }' '}'
}

ks_firewall_lan() { # the table kiwi_lan, kept across reboots by kiwi-lan.service; none: gone
    local f=/etc/kiwi-server/lan.nft nft
    nft=$(command -v nft || true)
    if [[ ${#KS_STACK_LAN_RULES[@]} -eq 0 ]]; then
        if [[ -f $f ]]; then
            ks_say "the groups of the home network: none any more"
            [[ -n $nft ]] && "$nft" delete table inet kiwi_lan 2>/dev/null || true
            systemctl disable -q kiwi-lan.service 2>/dev/null || true
            rm -f "$f" "${KS_SYSTEMD_DIR:-/etc/systemd/system}/kiwi-lan.service"
        fi
        return 0
    fi
    [[ -n $nft ]] || { ks_warn "no nft here: the groups of the home network are not kept on this machine"; return 0; }
    install -d -m 0755 /etc/kiwi-server
    ks_lan_nft > "$f.new"
    if ! "$nft" -c -f "$f.new"; then
        rm -f "$f.new"
        ks_warn "the rules for the groups of the home network do not load: left as they were"
        return 0
    fi
    mv "$f.new" "$f"
    "$nft" -f "$f"
    ks_say "the groups of the home network: ${#KS_STACK_LAN_RULES[@]} kept on this machine (nftables kiwi_lan)"
    ks_unit kiwi-lan.service <<UNIT
[Unit]
Description=kiwi-server: what the groups of the home network may open here
DefaultDependencies=no
Before=network-pre.target
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$nft -f $f

[Install]
WantedBy=multi-user.target
UNIT
    systemctl enable -q kiwi-lan.service 2>/dev/null || true
}

ks_vlan_name() { # PARENT ID   — the tagged interface's name: eth0.20, or vlan20 where that is too long
    local n="$1.$2"
    (( ${#n} <= 15 )) && echo "$n" || echo "vlan$2"
}

ks_vlan_lines() { # KS_STACK_VLANS ("ID ADDRESS [PARENT]") as "PARENT ID ADDRESS NAME"; no parent: the default route's card
    local v id addr parent def
    [[ -v KS_STACK_VLANS ]] || return 0          # a bundle from before 0.9.18
    def=$(ip -o -4 route show default 2>/dev/null | awk '{print $5; exit}')
    for v in "${KS_STACK_VLANS[@]}"; do
        read -r id addr parent <<<"$v"
        parent=${parent:-$def}
        if [[ ! $id =~ ^[0-9]+$ ]] || (( id < 1 || id > 4094 )) || [[ ! $addr =~ ^[0-9.]+/[0-9]+$ ]]; then
            ks_warn "vlan $v: not ID ADDRESS/PREFIX, left out"; continue
        fi
        [[ -n $parent ]] || { ks_warn "vlan $id: no card named and no default route to find one, left out"; continue; }
        echo "$parent $id $addr $(ks_vlan_name "$parent" "$id")"
    done
}

KS_VLANS_UP='#!/bin/bash
# kiwi-server: this machine'"'"'s tagged interfaces (network.vlans in the fleet file), kept by
# kiwi-vlans.service. Each line of the file: PARENT ID ADDRESS NAME. No default route on them.
f=${1:-/etc/kiwi-server/vlans}
rc=0
while read -r parent id addr name; do
    [[ -n ${name:-} && ${parent:0:1} != "#" ]] || continue
    # at boot this runs before the network: a tagged interface comes up only on a card that is up
    ip link set dev "$parent" up || rc=1
    if ! ip link show dev "$name" >/dev/null 2>&1; then
        ip link add link "$parent" name "$name" type vlan id "$id" || { rc=1; continue; }
    fi
    ip addr replace "$addr" dev "$name" || rc=1
    ip link set dev "$name" up || rc=1
done < "$f"
exit $rc
'

ks_vlans() { # the tagged interfaces of KS_STACK_VLANS, kept across reboots by kiwi-vlans.service; none: gone
    local f=${KS_VLANS_FILE:-/etc/kiwi-server/vlans} up=${KS_VLANS_BIN:-/usr/local/sbin/kiwi-vlans} old new line name
    local unit=${KS_SYSTEMD_DIR:-/etc/systemd/system}/kiwi-vlans.service
    new=$(ks_vlan_lines)
    old=$(grep -v '^#' "$f" 2>/dev/null || true)
    # what is no more, or changed: its interface goes (the address with it), before the new ones come
    while read -r line; do
        [[ -n $line ]] || continue
        if ! grep -qxF "$line" <<<"$new"; then
            name=${line##* }
            ip link del dev "$name" 2>/dev/null || true
        fi
    done <<<"$old"
    if [[ -z $new ]]; then
        if [[ -f $f ]]; then
            ks_say "tagged interfaces (VLANs): none any more"
            systemctl disable -q kiwi-vlans.service 2>/dev/null || true
            rm -f "$f" "$up" "$unit"
        fi
        return 0
    fi
    install -d -m 0755 "$(dirname "$f")" "$(dirname "$up")"
    { echo "# kiwi-server: PARENT ID ADDRESS NAME (network.vlans in the fleet file)"; echo "$new"; } > "$f.new"
    mv "$f.new" "$f"
    printf '%s' "$KS_VLANS_UP" > "$up.new"
    chmod 0755 "$up.new"
    mv "$up.new" "$up"
    if "$up" "$f"; then
        ks_say "tagged interfaces (VLANs): $(awk '!/^#/ {printf "%s%s %s", sep, $4, $3; sep=", "}' "$f")"
    else
        ks_warn "a tagged interface did not come up (is the card there?): $(tr '\n' ' ' < "$f")"
    fi
    local after="" p
    for p in $(awk '!/^#/ {print $1}' "$f" | sort -u); do
        after+=" sys-subsystem-net-devices-$p.device"
    done
    ks_unit kiwi-vlans.service <<UNIT
[Unit]
Description=kiwi-server: this machine's tagged interfaces (VLANs)
DefaultDependencies=no
Wants=network-pre.target$after
After=network-pre.target$after
Before=network.target docker.service podman.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$up $f

[Install]
WantedBy=multi-user.target
UNIT
    systemctl enable -q kiwi-vlans.service 2>/dev/null || true
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
        # New on a machine whose stack runs (the dns module was added later): podman's
        # resolver holds port 53 until its network has no container left, so the whole
        # stack stops once and starts again; restarting only what changed left Pi-hole
        # with "address already in use" (an Odroid that became the home's ad blocker).
        if [[ ! -f /etc/containers/containers.conf.d/50-kiwi-dns.conf ]] || ss -Hlunp 2>/dev/null | grep ':53 ' | grep -q aardvark-dns; then
            KS_STACK_FROM_THE_START=1
        fi
        printf '%s\n' '# kiwi-server stack: podman'"'"'s own DNS off port 53, so the dns module may publish it' \
            '[network]' 'dns_bind_port = 5533' > /etc/containers/containers.conf.d/50-kiwi-dns.conf
    fi
    # systemd-networkd (Armbian, some Debian) removes routes and routing rules it did not
    # make whenever it sets a card up anew: the stack's route to the mesh and the gateway's
    # rule for LAN clients went with a new address or a link that came back, and stayed
    # away until the stack was restarted. They are the stack's: networkd leaves them be.
    if systemctl is-active -q systemd-networkd 2>/dev/null; then
        install -d -m 0755 /etc/systemd/networkd.conf.d
        if ! grep -qs 'ManageForeignRoutes=no' /etc/systemd/networkd.conf.d/50-kiwi-stack.conf; then
            printf '%s\n' '# kiwi-server stack: the routes and rules of the stack are not networkd'"'"'s to remove' \
                '[Network]' 'ManageForeignRoutingPolicyRules=no' 'ManageForeignRoutes=no' > /etc/systemd/networkd.conf.d/50-kiwi-stack.conf
            # read at its start only (a reload reads the cards' files, not this one). A restart
            # keeps the addresses of running cards, so an apply from elsewhere survives it
            systemctl restart systemd-networkd 2>/dev/null || true
            ks_say "systemd-networkd leaves the stack's routes and rules alone from now on"
        fi
    fi
    # Pi-hole wants port 53 on the host; systemd-resolved's stub listener (Fedora
    # CoreOS, uCore) sits on 127.0.0.53:53 and would block it. Debian's default
    # install has no systemd-resolved, so nothing happens there.
    if systemctl is-active -q systemd-resolved 2>/dev/null && { (( KS_STACK_NO_RESOLVED_STUB )) || [[ -n ${KS_STACK_DNS_IP:-} ]]; }; then
        # the stub listener on 127.0.0.53:53 would block the dns module's port 53; and the
        # host itself asks this stack's Pi-hole (a public resolver behind it while the stack is down),
        # so the fleet's names and the blocklist hold for the host, not only its containers
        if [[ -n ${KS_STACK_DNS_IP:-} ]]; then
            ks_say "systemd-resolved: the host resolves through this stack's Pi-hole at $KS_STACK_DNS_IP while the stack runs (its own resolvers before and after); stub listener off"
        else
            ks_say "turning off systemd-resolved's stub listener (port 53 goes to the dns module)"
        fi
        install -d -m 0755 /etc/systemd/resolved.conf.d
        {
            echo '[Resolve]'
            (( KS_STACK_NO_RESOLVED_STUB )) && echo 'DNSStubListener=no'
            # not the Pi-hole here: set for good, the machine had nobody to ask at boot until the
            # stack was up (kiwi-stack sets it in /run while the stack runs)
            (( KS_STACK_NO_RESOLVED_STUB )) || echo '# nothing set for good: see /run/systemd/resolved.conf.d'
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

ks_local_cache() {
    # What the cache node on this LAN keeps is asked there first (kiwi-server's cache module,
    # docs/design/local-first.md): podman's registry mirrors, which fall back to the registry
    # by themselves, and apt's proxy through a script that says DIRECT when the cache is away.
    local conf=/etc/containers/registries.conf.d/50-kiwi-mirrors.conf reg
    local sigs=/etc/containers/registries.d/50-kiwi-mirrors.yaml
    if [[ $KS_STACK_RUNTIME == podman ]] && declare -p KS_ROLE_REGISTRY_MIRRORS >/dev/null 2>&1 \
       && (( ${#KS_ROLE_REGISTRY_MIRRORS[@]} )); then
        install -d -m 0755 /etc/containers/registries.conf.d
        {
            echo "# kiwi-server: the cache on this LAN first, the registry itself when it does not answer"
            while IFS= read -r reg; do
                printf '\n[[registry]]\nprefix = "%s"\nlocation = "%s"\n\n[[registry.mirror]]\nlocation = "%s"\n' \
                    "$reg" "$reg" "${KS_ROLE_REGISTRY_MIRRORS[$reg]}"
            done < <(printf '%s\n' "${!KS_ROLE_REGISTRY_MIRRORS[@]}" | sort)
        } | ks_write "$conf" 0644
        # A signed image (the system's own, checked at every update) is checked where it is
        # fetched: without this the cache counts as a place without signatures, the check
        # fails and the registry itself is never asked.
        {
            echo "# kiwi-server: signatures are read at the LAN's cache too"
            echo "docker:"
            while IFS= read -r reg; do
                printf '  %s:\n    use-sigstore-attachments: true\n' "$reg"
            done < <(for reg in "${KS_ROLE_REGISTRY_MIRRORS[@]}"; do printf '%s\n' "${reg%%/*}"; done | sort -u)
        } | ks_write "$sigs" 0644
        if systemctl is-failed --quiet rpm-ostreed-automatic.service 2>/dev/null; then
            systemctl reset-failed rpm-ostreed-automatic.service     # it failed on the cache; the timer asks again
        fi
        ks_say "images come through the LAN's cache first (${!KS_ROLE_REGISTRY_MIRRORS[*]})"
    else
        rm -f "$conf" "$sigs"
    fi
    ks_apt_proxy
}

ks_apt_proxy() {
    # Also before the first apt of a run (ks_base): a script from an older release sent every
    # source to the cache, and the run that would have replaced it stopped at its own apt.
    local aptconf=/etc/apt/apt.conf.d/30kiwi-proxy detect=/usr/local/bin/kiwi-apt-proxy at
    if ks_is_debian && [[ ${KS_ROLE_APT_PROXY:-} == http://*:* ]]; then
        at=${KS_ROLE_APT_PROXY#http://}; at=${at%%/*}
        {
            echo '#!/bin/sh'
            echo "# kiwi-server: apt asks this for every http source, with its address. Debian's own sources go"
            echo "# through the LAN's package cache while it answers. Any other (Armbian's, a vendor's) goes direct:"
            echo "# the cache keeps Debian only and refuses the rest, which stopped every install on an Armbian board."
            # shellcheck disable=SC2016
            printf '%s\n' 'case "$1" in' '    http://deb.debian.org/*|http://security.debian.org/*) ;;' '    *) echo DIRECT; exit 0 ;;' 'esac'
            printf 'if timeout 2 bash -c "</dev/tcp/%s/%s" 2>/dev/null; then echo "%s"; else echo DIRECT; fi\n' \
                "${at%:*}" "${at##*:}" "$KS_ROLE_APT_PROXY"
        } | ks_write "$detect" 0755
        printf 'Acquire::http::Proxy-Auto-Detect "%s";\n' "$detect" | ks_write "$aptconf" 0644
        [[ ${1:-} == quiet ]] || ks_say "packages come through the LAN's cache first ($KS_ROLE_APT_PROXY)"
    else
        rm -f "$aptconf" "$detect"
    fi
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

ks_stack_drop_files() { # DIR
    # A file the last apply rendered and this one does not (its module left the stack) goes
    # as well: a backup script left behind still looked like a backup to `kiwi-server status`.
    # Data is never rendered, so nothing a container wrote is touched.
    local dd=$1 old='' old_dir='' rel entry keep
    if [[ -f $KS_STACK_ENV ]]; then
        old=$(sed -n 's/^STACK_FILES="\(.*\)"$/\1/p' "$KS_STACK_ENV")
        old_dir=$(sed -n 's/^STACK_DIR=//p' "$KS_STACK_ENV")
    fi
    [[ $old_dir == "$dd" ]] || return 0
    for rel in $old; do
        [[ $rel =~ ^[A-Za-z0-9@._/-]+$ && $rel != *..* && $rel != /* ]] || continue
        keep=0
        for entry in "${KS_STACK_FILES[@]}"; do [[ ${entry%:*} == "$rel" ]] && keep=1; done
        (( keep )) && continue
        [[ -f $dd/$rel ]] || continue
        ks_say "removing $rel — no module renders it any more"
        rm -f "${dd:?}/$rel"
    done
}

# ---- what an apply changed, so a running stack restarts only that (problem 55) ------------
KS_QUADLET_DIR=${KS_QUADLET_DIR:-/etc/containers/systemd}
declare -gA KS_BEFORE=()
KS_CHANGED=()

ks_stack_remember() {   # the files and quadlets as they are before the apply writes them
    local entry rel q
    KS_BEFORE=()
    for entry in "${KS_STACK_FILES[@]}"; do
        rel=${entry%:*}
        [[ -f $KS_STACK_DIR/$rel ]] && KS_BEFORE[$KS_STACK_DIR/$rel]=$(sha256sum < "$KS_STACK_DIR/$rel")
    done
    for q in "${KS_STACK_QUADLETS[@]}"; do
        [[ -f $KS_QUADLET_DIR/$q ]] && KS_BEFORE[$KS_QUADLET_DIR/$q]=$(sha256sum < "$KS_QUADLET_DIR/$q")
    done
    return 0
}

ks_stack_compare() {    # KS_CHANGED: every file or quadlet that is new or different now
    local entry p q
    KS_CHANGED=()
    for entry in "${KS_STACK_FILES[@]}"; do
        p=$KS_STACK_DIR/${entry%:*}
        [[ -f $p && ${KS_BEFORE[$p]:-} == "$(sha256sum < "$p")" ]] || KS_CHANGED+=("$p")
    done
    for q in "${KS_STACK_QUADLETS[@]}"; do
        p=$KS_QUADLET_DIR/$q
        [[ -f $p && ${KS_BEFORE[$p]:-} == "$(sha256sum < "$p")" ]] || KS_CHANGED+=("$p")
    done
    return 0
}

# What changed and has not restarted yet, kept across a failed apply: the files of a run that
# ended before its restarts (an image that did not pull) are the same at the next run, which
# then saw nothing to restart and left the old container serving the old settings.
KS_PENDING=${KS_PENDING:-/etc/kiwi-server/restart-pending}
ks_stack_note_pending() {
    local svc all=()
    mapfile -t all < <(ks_stack_restart_due)
    install -d -m 0755 "$(dirname "$KS_PENDING")"
    if (( ${#all[@]} )); then
        printf '%s\n' "${all[@]}" > "$KS_PENDING.tmp" && ks_replace "$KS_PENDING.tmp" "$KS_PENDING"
    fi
    return 0
}
ks_stack_restart_due() {        # what changed now, and what an earlier run did not get to
    # (no file is the usual case: under the role's ERR trap and pipefail nothing here may fail)
    local due
    due=$(ks_stack_changed_services; if [[ -f $KS_PENDING ]]; then cat -- "$KS_PENDING"; fi)
    [[ -n $due ]] && printf '%s\n' "$due" | sort -u | grep -v '^$'
    return 0
}

ks_stack_changed_services() {   # the containers whose quadlet, or a file they mount or read, changed
    local svc q line src p hit
    for svc in "${KS_STACK_SERVICES[@]}"; do
        q=$KS_QUADLET_DIR/$svc.container
        hit=0
        for p in "${KS_CHANGED[@]}"; do
            [[ $p == "$q" ]] && { hit=1; break; }
        done
        if (( ! hit )) && [[ -f $q ]]; then
            while IFS= read -r line; do
                case $line in
                    Volume=/*)          src=${line#Volume=}; src=${src%%:*} ;;
                    EnvironmentFile=/*) src=${line#EnvironmentFile=} ;;
                    *)                  continue ;;
                esac
                for p in "${KS_CHANGED[@]}"; do
                    [[ $p == "$src" || $p == "$src"/* ]] && { hit=1; break 2; }
                done
            done < "$q"
        fi
        (( hit )) && echo "$svc"
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
    local dd=$KS_STACK_DIR u=$KS_STACK_USER d entry rel mode unit old_rt='' old_dir='' old_services='' svc keep
    if [[ -f $KS_STACK_ENV ]]; then
        local old_seed
        old_seed=$(sed -n 's/^STACK_SEED_ID=//p' "$KS_STACK_ENV")
        if [[ -n $old_seed && -n ${KS_SEED_ID:-} && $old_seed != "$KS_SEED_ID" && $old_seed == "${KS_SEED_FROM:-}" ]]; then
            # the fleet replaced its seed and says so: what changed with it are secrets the
            # containers read at every start (the fleet keeps the others as they were), and
            # the containers that read them restart below
            ks_say "the fleet's seed was replaced ($old_seed -> $KS_SEED_ID): the secrets that follow it change now"
        elif [[ -n $old_seed && -n ${KS_SEED_ID:-} && $old_seed != "$KS_SEED_ID" ]]; then
            ks_die "the fleet's seed changed ($old_seed -> $KS_SEED_ID): the generated secrets differ from the ones the" \
                   "running databases were set up with. Restore secrets/seed from the fleet backup; to start over with" \
                   "new secrets on an empty stack, remove $KS_STACK_ENV and the data directories first"
        fi
        old_rt=$(sed -n 's/^STACK_RUNTIME=//p' "$KS_STACK_ENV")
        old_dir=$(sed -n 's/^STACK_DIR=//p' "$KS_STACK_ENV")
        old_services=$(sed -n 's/^STACK_SERVICES="\(.*\)"$/\1/p' "$KS_STACK_ENV")
        # and those of parts it kept ready (one started by a switch runs until it goes here)
        old_services+=" $(sed -n 's/^STACK_READY="\(.*\)"$/\1/p' "$KS_STACK_ENV")"
    fi
    if [[ -n $old_rt && ( $old_rt != "$KS_STACK_RUNTIME" || ( -n $old_dir && $old_dir != "$dd" ) ) ]] && [[ -x /usr/local/bin/kiwi-stack ]]; then
        # the installed kiwi-stack still speaks the old runtime and directory: take that stack
        # down before stack.env describes the new one, or both fight for the same ports
        ks_say "the stack moves from $old_rt in $old_dir to $KS_STACK_RUNTIME in $dd — stopping the old stack first"
        /usr/local/bin/kiwi-stack stop || ks_warn "the old stack did not stop cleanly — check with podman ps / docker ps"
        old_services=''
    fi
    # after the block above: until then a move from docker still has its stack running there
    [[ $KS_STACK_RUNTIME != podman ]] || ks_retire_docker
    ks_stack_remember
    if [[ $KS_STACK_RUNTIME == podman && -n $old_services ]]; then
        # a container whose module left the stack: its quadlet goes below, the running
        # unit would stay (a target does not stop what merely wants it)
        for svc in $old_services; do
            keep=0
            for u in "${KS_STACK_SERVICES[@]}" "${KS_STACK_READY[@]}"; do [[ $u == "$svc" ]] && keep=1; done
            (( keep )) && continue
            [[ $svc =~ ^[A-Za-z0-9_.-]+$ ]] || continue
            ks_say "stopping $svc — no module runs it any more"
            systemctl stop "$svc.service" 2>/dev/null || true
            podman rm -f "$svc" >/dev/null 2>&1 || true
            # a service that ended with an error on its way out would stay listed as failed,
            # though its unit is gone: nothing is wrong with the machine
            systemctl reset-failed "$svc.service" 2>/dev/null || true
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
                # its container may own it as another user (Tang's runs as 999): only a directory
                # an older release left to root becomes the stack user's. Changing the owner of a
                # running container's data broke it until its next start.
                [[ $(stat -c %u "$dd/$d") != 0 ]] || chown "$u:$u" "$dd/$d"
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
    ks_stack_drop_files "$dd"
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
    ks_stack_compare
    ks_stack_note_pending
    local files=() entry
    for entry in "${KS_STACK_FILES[@]}"; do files+=("${entry%:*}"); done
    install -d -m 0755 /etc/kiwi-server
    {
        echo "# written by kiwi-server $KS_VERSION — read by /usr/local/bin/kiwi-stack"
        echo "STACK_DIR=$dd"
        echo "STACK_USER=$u"
        echo "STACK_RUNTIME=$KS_STACK_RUNTIME"
        echo "STACK_SERVICES=\"${KS_STACK_SERVICES[*]}\""
        echo "STACK_READY=\"${KS_STACK_READY[*]:-}\""
        echo "STACK_PREFIX=$KS_STACK_PREFIX"
        echo "STACK_VPN_CONTAINER=$KS_STACK_VPN_CONTAINER"
        echo "STACK_VPN_DEPENDENTS=\"${KS_STACK_VPN_DEPENDENTS[*]}\""
        echo "STACK_MESH_SUBNET=$KS_STACK_MESH_SUBNET"
        echo "STACK_MESH_VIA=$KS_STACK_MESH_VIA"
        echo "STACK_DNS_IP=${KS_STACK_DNS_IP:-}"
        echo "STACK_UNITS=\"${KS_STACK_UNITS[*]}\""
        echo "STACK_FILES=\"${files[*]}\""
        echo "STACK_MODULES=\"${KS_STACK_MODULES[*]}\""
        echo "STACK_SEED_ID=${KS_SEED_ID:-}"
        echo "STACK_AUTO_UPDATE=${KS_STACK_AUTO_UPDATE:-registry}"
    } | ks_write "$KS_STACK_ENV" 0644
}

ks_stack_ready() {   # the parts kept ready for another machine: made here, not started
    # Their images were pulled with the others. A quadlet without [Install] is all podman needs;
    # docker gets the containers made (a profile no `up` starts). `kiwi-stack ready start` (a switch)
    # starts them.
    [[ -n ${KS_STACK_READY[*]:-} ]] || return 0
    # One that runs is one this machine ran as its own until now (a switch moved the part away while
    # it was gone): it stops, before the standby's fetch writes under it. Its part runs elsewhere.
    local svc
    for svc in "${KS_STACK_READY[@]}"; do
        if [[ $KS_STACK_RUNTIME == podman ]] && systemctl -q is-active "$svc.service" 2>/dev/null; then
            systemctl stop "$svc.service" && ks_say "$svc stopped: its part runs on another machine now, this one keeps it ready"
        elif [[ $KS_STACK_RUNTIME == docker ]] && [[ $(docker inspect -f '{{.State.Running}}' "$svc" 2>/dev/null) == true ]]; then
            docker stop "$svc" >/dev/null && ks_say "$svc stopped: its part runs on another machine now, this one keeps it ready"
        fi
    done
    if [[ $KS_STACK_RUNTIME == docker ]]; then
        docker compose --file "$KS_STACK_DIR/docker-compose.yml" --profile kiwi-ready up --no-start "${KS_STACK_READY[@]}" >/dev/null 2>&1 \
            || ks_warn "the parts kept ready here could not be made (${KS_STACK_READY[*]}): a switch makes them then"
    fi
    ks_say "kept ready for another machine, not started: ${KS_STACK_READY[*]}"
    return 0
}

ks_image_here() { # IMAGE
    if [[ $KS_STACK_RUNTIME == podman ]]; then
        podman image exists "$1" 2>/dev/null
    else
        docker image inspect "$1" >/dev/null 2>&1
    fi
}

ks_stack_prune() {
    # Images nothing here uses any more, once the disk under them runs full: a master in RAM
    # (crumble, 18 Nov 2026) had 690 MB of images in its 913 MB, two of them from modules it no
    # longer ran. What a container or a module of the stack uses stays; an image a script pulls
    # for itself comes back by itself when it is needed. Old copies go always (as `update` does).
    [[ $KS_STACK_RUNTIME == podman ]] || return 0
    podman image prune -f >/dev/null 2>&1 || true
    local root use keep img id gone=0
    root=$(podman info --format '{{.Store.GraphRoot}}' 2>/dev/null) || return 0
    use=$(df --output=pcent "$root" 2>/dev/null | tail -1 | tr -dc 0-9)
    (( ${use:-0} >= 75 )) || return 0
    keep=" $(podman ps -a --format '{{.ImageID}}' 2>/dev/null | tr '\n' ' ' || true) "
    for img in "${KS_STACK_IMAGES[@]}"; do
        keep+="$(podman image inspect --format '{{.Id}}' "$img" 2>/dev/null || true) "
    done
    while read -r id; do
        id=${id#sha256:}
        [[ -n $id && $keep != *" $id "* ]] || continue
        podman rmi "$id" >/dev/null 2>&1 && gone=$((gone + 1))
    done < <(podman images -q --no-trunc)
    if (( gone )); then
        ks_say "the disk under the images was ${use}% full: $gone image(s) that nothing here uses removed"
    fi
    return 0
}

ks_stack_make_room() { # WANT...   — on a nearly full disk, room for images this machine lacks
    # A changed image (nginx:1 to nginx:1-alpine) is pulled while the container still holds the
    # old one: on a master in RAM (crumble, 19 Nov 2026: 55 MB free) the pull fails. A container
    # whose image the stack no longer names is stopped and goes, with its image, before the pull;
    # the units bring it back on the new one a moment later.
    [[ $KS_STACK_RUNTIME == podman && $# -gt 0 ]] || return 0
    local root use names keep id name img gone=0
    root=$(podman info --format '{{.Store.GraphRoot}}' 2>/dev/null) || return 0
    use=$(df --output=pcent "$root" 2>/dev/null | tail -1 | tr -dc 0-9)
    (( ${use:-0} >= ${KS_ROOM_AT:-85} )) || return 0
    # compared by their first 12 hex digits: `podman ps` shows those, `image inspect` all 64
    keep=" "
    for img in "${KS_STACK_IMAGES[@]}"; do
        id=$(podman image inspect --format '{{.Id}}' "$img" 2>/dev/null || true)
        id=${id#sha256:}
        [[ -n $id ]] && keep+="${id:0:12} "
    done
    names=" ${KS_STACK_SERVICES[*]} "
    while read -r name id; do
        id=${id#sha256:}
        id=${id:0:12}
        [[ -n $name && -n $id && $names == *" $name "* && $keep != *" $id "* ]] || continue
        ks_say "the disk is ${use}% full: $name stops for a moment, its old image makes room for the new one"
        # its unit first, so that systemd does not bring it back on the old image meanwhile
        systemctl stop "$name.service" >/dev/null 2>&1 || podman stop -t 20 "$name" >/dev/null 2>&1 || true
        podman rm -f "$name" >/dev/null 2>&1 || true
        podman rmi "$id" >/dev/null 2>&1 && gone=$((gone + 1))
    done < <(podman ps -a --format '{{.Names}} {{.ImageID}}' 2>/dev/null)
    return 0
}

ks_stack_pull() {
    # Every image before the first start, one after the other with retries and a
    # visible name: a target that starts a dozen cold pulls at once on a home
    # uplink times units out and the stack looks up while nothing runs.
    # An image this machine has already is not asked for again: newer ones come with the update
    # (the weekly timer, kiwi-stack update, kiwi-server apply --update), so an apply that changes
    # a setting does not wait for every registry (the owner, 18 Nov 2026: 31 images on papaya at
    # every apply). With auto_update off the apply is how images change: then every one is asked.
    local img n ok want=()
    [[ ${#KS_STACK_IMAGES[@]} -gt 0 ]] || return 0
    for img in "${KS_STACK_IMAGES[@]}"; do
        if [[ ${KS_STACK_AUTO_UPDATE:-registry} == registry ]] && ks_image_here "$img"; then
            continue
        fi
        want+=("$img")
    done
    if [[ ${#want[@]} -eq 0 ]]; then
        ks_say "all ${#KS_STACK_IMAGES[@]} images are here (newer ones come with the update: kiwi-stack update)"
        return 0
    fi
    ks_stack_make_room "${want[@]}"
    if [[ ${KS_STACK_AUTO_UPDATE:-registry} == registry ]]; then
        ks_say "pulling ${#want[@]} of ${#KS_STACK_IMAGES[@]} images: the ones this machine does not have yet"
    else
        ks_say "pulling ${#want[@]} images (auto_update is off: the apply brings the newer ones)"
    fi
    for img in "${want[@]}"; do
        ok=0
        for n in 1 2 3; do
            if [[ $KS_STACK_RUNTIME == podman ]]; then
                podman pull -q "$img" >/dev/null && ok=1 && break
            else
                docker pull -q "$img" >/dev/null && ok=1 && break
            fi
            ks_warn "pull of $img failed (try $n of 3)"; sleep $((n * 10))
        done
        if (( ! ok )); then
            # a registry that is down must not stop an apply that has the image already
            if { [[ $KS_STACK_RUNTIME == podman ]] && podman image exists "$img"; } ||
               { [[ $KS_STACK_RUNTIME == docker ]] && docker image inspect "$img" >/dev/null 2>&1; }; then
                ks_warn "cannot pull $img: going on with the copy this machine has"
                continue
            fi
            ks_die "cannot pull $img, and this machine has no copy — is the Internet reachable from this host?"
        fi
        ks_say "  $img"
    done
}

ks_stack_install_cli() {
    # the kiwi-stack CLI and this library itself, so `kiwi-stack apply DIR` can run a
    # bundle rendered elsewhere (kiwi-modules) without a role script
    ks_file kiwi-stack /usr/local/bin/kiwi-stack 0755
    ks_file kiwi-stack-lib /usr/local/lib/kiwi-stack/lib.sh 0644
}

ks_time_sync_at_boot() {
    # WireGuard ignores a handshake older than the newest it has seen from that peer, so a
    # tunnel opened before NTP steps a fast clock back shuts the machine out until real time
    # catches up. A stack with a VPN client waits for the clock at boot, bounded so a machine
    # without NTP still comes up.
    [[ -n ${KS_STACK_VPN_CONTAINER:-} ]] || return 0
    local wait=systemd-time-wait-sync.service
    # systemd's waiter only wakes when the clock is stepped or timesyncd says so. chrony
    # trims a clock that is already close, so behind chrony it sits out its whole timeout
    # and fails, at every boot. chrony brings its own waiter.
    if systemctl is-enabled -q chronyd.service 2>/dev/null && systemctl cat chrony-wait.service >/dev/null 2>&1; then
        systemctl disable -q "$wait" 2>/dev/null || true
        systemctl reset-failed "$wait" 2>/dev/null || true
        wait=chrony-wait.service
    elif [[ ! -x /usr/lib/systemd/systemd-time-wait-sync ]]; then
        return 0
    fi
    # Bounded, and ending well either way: run into systemd's timeout the waiter counted as a
    # failed unit, which `status` then showed as a problem of a machine that had none.
    local cmd
    if [[ $wait == chrony-wait.service ]]; then
        cmd="$(command -v chronyc || echo /usr/bin/chronyc) -h 127.0.0.1,::1 waitsync 30 0.1 0.0 3"
    else
        cmd="timeout 90 /usr/lib/systemd/systemd-time-wait-sync"
    fi
    printf '%s\n' '[Service]' 'ExecStart=' \
        "ExecStart=/bin/sh -c '$cmd || echo \"the clock is not synchronized after 90 seconds: going on\"'" \
        'TimeoutStartSec=120' |
        ks_unit "$wait.d/90-kiwi.conf"
    systemctl enable -q "$wait"
    systemctl reset-failed "$wait" 2>/dev/null || true
}

ks_time_servers() { # KS_STACK_NTP: the time servers this machine asks first (its site's, else the master's)
    # The network's own Pi-hole answers time questions (the dns module); the public pool stays behind
    # it. timesyncd takes its fallbacks only when no server is named, so the pool is named after ours.
    local conf=/etc/systemd/timesyncd.conf.d/50-kiwi.conf begin="# kiwi-server: begin" end="# kiwi-server: end"
    local servers=("${KS_STACK_NTP[@]+"${KS_STACK_NTP[@]}"}") changed=""     # a bundle from before has none
    if systemctl is-active -q chronyd.service 2>/dev/null || systemctl is-enabled -q chronyd.service 2>/dev/null; then
        local f=/etc/chrony.conf lines="" old=""
        [[ -f $f ]] || return 0
        if [[ ${#servers[@]} -gt 0 ]]; then
            lines=$(printf '%s\n' "$begin (the network's time servers first, the pool below stays)"; printf 'server %s iburst prefer\n' "${servers[@]}"; printf '%s\n' "$end")
        fi
        old=$(sed -n "/^$begin/,/^$end/p" "$f")
        if [[ $old != "$lines" ]]; then
            sed -i "/^$begin/,/^$end/d" "$f"
            if [[ -n $lines ]]; then
                printf '%s\n' "$lines" | cat - "$f" > "$f.kiwi" && cat "$f.kiwi" > "$f" && rm -f "$f.kiwi"
            fi
            systemctl restart chronyd.service 2>/dev/null || true
            changed=chrony
        fi
    elif systemctl cat systemd-timesyncd.service >/dev/null 2>&1; then
        if [[ ${#servers[@]} -gt 0 ]]; then
            local pool=pool.ntp.org
            [[ -r /etc/os-release ]] && grep -qi '^ID=debian\|^ID_LIKE=.*debian' /etc/os-release && pool="0.debian.pool.ntp.org 1.debian.pool.ntp.org"
            local drop
            drop=$(printf '%s\n' "# kiwi-server: the network's time servers first, then the public pool" '[Time]' "NTP=${servers[*]} $pool")
            if [[ $(cat "$conf" 2>/dev/null) != "$drop" ]]; then
                mkdir -p "${conf%/*}"
                printf '%s\n' "$drop" > "$conf"
                changed=timesyncd
            fi
        elif [[ -f $conf ]]; then
            rm -f "$conf"
            changed=timesyncd
        fi
        [[ -n $changed ]] && systemctl restart systemd-timesyncd.service 2>/dev/null || true
    fi
    if [[ -n $changed ]]; then
        if [[ ${#servers[@]} -gt 0 ]]; then
            ks_say "time: asks ${servers[*]} first ($changed), the public pool behind it"
        else
            ks_say "time: the public pool only ($changed)"
        fi
    fi
}

ks_stack_units() { # the service that brings the stack up, and the two maintenance timers
    local vpn_restart=${KS_ROLE_DAILY_VPN_RESTART:-} weekly=${KS_ROLE_WEEKLY_UPDATE:-} rt_unit="" rt_requires=""
    if [[ $KS_STACK_RUNTIME == docker ]]; then rt_unit="docker.service"; rt_requires="Requires=docker.service"; fi
    ks_unit kiwi-stack.service <<UNIT
[Unit]
Description=Kiwi Server stack ($KS_ROLE: ${KS_STACK_MODULES[*]}, $KS_STACK_RUNTIME)
Documentation=https://github.com/derlocke-ng/kiwi-server
After=$rt_unit network-online.target time-sync.target
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
    ks_local_cache
    ks_stack_unpack
    ks_stack_install_cli
    ks_firewall_trust_mesh
    ks_firewall_lan
    ks_vlans
    ks_time_sync_at_boot
    ks_time_servers
    if [[ ${#KS_STACK_PORTS[@]} -gt 0 ]]; then
        ks_firewall_open "${KS_STACK_PORTS[@]}"
    fi
    systemctl daemon-reload
    ks_stack_pull
    ks_say "starting the stack (${KS_STACK_MODULES[*]})"
    ks_stack_units
    systemctl enable -q kiwi-stack.service
    if [[ $KS_STACK_RUNTIME == podman ]] && systemctl -q is-active kiwi-stack.service && (( ! ${KS_STACK_FROM_THE_START:-0} )); then
        # a running stack: what is new starts, what changed restarts (a bind mount keeps the old
        # inode, so a changed file needs it), and the rest goes on serving (problem 55). What
        # lives in another's network follows it by BindsTo.
        local changed=() svc
        mapfile -t changed < <(ks_stack_restart_due)
        ks_say "the stack is running: restarting what changed (${changed[*]:-nothing}), starting what is new"
        /usr/local/bin/kiwi-stack up || ks_die "the stack did not start — journalctl -u kiwi-stack"
        local failed=0
        for svc in "${changed[@]}"; do
            systemctl restart "$svc.service" || { ks_warn "$svc did not restart — kiwi-stack logs $svc"; failed=1; }
        done
        (( failed )) || rm -f "$KS_PENDING"
    else
        # the first apply, a stopped stack or a recreated network: everything from the start
        systemctl restart kiwi-stack.service || ks_die "kiwi-stack.service did not start — journalctl -u kiwi-stack"
        rm -f "$KS_PENDING"
    fi
    ks_say "checking that every container is running"
    /usr/local/bin/kiwi-stack verify || ks_die "the stack is not up — the containers above are not running (kiwi-stack logs <name>)"
    ks_stack_ready
    ks_stack_prune
    for unit in "${KS_STACK_UNITS[@]}"; do
        systemctl enable -q "$unit" 2>/dev/null || true   # a service its timer owns has no [Install]
        if [[ $unit == *.service && " ${KS_STACK_UNITS[*]} " == *" ${unit%.service}.timer "* ]]; then
            # a timer's service: run it now, but not on the apply's critical path (it may fetch from the Internet)
            systemctl restart --no-block "$unit" || ks_warn "$unit did not start — journalctl -u $unit"
        else
            systemctl restart "$unit" || ks_warn "$unit did not start — journalctl -u $unit"
        fi
    done
    # what was just written, forced to the disk: after a power cut in the next minute the
    # stack's files would otherwise come back empty, and nothing would start
    sync -f "$KS_STACK_DIR" 2>/dev/null || sync
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
    ks_apt_proxy quiet
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
    sync            # a role that says it is done is on the disk
    date -u +%FT%TZ > "$KS_MARKER"
    ks_say "role $KS_ROLE applied — log: $KS_LOG"
    # the script carries every secret of this host: it stays for `kiwi-stack` re-runs
    # (apply puts a fresh copy), readable by root alone
    chmod 0600 "$0" 2>/dev/null || true
}
