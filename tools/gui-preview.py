#!/usr/bin/env python3
"""One part of the app on its own, to look at while working on it: the setup wizard, without
the file dialog in front of it (a desktop portal would open that on the real screen).

    tools/private-session.sh sh -c 'gtk4-broadwayd :7 & sleep 1; GDK_BACKEND=broadway BROADWAY_DISPLAY=:7 \
        tools/gui-preview.py unlock ~/kiwi/fleet.yaml'          (or: wizard /tmp/x/fleet.yaml, move PATH)
Only inside tools/private-session.sh: with the desktop's own runtime folder a preview's bus
started a second document portal, and the desktop lost its /run/user/$UID/doc (19 Nov 2026).

then open http://127.0.0.1:8087 in a browser. Uses this tree's bin/kiwi-server.
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
loader = importlib.machinery.SourceFileLoader("ksgui", os.path.join(ROOT, "gui", "kiwi-server-gui"))
spec = importlib.util.spec_from_loader("ksgui", loader)
gui = importlib.util.module_from_spec(spec)
loader.exec_module(gui)
from gi.repository import Adw  # noqa: E402  (loaded by the app module, versions set there)

CLI = os.path.join(ROOT, "bin", "kiwi-server")


class Host(Adw.ApplicationWindow):
    """Stands in for the app's window: what the wizard calls back."""

    def __init__(self, app):
        super().__init__(application=app, default_width=760, default_height=780, title="preview")
        self.cli = CLI
        self.set_content(Adw.StatusPage(title="gui-preview"))

    def alert(self, heading, body):
        print("alert:", heading, "—", body, flush=True)

    def open_path(self, path):
        print("would open", path, flush=True)


def private_or_refuse():
    """The owner's rule (19 Nov 2026): never on the desktop's runtime folder or bus."""
    real = "/run/user/%d" % os.getuid()
    rt = os.path.realpath(os.environ.get("XDG_RUNTIME_DIR") or real)
    bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS") or ""
    if rt == real or not rt.startswith(real + "/kiwi-private.") or real + "/bus" in bus \
            or os.environ.get("KIWI_PRIVATE_SESSION") != "1":
        sys.exit("gui-preview: refused: XDG_RUNTIME_DIR is the desktop's own (%s). Run it through "
                 "tools/private-session.sh, which gives it a throwaway folder and a bus that starts nothing." % rt)


def main():
    private_or_refuse()
    if len(sys.argv) != 3 or sys.argv[1] not in ("wizard", "unlock", "move"):
        sys.exit("usage: tools/gui-preview.py wizard|unlock|move PATH/fleet.yaml")
    path = os.path.abspath(sys.argv[2])

    def activate(app):
        w = Host(app)
        w.present()
        if sys.argv[1] == "unlock":
            w.doc = type("Doc", (), {"path": path})()
            gui.UnlockDialog(w).present(w)
            return
        if sys.argv[1] == "move":           # the move dialog of a made-up machine with two others of its kind
            w.doc = type("Doc", (), {"path": path})()
            w.cli = CLI
            w.status = {"quince": {"role": "node", "modules": ["vault", "paste", "backup"], "keep_ready": {"vault": "medlar"}},
                        "medlar": {"role": "node", "modules": ["git"]}, "lychee": {"role": "node", "modules": []},
                        "sorbet": {"role": "master", "modules": []}}
            gui.MoveDialog(w, "quince").present(w)
            return
        data = json.loads(subprocess.run([CLI, "init", "--questions"], capture_output=True, text=True, check=True).stdout)
        gui.SetupWizard(w, path, data).present(w)
    app = Adw.Application(application_id="eu.kiwinetwork.KiwiServer.Preview")
    app.connect("activate", activate)
    app.run([])


if __name__ == "__main__":
    main()
