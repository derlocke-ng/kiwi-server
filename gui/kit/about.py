"""About (ui-rules 4): the same dialog in every app, filled from AppInfo."""

from __future__ import annotations

from gi.repository import Adw, Gtk

from .info import AppInfo


def show(info: AppInfo, parent) -> None:
    dialog = Adw.AboutDialog(
        application_name=info.name,
        application_icon=info.app_id,
        version=info.version,
        comments=info.description,
        license_type=Gtk.License.GPL_3_0,
        website=info.homepage,
        issue_url=f"{info.homepage}/issues" if info.homepage else "",
    )
    dialog.add_link("Kiwi Network", "https://kiwi-network.eu")
    dialog.present(parent)
