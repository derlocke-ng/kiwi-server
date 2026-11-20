"""Who the app is: the one thing an app has to tell the kit."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default)


@dataclass(frozen=True)
class AppInfo:
    app: str  # the command and folder name: "kiwi-fox"
    app_id: str  # the GTK application id: "eu.kiwinetwork.KiwiFox"
    name: str  # as the UI writes it: "Kiwi Fox"
    version: str
    description: str = ""  # one line, as in kiwi.manifest
    homepage: str = ""  # https://github.com/…/<app>
    docs: str = ""  # the page the menu's Help opens; default: the homepage
    glyph: str | None = None  # key in kiwi_icon.GLYPHS, drawn in the app icon
    # The colourway the stock icon is drawn in, when that is not the glyph's
    # own hue (kiwi_icon.APP_HUES). Flat and Night of "the app's own" use it,
    # so the three styles of the stock choice have one colour.
    stock_colorway: str | None = None
    stock_icon: Path | None = None  # the app's stock SVG, for "back to stock"
    default_size: tuple[int, int] = (1040, 840)
    min_size: tuple[int, int] = (360, 420)
    extra: dict = field(default_factory=dict)  # the app's own, the kit ignores it

    def config_dir(self) -> Path:
        return _xdg("XDG_CONFIG_HOME", ".config") / self.app

    def data_dir(self) -> Path:
        return _xdg("XDG_DATA_HOME", ".local/share") / self.app

    def cache_dir(self) -> Path:
        return _xdg("XDG_CACHE_HOME", ".cache") / self.app

    def icons_dir(self) -> Path:
        """The user's own icon files (ui-rules 6)."""
        return self.config_dir() / "icons"

    def hicolor_dir(self) -> Path:
        return _xdg("XDG_DATA_HOME", ".local/share") / "icons" / "hicolor"

    def help_uri(self) -> str:
        return self.docs or self.homepage
