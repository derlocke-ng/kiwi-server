"""The main window remembers its size (ui-rules 7): ~/.config/<app>/window.json."""

from __future__ import annotations

import contextlib
import json

from .info import AppInfo


def saved(info: AppInfo) -> tuple[int, int, bool]:
    """(width, height, maximized) from window.json, else the app's default."""
    try:
        d = json.loads((info.config_dir() / "window.json").read_text())
        width = max(int(d["width"]), info.min_size[0])
        height = max(int(d["height"]), info.min_size[1])
        return width, height, bool(d.get("maximized"))
    except (OSError, ValueError, KeyError, TypeError):
        return info.default_size[0], info.default_size[1], False


def restore(window, info: AppInfo) -> None:
    """Call once, when the window is built."""
    width, height, maximized = saved(info)
    window.set_default_size(width, height)
    window.set_size_request(*info.min_size)
    if maximized:
        window.maximize()
    window.connect("close-request", lambda w: remember(w, info) or False)


def remember(window, info: AppInfo) -> None:
    """GTK 4 tracks the user's resizing in the default size, and keeps the
    un-maximized one while maximized."""
    width, height = window.get_default_size()
    try:
        info.config_dir().mkdir(parents=True, exist_ok=True)
        (info.config_dir() / "window.json").write_text(
            json.dumps({"width": width, "height": height, "maximized": window.is_maximized()})
        )
    except OSError:
        pass


def reset(window, info: AppInfo) -> None:
    with contextlib.suppress(OSError):
        (info.config_dir() / "window.json").unlink()
    window.unmaximize()
    window.set_default_size(*info.default_size)
