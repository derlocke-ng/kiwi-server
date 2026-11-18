#!/usr/bin/env python3
"""kiwi_icon - build a Kiwi-family app icon from the stock kiwi base.

Pick a glyph (or bring your own 24x24 icon path), a color (hue, hex or a
named colorway), an optional second accent color, and a style.
Pure Python, no dependencies. PNG export uses rsvg-convert or cairosvg
when one of them is installed.

CLI
  python3 kiwi_icon.py --glyph fox --colorway ocean -o kiwi-fox.svg
  python3 kiwi_icon.py --glyph fox --color "#3a7bd5" --accent "#f5a623" -o icon.svg
  python3 kiwi_icon.py --path "M4 12h16M12 4v16" --hue 300 --style flat -o custom.svg
  python3 kiwi_icon.py --glyph fox --colorway forest --install io.github.derlocke_ng.KiwiFox
  python3 kiwi_icon.py --list

Python
  from kiwi_icon import render, palette, COLORWAYS, GLYPHS
  svg = render(glyph="fox", colorway="ocean", style="classic")
  svg = render(glyph="fox", hue=50, accent=240)
  svg = render(path="M4 12h16M12 4v16", color="#7c3aed", style="night")
"""
from __future__ import annotations

import argparse
import html
import math
import os
import shutil
import subprocess
import sys

__version__ = "1.0.0"

STYLES = ("classic", "flat", "night")

# Glyphs live in the 128x128 icon space, inside the 52..76 box at the center.
# s = stroked path, f = filled path, dash = optional stroke-dasharray.
_ENV = "M55 56h18a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H55a2 2 0 0 1-2-2V58a2 2 0 0 1 2-2ZM54 58l10 7 10-7"
GLYPHS: dict[str, dict] = {
    "updater": {"s": "M64 53V68M57.5 61.5L64 68L70.5 61.5M55 75H73", "f": ""},
    "ensconce": {"s": "M55 64.5L61 70.5L73 58.5", "f": ""},
    "killswitch": {"s": "M64 53V63M58.3 57.5A9.5 9.5 0 1 0 69.7 57.5", "f": ""},
    "server": {"s": "M56 54.5H72a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H56a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2ZM56 66.5H72a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H56a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2Z", "f": ""},
    "cli": {"s": "M55 57L62 64L55 71M65 72H74", "f": ""},
    "fox": {"s": "", "f": "M53.5 58.5L55.5 52.5L61.5 58H66.5L72.5 52.5L74.5 58.5L64 76Z"},
    "pentesting": {"s": "M64 51V56M64 72V77M51 64H56M72 64H77M57 64a7 7 0 1 0 14 0a7 7 0 1 0-14 0", "f": "M62.2 64a1.8 1.8 0 1 0 3.6 0a1.8 1.8 0 1 0-3.6 0"},
    "network": {"s": "M64 57L56 71H72Z", "f": "M60.6 57a3.4 3.4 0 1 0 6.8 0a3.4 3.4 0 1 0-6.8 0M52.6 71a3.4 3.4 0 1 0 6.8 0a3.4 3.4 0 1 0-6.8 0M68.6 71a3.4 3.4 0 1 0 6.8 0a3.4 3.4 0 1 0-6.8 0"},
    "proxy": {"s": "M55.5 64H59M69 64h3.5M61 59h6a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2h-6a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2Z", "f": "M50.4 64a2.6 2.6 0 1 0 5.2 0a2.6 2.6 0 1 0-5.2 0M72.4 64a2.6 2.6 0 1 0 5.2 0a2.6 2.6 0 1 0-5.2 0"},
    "os": {"s": "M55 53.5h18a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H55a2 2 0 0 1-2-2v-12a2 2 0 0 1 2-2ZM60 74.5h8", "f": ""},
    "mobile": {"s": "M59 52h10a3 3 0 0 1 3 3v18a3 3 0 0 1-3 3H59a3 3 0 0 1-3-3V55a3 3 0 0 1 3-3ZM62.5 71.5h3", "f": ""},
    "cloud": {"s": "M57 73h14a5.5 5.5 0 0 0 0-11a7.5 7.5 0 0 0-14.2 1.8A4.6 4.6 0 0 0 57 73Z", "f": ""},
    "backup": {"s": "M55 64a9 9 0 1 0 2.6-6.4M55.5 54.5V59H60M64 59.5V64L67.5 66.5", "f": ""},
    "plugin": {"s": "M60 52v6M68 52v6M56 58h16v4a8 8 0 0 1-16 0ZM64 70v6", "f": ""},
    "files": {"s": "M53 58a2 2 0 0 1 2-2h6l3 3h9a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H55a2 2 0 0 1-2-2Z", "f": ""},
    "chat": {"s": "M55 55h18a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-9l-6 5v-5h-3a2 2 0 0 1-2-2V57a2 2 0 0 1 2-2Z", "f": ""},
    "docs": {"s": "M57 52h9l6 6v16a2 2 0 0 1-2 2H57a2 2 0 0 1-2-2V54a2 2 0 0 1 2-2ZM66 52v6h6M59 64h9M59 69h9", "f": ""},
    "monitor": {"s": "M52 64h6l3-8l6 16l3-8h6", "f": ""},
    "vault": {"s": "M54 60a5 5 0 1 0 10 0a5 5 0 1 0-10 0M62.5 63.5L74 75M70 71l3-3M67 68l2.5-2.5", "f": ""},
    "dns": {"s": "M53 64a11 11 0 1 0 22 0a11 11 0 1 0-22 0M53 64h22M64 53c-6 6-6 16 0 22M64 53c6 6 6 16 0 22", "f": ""},
    "mail": {"s": _ENV, "f": ""},
    "mailprivate": {"s": "M58.5 60v-3a5.5 5.5 0 0 1 11 0v3M55 60h18a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H55a2 2 0 0 1-2-2V62a2 2 0 0 1 2-2ZM54 62l10 6 10-6", "f": ""},
    "mailtemp": {"s": _ENV, "f": "", "dash": "4 3"},
    "mailalias": {"s": "M68 64a4 4 0 1 1-8 0a4 4 0 1 1 8 0v2a3 3 0 0 0 6 0v-2a10 10 0 1 0-4 8", "f": ""},
    "mailsend": {"s": "M53 63L75 53L67 75L63 66ZM63 66L75 53", "f": ""},
    "mailinbox": {"s": "M53 66h7l2 3h4l2-3h7M53 66l4-11h14l4 11v7a1 1 0 0 1-1 1H54a1 1 0 0 1-1-1Z", "f": ""},
}

# Each family app's own hue (matches palette/kiwi-palette.json).
APP_HUES = {
    "updater": 132, "ensconce": 245, "killswitch": 22, "server": 85, "cli": 195,
    "fox": 50, "pentesting": 345, "network": 295, "proxy": 142, "os": 315,
    "mobile": 70, "cloud": 220, "backup": 165, "plugin": 270, "files": 35,
    "chat": 180, "docs": 232, "monitor": 150, "vault": 330, "dns": 283,
    "mail": 105, "mailprivate": 105, "mailtemp": 105, "mailalias": 105,
    "mailsend": 105, "mailinbox": 105,
}

# Named colorways users can pick from. hue = main color, tone = saturation
# (1 = family default, ~0.1 = near grey), accent = optional second hue that
# tints the core disc and the glyph.
COLORWAYS: dict[str, dict] = {
    "ember":    {"label": "Ember",    "hue": 50,  "tone": 1.0},
    "crimson":  {"label": "Crimson",  "hue": 22,  "tone": 1.0},
    "rose":     {"label": "Rose",     "hue": 355, "tone": 1.0},
    "orchid":   {"label": "Orchid",   "hue": 318, "tone": 1.0},
    "violet":   {"label": "Violet",   "hue": 292, "tone": 1.0},
    "indigo":   {"label": "Indigo",   "hue": 268, "tone": 1.0},
    "ocean":    {"label": "Ocean",    "hue": 240, "tone": 1.0},
    "teal":     {"label": "Teal",     "hue": 192, "tone": 1.0},
    "forest":   {"label": "Forest",   "hue": 150, "tone": 1.0},
    "kiwi":     {"label": "Kiwi",     "hue": 128, "tone": 1.0},
    "honey":    {"label": "Honey",    "hue": 82,  "tone": 1.0},
    "graphite": {"label": "Graphite", "hue": 255, "tone": 0.1},
    "duo-ember-ocean":   {"label": "Ember + Ocean",   "hue": 50,  "tone": 1.0,  "accent": 240},
    "duo-violet-honey":  {"label": "Violet + Honey",  "hue": 292, "tone": 1.0,  "accent": 82},
    "duo-graphite-ember": {"label": "Graphite + Ember", "hue": 255, "tone": 0.1, "accent": 50},
}

# Lightness / chroma per role (OKLCH). Same numbers as the family palette.
ROLES = {
    "rim": (0.52, 0.13), "flesh": (0.80, 0.14), "core": (0.97, 0.03),
    "ink": (0.32, 0.09), "deep": (0.27, 0.06), "text": (0.45, 0.12),
}
ACCENT_ROLES = {"core": (0.95, 0.05), "glyph": (0.42, 0.15), "night_glyph": (0.82, 0.14)}


# ---------------------------------------------------------------- color math
def oklch_to_hex(L: float, C: float, h: float) -> str:
    a = C * math.cos(math.radians(h))
    b = C * math.sin(math.radians(h))
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    lin = (
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    )
    out = []
    for x in lin:
        x = max(0.0, min(1.0, x))
        x = 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055
        out.append(f"{round(x * 255):02X}")
    return "#" + "".join(out)


def hex_to_oklch(value: str) -> tuple[float, float, float]:
    v = value.strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) != 6:
        raise ValueError(f"not a hex color: {value!r}")
    rgb = []
    for i in (0, 2, 4):
        c = int(v[i:i + 2], 16) / 255
        rgb.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = rgb
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (math.copysign(abs(x) ** (1 / 3), x) for x in (l, m, s))
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    A = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    B = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    C = math.hypot(A, B)
    h = math.degrees(math.atan2(B, A)) % 360
    return L, C, h


def hex_to_hue_tone(value: str) -> tuple[float, float]:
    """A picked hex color -> (hue, tone). Muted picks give a muted icon."""
    _, C, h = hex_to_oklch(value)
    return round(h, 1), round(max(0.0, min(1.15, C / 0.12)), 3)


def palette(hue: float = 50, tone: float = 1.0, accent: float | None = None,
            accent_tone: float = 1.0, overrides: dict | None = None) -> dict:
    """All role colors as hex. Roles: rim flesh core ink deep text glyph night_glyph."""
    p = {role: oklch_to_hex(L, C * tone, hue) for role, (L, C) in ROLES.items()}
    p["glyph"] = p["ink"]
    p["night_glyph"] = p["flesh"]
    if accent is not None:
        for role, (L, C) in ACCENT_ROLES.items():
            p[role] = oklch_to_hex(L, C * accent_tone, accent)
    if overrides:
        unknown = set(overrides) - set(p)
        if unknown:
            raise ValueError(f"unknown color role(s): {', '.join(sorted(unknown))}")
        p.update(overrides)
    return p


# ---------------------------------------------------------------- rendering
_SEEDS_CLASSIC = [(64, 33, 0), (90.8, 48.5, 60), (90.8, 79.5, 120), (64, 95, 0), (37.2, 79.5, 60), (37.2, 48.5, 120)]
_SEEDS_NIGHT = [(64, 31, 0), (92.6, 47.5, 60), (92.6, 80.5, 120), (64, 97, 0), (35.4, 80.5, 60), (35.4, 47.5, 120)]
_SEEDS_FLAT = [(64, 22), (100.4, 43), (100.4, 85), (64, 106), (27.6, 85), (27.6, 43)]


def _ellipses(seeds, rx, ry):
    out = []
    for x, y, rot in seeds:
        t = f' transform="rotate({rot} {x} {y})"' if rot else ""
        out.append(f'<ellipse cx="{x}" cy="{y}" rx="{rx}" ry="{ry}"{t}/>')
    return "".join(out)


def _glyph_markup(color: str, stroke_w: float, glyph: dict | None, path: str | None, path_fill: bool) -> str:
    parts = []
    if glyph:
        dash = f' stroke-dasharray="{glyph["dash"]}"' if glyph.get("dash") else ""
        if glyph.get("s"):
            parts.append(f'<path d="{glyph["s"]}" fill="none" stroke="{color}" stroke-width="{stroke_w}" '
                         f'stroke-linecap="round" stroke-linejoin="round"{dash}/>')
        if glyph.get("f"):
            parts.append(f'<path d="{glyph["f"]}" fill="{color}"/>')
    if path:
        # Custom icon on a 24x24 grid (Lucide / Tabler / Material outline paths).
        if path_fill:
            parts.append(f'<path transform="translate(52 52)" d="{path}" fill="{color}"/>')
        else:
            parts.append(f'<path transform="translate(52 52)" d="{path}" fill="none" stroke="{color}" '
                         f'stroke-width="{stroke_w}" stroke-linecap="round" stroke-linejoin="round"/>')
    return "".join(parts)


def render(glyph: str | None = "fox", *, style: str = "classic", hue: float | None = None,
           tone: float | None = None, accent: float | None = None, accent_tone: float = 1.0,
           color: str | None = None, accent_color: str | None = None, colorway: str | None = None,
           colors: dict | None = None, path: str | None = None, path_fill: bool = False,
           size: int | None = 128, title: str | None = None) -> str:
    """Return a complete SVG string.

    glyph       key from GLYPHS, or None for an empty core
    path        your own icon as a 24x24 path `d` (drawn instead of / on top of glyph)
    colorway    name from COLORWAYS (sets hue/tone/accent)
    hue/tone    main color as OKLCH hue (0-360) and saturation scale
    color       main color as hex instead of hue/tone
    accent      second hue for core + glyph; accent_color = same as hex
    colors      explicit hex per role, wins over everything
    """
    if style not in STYLES:
        raise ValueError(f"style must be one of {STYLES}")
    g = None
    if glyph:
        if glyph not in GLYPHS:
            raise ValueError(f"unknown glyph {glyph!r}; see --list")
        g = GLYPHS[glyph]

    h, t, a = APP_HUES.get(glyph or "", 50), 1.0, None
    if colorway:
        if colorway not in COLORWAYS:
            raise ValueError(f"unknown colorway {colorway!r}; see --list")
        cw = COLORWAYS[colorway]
        h, t, a = cw["hue"], cw.get("tone", 1.0), cw.get("accent")
    if color:
        h, t = hex_to_hue_tone(color)
    if hue is not None:
        h = hue
    if tone is not None:
        t = tone
    if accent_color:
        a, accent_tone = hex_to_hue_tone(accent_color)
    if accent is not None:
        a = accent
    c = palette(h, t, a, accent_tone, colors)

    dim = f' width="{size}" height="{size}"' if size else ""
    label = html.escape(title or "kiwi icon")
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128"{dim} role="img" '
            f'aria-label="{label}"><title>{label}</title>')

    if style == "classic":
        body = (f'<circle cx="64" cy="64" r="58" fill="{c["rim"]}"/>'
                f'<circle cx="64" cy="64" r="51" fill="{c["flesh"]}"/>'
                f'<g fill="{c["ink"]}">{_ellipses(_SEEDS_CLASSIC, 4, 5.6)}</g>'
                f'<circle cx="64" cy="64" r="21" fill="{c["core"]}"/>'
                f'<g transform="translate(64 64) scale(0.95) translate(-64 -64)">'
                f'{_glyph_markup(c["glyph"], 3.6, g, path, path_fill)}</g>')
    elif style == "flat":
        dots = "".join(f'<circle cx="{x}" cy="{y}" r="3.6"/>' for x, y in _SEEDS_FLAT)
        body = (f'<circle cx="64" cy="64" r="58" fill="{c["flesh"]}"/>'
                f'<g fill="{c["ink"]}">{dots}</g>'
                f'<circle cx="64" cy="64" r="27" fill="{c["core"]}"/>'
                f'<g transform="translate(64 64) scale(1.3) translate(-64 -64)">'
                f'{_glyph_markup(c["glyph"], 3.2, g, path, path_fill)}</g>')
    else:  # night
        body = (f'<circle cx="64" cy="64" r="58" fill="{c["deep"]}"/>'
                f'<circle cx="64" cy="64" r="49" fill="none" stroke="{c["flesh"]}" stroke-width="4"/>'
                f'<g fill="{c["flesh"]}">{_ellipses(_SEEDS_NIGHT, 3.4, 5)}</g>'
                f'<g transform="translate(64 64) scale(1.2) translate(-64 -64)">'
                f'{_glyph_markup(c["night_glyph"], 3.4, g, path, path_fill)}</g>')
    return head + body + "</svg>\n"


# ---------------------------------------------------------------- outputs
def to_png(svg: str, out: str, px: int) -> None:
    if shutil.which("rsvg-convert"):
        subprocess.run(["rsvg-convert", "-w", str(px), "-h", str(px), "-o", out],
                       input=svg.encode(), check=True)
        return
    try:
        import cairosvg  # type: ignore
    except ImportError:
        raise SystemExit("PNG export needs rsvg-convert (librsvg) or `pip install cairosvg`")
    cairosvg.svg2png(bytestring=svg.encode(), write_to=out, output_width=px, output_height=px)


def install(svg: str, app_id: str, prefix: str | None = None) -> str:
    """Install as a hicolor app icon so GNOME / KDE / GTK pick it up."""
    base = prefix or os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "icons", "hicolor")
    target_dir = os.path.join(base, "scalable", "apps")
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, f"{app_id}.svg")
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(svg)
    if shutil.which("gtk-update-icon-cache"):
        subprocess.run(["gtk-update-icon-cache", "-f", "-t", base], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return target


def _main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="kiwi_icon", description="Build a Kiwi-family icon from the stock kiwi base.")
    p.add_argument("--glyph", default="fox", help="glyph key, or 'none' (see --list)")
    p.add_argument("--path", help="your own icon: SVG path d on a 24x24 grid")
    p.add_argument("--path-fill", action="store_true", help="fill the custom path instead of stroking it")
    p.add_argument("--style", choices=STYLES, default="classic")
    p.add_argument("--colorway", help="named colorway (see --list)")
    p.add_argument("--hue", type=float, help="main hue 0-360 (OKLCH)")
    p.add_argument("--tone", type=float, help="saturation scale, 1 = default, 0.1 = near grey")
    p.add_argument("--color", help="main color as hex, e.g. '#3a7bd5'")
    p.add_argument("--accent", type=float, help="second hue for core + glyph")
    p.add_argument("--accent-color", help="second color as hex")
    p.add_argument("--set", action="append", default=[], metavar="ROLE=#HEX",
                   help="force one role color (rim flesh core ink deep glyph night_glyph); repeatable")
    p.add_argument("--size", type=int, default=128, help="width/height attribute of the SVG")
    p.add_argument("--title", help="accessible name inside the SVG")
    p.add_argument("-o", "--out", help="write SVG here (default: stdout)")
    p.add_argument("--png", type=int, metavar="PX", help="also write a PNG of this size next to --out")
    p.add_argument("--install", metavar="APP_ID", help="install into ~/.local/share/icons/hicolor as APP_ID.svg")
    p.add_argument("--palette", action="store_true", help="print the resolved role colors as JSON and exit")
    p.add_argument("--list", action="store_true", help="list glyphs and colorways")
    a = p.parse_args(argv)

    if a.list:
        print("glyphs:   ", " ".join(GLYPHS))
        print("styles:   ", " ".join(STYLES))
        print("colorways:")
        for k, v in COLORWAYS.items():
            extra = f"  accent {v['accent']}" if "accent" in v else ""
            print(f"  {k:<20} hue {v['hue']:<5} tone {v.get('tone', 1.0)}{extra}")
        return 0

    overrides = {}
    for item in a.set:
        role, _, val = item.partition("=")
        overrides[role.strip()] = val.strip()
    glyph = None if a.glyph in ("none", "") else a.glyph
    kwargs = dict(style=a.style, hue=a.hue, tone=a.tone, accent=a.accent, color=a.color,
                  accent_color=a.accent_color, colorway=a.colorway, colors=overrides or None,
                  path=a.path, path_fill=a.path_fill, size=a.size, title=a.title)
    svg = render(glyph, **kwargs)

    if a.palette:
        import json
        h, t, acc, at = APP_HUES.get(glyph or "", 50), 1.0, None, 1.0
        if a.colorway:
            cw = COLORWAYS[a.colorway]
            h, t, acc = cw["hue"], cw.get("tone", 1.0), cw.get("accent")
        if a.color:
            h, t = hex_to_hue_tone(a.color)
        h = a.hue if a.hue is not None else h
        t = a.tone if a.tone is not None else t
        if a.accent_color:
            acc, at = hex_to_hue_tone(a.accent_color)
        acc = a.accent if a.accent is not None else acc
        print(json.dumps(palette(h, t, acc, at, overrides or None), indent=2))
        return 0

    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(svg)
        if a.png:
            to_png(svg, os.path.splitext(a.out)[0] + f"-{a.png}.png", a.png)
    elif not a.install:
        sys.stdout.write(svg)
    if a.install:
        print(install(svg, a.install), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
