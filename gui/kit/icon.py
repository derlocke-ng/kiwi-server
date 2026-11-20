"""The app's own icon, chosen by the user (ui-rules 6).

The stock icon, a kiwi-icons colourway in one of three styles, or a PNG or
SVG of the user's own from ~/.config/<app>/icons/. The result is written
where the desktop entry finds it: the user's hicolor theme, under the
application id. The app's installer must leave that file alone when a choice
is stored (tools in the template's install.sh do).
"""

from __future__ import annotations

import base64
import contextlib
import shutil
import subprocess

from . import kiwi_icon
from .info import AppInfo
from .settings import Store

STYLES = [("classic", "Classic"), ("flat", "Flat"), ("night", "Night")]


def colors(info: AppInfo) -> list[tuple[str, str]]:
    """(key, label): the stock icon first, then every kiwi-icons colourway."""
    return [("", f"{info.name}'s own")] + [(k, v["label"]) for k, v in kiwi_icon.COLORWAYS.items()]


def own_files(info: AppInfo) -> list[str]:
    try:
        return sorted(
            p.name
            for p in info.icons_dir().iterdir()
            if p.is_file() and p.suffix.lower() in (".svg", ".png")
        )
    except OSError:
        return []


def target(info: AppInfo):
    return info.hicolor_dir() / "scalable" / "apps" / f"{info.app_id}.svg"


def is_custom(store: Store) -> bool:
    return (
        bool(store["kit.icon_file"] or store["kit.icon_color"])
        or store["kit.icon_style"] != "classic"
    )


def apply(info: AppInfo, store: Store) -> str | None:
    """Write the icon the settings ask for. Returns an error text, or None."""
    own, color, style = store["kit.icon_file"], store["kit.icon_color"], store["kit.icon_style"]
    out = target(info)
    try:
        if own:
            src = info.icons_dir() / own.replace("/", "")
            if src.suffix.lower() == ".png":
                # a PNG under a .svg name would not load; wrap it instead
                data = base64.b64encode(src.read_bytes()).decode()
                svg = (
                    '<svg xmlns="http://www.w3.org/2000/svg" '
                    'xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 128 128" '
                    'width="128" height="128"><image width="128" height="128" '
                    f'xlink:href="data:image/png;base64,{data}"/></svg>\n'
                )
            else:
                svg = src.read_text()
        elif color or style != "classic":
            svg = kiwi_icon.render(
                info.glyph, colorway=color or info.stock_colorway, style=style, title=info.name
            )
        elif info.stock_icon and info.stock_icon.is_file():
            svg = info.stock_icon.read_text()
        else:
            return f"the stock icon is missing — reinstall {info.app}"
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        tmp.write_text(svg)
        tmp.replace(out)
    except (OSError, ValueError, KeyError) as exc:
        return str(exc)
    if shutil.which("gtk-update-icon-cache"):
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["gtk-update-icon-cache", "-qtf", str(info.hicolor_dir())],
                timeout=20,
                capture_output=True,
                stdin=subprocess.DEVNULL,
            )
    return None
