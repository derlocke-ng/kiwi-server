#!/bin/bash
# The app with a made-up network (madeup.py), shown in a browser by GTK's Broadway: to look at
# the app and click it without a real network. Nothing real is asked or changed: `list`,
# `status` and `secrets status` are answered by the stand-in, the rest by this checkout.
#
#   tests/drills/app-preview.sh        then open http://127.0.0.1:8087   (Ctrl+C ends it)
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
r=$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/kiwi-app-preview.XXXXXX")
mkdir -p "$r/rt" "$r/home" "$r/fleet" "$r/bin"
chmod 700 "$r/rt"
python3 -c "import sys; sys.path.insert(0, '$here'); import madeup; print(madeup.fleet_yaml(), end='')" > "$r/fleet/fleet.yaml"
cat > "$r/bin/kiwi-server" <<STAND
#!/bin/bash
case "\${1:-}" in
    list|status|secrets|onion) exec python3 "$here/madeup.py" "\$@" ;;
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
# a session bus of its own: the app is single-instance, and on the desktop's bus a running Kiwi
# Server would be handed the made-up network instead of this one starting
HOME="$r/home" PYTHONUSERBASE="$base" XDG_RUNTIME_DIR="$r/rt" GDK_BACKEND=broadway BROADWAY_DISPLAY=:7 \
    KIWI_SERVER_CLI="$r/bin/kiwi-server" dbus-run-session -- python3 "$root/gui/kiwi-server-gui" "$r/fleet/fleet.yaml"
