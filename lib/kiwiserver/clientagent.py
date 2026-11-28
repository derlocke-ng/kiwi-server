"""The agent on a person's computer (docs/design/clients.md): a desktop, a laptop, a pentest system
on a portable SSD. Once a minute it tells the control node how the computer is: the box it runs on
(a sum of the board's id, salted for this network), its system, its VPN, its disk, its updates.
Signed with a key of its own that derives from the seed by the client's name, sent over the
network's own HTTPS. Nothing listens on the computer. A client is the installed system, not the
box: the same SSD booted on two machines is one client on two boxes.

This is its first step: it reports. Its profile (CA, VPN, DNS mode) and its orders come next.
"""
import hashlib
import hmac
import os
import re
import time

from . import unitsh, util
from .util import KiwiError

DIR = "/etc/kiwi-client"
SCRIPT_PATH = "/usr/local/sbin/kiwi-client-agent"
UNIT = "kiwi-client-agent"
NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
FRESH = 150         # seconds a report counts as how the computer is now
SKEW = 300
LIMIT = 64 * 1024
VERSION = 5
ORDERS = ("update", "profile")     # what a client's agent takes; nothing else, never a command

FACTS = r'''
# what the control node keeps of this computer, one fact a line
# a board without an id of its own (all zeros or all F: many cheap ones, a VM without one) has no box
uuid=$(cat /sys/class/dmi/id/product_uuid 2>/dev/null | tr -d ' \n' | grep -viE '^[0-]*$|^[f-]*$')
[ -n "$uuid" ] && echo "box $(printf '%%s\n%%s' "$(cat "$d/salt")" "$uuid" | sha256sum | cut -c1-16)"
if [ -r /etc/os-release ]; then . /etc/os-release; echo "os ${PRETTY_NAME:-$ID}"; echo "os_id ${ID:-} ${VERSION_ID:-}"; fi
echo "kernel $(uname -r)"
echo "uptime $(cut -d. -f1 /proc/uptime)"
echo "hostname $(cat /proc/sys/kernel/hostname)"
for i in $(ip -o link show type wireguard 2>/dev/null | awk -F': ' '{print $2}' | cut -d@ -f1); do
    echo "vpn $i $(ip -o -4 addr show dev "$i" 2>/dev/null | awk '{print $4}' | head -n 1)"
done
r=$(ip -o route get 1.1.1.1 2>/dev/null | grep -o 'dev [^ ]*' | cut -d' ' -f2); [ -n "$r" ] && echo "route $r"
echo "crypt $(lsblk -rno TYPE 2>/dev/null | grep -c '^crypt$')"
if command -v clevis >/dev/null 2>&1; then
    n=0; for dev in $(lsblk -rpno NAME,FSTYPE 2>/dev/null | awk '$2 == "crypto_LUKS" {print $1}'); do
        clevis luks list -d "$dev" 2>/dev/null | grep -q . && n=$((n + 1))
    done; echo "clevis $n"
fi
if command -v rpm-ostree >/dev/null 2>&1; then
    rpm-ostree status --json 2>/dev/null | tr -d ' \n' | grep -q '"staged":true' && echo "updates staged" || echo "updates none-staged"
elif command -v apt-get >/dev/null 2>&1; then
    echo "updates $(apt-get -s -o Debug::NoLocking=1 upgrade 2>/dev/null | grep -c '^Inst ')"
fi
[ -s /var/lib/kiwi-client/profile.sum ] && echo "profile $(cut -c1-12 /var/lib/kiwi-client/profile.sum)"
if [ -s /var/lib/kiwi-client/unlock.json ]; then [ -s /var/lib/kiwi-client/unlock.slot ] && echo "unlock bound" || echo "unlock waits"; fi
echo "agent %(version)s"
'''

SCRIPT = r'''#!/bin/bash
# kiwi-server: this computer's agent (docs/design/clients.md). Every minute it tells the control
# node how this computer is. Signed with a key of its own, sent over the network's own HTTPS. Away
# from the network it says nothing and tries again a minute later. The control node's answer says
# which profile is this computer's (the network's CA, its VPN, how names are asked) and may carry
# orders, both signed with the same key: update the system now, apply the profile now. Nothing
# else: never a command.
d=%(dir)s
VERSION=%(version)s
. "$d/conf" || exit 0
st=/var/lib/kiwi-client; mkdir -p -m 0700 "$st" 2>/dev/null
luks() { lsblk -rpno NAME,FSTYPE 2>/dev/null | awk '$2 == "crypto_LUKS" {print $1; exit}'; }
slots() { clevis luks list -d "$1" 2>/dev/null | cut -d: -f1 | sort; }
if [ "${1:-}" = bind ]; then
    # once, by the person at the computer: its disk bound to the network's rings (its profile's
    # policy), beside its passphrase, which keeps working. clevis asks for that passphrase.
    [ "$(id -u)" = 0 ] || { echo "as root: sudo $0 bind" >&2; exit 2; }
    [ -s "$st/unlock.json" ] || { echo "this computer's profile has no disk policy: the network's settings give it one (unlock: rings: under its name in clients:); then wait a minute and run this again" >&2; exit 2; }
    dev=$(luks); [ -n "$dev" ] || { echo "no encrypted disk here: nothing to bind" >&2; exit 2; }
    if command -v rpm-ostree >/dev/null 2>&1; then
        echo "clevis into the system image and its boot system, and the network at boot (rpm-ostree)"
        rpm-ostree install -y --idempotent clevis clevis-luks clevis-dracut >/dev/null || exit 1
        rpm-ostree initramfs --enable --arg=-a --arg=clevis --arg=-a --arg=clevis-pin-tang --arg=-a --arg=clevis-pin-sss >/dev/null || exit 1
        rpm-ostree kargs --append-if-missing=rd.neednet=1 --append-if-missing=ip=dhcp >/dev/null || exit 1
        command -v clevis >/dev/null 2>&1 || { echo "that counts from the next start: restart the computer, then run this again"; exit 0; }
    elif command -v apt-get >/dev/null 2>&1; then
        echo "clevis and its boot-system part, and the network at boot (apt, grub)"
        DEBIAN_FRONTEND=noninteractive apt-get install -y -q clevis clevis-luks clevis-initramfs >/dev/null || exit 1
        # ip=::::::dhcp, not ip=dhcp: clevis-initramfs takes field 6 of ip= as the device to wait for
        mkdir -p /etc/default/grub.d && printf 'GRUB_CMDLINE_LINUX="$GRUB_CMDLINE_LINUX ip=::::::dhcp"\n' > /etc/default/grub.d/kiwi-clevis.cfg
        update-grub >/dev/null 2>&1 || echo "update-grub did not run: the boot system may find no network" >&2
    else
        command -v clevis >/dev/null 2>&1 || { echo "no rpm-ostree, no apt-get here: install clevis with its boot-system part, then run this again" >&2; exit 2; }
    fi
    before=$(slots "$dev")
    if [ "${2:-}" = --stdin ]; then      # the app: the passphrase on stdin, from its own field
        clevis luks bind -y -k - -d "$dev" sss "$(cat "$st/unlock.json")" || exit 1
    else
        echo "the disk's passphrase now ($dev):"
        clevis luks bind -y -d "$dev" sss "$(cat "$st/unlock.json")" || exit 1
    fi
    slots "$dev" | comm -13 <(printf '%%s\n' "$before") - | head -n 1 > "$st/unlock.slot"
    sha256sum < "$st/unlock.json" | cut -d' ' -f1 > "$st/unlock.sum"
    command -v update-initramfs >/dev/null 2>&1 && update-initramfs -u -k all >/dev/null 2>&1
    echo "bound: from the next start the disk opens by the network's rings, and still by its passphrase"
    exit 0
fi
if [ "${1:-}" = update ]; then          # the system's update now, as the order does (the app's button)
    [ "$(id -u)" = 0 ] || { echo "as root: sudo $0 update" >&2; exit 2; }
    if command -v rpm-ostree >/dev/null 2>&1; then exec systemd-run --quiet --collect --unit=kiwi-update-now --wait rpm-ostree upgrade
    elif command -v apt-get >/dev/null 2>&1; then exec systemd-run --quiet --collect --unit=kiwi-update-now --wait \
        --setenv=DEBIAN_FRONTEND=noninteractive sh -c 'apt-get update -q && apt-get upgrade -y -q'
    fi
    echo "no rpm-ostree, no apt-get here" >&2; exit 2
fi
if [ "${1:-}" = now ]; then             # report now, not with the next minute (the app's Refresh)
    exec systemctl start kiwi-client-agent.service
fi
if [ "${1:-}" = leave ]; then
    # this computer leaves the network: everything the agent put on it goes, the agent last
    [ "$(id -u)" = 0 ] || { echo "as root: sudo $0 leave" >&2; exit 2; }
    systemctl disable --now -q kiwi-client-agent.timer 2>/dev/null
    dev=$(luks); slot=$(cat "$st/unlock.slot" 2>/dev/null)
    if [ -n "$slot" ] && [ -n "$dev" ]; then clevis luks unbind -d "$dev" -s "$slot" -f >/dev/null 2>&1 && echo "the disk no longer opens by the network's rings (its passphrase does, as before)"; fi
    con=$(cat "$st/vpn.name" 2>/dev/null)     # the one it made, not one of the same name made by hand
    if [ -n "$con" ] && [ -f "$st/vpn.sum" ] && nmcli connection delete "$con" >/dev/null 2>&1; then echo "its VPN connection $con is gone"; fi
    for f in /etc/pki/ca-trust/source/anchors/kiwi-*.pem /usr/local/share/ca-certificates/kiwi-*.crt; do
        [ -f "$f" ] || continue
        rm -f "$f"; echo "the network's CA is no longer trusted ($f)"
        update-ca-trust >/dev/null 2>&1 || update-ca-certificates >/dev/null 2>&1
    done
    rm -f /etc/default/grub.d/kiwi-clevis.cfg
    rm -rf "$d" "$st" /etc/systemd/system/kiwi-client-agent.service /etc/systemd/system/kiwi-client-agent.timer
    systemctl daemon-reload
    echo "this computer has left the network; the network's own list loses it with kiwi-server client remove, or Remove on the page"
    rm -f "$0"
    exit 0
fi
exec 9>/run/kiwi-client-agent.lock; flock -n 9 || exit 0
body=$(mktemp) || exit 0; resp=$(mktemp) || exit 0; err=$(mktemp) || exit 0; trap 'rm -f "$body" "$resp" "$err"' EXIT
hmac() { openssl dgst -sha256 -hmac "$(cat "$d/key")" -r 2>/dev/null | cut -d' ' -f1; }
say() { logger -t kiwi-client-agent "$*"; }
unlocked() {    # POLICY or nothing: a disk a person bound (bind) kept bound to it; unbound when there is none
    local cfg=$1 dev slot key new sum before
    dev=$(luks); slot=$(cat "$st/unlock.slot" 2>/dev/null)
    if [ -z "$cfg" ]; then
        if [ -n "$slot" ] && [ -n "$dev" ] && clevis luks unbind -d "$dev" -s "$slot" -f >/dev/null 2>&1; then
            say "its disk no longer opens by the network's rings: the profile has none"
        fi
        rm -f "$st/unlock.json" "$st/unlock.slot" "$st/unlock.sum"; return 0
    fi
    printf '%%s' "$cfg" > "$st/unlock.json"
    sum=$(sha256sum < "$st/unlock.json" | cut -d' ' -f1)
    [ -n "$dev" ] || return 0
    if [ -z "$slot" ]; then
        [ -f "$st/unlock.told" ] || { say "its disk waits to be bound to the network's rings: sudo kiwi-client-agent bind, once, at the computer (it asks for the disk's passphrase)"; : > "$st/unlock.told"; }
        return 0
    fi
    [ "$sum" = "$(cat "$st/unlock.sum" 2>/dev/null)" ] && return 0
    # the policy changed (another ring, a new key): bound anew from the binding it has, no passphrase;
    # its rings must answer now, else it is tried again with the next report
    key=$(clevis luks pass -d "$dev" -s "$slot" 2>/dev/null) || { say "its disk's new policy waits: its rings do not answer now"; return 0; }
    before=$(slots "$dev")
    if printf '%%s' "$key" | clevis luks bind -y -k - -d "$dev" sss "$cfg" >/dev/null 2>"$err"; then
        new=$(slots "$dev" | comm -13 <(printf '%%s\n' "$before") - | head -n 1)
        clevis luks unbind -d "$dev" -s "$slot" -f >/dev/null 2>&1
        echo "$new" > "$st/unlock.slot"; echo "$sum" > "$st/unlock.sum"
        say "its disk opens by the network's rings as they are now (slot $new)"
    else
        say "its disk could not be bound anew: $(tr '\n' ' ' < "$err" | cut -c1-160)"
    fi
    key=
}
toml_list() { printf '%%s' "$1" | tr ',' '\n' | grep -v '^$' | sed 's/.*/"&"/' | paste -sd, - | sed 's/,/, /g; s/^/[/; s/$/]/'; }
managed() {     # PROFILE: a managed computer: its people log in with the network's accounts (Kanidm's
    # kanidm-unixd, kept for when it is away), and the network's admins get sudo here
    local p=$1 idm login admins conf unixd sudo_rules want prof
    idm=$(sed -n 's/^accounts //p' "$p"); [ -n "$idm" ] || return 0
    login=$(sed -n 's/^login_groups //p' "$p"); admins=$(sed -n 's/^admin_groups //p' "$p")
    if ! command -v kanidm_unixd >/dev/null 2>&1 && [ ! -x /usr/sbin/kanidm_unixd ]; then
        if command -v rpm-ostree >/dev/null 2>&1; then
            if ! rpm-ostree status 2>/dev/null | grep -q kanidm-unixd-clients; then
                rpm-ostree install -y --idempotent kanidm-unixd-clients >/dev/null 2>"$err" \
                    || { say "kanidm-unixd could not be layered: $(tail -n 1 "$err" | cut -c1-160)"; return 1; }
            fi
            # it comes with the next start: now, while nobody is logged in; else at the next restart
            if loginctl list-sessions --no-legend 2>/dev/null | awk '{print $3}' | grep -qv '^gdm\|^$'; then
                say "kanidm-unixd is layered: logins with the network's accounts from the next start"
            else
                say "kanidm-unixd is layered: restarting now, nobody is logged in"; systemctl reboot
            fi
            return 0
        elif command -v dnf >/dev/null 2>&1; then
            dnf install -y -q kanidm-unixd-clients >/dev/null 2>"$err" || { say "kanidm-unixd did not install: $(tail -n 1 "$err" | cut -c1-160)"; return 1; }
        else
            say "logins with the network's accounts are for Fedora-based computers (kanidm-unixd): not on this one"; return 0
        fi
    fi
    mkdir -p /etc/kanidm
    [ -f /etc/kanidm/ca.pem ] && cmp -s "$d/ca.pem" /etc/kanidm/ca.pem || install -m 0644 "$d/ca.pem" /etc/kanidm/ca.pem
    conf=$(printf 'uri = "%%s"\nca_path = "/etc/kanidm/ca.pem"' "$idm")
    unixd=$(printf "version = '2'\ndefault_shell = \"/bin/bash\"\nhome_alias = \"name\"\nuid_attr_map = \"name\"\ngid_attr_map = \"name\"\n\n[kanidm]\npam_allowed_login_groups = %%s" "$(toml_list "${login:-computers}")")
    changed=
    [ "$(cat /etc/kanidm/config 2>/dev/null)" = "$conf" ] || { printf '%%s\n' "$conf" > /etc/kanidm/config; changed=1; }
    [ "$(cat /etc/kanidm/unixd 2>/dev/null)" = "$unixd" ] || { printf '%%s\n' "$unixd" > /etc/kanidm/unixd; changed=1; }
    # PAM and nsswitch through Kanidm: authselect's own way, a profile of the sssd one (Kanidm's book)
    if [ "$(authselect current --raw 2>/dev/null | cut -d' ' -f1)" != custom/kanidm ]; then
        prof=/etc/authselect/custom/kanidm
        [ -d "$prof" ] || authselect create-profile kanidm -b sssd >/dev/null 2>&1 || { say "authselect could not make its profile"; return 1; }
        for f in system-auth password-auth; do
            sed -i -e 's/^auth\([[:space:]]\+\)sufficient\([[:space:]]\+\)pam_sss\.so.*/auth        sufficient                                   pam_kanidm.so ignore_unknown_user/' \
                   -e 's/^account\([[:space:]]\+\).*pam_sss\.so.*/account     sufficient                                   pam_kanidm.so ignore_unknown_user/' \
                   -e '/^password.*pam_sss\.so/d' -e '/pam_sss_gss\.so/d' \
                   -e 's/^session\([[:space:]]\+\)optional\([[:space:]]\+\)pam_sss\.so.*/session     optional                                     pam_kanidm.so/' "$prof/$f"
        done
        sed -i -e '/^\(passwd\|group\):/ s/\bsss\b/kanidm/' -e '/^\(passwd\|group\):/! s/ sss\b//' "$prof/nsswitch.conf"
        authselect select custom/kanidm --force >/dev/null 2>"$err" || { say "authselect: $(tail -n 1 "$err" | cut -c1-160)"; return 1; }
        changed=1
    fi
    systemctl enable -q --now kanidm-unixd.service kanidm-unixd-tasks.service 2>/dev/null
    [ -n "$changed" ] && systemctl restart kanidm-unixd.service kanidm-unixd-tasks.service 2>/dev/null
    # sudo for the network's admins, here: their groups as Kanidm names them
    sudo_rules=$(printf '%%s' "${admins:-admins}" | tr ',' '\n' | grep -v '^$' | sed 's/.*/%%& ALL=(ALL) ALL/')
    want="# kiwi-server: the network's admins on this computer (its profile)
$sudo_rules"
    if [ "$(cat /etc/sudoers.d/kiwi-network 2>/dev/null)" != "$want" ]; then
        printf '%%s\n' "$want" > /etc/sudoers.d/kiwi-network.new && chmod 0440 /etc/sudoers.d/kiwi-network.new
        if visudo -cf /etc/sudoers.d/kiwi-network.new >/dev/null 2>&1; then mv -f /etc/sudoers.d/kiwi-network.new /etc/sudoers.d/kiwi-network
        else rm -f /etc/sudoers.d/kiwi-network.new; say "the sudo rule for the network's admins did not pass visudo: left out"; fi
    fi
    [ -n "$changed" ] && say "logins with the network's accounts ($idm; groups: ${login:-computers}; sudo: ${admins:-admins})"
    return 0
}
apply() {       # PROFILE: the network's CA into the system's trust, the VPN as a NetworkManager connection
    local p=$1 ca f upd vpn con mode search tmp
    ca=$(sed -n 's/^ca //p' "$p" | base64 -d 2>/dev/null)
    if [ -n "$ca" ]; then
        if [ -d /etc/pki/ca-trust/source/anchors ]; then f=/etc/pki/ca-trust/source/anchors/kiwi-$NETWORK.pem; upd=update-ca-trust
        elif [ -d /usr/local/share/ca-certificates ]; then f=/usr/local/share/ca-certificates/kiwi-$NETWORK.crt; upd=update-ca-certificates
        fi
        if [ -n "${f:-}" ] && [ "$(cat "$f" 2>/dev/null)" != "$ca" ]; then
            printf '%%s\n' "$ca" > "$f" && chmod 0644 "$f" && $upd >/dev/null 2>&1 && say "the network's CA is trusted by the system now ($f)"
        fi
    fi
    unlocked "$(sed -n 's/^unlock //p' "$p" | base64 -d 2>/dev/null)"
    [ "$(sed -n 's/^kind //p' "$p")" = managed ] && managed "$p"
    con=$(sed -n 's/^connection //p' "$p"); vpn=$(sed -n 's/^vpn //p' "$p")
    if [ -z "$vpn" ]; then      # none (any more): a connection this agent made goes
        if [ -f "$st/vpn.sum" ] && [ -n "$con" ]; then nmcli connection delete "$con" >/dev/null 2>&1; rm -f "$st/vpn.sum"; say "its VPN connection $con is gone: the profile has none"; fi
        return 0
    fi
    command -v nmcli >/dev/null 2>&1 || { say "no NetworkManager here: the profile's VPN is not set up"; return 1; }
    mode=$(sed -n 's/^vpn_mode //p' "$p"); search=$(sed -n 's/^search //p' "$p")
    if [ "$(printf '%%s' "$vpn" | sha256sum | cut -d' ' -f1)" != "$(cat "$st/vpn.sum" 2>/dev/null)" ] || ! nmcli -t -f NAME connection show | grep -qx "$con"; then
        tmp=$(mktemp -d /run/kiwi-client.XXXXXX) || return 1       # nmcli names the connection after the file; in memory only
        printf '%%s' "$vpn" | base64 -d > "$tmp/$con.conf" && chmod 0600 "$tmp/$con.conf"
        nmcli connection delete "$con" >/dev/null 2>&1
        if ! nmcli connection import type wireguard file "$tmp/$con.conf" >/dev/null 2>"$err"; then
            say "its VPN connection could not be made: $(tr '\n' ' ' < "$err" | cut -c1-160)"; rm -rf "$tmp"; return 1
        fi
        rm -rf "$tmp"
        printf '%%s' "$vpn" | sha256sum | cut -d' ' -f1 > "$st/vpn.sum"; echo "$con" > "$st/vpn.name"
        say "its VPN connection $con is made"
    fi
    if [ "$mode" = full ]; then     # everything through the VPN, every name asked there
        nmcli connection modify "$con" ipv4.never-default no ipv4.dns-search "~." ipv6.method disabled connection.autoconnect yes
    else                            # only the network: its names to its DNS, the Internet as it was
        nmcli connection modify "$con" ipv4.never-default yes ipv4.dns-search "$search" ipv6.method disabled connection.autoconnect yes
    fi
    nmcli connection up "$con" >/dev/null 2>&1 || say "its VPN connection $con did not come up now: NetworkManager tries again"
    return 0
}
renew() {       # VERSION: a newer agent from the control node, signed with this computer's key, installs itself
    local p h when sig code got sum
    p=$(mktemp); h=$(mktemp); when=$(date +%%s)
    sig=$(printf 'client-agent\n%%s\n%%s' "$NAME" "$when" | hmac)
    code=$(curl -sS --max-time 30 --cacert "$d/ca.pem" -o "$p" -D "$h" -w '%%{http_code}' -H "X-Kiwi-Client: $NAME" \
        -H "X-Kiwi-Time: $when" -H "X-Kiwi-Sign: $sig" "$URL/api/client/agent" 2>/dev/null)
    got=$(sed -n 's/^[Xx]-[Kk]iwi-[Ss]ign: *//p' "$h" | tr -d '\r\n'); sum=$(sha256sum < "$p" | cut -d' ' -f1)
    if [ "$code" != 200 ]; then say "its new agent ($1): $code"
    elif [ -z "$got" ] || [ "$got" != "$(printf 'agent\n%%s\n%%s' "$NAME" "$sum" | hmac)" ]; then say "its new agent is not signed by the control node: left"
    elif sh "$p" < "$d/key" > "$err" 2>&1; then say "its agent is version $1 now (it was $VERSION)"
    else say "its new agent did not install: $(tail -n 2 "$err" | tr '\n' ' ' | cut -c1-160)"; fi
    rm -f "$p" "$h"
}
profile() {     # the profile from the control node, signed with this computer's key; applied when it is new
    local p h when sig code got sum
    p=$(mktemp); h=$(mktemp); when=$(date +%%s)
    sig=$(printf 'client-profile\n%%s\n%%s' "$NAME" "$when" | hmac)
    code=$(curl -sS --max-time 20 --cacert "$d/ca.pem" -o "$p" -D "$h" -w '%%{http_code}' -H "X-Kiwi-Client: $NAME" \
        -H "X-Kiwi-Time: $when" -H "X-Kiwi-Sign: $sig" "$URL/api/client/profile" 2>/dev/null)
    got=$(sed -n 's/^[Xx]-[Kk]iwi-[Ss]ign: *//p' "$h" | tr -d '\r\n'); sum=$(sha256sum < "$p" | cut -d' ' -f1)
    if [ "$code" != 200 ]; then say "its profile: $code"
    elif [ -z "$got" ] || [ "$got" != "$(printf 'profile\n%%s\n%%s' "$NAME" "$sum" | hmac)" ]; then say "its profile is not signed by the control node: left"
    else
        NETWORK=$(sed -n 's/^network //p' "$p" | tr -cd 'a-z0-9-'); NETWORK=${NETWORK:-kiwi}
        if apply "$p"; then echo "$sum" > "$st/profile.sum"; say "its profile is applied (${sum:0:12})"; fi
        av=$(sed -n 's/^agent_version //p' "$p" | tr -cd '0-9')
        if [ -n "$av" ] && [ "$av" -gt "$VERSION" ]; then renew "$av"; fi
    fi
    rm -f "$p" "$h"
}
orders() {      # the control node's answer: lines "order ID WHAT - AT SIGNATURE"
    local kind id what target at sign said rc now
    while read -r kind id what target at sign; do
        [ "$kind" = order ] || continue
        case $id in ""|*[!A-Za-z0-9-]*) continue ;; esac
        case $at in ""|*[!0-9]*) continue ;; esac
        grep -qx "$id" "$st/done" 2>/dev/null && continue             # done once, never again
        if [ "$(printf 'client-order\n%%s\n%%s\n%%s\n%%s\n%%s' "$NAME" "$id" "$what" "$target" "$at" | hmac)" != "$sign" ]; then
            say "order $id is not signed by the control node: left"; continue
        fi
        echo "$id" >> "$st/done"
        now=$(date +%%s)
        if [ $((now - at)) -gt 900 ] || [ $((at - now)) -gt 300 ]; then echo "kiwi-done $id 2 too old" >> "$st/told"; continue; fi
        case $what in
            update)
                if command -v rpm-ostree >/dev/null 2>&1; then
                    said=$(systemd-run --quiet --collect --unit="kiwi-order-$id" rpm-ostree upgrade 2>&1); rc=$?
                elif command -v apt-get >/dev/null 2>&1; then
                    said=$(systemd-run --quiet --collect --unit="kiwi-order-$id" --setenv=DEBIAN_FRONTEND=noninteractive \
                        sh -c 'apt-get update -q && apt-get upgrade -y -q' 2>&1); rc=$?
                else said="no rpm-ostree, no apt-get here"; rc=2; fi
                [ $rc = 0 ] && said="started (journalctl -u kiwi-order-$id); a new system image counts from the next start" ;;
            profile)
                rm -f "$st/profile.sum"; profile; rc=0; said="applied again" ;;
            *) echo "kiwi-done $id 2 not an order this agent takes: $what" >> "$st/told"; continue ;;
        esac
        say "order $id from the control node: $what: exit $rc"
        echo "kiwi-done $id $rc $(printf '%%s' "$said" | tr '\n' ' ' | cut -c1-200)" >> "$st/told"
    done < "$1"
    if [ -f "$st/done" ]; then tail -n 200 "$st/done" > "$st/done.new" && mv -f "$st/done.new" "$st/done"; fi
}
d="$d" timeout 45 bash "$d/facts.sh" > "$body" 2>/dev/null
[ -s "$st/told" ] && cat "$st/told" >> "$body"          # what its orders came to, since the last report taken
now=$(date +%%s)
sum=$(sha256sum "$body" | cut -d' ' -f1)
sign=$(printf 'client\n%%s\n%%s\n%%s' "$NAME" "$now" "$sum" | hmac)
if [ -z "$sign" ]; then out="no signature (is openssl there?)"; else
out=$(curl -sS --max-time 20 --cacert "$d/ca.pem" -o "$resp" -w '%%{http_code}' -X POST \
    -H "Content-Type: text/plain" -H "X-Kiwi-Client: $NAME" -H "X-Kiwi-Time: $now" -H "X-Kiwi-Sign: $sign" \
    -H "X-Kiwi-Orders: 1" --data-binary @"$body" "$URL/api/client/report" 2>"$err")
# refused: the control node's reason with it (a clock that is off, a key that is not this client's);
# not reached: curl's own (no name, no route, a certificate it does not trust)
case $out in 200) ;; 000|"") out="000 $(tr '\n' ' ' < "$err" | cut -c1-160)" ;; *) out="$out $(head -c 160 "$resp" | tr -d '\n')" ;; esac
fi
[ "$out" = "$(cat /run/kiwi-client-agent.last 2>/dev/null)" ] || { say "report to $URL: $out"; echo "$out" > /run/kiwi-client-agent.last; }
# how it is, for the app on this computer (kiwi-desktop) and anyone who looks: no secret in it
mkdir -p -m 0755 /run/kiwi-client 2>/dev/null
ok=$(sed -n 's/^ok //p' /run/kiwi-client/status 2>/dev/null); [ "$out" = 200 ] && ok=$now
{ echo "name $NAME"; echo "control $URL"; echo "asked $now"; echo "answer $out"; [ -n "$ok" ] && echo "ok $ok"
  grep -v '^kiwi-done ' "$body"; } > /run/kiwi-client/status.new 2>/dev/null && chmod 0644 /run/kiwi-client/status.new \
  && mv -f /run/kiwi-client/status.new /run/kiwi-client/status
if [ "$out" = 200 ]; then
    : > "$st/told"
    want=$(sed -n 's/^profile //p' "$resp" | head -n 1)
    if [ -n "$want" ] && [ "$want" != "$(cat "$st/profile.sum" 2>/dev/null)" ]; then profile
    elif [ -s "$st/unlock.slot" ] && [ -s "$st/unlock.json" ] && [ "$(sha256sum < "$st/unlock.json" | cut -d' ' -f1)" != "$(cat "$st/unlock.sum" 2>/dev/null)" ]; then
        unlocked "$(cat "$st/unlock.json")"      # a new policy that waited for its rings
    fi
    orders "$resp"
    # an order ran: what came of it goes at once, not with the next minute's report (once)
    if [ -s "$st/told" ] && [ -z "${KIWI_AGENT_AGAIN:-}" ]; then
        case $0 in */kiwi-client-agent) exec 9>&-; KIWI_AGENT_AGAIN=1 exec "$0" ;; esac
    fi
fi
'''
SERVICE = ("[Unit]\nDescription=kiwi-server: tell the network's control node how this computer is\n"
           "After=network-online.target\n\n[Service]\nType=oneshot\nExecStart=%s\n" % SCRIPT_PATH)
TIMER = ("[Unit]\nDescription=kiwi-server: report to the control node every minute\n\n[Timer]\nOnBootSec=30s\n"
         "OnUnitActiveSec=1min\nAccuracySec=5s\n\n[Install]\nWantedBy=timers.target\n")
# the key comes on stdin: it is never part of a command line, nor of a file on the computer that installs it
INSTALL = "set -eu\n" + unitsh.PRELUDE + r'''
for t in curl openssl sha256sum flock ip lsblk base64; do command -v "$t" >/dev/null || { echo "this computer has no $t" >&2; exit 3; }; done
install -d -m 0700 %(dir)s
key=$(cat)
umask 077
printf '%%s' "$key" > %(dir)s/key.new && mv %(dir)s/key.new %(dir)s/key
cat > %(dir)s/conf <<'KIWI_EOF'
URL=%(url)s
NAME=%(name)s
KIWI_EOF
cat > %(dir)s/salt <<'KIWI_EOF'
%(salt)s
KIWI_EOF
cat > %(dir)s/ca.pem <<'KIWI_EOF'
%(ca)sKIWI_EOF
cat > %(dir)s/facts.sh <<'KIWI_FACTS_EOF'
%(facts)s
KIWI_FACTS_EOF
umask 022
mkdir -p "$(dirname %(path)s)"
cat > %(path)s.new <<'KIWI_EOF'
%(script)sKIWI_EOF
chmod 0755 %(path)s.new && mv %(path)s.new %(path)s
kiwi_unit %(unit)s.service <<'KIWI_EOF'
%(service)sKIWI_EOF
kiwi_unit %(unit)s.timer <<'KIWI_EOF'
%(timer)sKIWI_EOF
kiwi_reload
kiwi_enable %(unit)s.timer
systemctl restart %(unit)s.timer
systemctl start --no-block %(unit)s.service
'''
# its VPN connection and the CA it put in the system's trust stay: the person decides about those
REMOVE = ("systemctl disable --now -q %s.timer 2>/dev/null || true; rm -rf %s %s /var/lib/kiwi-client /etc/systemd/system/%s.service "
          "/etc/systemd/system/%s.timer; systemctl daemon-reload" % (UNIT, DIR, SCRIPT_PATH, UNIT, UNIT))


def names(fleet):
    """The fleet's clients: {name: its block} from `clients:` at the top of the fleet file."""
    raw = (getattr(fleet, "raw", None) or {}).get("clients") or {}
    if not isinstance(raw, dict):
        raise KiwiError("clients: must be a mapping of a name to its settings")
    out = {}
    for n, block in raw.items():
        if not NAME.match(str(n)):
            raise KiwiError("clients: %r is not a plain name (lowercase letters, digits and dashes)" % n)
        if str(n) in fleet.hosts:
            raise KiwiError("clients: %s is a machine of the fleet already" % n)
        out[str(n)] = block if isinstance(block, dict) else {}
    return out


def key(fleet, name):
    """The client's own key for its reports: from the seed, by its name."""
    return fleet.derive_secret("client", name, "key")


def salt(fleet):
    """What the board's id is summed with: one for the network, so the sum means nothing outside it."""
    return fleet.derive_secret("box", "salt", length=16)


def signature(key_text, name, when, body):
    """'client' first: a client's report can never pass for a machine's, nor the other way round."""
    text = "client\n%s\n%s\n%s" % (name, when, hashlib.sha256(body).hexdigest())
    return hmac.new(key_text.encode(), text.encode(), hashlib.sha256).hexdigest()


def check(key_text, name, when, sign, body, now=None):
    """None when the report is this client's and of now; else why not."""
    try:
        t = int(when)
    except (TypeError, ValueError):
        return "no time"
    if abs((now if now is not None else time.time()) - t) > SKEW:
        return "its clock and this one's are more than %d minutes apart" % (SKEW // 60)
    if len(body) > LIMIT:
        return "too big"
    if not hmac.compare_digest(signature(key_text, name, when, body), str(sign or "").strip().lower()):
        return "the signature is not this client's"
    return None


def install_script(fleet, name, url, ca):
    """The script that puts the agent on the computer, run there as root; the key goes on its stdin."""
    if not url:
        raise KiwiError("the network has no control node: a client reports to one")
    if not ca.strip():
        raise KiwiError("the network's CA is not there (secrets/ca/kiwiCA.pem): render or apply once first")
    facts = FACTS % {"version": VERSION}
    script = SCRIPT % {"dir": DIR, "version": VERSION}
    return INSTALL % {"dir": DIR, "url": url, "name": name, "salt": salt(fleet), "ca": ca if ca.endswith("\n") else ca + "\n",
                      "facts": facts, "path": SCRIPT_PATH, "script": script, "unit": UNIT, "service": SERVICE, "timer": TIMER}


def parse(text):
    """A report's facts: {box, os, os_id, kernel, uptime, hostname, vpn: [(link, address)], route,
    crypt, clevis, updates, agent}."""
    out = {"vpn": []}
    for line in (text or "").splitlines():
        k, _sp, v = line.partition(" ")
        v = v.strip()
        if k == "vpn":
            link, _sp, addr = v.partition(" ")
            out["vpn"].append({"link": link[:32], "address": addr[:64]})
        elif k in ("uptime", "crypt", "clevis") and v.isdigit():
            out[k] = int(v)
        elif k in ("box", "os", "os_id", "kernel", "hostname", "route", "updates", "agent", "profile", "unlock"):
            out[k] = v[:200]
    return out


def report_path(folder, name):
    return os.path.join(folder, "clients", name + ".txt")


def keep(folder, name, body):
    """The report, kept as the client's state: the last one only."""
    path = report_path(folder, name)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    util.write_bytes(path, body, 0o600)


def rows(folder, clients, boxes=None, now=None):
    """[{name, state, seen, facts, box_of}] for the page and `kiwi-server clients`: its last report,
    whether it is of now, and the machine of the fleet whose box it runs on (`boxes`: {sum: name})."""
    now = now if now is not None else time.time()
    out = []
    for name in sorted(clients):
        path = report_path(folder, name)
        try:
            came = os.path.getmtime(path)
            facts = parse(util.read_text(path))
        except OSError:
            out.append({"name": name, "state": "never", "seen": None, "facts": {}, "box_of": None})
            continue
        out.append({"name": name, "state": "on" if now - came <= FRESH else "away", "seen": int(came), "facts": facts,
                    "box_of": (boxes or {}).get(facts.get("box"))})
    return out


def request_signature(key_text, name, when, what="profile"):
    """What signs a client's request for its profile (or its new agent): its name and the time."""
    return hmac.new(key_text.encode(), ("client-%s\n%s\n%s" % (what, name, when)).encode(), hashlib.sha256).hexdigest()


def agent_signature(key_text, name, body):
    """What signs a new agent for the client, as profile_signature a profile."""
    text = "agent\n%s\n%s" % (name, hashlib.sha256(body).hexdigest())
    return hmac.new(key_text.encode(), text.encode(), hashlib.sha256).hexdigest()


def check_request(key_text, name, when, sign, now=None, what="profile"):
    try:
        t = int(when)
    except (TypeError, ValueError):
        return "no time"
    if abs((now if now is not None else time.time()) - t) > SKEW:
        return "its clock and this one's are more than %d minutes apart" % (SKEW // 60)
    if not hmac.compare_digest(request_signature(key_text, name, when, what), str(sign or "").strip().lower()):
        return "the signature is not this client's"
    return None


def profile_signature(key_text, name, body):
    """What signs a profile for the client: only the control node (with the seed) and the client can."""
    text = "profile\n%s\n%s" % (name, hashlib.sha256(body).hexdigest())
    return hmac.new(key_text.encode(), text.encode(), hashlib.sha256).hexdigest()


def order_signature(key_text, name, oid, what, target, at):
    """'client-order' first: an order for a client never passes for one for a machine."""
    text = "client-order\n%s\n%s\n%s\n%s\n%s" % (name, oid, what, target, at)
    return hmac.new(key_text.encode(), text.encode(), hashlib.sha256).hexdigest()


def vpn_conf_path(fleet, name):
    """The client's place on the VPN, when it has one: a device of the same name (`client add --vpn`)."""
    return fleet.resolve_path("secrets/devices/%s.conf" % name)


def managed_of(block, accounts_url):
    """A managed computer's part of its profile: where its people's accounts are, who may log in,
    who gets sudo. None for a personal one, or without the network's accounts."""
    if str((block or {}).get("kind") or "personal") != "managed":
        return None
    if not accounts_url:
        raise KiwiError("a managed computer needs the network's accounts (the accounts module) for its logins")

    def groups(key, default):
        got = block.get(key) or [default]
        got = [got] if isinstance(got, str) else got
        for g in got:
            if not NAME.match(str(g)):
                raise KiwiError("%s: %r is not a plain group name" % (key, g))
        return [str(g) for g in got]
    return {"accounts": accounts_url, "login": groups("login_groups", "computers"), "admins": groups("admin_groups", "admins")}


def profile(fleet, name, linked=(), unlock=None, managed=None):
    """The client's profile, as the agent reads it: one fact a line. The network's CA; its VPN (a
    WireGuard config, as NetworkManager imports it) when it has a place on the VPN, with how much
    goes through it: `split` only the network (its names to its DNS, the Internet as it was), `full`
    everything (the config's own AllowedIPs say which)."""
    import base64
    from . import connect as connectmod
    domain = str(fleet.defaults.get("domain") or "home")
    tls = fleet.defaults.get("tls") or {}
    ca_path = os.path.join(fleet.resolve_path(tls.get("ca_dir") or "secrets/ca"), "kiwiCA.pem")
    lines = ["profile 1", "network %s" % re.sub(r"[^a-z0-9-]", "", domain.split(".")[0].lower()),
             "connection %s" % connectmod.connection_name(domain), "agent_version %d" % VERSION]
    if os.path.isfile(ca_path):
        lines.append("ca " + base64.b64encode(util.read_bytes(ca_path)).decode())
    conf = vpn_conf_path(fleet, name)
    if os.path.isfile(conf):
        data = util.read_bytes(conf)
        full = any(re.match(r"\s*AllowedIPs\s*=.*\b0\.0\.0\.0/0", line) for line in data.decode(errors="replace").splitlines())
        lines += ["vpn " + base64.b64encode(data).decode(), "vpn_mode " + ("full" if full else "split"),
                  "search " + ",".join(["~" + domain] + ["~" + d for d in sorted(set(linked)) if d and d != domain])]
    if managed:         # a managed computer: logins with the network's accounts, sudo for its admins
        lines += ["kind managed", "accounts " + managed["accounts"], "login_groups " + ",".join(managed["login"]),
                  "admin_groups " + ",".join(managed["admins"])]
    if unlock:          # its disk's policy (unlock_tree): kept bound by the agent once a person bound it
        import json
        lines.append("unlock " + base64.b64encode(json.dumps(unlock, sort_keys=True, separators=(",", ":")).encode()).decode())
    return ("\n".join(lines) + "\n").encode()


def join_script(fleet, name, url, ca):
    """The install script with the client's key in it, for a computer that joins by a one-time code:
    it goes through the pipe into `sudo sh` and lies on no disk."""
    secret = key(fleet, name)
    if not re.fullmatch(r"[A-Za-z0-9]+", secret):
        raise KiwiError("the client's key is not of letters and digits")
    script = install_script(fleet, name, url, ca)
    return script.replace("key=$(cat)\n", "key='%s'\n" % secret, 1)


def join_command(url, code, ca_b64):
    """The one command a person runs on the computer: the network's CA travels with it, so the
    download is checked against it; then the script, as root."""
    # the script (with the key) only in a variable and the pipe; a code used up says why, and fails
    return ("c=$(mktemp); echo %s | base64 -d > \"$c\"; s=$(curl -sS --fail-with-body --cacert \"$c\" %s/api/client/join/%s); "
            "r=$?; rm -f \"$c\"; if [ $r = 0 ]; then printf '%%s\\n' \"$s\" | sudo sh; else printf '%%s\\n' \"$s\" >&2; false; fi"
            % (ca_b64, url, code))


def unlock_tree(name, block, servers, facts):
    """The clevis policy of a client's disk (docs/design/unlock.md, desktops; the switched rings of
    the machines): every ring it names (or `threshold` of them), and with `switch` its own ring as
    well, which the control node runs, stops or runs for a while: auto-unlock on, off, for N
    minutes. None without rings. `servers`: unlock.ring_servers; `facts(server, ring)`: (thumbprint,
    advertisement). A computer finds a ring at boot at its machine's fixed address at home, or
    where `unlock.via` says."""
    import json
    from . import unlock as unlockmod
    u = (block or {}).get("unlock") or {}
    rings = [str(r) for r in (u.get("rings") or [])]
    if not rings:
        return None
    via = u.get("via") or {}

    def pin(ring):
        if ring not in servers:
            raise KiwiError("%s: no machine of this network serves the ring %s (the tang module's rings)" % (name, ring))
        server, port = servers[ring]
        if isinstance(server, unlockmod.OperatorRing):
            raise KiwiError("%s: a computer opens with the network's rings, not with the operator's" % name)
        lan = unlockmod._lan(server)
        if via.get(ring):
            where = str(via[ring])
        elif lan and unlockmod._site(server) == str(u.get("site") or "home"):
            where = "http://%s:%d" % (lan.ip, port)
        else:
            raise KiwiError("%s: the ring %s is on %s, which has no fixed address at home: unlock.via says where "
                            "the computer finds it at boot" % (name, ring, server.name))
        _thp, adv = facts(server, ring)
        return {"url": where, "adv": json.loads(adv)}
    t = int(u.get("threshold") or len(rings))
    if not 1 <= t <= len(rings):
        raise KiwiError("%s: unlock.threshold is between 1 and %d, its rings" % (name, len(rings)))
    base = {"t": t, "pins": {"tang": [pin(r) for r in rings]}}
    if u.get("switch"):
        return {"t": 2, "pins": {"sss": [base], "tang": [pin(str(u["switch"]))]}}
    return base
