#!/usr/bin/env bash
# installer for kiwi-server — kiwi-updater convention:
#   ./install.sh install|update|uninstall [--purge]
#
# Everything is user-level (SCOPES=user): no root, nothing outside your home.
#   kiwi-server, kiwi-server-gui  -> $KIWI_PREFIX/bin        (~/.local/bin)
#   python package, roles, container -> $KIWI_PREFIX/lib/kiwi-server
#   desktop entry, icon, completion  -> $KIWI_PREFIX/share
# Building ISOs needs butane, coreos-installer and xorriso — installed natively
# or, on an ostree desktop, inside a container: kiwi-server toolchain build.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${KIWI_PREFIX:-$HOME/.local}"
SHARE="${XDG_DATA_HOME:-$HOME/.local/share}"
LIB="$PREFIX/lib/kiwi-server"
APP_ID="eu.kiwinetwork.KiwiServer"
ACTION="${1:-${KIWI_ACTION:-install}}"
PURGE="${KIWI_PURGE:-0}"
[[ "${2:-}" == --purge ]] && PURGE=1

say()  { printf ':: %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }

want_gui() {
    [[ ${KIWI_GUI:-} == 0 ]] && return 1
    [[ -n ${KIWI_GUI:-} ]] && return 0
    [[ -e /usr/lib64/girepository-1.0/Gtk-4.0.typelib ||
       -e /usr/lib/girepository-1.0/Gtk-4.0.typelib ]]
}

do_install() {
    command -v python3 >/dev/null 2>&1 || { echo "error: python3 is required" >&2; exit 1; }
    python3 -c 'import yaml' 2>/dev/null ||
        warn "python3 cannot import yaml — install python3-pyyaml (rpm-ostree install python3-pyyaml, or pip install --user pyyaml)"
    python3 -c 'import jinja2' 2>/dev/null ||
        warn "python3 cannot import jinja2 — module roles need it (pip install --user jinja2, or rpm-ostree install python3-jinja2)"

    say "installing the library to $LIB"
    rm -rf "$LIB"
    mkdir -p "$LIB"
    cp -r "$SRC/lib/kiwiserver" "$SRC/roles" "$SRC/modules" "$SRC/container" "$SRC/examples" "$LIB/"
    find "$LIB" -name '__pycache__' -type d -prune -exec rm -rf {} +
    # the example secrets dir holds only a note; the user's own files never come from here
    rm -rf "$LIB/examples/secrets"

    say "installing kiwi-server to $PREFIX/bin"
    install -Dm755 "$SRC/bin/kiwi-server" "$PREFIX/bin/kiwi-server"
    install -Dm644 "$SRC/data/bash-completion/kiwi-server" \
        "$SHARE/bash-completion/completions/kiwi-server"

    if want_gui; then
        say "installing kiwi-server-gui + desktop entry + icon"
        install -Dm755 "$SRC/gui/kiwi-server-gui" "$PREFIX/bin/kiwi-server-gui"
        mkdir -p "$SHARE/applications"
        sed "s|^Exec=.*|Exec=$PREFIX/bin/kiwi-server-gui|" "$SRC/data/kiwi-server.desktop" \
            > "$SHARE/applications/$APP_ID.desktop"
        install -Dm644 "$SRC/data/icons/$APP_ID.svg" \
            "$SHARE/icons/hicolor/scalable/apps/$APP_ID.svg"
        command -v gtk-update-icon-cache >/dev/null 2>&1 &&
            gtk-update-icon-cache -qtf "$SHARE/icons/hicolor" 2>/dev/null || true
        command -v update-desktop-database >/dev/null 2>&1 &&
            update-desktop-database -q "$SHARE/applications" 2>/dev/null || true
    else
        say "no GTK4 stack (or KIWI_GUI=0) — skipping the GUI"
    fi

    say "done — try: kiwi-server doctor  |  kiwi-server init  |  kiwi-server-gui"
    if ! command -v butane >/dev/null 2>&1 || ! command -v coreos-installer >/dev/null 2>&1 ||
       ! command -v xorriso >/dev/null 2>&1; then
        say "ISO tools (butane, coreos-installer, xorriso) are not all installed natively:"
        say "  kiwi-server toolchain build   puts them in a podman/docker image instead"
    fi
}

do_update() { do_install; }

do_uninstall() {
    say "removing kiwi-server"
    rm -f "$PREFIX/bin/kiwi-server" "$PREFIX/bin/kiwi-server-gui" \
          "$SHARE/bash-completion/completions/kiwi-server" \
          "$SHARE/applications/$APP_ID.desktop" \
          "$SHARE/icons/hicolor/scalable/apps/$APP_ID.svg"
    rm -rf "$LIB"
    if [[ $PURGE == 1 ]]; then
        say "purging the ISO cache ${XDG_CACHE_HOME:-$HOME/.cache}/kiwi-server"
        rm -rf "${XDG_CACHE_HOME:-$HOME/.cache}/kiwi-server"
    else
        say "kept the ISO cache in ${XDG_CACHE_HOME:-$HOME/.cache}/kiwi-server (uninstall --purge removes it)"
    fi
    say "fleet files and output directories are yours — nothing of those was touched"
}

case "$ACTION" in
    install)   do_install ;;
    update)    do_update ;;
    uninstall) do_uninstall ;;
    *) echo "usage: $0 install|update|uninstall [--purge]" >&2; exit 1 ;;
esac
