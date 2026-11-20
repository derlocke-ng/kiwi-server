"""The header bar and the main menu (ui-rules 2 and 3).

Start: the one main action. End, from the edge inwards: menu, Preferences,
Refresh, then the app's own buttons. The menu holds the app's own actions,
then Help, then About.

    header = build(window, info,
                   main=("New Note", "list-add-symbolic", self.on_new),
                   on_refresh=self.refresh,
                   on_preferences=self.open_preferences,
                   menu_actions=[("Stop everything", "win.stop-all")])

Returns the Adw.HeaderBar; `header.kit_refresh` is the Refresh button (to
make insensitive while refreshing), `header.kit_title` the Adw.WindowTitle.
"""

from __future__ import annotations

from gi.repository import Adw, Gio, Gtk

from . import about
from .info import AppInfo


def _action(window, name: str, callback, accel: str | None = None) -> None:
    action = Gio.SimpleAction.new(name, None)
    action.connect("activate", lambda *_a: callback())
    window.add_action(action)
    if accel and window.get_application():
        window.get_application().set_accels_for_action(f"win.{name}", [accel])


def build(
    window,
    info: AppInfo,
    *,
    main: tuple[str, str, object] | None = None,
    on_refresh=None,
    on_preferences=None,
    menu_actions: list[tuple[str, str]] | tuple = (),
    extra_end: list | tuple = (),
) -> Adw.HeaderBar:
    header = Adw.HeaderBar()
    header.kit_title = Adw.WindowTitle(title=info.name, subtitle="")
    header.set_title_widget(header.kit_title)

    if main:
        label, icon_name, callback = main
        button = Gtk.Button(icon_name=icon_name, tooltip_text=label)
        button.connect("clicked", lambda *_a: callback())
        header.pack_start(button)

    menu = Gio.Menu()
    if menu_actions:
        own = Gio.Menu()
        for label, action in menu_actions:
            own.append(label, action)
        menu.append_section(None, own)
    fixed = Gio.Menu()
    if info.help_uri():
        fixed.append("Help", "win.help")
        _action(
            window,
            "help",
            lambda: Gtk.UriLauncher(uri=info.help_uri()).launch(window, None, None, None),
            "F1",
        )
    fixed.append(f"About {info.name}", "win.about")
    _action(window, "about", lambda: about.show(info, window))
    menu.append_section(None, fixed)
    header.pack_end(
        Gtk.MenuButton(
            icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Main Menu", primary=True
        )
    )

    if on_preferences:
        gear = Gtk.Button(icon_name="emblem-system-symbolic", tooltip_text="Preferences")
        gear.connect("clicked", lambda *_a: on_preferences())
        header.pack_end(gear)
        _action(window, "preferences", on_preferences, "<Control>comma")

    header.kit_refresh = None
    if on_refresh:
        header.kit_refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh")
        header.kit_refresh.connect("clicked", lambda *_a: on_refresh())
        header.pack_end(header.kit_refresh)
        _action(window, "refresh", on_refresh, "<Control>r")

    for widget in extra_end:
        header.pack_end(widget)
    return header
