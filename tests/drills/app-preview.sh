#!/bin/bash
# The app with a made-up network (madeup.py), shown in a browser by GTK's Broadway: to look at
# the app and click it without a real network. Nothing real is asked or changed: `list`,
# `status` and `secrets status` are answered by the stand-in, the rest by this checkout.
#
#   tests/drills/app-preview.sh        then open http://127.0.0.1:8087   (Ctrl+C ends it)
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
# only in a private session: a throwaway runtime folder and a bus that starts nothing (the
# owner's rules, 19 Nov 2026; tools/private-session.sh says why)
[ "${KIWI_PRIVATE_SESSION:-}" = 1 ] || exec "$root/tools/private-session.sh" "$0" "$@"
r=$(mktemp -d "$XDG_RUNTIME_DIR/kiwi-app-preview.XXXXXX")
mkdir -p "$r/rt" "$r/home" "$r/fleet" "$r/bin"
chmod 700 "$r/rt"
python3 -c "import sys; sys.path.insert(0, '$here'); import madeup; print(madeup.fleet_yaml(), end='')" > "$r/fleet/fleet.yaml"
cat > "$r/bin/kiwi-server" <<STAND
#!/bin/bash
case "\${1:-}" in
    list|status|secrets|onion|history) exec python3 "$here/madeup.py" "\$@" ;;
    control) if [ "\${2:-}" = activity ]; then exec python3 "$here/madeup.py" "\$@"; else exec "$root/bin/kiwi-server" "\$@"; fi ;;
    agent) exit 1 ;;     # no resident one: every read goes to the made-up answers above
    *) exec "$root/bin/kiwi-server" "\$@" ;;
esac
STAND
chmod +x "$r/bin/kiwi-server"
XDG_RUNTIME_DIR="$r/rt" gtk4-broadwayd :7 &
bw=$!
trap 'kill $bw 2>/dev/null; rm -rf "$r"' EXIT
sleep 1
echo "http://127.0.0.1:8087   (Ctrl+C ends it)"
base=${PYTHONUSERBASE:-$HOME/.local}          # a throwaway HOME hides what pip put into the real one
# the private session's own bus: the app is single-instance, and on the desktop's bus a running
# Kiwi Server would be handed the made-up network instead of this one starting
HOME="$r/home" PYTHONUSERBASE="$base" XDG_RUNTIME_DIR="$r/rt" GDK_BACKEND=broadway BROADWAY_DISPLAY=:7 \
    KIWI_SERVER_CLI="$r/bin/kiwi-server" python3 "$root/gui/kiwi-server-gui" "$r/fleet/fleet.yaml"
