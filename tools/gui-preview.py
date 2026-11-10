#!/usr/bin/env python3
"""One part of the app on its own, to look at while working on it: the setup wizard, without
the file dialog in front of it (a desktop portal would open that on the real screen).

    gtk4-broadwayd :7 &
    GDK_BACKEND=broadway BROADWAY_DISPLAY=:7 tools/gui-preview.py wizard /tmp/x/fleet.yaml

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


def main():
    if len(sys.argv) != 3 or sys.argv[1] != "wizard":
        sys.exit("usage: tools/gui-preview.py wizard PATH/fleet.yaml")
    data = json.loads(subprocess.run([CLI, "init", "--questions"], capture_output=True, text=True, check=True).stdout)

    def activate(app):
        w = Host(app)
        w.present()
        gui.SetupWizard(w, os.path.abspath(sys.argv[2]), data).present(w)
    app = Adw.Application(application_id="eu.kiwinetwork.KiwiServer.Preview")
    app.connect("activate", activate)
    app.run([])


if __name__ == "__main__":
    main()
