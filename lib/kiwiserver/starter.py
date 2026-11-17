"""The starter image: one ISO, the same for everybody, with no fleet and no secret in it.

It is the stock Fedora CoreOS live ISO with two Ignition configs (docs/design/starter.md):

- the live system's (the stick): a script that picks the machine's one disk, installs there
  and starts the installed system with kexec. It never installs over a Kiwi system, and with
  no disk or several it installs nothing and says so on the consoles.
- the installed system's: hostname `kiwi`, answering to kiwi.local (mDNS), the control
  container's first-run page on port 8740 and port 80, its setup code and a QR code of the
  address on the screen, and a key pair made at the first boot for the control container.
"""
import json
import os
import shutil
import tempfile

from . import iso as isomod, util
from .targets import coreos as coreos_t

CONTROL_IMAGE = "ghcr.io/derlocke-ng/kiwi-server-control:2"
HOSTNAME = "kiwi"
MIN_DISK_BYTES = 16 * 1000 ** 3
MARKER = "kiwi-starter.installed"       # on the installed disk's boot file system (label `boot`)
KEY_DIR = "/var/lib/kiwi-starter"        # the starter key; /starter in the control container, read-only
# /fleet in the control container: where a node's control part keeps the fleet (<docker dir>/kn-control,
# /home being /var/home on CoreOS), so that the node this machine becomes finds it there
FLEET_DIR = "/var/home/user/docker/kn-control"
DONE = "/usr/local/bin/kiwi-starter-done"
ISSUE = "/etc/issue.d/50-kiwi.issue"
PAGE_DIR = "/run/kiwi-starter/page"      # the live system's page: what it shows, the number, the yes
CONSOLE_KARGS = ["console=tty0", "console=ttyS0,115200n8"]   # the last is /dev/console
DEBUG_KARG = "systemd.debug_shell=ttyS1"   # --debug-shell (drills only): a root shell on the second serial port

# Shared by both systems: say something on every console and in the journal.
SAY_SH = r"""
CONSOLES=${KIWI_CONSOLES:-/dev/tty1 /dev/ttyS0}
say() {
    printf '%s\n' "$@"
    local t
    for t in $CONSOLES; do
        [ -c "$t" ] || continue
        # a serial port without a cable must not hang the script
        timeout 3 sh -c '{ printf "\n"; printf "%s\n" "$@"; } > "$0"' "$t" "$@" 2>/dev/null || true
    done
}
show() {    # the message on the login screens too, and again for whoever looks later
    mkdir -p "$(dirname "$ISSUE")"
    { echo; printf '%s\n' "$@"; echo; } > "$ISSUE.tmp" && mv -f "$ISSUE.tmp" "$ISSUE"
    agetty --reload 2>/dev/null || true
    if [ -n "${PAGE_DIR:-}" ]; then     # the live system's page says the same
        mkdir -p "$PAGE_DIR" && printf '%s\n' "$@" > "$PAGE_DIR/message.tmp" && mv -f "$PAGE_DIR/message.tmp" "$PAGE_DIR/message"
    fi
    say "$@"
}
"""

INSTALL_SH = r"""#!/bin/bash
# Kiwi Server starter, the live system: install this machine on its one disk, carefully.
#   - candidates: whole disks, not the stick this started from, not removable, not USB, 16 GB or more
#   - a candidate that holds a Kiwi system (the marker file on its `boot` file system) is
#     not installed over: the installed system is started instead
#   - exactly one candidate and it is blank: install there, mark it, start the installed system (kexec)
#   - exactly one candidate that holds something: say what, and install only after a yes: ERASE
#     typed at a console, or the button on the page this system serves on port 80
#   - none or several: install nothing, say so on every console and the page, keep saying it
#   - any failure: stop and say what failed; nothing restarts
set -u
MIN_BYTES=${KIWI_MIN_BYTES:-@MIN_BYTES@}
MARKER=@MARKER@
IGNITION=${KIWI_IGNITION:-/etc/kiwi-starter/installed.ign}
IGNITION_PLAIN=${KIWI_IGNITION_PLAIN:-/etc/kiwi-starter/installed-plain.ign}
TPM_DIR=${KIWI_TPM_DIR:-/sys/class/tpm/tpm0}
MIN_MEM_KB=${KIWI_MIN_MEM_KB:-3500000}   # CoreOS encrypts its root at the first start in memory: 4 GB
ISSUE=${KIWI_ISSUE:-@ISSUE@}
PAGE_DIR=${KIWI_PAGE_DIR:-@PAGE_DIR@}
INSTALL_ARGS=(@INSTALL_ARGS@)
@SAY_SH@
live_device() {     # the disk the live system started from (the stick), if it can tell
    local src parent
    src=$(findmnt -no SOURCE /run/media/iso 2>/dev/null | head -n1)
    [ -n "$src" ] || return 0
    parent=$(lsblk -no PKNAME "$src" 2>/dev/null | head -n1)
    if [ -n "$parent" ]; then echo "/dev/$parent"; else echo "$src"; fi
}

disks() {   # NAME|TYPE|RM|RO|TRAN|SIZE|MODEL, one disk a line; empty fields stay empty
    lsblk -dnbrp -o NAME,TYPE,RM,RO,TRAN,SIZE,MODEL 2>/dev/null |
        awk 'BEGIN { FS = "[ ]"; OFS = "|" } $2 == "disk" { $1 = $1; print }'
}

gb() { awk -v b="$1" 'BEGIN { printf "%.1f GB\n", b / 1e9 }'; }

boot_partition() {
    lsblk -nrpo NAME,LABEL "$1" 2>/dev/null | awk '$2 == "boot" { print $1; exit }'
}

holds_kiwi() {      # does this disk hold a system the starter installed?
    local part m rc=1
    part=$(boot_partition "$1")
    [ -n "$part" ] || return 1
    m=$(mktemp -d)
    if mount -o ro,noload "$part" "$m" 2>/dev/null || mount -o ro "$part" "$m" 2>/dev/null; then
        [ -f "$m/$MARKER" ] && rc=0
        umount "$m" 2>/dev/null
    fi
    rmdir "$m" 2>/dev/null
    return $rc
}

parts() {   # NAME|TYPE|FSTYPE|PARTTYPE of the disk and its partitions
    lsblk -nrpo NAME,TYPE,FSTYPE,PARTTYPE "$1" 2>/dev/null | awk 'BEGIN { FS = "[ ]"; OFS = "|" } { $1 = $1; print }'
}

is_blank() {    # no partition, no file system or other signature; a partition table alone is blank
    local n sigs
    n=$(parts "$1" | wc -l)
    [ "$n" -eq 1 ] || return 1
    [ -z "$(parts "$1" | cut -d'|' -f3)" ] || return 1
    sigs=$(wipefs --no-act --noheadings --output TYPE "$1" 2>/dev/null) || return 1
    ! printf '%s\n' "$sigs" | grep -qvxE 'gpt|PMBR|dos|'
}

contents() {    # what is on the disk, in a person's words, as far as it can be told cheaply
    local name _type fstype parttype w words=()
    while IFS="|" read -r name _type fstype parttype; do
        w=
        case ${fstype,,} in
            ntfs|refs|bitlocker) w=Windows ;;
            ext2|ext3|ext4|xfs|btrfs|lvm2_member|crypto_luks|swap|f2fs|zfs_member) w=Linux ;;
            apfs|hfsplus|hfs) w=macOS ;;
        esac
        if [ -z "$w" ]; then
            case ${parttype,,} in
                e3c9e316-0b5c-4db8-817d-f92df00215ae|ebd0a0a2-b9e5-4433-87c0-68b6b72699c7|de94bba4-06d1-4d40-a16a-bfd50179d6ac|0x7|0x27) w=Windows ;;
                0fc63daf-8483-4772-8e79-3d69d8477de4|e6d6d379-f507-44c2-a23c-238f2a3df928|0657fd6d-a4ab-43c4-84e5-0933c84b4f4f|0x83|0x8e|0x82) w=Linux ;;
                7c3457ef-0000-11aa-aa11-00306543ecac|48465300-0000-11aa-aa11-00306543ecac) w=macOS ;;
                c12a7328-f81f-11d2-ba4b-00a0c93ec93b|21686148-6449-6e6f-744e-656564454649|0xef) continue ;;   # boot partitions say nothing
                *) [ -n "$fstype$parttype" ] || continue; w=data ;;
            esac
        fi
        [[ " ${words[*]} " == *" $w "* ]] || words+=("$w")
    done < <(parts "$1")
    [ "${#words[@]}" -gt 0 ] || words=(data)
    local out=${words[0]} i
    for ((i = 1; i < ${#words[@]}; i++)); do out+=" and ${words[i]}"; done
    echo "$out"
}

console_yes() { [ "${1%$'\r'}" = "${2:-ERASE}" ]; }     # the word in full, in capitals; anything else is no

address() {
    ip -4 -o route get 192.0.2.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p'
}

describe() {    # every disk, for a message: name, size, model, and why it is left out
    local live name _type rm ro tran size model why
    live=$(live_device)
    while IFS='|' read -r name _type rm ro tran size model; do
        model=$(printf '%b' "$model")
        why=
        if [ "$name" = "$live" ]; then why="the stick this started from"
        elif [ "$tran" = usb ]; then why="USB, left alone"
        elif [ "$rm" = 1 ]; then why="removable, left alone"
        elif [ "$ro" = 1 ]; then why="read-only"
        elif [[ $name == */zram* || $name == */loop* ]]; then continue
        elif [ "$size" -lt "$MIN_BYTES" ]; then why="too small, 16 GB at least"
        fi
        echo "  ${name#/dev/}  $(gb "$size")  ${model:-no model}${why:+  ($why)}"
    done < <(disks)
}

decide() {  # one line: install DISK | ask DISK | installed DISK | none | small DISK... | several DISK...
    local live name _type rm ro tran size model cands=() small=() d
    live=$(live_device)
    while IFS='|' read -r name _type rm ro tran size model; do
        [ "$name" = "$live" ] && continue
        [[ $name == */zram* || $name == */loop* ]] && continue
        [ "$rm" = 1 ] || [ "$tran" = usb ] || [ "$ro" = 1 ] && continue
        if [ "$size" -lt "$MIN_BYTES" ]; then small+=("$name"); continue; fi
        cands+=("$name")
    done < <(disks)
    for d in "${cands[@]}"; do
        if holds_kiwi "$d"; then echo "installed $d"; return; fi
    done
    if [ "${#cands[@]}" -eq 1 ]; then
        if is_blank "${cands[0]}"; then echo "install ${cands[0]}"; else echo "ask ${cands[0]}"; fi
    elif [ "${#cands[@]}" -gt 1 ]; then echo "several ${cands[*]}"
    elif [ "${#small[@]}" -gt 0 ]; then echo "small ${small[*]}"
    else echo "none"
    fi
}

ask_console() {     # TTY [WORD]: read lines until one is the word (ERASE)
    local t=$1 word=${2:-ERASE} fd ans
    [ -c "$t" ] || return 0
    stty -F "$t" sane clocal 2>/dev/null
    exec {fd}<"$t" || return 0
    while IFS= read -r -u "$fd" ans; do
        if console_yes "$ans" "$word"; then : > "$PAGE_DIR/yes"; return 0; fi
        if [ -n "${ans//[[:space:]]/}" ]; then
            if [ "$word" = ERASE ]; then
                say "kiwi: that was not ERASE, nothing was erased. To erase this disk and install, type ERASE and press Enter."
            else
                say "kiwi: that was not $word, nothing was installed. To install without encryption, type $word and press Enter."
            fi
        fi
    done
}

cannot_encrypt() {  # why this machine's disk cannot be encrypted here; nothing when it can
    local mem
    mem=$(awk '/^MemTotal:/ { print $2 }' /proc/meminfo 2>/dev/null)
    if [ "$(cat "$TPM_DIR/tpm_version_major" 2>/dev/null)" != 2 ]; then
        echo "it has no TPM 2.0 chip that is switched on (in the firmware settings: TPM, fTPM or PTT)"
    elif [ "${mem:-0}" -lt "$MIN_MEM_KB" ]; then
        echo "it has less than 4 GB of memory, which encrypting the disk needs at the first start"
    fi
}

choose_encryption() {   # the disk is encrypted, or the person typed PLAIN (or pressed the page's button)
    local why n t pids=() i=0 lines
    why=$(cannot_encrypt)
    [ -n "$why" ] || return 0
    lines=("kiwi: this machine's disk cannot be encrypted: $why." \
           "Encrypted is how Kiwi installs. Switch the TPM on and start the machine again from the stick," \
           "or type PLAIN and press Enter to install WITHOUT encryption: everything on this disk could then be read by whoever takes it.")
    mkdir -p "$PAGE_DIR"
    rm -f "$PAGE_DIR/yes"
    n=$(od -An -N8 -tu8 /dev/urandom | tr -dc 0-9)
    printf '%s\n' "$n" > "$PAGE_DIR/nonce"
    show "${lines[@]}"
    printf '%s\n' "${lines[@]:0:2}" "Or press the button to install WITHOUT encryption: everything on this disk could then be read by whoever takes it." \
        > "$PAGE_DIR/message"
    echo plain > "$PAGE_DIR/state"
    systemctl stop getty@tty1.service serial-getty@ttyS0.service 2>/dev/null
    for t in $CONSOLES; do ask_console "$t" PLAIN & pids+=($!); done
    until [ -e "$PAGE_DIR/yes" ]; do
        sleep 1
        i=$((i + 1))
        [ $((i % 30)) = 0 ] && say "${lines[@]}"
    done
    kill "${pids[@]}" 2>/dev/null
    rm -f "$PAGE_DIR/nonce"
    echo installing > "$PAGE_DIR/state"
    IGNITION=$IGNITION_PLAIN
    show "kiwi: PLAIN given: installing without encryption." "Installing. This page changes when the machine has started again."
}

wait_for_yes() {    # DISK: say what is on it, then wait for ERASE at a console or the page's button
    local disk=$1 what one n t pids=() i=0 lines
    what=$(contents "$disk")
    one=$(describe | grep -m1 "^  ${disk#/dev/} ")
    one=${one#  }
    ask_lines() {
        local a; a=$(address)
        printf '%s\n' "kiwi: nothing was installed yet: the one disk here is not empty." \
            "  $one: it holds $what." \
            "Installing Kiwi ERASES this disk and everything on it." \
            "To erase this disk and install, type ERASE and press Enter." \
            "Or answer on a phone or computer in this home network: http://kiwi.local${a:+ or http://$a}"
    }
    mkdir -p "$PAGE_DIR"
    rm -f "$PAGE_DIR/yes"
    n=$(od -An -N8 -tu8 /dev/urandom | tr -dc 0-9)
    printf '%s\n' "$n" > "$PAGE_DIR/nonce"
    mapfile -t lines < <(ask_lines)
    show "${lines[@]}"
    printf '%s\n' "${lines[@]:0:3}" "To erase this disk and install, press the button. At the machine, typing ERASE does the same." \
        > "$PAGE_DIR/message"
    echo ask > "$PAGE_DIR/state"
    # the consoles are ours now: no login is possible on the stick anyway
    systemctl stop getty@tty1.service serial-getty@ttyS0.service 2>/dev/null
    for t in $CONSOLES; do ask_console "$t" & pids+=($!); done
    until [ -e "$PAGE_DIR/yes" ]; do
        sleep 1
        i=$((i + 1))
        if [ $((i % 30)) = 0 ]; then mapfile -t lines < <(ask_lines); say "${lines[@]}"; fi
    done
    kill "${pids[@]}" 2>/dev/null
    rm -f "$PAGE_DIR/nonce"
    echo installing > "$PAGE_DIR/state"
    show "kiwi: yes given: erasing ${disk#/dev/} and installing." "Installing. This page changes when the machine has started again."
}

choose_disk() {     # DISKS…: several disks; the page offers one button each. Prints the one chosen
    local n i=0 d line pick
    mkdir -p "$PAGE_DIR"
    rm -f "$PAGE_DIR/yes"
    n=$(od -An -N8 -tu8 /dev/urandom | tr -dc 0-9)
    printf '%s\n' "$n" > "$PAGE_DIR/nonce"
    : > "$PAGE_DIR/choices.tmp"
    for d in "$@"; do
        i=$((i + 1))
        line=$(describe | grep -m1 "^  ${d#/dev/} ")
        printf '%s|%s\n' "$i" "${line#  }" >> "$PAGE_DIR/choices.tmp"
    done
    mv -f "$PAGE_DIR/choices.tmp" "$PAGE_DIR/choices"
    printf '%s\n' "kiwi: $# disks here. Choose the one to install Kiwi on: it is ERASED, with everything on it." \
        "The others stay as they are." > "$PAGE_DIR/message"
    echo choose > "$PAGE_DIR/state"
    until [ -s "$PAGE_DIR/yes" ]; do sleep 1; done
    pick=$(tr -dc 0-9 < "$PAGE_DIR/yes")
    rm -f "$PAGE_DIR/nonce" "$PAGE_DIR/choices"
    [[ $pick =~ ^[0-9]+$ ]] && [ "$pick" -ge 1 ] && [ "$pick" -le $# ] || return 1
    echo "${!pick}"
}

keep_saying() {     # the message stays on the screen; nothing restarts
    while sleep 30; do say "$@"; done
}

fail() {
    [ -n "${PAGE_DIR:-}" ] && mkdir -p "$PAGE_DIR" && echo info > "$PAGE_DIR/state"
    # what the installer said, for the page: a phone has no console to read the journal on
    # (the stick holds no secret: the lines are safe to show)
    [ -n "${PAGE_DIR:-}" ] && journalctl -u kiwi-starter-install -n 30 --no-pager -o cat > "$PAGE_DIR/details.tmp" 2>/dev/null \
        && mv -f "$PAGE_DIR/details.tmp" "$PAGE_DIR/details"
    show "kiwi: the install stopped: $*" "Nothing was restarted. Look at the machine's disks, then start it again from the stick."
    keep_saying "kiwi: the install stopped: $*"
}

install_to() {
    local disk=$1 part m
    choose_encryption
    say "kiwi: installing on ${disk#/dev/}: everything on it is erased$([ "$IGNITION" = "$IGNITION_PLAIN" ] && echo ", NOT encrypted" || echo ", encrypted")"
    coreos-installer install "$disk" --offline -i "$IGNITION" --console tty0 --console ttyS0,115200n8 \
        "${INSTALL_ARGS[@]}" || { fail "coreos-installer install $disk failed (journalctl -u kiwi-starter-install says why)"; return 1; }
    udevadm settle
    part=$(boot_partition "$disk")
    [ -n "$part" ] || { fail "the installed system on $disk has no boot partition"; return 1; }
    m=$(mktemp -d)
    mount "$part" "$m" || { fail "cannot mount $part to mark the install"; return 1; }
    { date -u +%Y-%m-%dT%H:%M:%SZ; echo "installed by the Kiwi Server starter"; } > "$m/$MARKER" && sync
    local rc=$?
    umount "$m"; rmdir "$m"
    [ "$rc" = 0 ] || { fail "cannot write the marker on $part"; return 1; }
}

start_installed() {     # kexec into the installed system: the firmware's boot order does not matter
    local part m entry best=-1 v e linux initrd opts
    part=$(boot_partition "$1")
    m=/run/kiwi-starter/boot
    mkdir -p "$m"
    mount -o ro "$part" "$m" || return 1
    for e in "$m"/loader/entries/*.conf; do     # the newest deployment has the highest version
        [ -f "$e" ] || continue
        v=$(awk '$1 == "version" { print $2; exit }' "$e")
        [[ $v =~ ^[0-9]+$ ]] || v=0
        if [ "$v" -gt "$best" ]; then best=$v; entry=$e; fi
    done
    if [ -z "${entry:-}" ]; then umount "$m"; return 1; fi
    linux=$(awk '$1 == "linux" { print $2; exit }' "$entry")
    initrd=$(awk '$1 == "initrd" { print $2; exit }' "$entry")
    opts=$(sed -n 's/^options[[:space:]]*//p' "$entry" | head -n1)
    # what grub would add: ignition.firstboot while /boot/ignition.firstboot is there
    if [ -e "$m/ignition.firstboot" ]; then opts=${opts//\$ignition_firstboot/ignition.firstboot}; fi
    opts=$(printf '%s\n' "$opts" | sed 's/\$[A-Za-z_][A-Za-z_0-9]*//g')
    kexec -s -l "$m$linux" --initrd="$m$initrd" --command-line="$opts" 2>/dev/null ||
        kexec -l "$m$linux" --initrd="$m$initrd" --command-line="$opts" || { umount "$m"; return 1; }
    umount "$m"
    systemctl kexec
}

main() {
    local what disks n lines
    udevadm settle 2>/dev/null
    read -r what disks < <(decide)
    mkdir -p "$PAGE_DIR"; echo info > "$PAGE_DIR/state"
    case $what in
        ask)
            wait_for_yes "$disks"
            ;&
        install)
            echo installing > "$PAGE_DIR/state"
            install_to "$disks" || return 1
            show "kiwi: installed: take the stick out" "The installed system starts now." \
                "Installing. This page changes when the machine has started again."
            sleep 5
            start_installed "$disks" || fail "installed, but the installed system did not start: take the stick out and restart the machine"
            ;;
        installed)
            show "kiwi: this machine is installed already: take the stick out" "The installed system on ${disks#/dev/} starts now."
            sleep 5
            start_installed "$disks" || {
                show "kiwi: this machine is installed already: take the stick out" "Then restart the machine."
                keep_saying "kiwi: this machine is installed already: take the stick out, then restart the machine"
            }
            ;;
        several)
            n=$(wc -w <<< "$disks")
            mapfile -t lines < <(describe)
            show "kiwi: $n disks found, so nothing was installed yet. The disks:" "${lines[@]}" \
                "Choose one on a phone or computer in this home network: http://kiwi.local$(a=$(address); echo "${a:+ or http://$a}")" \
                "Or unplug the disks that should stay as they are, keep one, and start the machine again."
            # the page's message comes after show's: choose_disk writes it
            local chosen
            ( keep_saying "kiwi: $n disks found, so nothing was installed yet: choose one at http://kiwi.local" ) & local talker=$!
            chosen=$(choose_disk $disks) || { kill "$talker" 2>/dev/null; fail "no disk was chosen"; return 1; }
            kill "$talker" 2>/dev/null
            echo installing > "$PAGE_DIR/state"
            show "kiwi: ${chosen#/dev/} chosen: erasing it and installing." "Installing. This page changes when the machine has started again."
            install_to "$chosen" || return 1
            show "kiwi: installed: take the stick out" "The installed system starts now." \
                "Installing. This page changes when the machine has started again."
            sleep 5
            start_installed "$chosen" || fail "installed, but the installed system did not start: take the stick out and restart the machine"
            ;;
        small)
            mapfile -t lines < <(describe)
            show "kiwi: nothing was installed: the disk is too small, Kiwi needs 16 GB at least. The disks:" "${lines[@]}"
            keep_saying "kiwi: nothing was installed: the disk is too small, Kiwi needs 16 GB at least:" "${lines[@]}"
            ;;
        *)
            mapfile -t lines < <(describe)
            show "kiwi: nothing was installed: no disk found to install on. The disks:" "${lines[@]:-  (none)}" \
                "USB and removable disks are left alone. Put in a disk of 16 GB or more, then start the machine again."
            keep_saying "kiwi: nothing was installed: no disk found to install on:" "${lines[@]:-  (none)}"
            ;;
    esac
}

(return 0 2>/dev/null) || main "$@"     # sourced (the tests): the functions only
"""

PAGE_SH = r"""#!/bin/bash
# Kiwi Server starter, the live system: the small page on port 80, one connection per run
# (a systemd socket with Accept=yes). It shows what the stick's install script says and, while
# that script waits for a yes, a button that posts back the number this page carries. Nothing
# from the request reaches a shell or a file: the method, the path and the number are compared.
set -u
export LC_ALL=C
D=${KIWI_PAGE_DIR:-@PAGE_DIR@}

esc() {
    local s=$1
    s=${s//&/"&amp;"}; s=${s//</"&lt;"}; s=${s//>/"&gt;"}; s=${s//\"/"&quot;"}     # quoted: & is literal
    printf '%s' "$s"
}

reply() {   # CODE REASON BODY [HEAD]
    local body
    body="<!doctype html><html><head><meta charset=utf-8><meta name=viewport content=\"width=device-width\">"
    body+="<title>Kiwi Server</title>${4:-}<style>body{font-family:sans-serif;max-width:36em;margin:1em auto;padding:0 1em;line-height:1.4}"
    body+="button{font-size:1.1em;padding:.6em 1em;background:#b00020;color:#fff;border:0;border-radius:4px}</style></head>"
    body+="<body><h1>Kiwi Server</h1>$3</body></html>"
    printf 'HTTP/1.0 %s %s\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: %d\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n%s' \
        "$1" "$2" "${#body}" "$body"
}

message() {
    local line out=
    if [ -f "$D/message" ]; then
        while IFS= read -r line; do out+="<p>$(esc "${line#kiwi: }")</p>"; done < "$D/message"
    fi
    if [ -s "$D/details" ]; then        # a stopped install: what the installer said, word for word
        out+="<details><summary>What the installer said</summary><pre style=\"white-space:pre-wrap\">"
        while IFS= read -r line; do out+="$(esc "$line")"$'\n'; done < "$D/details"
        out+="</pre></details>"
    fi
    printf '%s' "$out"
}

IFS= read -r -t 10 line || exit 0
line=${line%$'\r'}
method=${line%% *}
rest=${line#* }
path=${rest%% *}
len=0
while IFS= read -r -t 10 h; do
    h=${h%$'\r'}
    [ -z "$h" ] && break
    case ${h,,} in
        content-length:*) v=${h#*:}; v=${v//[[:space:]]/}; [[ $v =~ ^[0-9]{1,4}$ ]] && len=$((10#$v)) ;;
    esac
done
state=$(cat "$D/state" 2>/dev/null)
nonce=$(cat "$D/nonce" 2>/dev/null)
refresh=
[ "$state" = installing ] && refresh="<meta http-equiv=refresh content=15>"
case "$method $path" in
    "GET /" | "GET /index.html")
        body=$(message)
        if [ "$state" = ask ] && [[ $nonce =~ ^[0-9]+$ ]]; then
            body+="<form method=post action=/erase><input type=hidden name=n value=$nonce>"
            body+="<button>Erase this disk and install</button></form>"
        fi
        if [ "$state" = plain ] && [[ $nonce =~ ^[0-9]+$ ]]; then
            body+="<form method=post action=/erase><input type=hidden name=n value=$nonce>"
            body+="<button>Install WITHOUT encryption</button></form>"
        fi
        if [ "$state" = choose ] && [[ $nonce =~ ^[0-9]+$ ]] && [ -f "$D/choices" ]; then
            while IFS='|' read -r i label; do       # one button a disk: its number, never its name, comes back
                [[ $i =~ ^[0-9]{1,2}$ ]] || continue
                body+="<form method=post action=/erase><input type=hidden name=n value=$nonce><input type=hidden name=d value=$i>"
                body+="<p><button>Erase $(esc "${label%% *}") and install</button> <small>$(esc "$label")</small></p></form>"
            done < "$D/choices"
        fi
        reply 200 OK "$body" "$refresh"
        ;;
    "POST /erase")
        given='' pick=''
        if [ "$len" -gt 0 ] && [ "$len" -le 256 ] && IFS= read -r -N "$len" -t 10 form; then
            [[ $form =~ (^|&)n=([0-9]{1,24})(&|$) ]] && given=${BASH_REMATCH[2]}
            [[ $form =~ (^|&)d=([0-9]{1,2})(&|$) ]] && pick=${BASH_REMATCH[2]}
        fi
        count=$(grep -c . "$D/choices" 2>/dev/null)
        if [ "$state" = choose ] && [[ $nonce =~ ^[0-9]+$ ]] && [ "$given" = "$nonce" ] && [ -n "$pick" ] \
                && [ "$((10#$pick))" -ge 1 ] && [ "$((10#$pick))" -le "${count:-0}" ]; then
            printf '%s\n' "$((10#$pick))" > "$D/yes"
            echo installing > "$D/state"
            reply 200 OK "<p>Installing. This page changes when the machine has started again.</p>" \
                "<meta http-equiv=refresh content=15>"
        elif { [ "$state" = ask ] || [ "$state" = plain ]; } && [[ $nonce =~ ^[0-9]+$ ]] && [ "$given" = "$nonce" ]; then
            : > "$D/yes"
            echo installing > "$D/state"
            reply 200 OK "<p>Installing. This page changes when the machine has started again.</p>" \
                "<meta http-equiv=refresh content=15>"
        else
            reply 403 Forbidden "<p>Nothing was erased: that button is not from this page as it is now. <a href=/>Open the page again</a>.</p>"
        fi
        ;;
    *)
        reply 404 "Not Found" "<p><a href=/>The page is here.</a></p>"
        ;;
esac
"""

KEY_SH = r"""#!/bin/bash
# Kiwi Server starter: the starter key, made at the first boot. The private half stays in
# @KEY_DIR@ (root, 0600; mounted read-only into the control container), the public half is
# the core user's one authorized key until the role replaces it with the fleet's.
set -eu
d=@KEY_DIR@
install -d -m 0700 -o root -g root "$d"
if [ ! -s "$d/id_ed25519" ] || [ ! -s "$d/id_ed25519.pub" ]; then
    rm -f "$d/id_ed25519" "$d/id_ed25519.pub"
    ssh-keygen -q -t ed25519 -N '' -C kiwi-starter -f "$d/id_ed25519"
fi
chmod 0600 "$d/id_ed25519"
chmod 0644 "$d/id_ed25519.pub"
pub=$(cat "$d/id_ed25519.pub")
h=/home/core/.ssh
install -d -m 0700 -o core -g core "$h"
touch "$h/authorized_keys"
grep -qxF "$pub" "$h/authorized_keys" || printf '%s\n' "$pub" >> "$h/authorized_keys"
chown core:core "$h/authorized_keys"
chmod 0600 "$h/authorized_keys"
restorecon -R "$h" 2>/dev/null || true
"""

SCREEN_SH = r"""#!/bin/bash
# Kiwi Server starter: the address, the setup code and a QR code of the address on the
# screen (/etc/issue.d and every console). Written again when the address changes or the
# control container starts anew: the code is read from the log of its current start only.
set -u
ISSUE=@ISSUE@
UNIT=kn-control.service
@SAY_SH@
CONSOLES=       # agetty shows the issue on every console: printed once more, it would land after the login prompt
address() {
    local a
    a=$(ip -4 -o route get 192.0.2.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p')
    [ -n "$a" ] || a=$(ip -4 -o addr show scope global 2>/dev/null | awk '{ split($4, x, "/"); print x[1]; exit }')
    echo "$a"
}

setup_code() {      # from this start of the container only: a code of an earlier start is gone
    local id
    id=$(systemctl show -p InvocationID --value "$UNIT" 2>/dev/null)
    [ -n "$id" ] || return 0
    journalctl -q -o cat "_SYSTEMD_INVOCATION_ID=$id" 2>/dev/null |
        sed -n 's/.*Its setup code: \([0-9a-f]\{4\}-[0-9a-f]\{4\}\).*/\1/p' | tail -n1
}

fingerprint() {     # the page's certificate of this start, as a browser shows it (the first 8 pairs)
    local id
    id=$(systemctl show -p InvocationID --value "$UNIT" 2>/dev/null)
    [ -n "$id" ] || return 0
    journalctl -q -o cat "_SYSTEMD_INVOCATION_ID=$id" 2>/dev/null |
        sed -n "s/.*Its page's certificate: \([0-9A-F:]*\).*/\1/p" | tail -n1 | cut -d: -f1-8
}

qr() {  # the control image has kiwi-server's QR encoder; text blocks for a terminal
    podman exec kn-control python3 -c 'import sys; from kiwiserver import qr; print(qr.text_blocks(sys.argv[1]))' "$1" 2>/dev/null
}

last=
while :; do
    id=$(systemctl show -p InvocationID --value "$UNIT" 2>/dev/null)
    addr=$(address)
    code=$(setup_code)
    fp=$(fingerprint)
    state="$id|$addr|$code|$fp"
    if [ "$state" != "$last" ]; then
        lines=("Kiwi Server")
        if [ -z "$addr" ]; then
            lines+=("This machine waits for an address from the router: is the network cable in?")
        else
            lines+=("Open https://kiwi.local or https://$addr" "on a phone or computer in this home network.")
            [ -n "$fp" ] && lines+=("The browser warns once: this machine made its own certificate." \
                                    "It is the right one when it begins $fp")
        fi
        if cryptsetup status root >/dev/null 2>&1; then
            lines+=("This machine's disk is encrypted. Its TPM opens it until a network takes it in.")
        else
            lines+=("This machine's disk is NOT encrypted: whoever takes the disk can read it.")
        fi
        if [ -n "$code" ]; then
            lines+=("Setup code: $code")
        elif ! systemctl is-active -q "$UNIT"; then
            lines+=("The page is starting (the first start fetches it, a few minutes).")
        fi
        block=
        if [ -n "$addr" ] && [ -n "$code" ]; then
            block=$(qr "https://$addr/") || block=
            [ -n "$block" ] || state=      # the container is not there yet: try again
        fi
        [ -n "$block" ] && mapfile -t qrl <<< "$block" && lines+=("${qrl[@]}")
        show "${lines[@]}"
        last=$state
    fi
    sleep 5
done
"""

PORT80_NFT = """# Kiwi Server starter: port 80 leads to the control page's redirect on 8740, port 443 to the page on 8743
table inet kiwi_starter
delete table inet kiwi_starter
table inet kiwi_starter {
    chain prerouting {
        type nat hook prerouting priority dstnat; policy accept;
        tcp dport 80 redirect to :8740
        tcp dport 443 redirect to :8743
    }
    chain output {
        type nat hook output priority -100; policy accept;
        fib daddr type local tcp dport 80 redirect to :8740
        fib daddr type local tcp dport 443 redirect to :8743
    }
}
"""


DONE_SH = r"""#!/bin/bash
# Kiwi Server starter: this machine is a node of the network now. What the starter needed goes:
# its key, its page on port 80, its message on the screen. Run after the machine's first role
# (kiwi-server apply): on the network's first node once the stack has taken over the control
# part; with `joined`, on a machine another control node took in, which has no control part:
# the starter's goes too.
set -u
q=/etc/containers/systemd/kn-control.container
joined=${1:-}
if [ "$joined" = joined ]; then
    if grep -q '^# kiwi-server stack' "$q" 2>/dev/null; then
        joined=        # it has a control part of the network's own after all: that one stays
    else
        systemctl stop kn-control.service 2>/dev/null
        rm -f "$q"
        podman rm -f kn-control >/dev/null 2>&1
        rm -rf @FLEET_DIR@
    fi
elif ! grep -q '^# kiwi-server stack' "$q" 2>/dev/null; then
    echo "kiwi-starter-done: the stack has not taken over the control part: nothing is taken away" >&2
    exit 1
fi
systemctl disable --now kiwi-starter-screen.service kiwi-starter-port80.service 2>/dev/null
nft delete table inet kiwi_starter 2>/dev/null
h=/home/core/.ssh
if [ -s @KEY_DIR@/id_ed25519.pub ] && [ -f "$h/authorized_keys" ]; then
    grep -vxF "$(cat @KEY_DIR@/id_ed25519.pub)" "$h/authorized_keys" > "$h/authorized_keys.new"
    install -m 0600 -o core -g core "$h/authorized_keys.new" "$h/authorized_keys"
    rm -f "$h/authorized_keys.new"
    restorecon -R "$h" 2>/dev/null
fi
systemctl disable kiwi-starter-key.service 2>/dev/null
rm -rf @KEY_DIR@ /etc/kiwi-starter @ISSUE@ /usr/local/bin/kiwi-starter-key /usr/local/bin/kiwi-starter-screen \
    /etc/systemd/system/kiwi-starter-key.service /etc/systemd/system/kiwi-starter-port80.service \
    /etc/systemd/system/kiwi-starter-screen.service \
    /etc/systemd/resolved.conf.d/50-kiwi-mdns.conf /etc/NetworkManager/conf.d/50-kiwi-mdns.conf
systemctl daemon-reload
# the control part as the stack defines it: on the network's own names from now on, its keys locked
# until someone logs in there
[ "$joined" = joined ] || systemctl restart kn-control.service
rm -f "$0"
echo "kiwi-starter-done: this machine is a node now"
"""


def done_script():
    return _fill(DONE_SH, KEY_DIR=KEY_DIR, ISSUE=ISSUE, FLEET_DIR=FLEET_DIR)


def _fill(text, **kw):
    for k, v in kw.items():
        text = text.replace("@%s@" % k, v)
    return text


def install_script(debug_shell=False):
    args = ["--append-karg", DEBUG_KARG] if debug_shell else []
    return _fill(INSTALL_SH, MIN_BYTES=str(MIN_DISK_BYTES), MARKER=MARKER, ISSUE=ISSUE, PAGE_DIR=PAGE_DIR,
                 INSTALL_ARGS=" ".join(args), SAY_SH=SAY_SH.strip("\n"))


def page_script():
    return _fill(PAGE_SH, PAGE_DIR=PAGE_DIR)


def screen_script():
    return _fill(SCREEN_SH, ISSUE=ISSUE, SAY_SH=SAY_SH.strip("\n"))


def key_script():
    return _fill(KEY_SH, KEY_DIR=KEY_DIR)


def control_quadlet(image=CONTROL_IMAGE):
    return "\n".join([
        "[Unit]",
        "Description=Kiwi Server: the control page (first run)",
        "Wants=network-online.target",
        "After=network-online.target kiwi-starter-key.service",
        "Requires=kiwi-starter-key.service",
        "",
        "[Container]",
        "Image=%s" % image,
        "ContainerName=kn-control",
        "Network=host",
        "Volume=%s:/fleet:Z" % FLEET_DIR,
        # the starter key, read-only; :Z gives it this container's own label, no other container reads it
        "Volume=%s:/starter:ro,Z" % KEY_DIR,
        "Tmpfs=/run/kiwi:size=2g,mode=0700",
        "Environment=XDG_RUNTIME_DIR=/run/kiwi",
        "Environment=KIWI_STARTER=1",       # the fleet's first node is the machine this runs on
        # HTTPS with a certificate of its own (made at each start, in RAM): the setup code, the password
        # and the hoster's sign-in never cross the home network unencrypted; 8740 only sends the browser on
        "Exec=serve /fleet/fleet.yaml --listen 0.0.0.0:8740 --https 0.0.0.0:8743 --wait",
        "",
        "[Service]",
        "Restart=always",
        "RestartSec=5",
        "TimeoutStartSec=900",      # the first start pulls the image
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ])


def _unit(name, desc, body, after="", wanted="multi-user.target", extra_unit=""):
    return {"name": name, "enabled": True, "contents": (
        "[Unit]\nDescription=%s\n%s%s\n[Service]\n%s\n[Install]\nWantedBy=%s\n"
        % (desc, ("After=%s\n" % after) if after else "", extra_unit, body, wanted))}


def name_files():
    """The name kiwi, announced on the home network (mDNS): kiwi.local, on the stick and installed."""
    f = coreos_t._file
    return [
        f("/etc/hostname", HOSTNAME + "\n"),
        f("/etc/systemd/resolved.conf.d/50-kiwi-mdns.conf", "[Resolve]\nMulticastDNS=yes\n"),
        # every connection NetworkManager makes announces the name (2: yes)
        f("/etc/NetworkManager/conf.d/50-kiwi-mdns.conf", "[connection]\nconnection.mdns=2\n"),
    ]


def installed_butane(encrypted=True):
    """The installed system: a control node that waits to be set up. No password, no key but
    the starter key made at its first boot. `encrypted`: its root in LUKS, opened by the
    machine's TPM until a network takes it in and binds its rings instead (docs/design/encryption.md)."""
    f = coreos_t._file
    files = name_files() + [
        f("/etc/containers/systemd/kn-control.container", control_quadlet()),
        f("/etc/kiwi-starter/port80.nft", PORT80_NFT),
        f("/usr/local/bin/kiwi-starter-key", key_script(), mode=0o755),
        f("/usr/local/bin/kiwi-starter-screen", screen_script(), mode=0o755),
        f(DONE, done_script(), mode=0o755),
    ]
    dirs = [{"path": KEY_DIR, "mode": 0o700}, {"path": FLEET_DIR, "mode": 0o700}]
    units = [
        _unit("kiwi-starter-key.service", "Kiwi Server: the starter key",
              "Type=oneshot\nRemainAfterExit=yes\nExecStart=/usr/local/bin/kiwi-starter-key\n",
              extra_unit="Before=kn-control.service\n"),
        _unit("kiwi-starter-port80.service", "Kiwi Server: port 80 leads to the control page",
              "Type=oneshot\nRemainAfterExit=yes\nExecStart=/usr/sbin/nft -f /etc/kiwi-starter/port80.nft\n"
              "ExecStop=-/usr/sbin/nft delete table inet kiwi_starter\n"),
        _unit("kiwi-starter-screen.service", "Kiwi Server: the address and the setup code on the screen",
              "ExecStart=/usr/local/bin/kiwi-starter-screen\nRestart=always\nRestartSec=5\n",
              after="systemd-user-sessions.service"),
    ]
    bu = {"variant": coreos_t.BUTANE_VARIANT, "version": coreos_t.BUTANE_VERSION,
          "storage": {"directories": dirs, "files": files}, "systemd": {"units": units}}
    if encrypted:
        bu["boot_device"] = {"luks": {"tpm2": True, "discard": True}}
    return bu


def live_butane(installed_ign, debug_shell=False, plain_ign=None):
    """The stick: the install script, the installed system's Ignition, the unit that runs the
    script, and the page on port 80. No user, no password: there is nothing to log in to."""
    f = coreos_t._file
    unit = _unit("kiwi-starter-install.service", "Kiwi Server: install this machine from the starter stick",
                 "Type=simple\nExecStart=/bin/bash /usr/local/bin/kiwi-starter-install\n",
                 after="systemd-udev-settle.service local-fs.target",
                 extra_unit="Wants=systemd-udev-settle.service\nConditionKernelCommandLine=coreos.liveiso\n")
    # the page: one connection, one run of the script; a slow or endless request ends after 20 s
    socket = {"name": "kiwi-starter-page.socket", "enabled": True, "contents": (
        "[Unit]\nDescription=Kiwi Server: the starter's page on port 80\n\n"
        "[Socket]\nListenStream=80\nAccept=yes\nMaxConnections=16\n\n[Install]\nWantedBy=sockets.target\n")}
    page = {"name": "kiwi-starter-page@.service", "contents": (
        "[Unit]\nDescription=Kiwi Server: the starter's page, one request\n\n"
        "[Service]\nExecStart=/bin/bash /usr/local/bin/kiwi-starter-page\nStandardInput=socket\n"
        "StandardOutput=socket\nStandardError=journal\nRuntimeMaxSec=20\n")}
    return {"variant": coreos_t.BUTANE_VARIANT, "version": coreos_t.BUTANE_VERSION,
            "storage": {"files": name_files() + [
                f("/usr/local/bin/kiwi-starter-install", install_script(debug_shell), mode=0o755),
                f("/usr/local/bin/kiwi-starter-page", page_script(), mode=0o755),
                f("/etc/kiwi-starter/installed.ign", installed_ign, mode=0o600),
            ] + ([f("/etc/kiwi-starter/installed-plain.ign", plain_ign, mode=0o600)] if plain_ign else [])},
            "systemd": {"units": [unit, socket, page]}}


def build_iso(tc, out_iso, cache_dir, stream="stable", base=None, debug_shell=False):
    """The starter ISO at out_iso. Everything is made in a fresh folder beside it, which is the
    only folder the toolchain container gets (with the base ISO's)."""
    out_iso = os.path.abspath(out_iso)
    os.makedirs(util.parent(out_iso), exist_ok=True)
    work = tempfile.mkdtemp(prefix=".kiwi-starter-", dir=util.parent(out_iso))
    try:
        igns = {}
        for kind, encrypted in (("installed", True), ("installed-plain", False)):
            inst_bu, inst_ign = os.path.join(work, kind + ".bu"), os.path.join(work, kind + ".ign")
            util.write_text(inst_bu, coreos_t.to_yaml(installed_butane(encrypted)))
            coreos_t.to_ignition(tc, inst_bu, inst_ign)
            igns[kind] = util.read_text(inst_ign)
            json.loads(igns[kind])     # butane made something: an empty or broken file is an error here, not on the stick
        live_bu, live_ign = os.path.join(work, "live.bu"), os.path.join(work, "live.ign")
        util.write_text(live_bu, coreos_t.to_yaml(live_butane(igns["installed"], debug_shell, igns["installed-plain"])))
        coreos_t.to_ignition(tc, live_bu, live_ign)
        base = os.path.abspath(base) if base else isomod.fcos_base_iso(tc, stream, cache_dir)
        tmp_iso = os.path.join(work, "starter.iso")
        argv = ["coreos-installer", "iso", "customize", "--live-ignition", live_ign]
        for k in CONSOLE_KARGS + ([DEBUG_KARG] if debug_shell else []):
            argv += ["--live-karg-append", k]
        argv += ["-o", tmp_iso, base]
        tc.run(argv, mounts=[work, util.parent(base)], relabel=False)
        if os.path.exists(out_iso):
            os.unlink(out_iso)
        shutil.move(tmp_iso, out_iso)
        os.chmod(out_iso, 0o644)        # no secret in it: every copy is the same
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out_iso
