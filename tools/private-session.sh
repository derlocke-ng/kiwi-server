#!/bin/bash
# A private session for previews and tests of desktop apps (the owner's rules, 19 Nov 2026):
# its own runtime folder and its own session bus that can start nothing, so no second document
# portal or gvfsd can come up on the desktop's /run/user/$UID/doc or gvfs. One did on 7 and on
# 19 November: when it was killed the desktop's mount went with it, and every Flatpak app lost
# its files until xdg-document-portal.service was restarted.
#
#   tools/private-session.sh COMMAND [ARGS...]
#
# COMMAND gets: XDG_RUNTIME_DIR (a throwaway folder under /run/user/$UID), a session bus of its
# own without service folders, GDK_DEBUG=no-portals, GIO_USE_VFS=local, and
# KIWI_PRIVATE_SESSION=1. Start broadwayd inside COMMAND, so it shares the folder. Afterwards
# only what lives in that folder is stopped, unmounted and removed; then the desktop's document
# portal mount is checked, and the portal restarted (and said) if it went missing.
set -eu
uid=$(id -u)
real=/run/user/$uid
[ $# -gt 0 ] || { echo "usage: tools/private-session.sh COMMAND [ARGS...]" >&2; exit 2; }
command -v dbus-run-session >/dev/null || { echo "private-session: dbus-run-session is missing" >&2; exit 2; }
had_doc=0; grep -q " $real/doc " /proc/mounts && had_doc=1

rt=$(mktemp -d "$real/kiwi-private.XXXXXX")
chmod 700 "$rt"
case "$rt" in "$real"/kiwi-private.?*) ;; *) echo "private-session: odd runtime folder $rt" >&2; exit 2 ;; esac

# a bus that only listens: no <servicedir>, no <standard_session_servicedirs/>, so it activates nothing
cat > "$rt/bus.conf" <<EOF
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <keep_umask/>
  <listen>unix:dir=$rt</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
EOF

inside() {      # PID: does it run with this throwaway folder? (never anything of the desktop)
    { tr '\0' '\n' < "/proc/$1/environ" | grep -qE "^XDG_RUNTIME_DIR=$rt(/.*)?$"; } 2>/dev/null
}
cleanup() {
    local d p m
    for d in /proc/[0-9]*; do
        p=${d#/proc/}
        [ "$p" = "$$" ] && continue
        inside "$p" && kill "$p" 2>/dev/null
    done
    sleep 1
    for d in /proc/[0-9]*; do p=${d#/proc/}; [ "$p" != "$$" ] && inside "$p" && kill -9 "$p" 2>/dev/null; done
    # unmount only below the throwaway folder; the desktop's doc and gvfs are never touched
    while read -r _ m _; do
        case "$m" in "$real/doc"|"$real/gvfs"|"$real/doc/"*|"$real/gvfs/"*) continue ;; "$rt"/*) fusermount -u -z "$m" 2>/dev/null ;; esac
    done < /proc/mounts
    case "$rt" in "$real"/kiwi-private.?*) rm -rf "$rt" ;; esac
    if [ "$had_doc" = 1 ] && ! grep -q " $real/doc " /proc/mounts; then
        systemctl --user restart xdg-document-portal.service
        echo "private-session: the desktop's $real/doc mount was gone: restarted xdg-document-portal.service" >&2
    fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

export XDG_RUNTIME_DIR="$rt" GDK_DEBUG=no-portals GIO_USE_VFS=local KIWI_PRIVATE_SESSION=1
unset DBUS_SESSION_BUS_ADDRESS
dbus-run-session --config-file="$rt/bus.conf" -- "$@"
