"""Preferences (ui-rules 5): one dialog behind the gear.

General holds the app's own groups first, then Appearance and Window, which
are the same in every app. An app adds its groups and further pages:

    dialog = Preferences(info, store, window, jobs=True)
    dialog.general.add(my_group)          # before Appearance — see add_own
    dialog.add(my_snapshots_page)
    dialog.present(window)
"""

from __future__ import annotations

from gi.repository import Adw, Gio, GLib, Gtk

from . import geometry, icon
from .info import AppInfo
from .settings import Store


class Preferences(Adw.PreferencesDialog):
    def __init__(self, info: AppInfo, store: Store, window, *, own_groups=(), jobs=False) -> None:
        """own_groups: the app's Adw.PreferencesGroups for General, in order.
        jobs: the app runs jobs with an output pane (adds its switch)."""
        super().__init__(title="Preferences")
        self.info, self.store, self.window = info, store, window
        self.general = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")
        self.add(self.general)
        for group in own_groups:
            self.general.add(group)
        self._build_appearance()
        self._build_window(jobs)

    # ── Appearance ────────────────────────────────────────────────────────
    def _build_appearance(self) -> None:
        # Said once, here, and not as a toast after every change: trying the
        # colours and styles one after the other is what this group is for.
        group = Adw.PreferencesGroup(
            title="Appearance",
            description="The app grid may show a new icon only after you log in again.",
        )
        self.general.add(group)
        self._busy = True
        self._preview = Gtk.Image(pixel_size=48, valign=Gtk.Align.CENTER)
        self._choices = icon.colors(self.info) + [
            (f"own:{name}", f"Your own: {name}") for name in icon.own_files(self.info)
        ]
        self._icon_row = Adw.ComboRow(
            title="App Icon",
            subtitle="in the app grid, the dash and this window",
            model=Gtk.StringList.new([label for _key, label in self._choices]),
        )
        self._icon_row.add_prefix(self._preview)
        own = self.store["kit.icon_file"]
        have = f"own:{own}" if own else self.store["kit.icon_color"]
        keys = [key for key, _label in self._choices]
        self._icon_row.set_selected(keys.index(have) if have in keys else 0)
        folder = Gtk.Button(
            icon_name="folder-open-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text="Your own icons: put PNG or SVG files in this folder, "
            "then open Preferences again",
        )
        folder.add_css_class("flat")
        folder.connect("clicked", self._open_icons_folder)
        self._icon_row.add_suffix(folder)
        group.add(self._icon_row)

        self._style_row = Adw.ComboRow(
            title="Icon Style",
            subtitle="kiwi-icons' three styles",
            model=Gtk.StringList.new([label for _key, label in icon.STYLES]),
        )
        styles = [key for key, _label in icon.STYLES]
        style = self.store["kit.icon_style"]
        self._style_row.set_selected(styles.index(style) if style in styles else 0)
        group.add(self._style_row)
        self._icon_row.connect("notify::selected", self._on_icon_changed)
        self._style_row.connect("notify::selected", self._on_icon_changed)
        self._busy = False
        self._show_icon()

    def _show_icon(self) -> None:
        key = self._choices[self._icon_row.get_selected()][0]
        self._style_row.set_visible(not key.startswith("own:"))
        path = icon.target(self.info)
        if path.is_file():
            self._preview.set_from_file(str(path))
        else:
            self._preview.set_from_icon_name(self.info.app_id)

    def _on_icon_changed(self, *_args) -> None:
        if self._busy:
            return
        key = self._choices[self._icon_row.get_selected()][0]
        own = key.startswith("own:")
        try:
            self.store.set("kit.icon_file", key[4:] if own else "")
            self.store.set("kit.icon_color", "" if own else key)
            self.store.set("kit.icon_style", icon.STYLES[self._style_row.get_selected()][0])
        except OSError as exc:
            self.toast(f"Not saved: {exc}")
            return
        error = icon.apply(self.info, self.store)
        self._show_icon()
        if error:
            self.toast(f"Icon not changed: {error}")

    def _open_icons_folder(self, *_args) -> None:
        folder = self.info.icons_dir()
        folder.mkdir(parents=True, exist_ok=True)
        Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(str(folder), None), None)

    # ── Window ────────────────────────────────────────────────────────────
    def _build_window(self, jobs: bool) -> None:
        group = Adw.PreferencesGroup(title="Window")
        self.general.add(group)
        size = Adw.ActionRow(title="Size")

        def show() -> None:
            width, height, maximized = geometry.saved(self.info)
            size.set_subtitle(
                "maximized" if maximized else f"{width} × {height}, kept when you close it"
            )

        def on_reset(*_args) -> None:
            geometry.reset(self.window, self.info)
            show()

        reset = Gtk.Button(label="Reset", valign=Gtk.Align.CENTER)
        reset.connect("clicked", on_reset)
        size.add_suffix(reset)
        show()
        group.add(size)

        if jobs:
            output = Adw.SwitchRow(
                title="Show Output While Working",
                subtitle="off: only the kiwi fills; the output opens if something fails",
                active=self.store["kit.show_output"],
            )
            output.connect(
                "notify::active",
                lambda row, _p: self.store.set("kit.show_output", row.get_active()),
            )
            group.add(output)

    def toast(self, text: str) -> None:
        self.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=4))
