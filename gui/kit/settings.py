"""The app's settings file: ~/.config/<app>/settings.json (ui-rules 8).

A missing or broken file means the defaults, never an error. Written to a
temporary name and renamed. Keys the app did not declare are kept, so an
older version of the app does not lose what a newer one wrote.

    store = Store(info, {"repair_on_start": True})
    store["repair_on_start"]            # read
    store.set("repair_on_start", False) # write, at once

The kit's own keys start with "kit." (icon, output pane).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .info import AppInfo

KIT_DEFAULTS = {
    "kit.icon_file": "",  # one of the user's own icon files, wins over colour
    "kit.icon_color": "",  # a kiwi-icons colourway, "" for the stock icon
    "kit.icon_style": "classic",
    "kit.show_output": True,  # open the output pane when a job starts
}


class Store:
    def __init__(self, info: AppInfo, defaults: dict[str, Any] | None = None) -> None:
        self.path: Path = info.config_dir() / "settings.json"
        self.defaults = {**KIT_DEFAULTS, **(defaults or {})}
        self._data: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        try:
            data = json.loads(self.path.read_text())
            self._data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self._data = {}

    def __getitem__(self, key: str) -> Any:
        """The stored value if it has the default's type, else the default."""
        default = self.defaults[key]
        value = self._data.get(key, default)
        return value if type(value) is type(default) else default

    def set(self, key: str, value: Any) -> None:
        if key not in self.defaults:
            raise KeyError(key)
        self._data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True) + "\n")
        tmp.replace(self.path)
