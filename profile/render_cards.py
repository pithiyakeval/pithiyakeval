#!/usr/bin/env python3
"""Render the animated SVG cards for the GitHub profile README.

    python render_cards.py            # animated cards  -> cards/*.svg
    STATIC=1 python render_cards.py   # final frame only, for screenshots

Reads data/contributions.json (written daily by .github/workflows/profile-cards.yml),
embeds subset WOFF fonts from fonts/, and rewrites the block between
<!-- cards:start --> and <!-- cards:end --> in README.md so the README always
lists the current pinned repos.

The cards are shown through <img>, so they only use what works there: one inline
<style> with CSS keyframes, SMIL <animate*> elements and base64 data-URI fonts.
No scripts, no external references, no hover.
"""
from __future__ import annotations

import base64
import datetime as dt
import html
import io
import json
import os
import sys
import tempfile
from collections import OrderedDict, defaultdict
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "cards"
DATA = Path(os.environ.get("DATA", ROOT / "data" / "contributions.json"))
STATIC = os.environ.get("STATIC") == "1"

# --------------------------------------------------------------------------- #
# Everything you'd want to change lives here.
# --------------------------------------------------------------------------- #
PROFILE = {
    "login": "pithiyakeval",
    "name": "Pithiya Kevalkumar",       # overridden by the GitHub name if set
    "kicker": "Final-year CS student · Open to work",
    "tagline": "Python & full-stack developer — Django, Flask, React, Node.js.",
    "monogram": "PK",                   # shown when there is no portrait
}
# Optional portrait: the first of these that exists is embedded in the hero.
PORTRAITS = ["assets/portrait.jpg", "assets/portrait.jpeg", "assets/portrait.png"]

GH = f"https://github.com/{PROFILE['login']}"
# Three linked tiles. `value` may use {repos} for the public repo count.
TILES = [
    {"label": "Repositories", "value": "{repos}", "sub": "public projects",
     "href": f"{GH}?tab=repositories"},
    {"label": "Stack", "value": "Python", "sub": "Django · Flask · React",
     "href": f"{GH}?tab=repositories&language=python"},
    # Swap this href for your email (mailto:) or LinkedIn.
    {"label": "Status", "value": "Open to work", "sub": "Dev · Data Analyst roles",
     "href": GH},
]

THEME = {
    "bg": "#0E1014",       # card fill
    "line": "#232832",     # hairlines, card stroke
    "empty": "#1B1F27",    # empty contribution cell
    "text": "#ECE9E4",     # primary ink
    "muted": "#8D929C",    # secondary ink
    "brand": "#FFB547",    # accent + contribution colour
    "radius": 16,
}

# key -> (file in fonts/, weight to instance a variable font at)
FONTS = {
    "display": ("SpaceGrotesk[wght].ttf", 700),
    "medium": ("SpaceGrotesk[wght].ttf", 500),
    "body": ("SpaceGrotesk[wght].ttf", 400),
    "mono": ("JetBrainsMono[wght].ttf", 400),
    "monob": ("JetBrainsMono[wght].ttf", 600),
}

W = 840            # full-width card width (README column is ~830px)
TILE_W = 272       # tiles are shown at 32.5% width
EASE = "cubic-bezier(.2,.7,.2,1)"

# --------------------------------------------------------------------------- #
# Fonts: instance the variable font, measure text, subset to WOFF.
# --------------------------------------------------------------------------- #
class Fonts:
    def __init__(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="cards-fonts-"))
        self.loaded: dict[str, tuple[Path, TTFont]] = {}

    def get(self, key: str) -> tuple[Path, TTFont]:
        if key not in self.loaded:
            file, weight = FONTS[key]
            font = TTFont(ROOT / "fonts" / file)
            if "fvar" in font:
                font = instancer.instantiateVariableFont(font, {"wght": weight})
            path = self.tmp / f"{key}.ttf"
            font.save(path)
            self.loaded[key] = (path, TTFont(path))
        return self.loaded[key]

    def width(self, key: str, text: str, size: float, spacing: float = 0) -> float:
        _, font = self.get(key)
        cmap, hmtx = font.getBestCmap(), font["hmtx"]
        upm = font["head"].unitsPerEm
        adv = sum(hmtx[cmap.get(ord(c), ".notdef")][0] for c in text)
        return adv * size / upm + spacing * max(len(text) - 1, 0)

    def fit(self, key: str, text: str, size: float, max_w: float, min_size: float) -> float:
        while size > min_size and self.width(key, text, size) > max_w:
            size -= 0.5
        return size

    def wrap(self, key: str, text: str, size: float, max_w: float, lines: int) -> list[str]:
        out, cur = [], ""
        for word in text.split():
            trial = f"{cur} {word}".strip()
            if self.width(key, trial, size) <= max_w or not cur:
                cur = trial
            else:
                out.append(cur)
                cur = word
        out.append(cur)
        if len(out) > lines:
            out = out[:lines]
            last = out[-1]
            while last and self.width(key, last + "…", size) > max_w:
                last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
            out[-1] = last.rstrip(" ,.;:—-") + "…"
        return out

    def woff_b64(self, key: str, chars: set[str]) -> str:
        path, font = self.get(key)
        cmap = font.getBestCmap()
        missing = sorted(c for c in chars if ord(c) not in cmap and c.strip())
        if missing:
            print(f"warning: {key} has no glyph for {''.join(missing)!r}", file=sys.stderr)
        out = self.tmp / f"{key}-{len(chars)}-{abs(hash(frozenset(chars)))}.woff"
        unicodes = ",".join(f"U+{ord(c):04X}" for c in sorted(chars | {" "}))
        subset.main([  # this is pyftsubset
            str(path), f"--unicodes={unicodes}", "--flavor=woff",
            f"--output-file={out}", "--layout-features=kern,liga,tnum",
            "--no-hinting", "--desubroutinize", "--name-IDs=1,2", "--notdef-outline",
        ])
        return base64.b64encode(out.read_bytes()).decode()


FONT = Fonts()

# --------------------------------------------------------------------------- #
# Card: collects markup + the glyphs each font needs, then emits one SVG.
# --------------------------------------------------------------------------- #
KEYFRAMES = """
@keyframes rise{from{opacity:0;transform:translateY(12px)}}
@keyframes fade{from{opacity:0}}
@keyframes slide{from{opacity:0;transform:translateX(-16px)}}
@keyframes growy{from{transform:scaleY(0)}}
@keyframes growx{from{transform:scaleX(0)}}
@keyframes pop{from{opacity:0;transform:scale(.3)}}
.a{animation-duration:.8s;animation-timing-function:%s;animation-fill-mode:both}
.rise{animation-name:rise}.fade{animation-name:fade}.slide{animation-name:slide}
.growy{animation-name:growy}.growx{animation-name:growx}.pop{animation-name:pop}
.growy,.growx,.pop,.rise{transform-box:fill-box}
.growy{transform-origin:50%% 100%%}.growx{transform-origin:0 50%%}.pop{transform-origin:50%% 50%%}
.down{transform-origin:50%% 0}
.still{display:none}
@media (prefers-reduced-motion:reduce){.a{animation:none!important}.smil{display:none}.still{display:inline}}
""" % EASE


def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


class Card:
    def __init__(self, name: str, w: int, h: int, title: str, delay: float) -> None:
        self.name, self.w, self.h, self.title, self.delay = name, w, h, title, delay
        self.parts: list[str] = []
        self.glyphs: dict[str, set[str]] = defaultdict(set)

    # animation attributes ------------------------------------------------- #
    def anim(self, kind: str, at: float, dur: float | None = None, cls: str = "") -> str:
        """class/style attributes for a CSS-animated element (nothing when STATIC)."""
        if STATIC:
            return f' class="{cls}"' if cls else ""
        style = f"animation-delay:{self.delay + at:.2f}s"
        if dur:
            style += f";animation-duration:{dur:.2f}s"
        return f' class="{("a " + kind + " " + cls).strip()}" style="{style}"'

    def smil(self, moving: str, still: str = "") -> None:
        """Decorative SMIL. CSS can't pause SMIL, so the moving copy is hidden
        under prefers-reduced-motion and a still copy shown instead."""
        if STATIC:
            self.parts.append(still)
        else:
            self.parts.append(f'<g class="smil">{moving}</g>')
            if still:
                self.parts.append(f'<g class="still">{still}</g>')

    def begin(self, at: float) -> str:
        return f"{self.delay + at:.2f}s"

    # drawing ------------------------------------------------------------- #
    def add(self, *markup: str) -> None:
        self.parts.extend(markup)

    def text(self, x: float, y: float, s: str, font: str, size: float, fill: str,
             anchor: str = "start", spacing: float = 0, anim: tuple | None = None,
             upper: bool = False) -> str:
        s = s.upper() if upper else s
        self.glyphs[font].update(s)
        a = self.anim(*anim) if anim else ""
        ls = f' letter-spacing="{spacing}"' if spacing else ""
        return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{font}" font-size="{size:g}" '
                f'fill="{fill}" text-anchor="{anchor}"{ls}{a}>{esc(s)}</text>')

    def frame(self) -> str:
        r = THEME["radius"]
        return (f'<rect x=".5" y=".5" width="{self.w - 1}" height="{self.h - 1}" rx="{r}" '
                f'fill="{THEME["bg"]}" stroke="{THEME["line"]}"/>')

    def render(self) -> str:
        faces = "".join(
            f"@font-face{{font-family:'{k}';src:url(data:font/woff;base64,"
            f"{FONT.woff_b64(k, chars)}) format('woff')}}"
            for k, chars in sorted(self.glyphs.items()) if chars)
        css = faces + ("" if STATIC else KEYFRAMES)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}" role="img" aria-labelledby="t">'
            f'<title id="t">{esc(self.title)}</title><style>{css}</style>'
            + self.frame() + "".join(self.parts) + "</svg>\n")

    def save(self) -> None:
        (OUT / f"{self.name}.svg").write_text(self.render(), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
def load() -> dict:
    raw = json.loads(DATA.read_text(encoding="utf-8"))
    if raw.get("_sample"):
        print("note: data/contributions.json is sample data; the workflow replaces it",
              file=sys.stderr)
    if raw.get("errors"):
        sys.exit(f"GraphQL errors in {DATA}: {raw['errors']}")
    user = raw["data"]["user"]
    cal = user["contributionsCollection"]["contributionCalendar"]
    days = [(dt.date.fromisoformat(d["date"]), d["contributionCount"])
            for w in cal["weeks"] for d in w["contributionDays"]]
    days.sort()
    return {
        "name": user.get("name") or PROFILE["name"],
        "repos": (user.get("repositories") or {}).get("totalCount", 0),
        "total": cal["totalContributions"],
        "weeks": [[(dt.date.fromisoformat(d["date"]), d["contributionCount"])
                   for d in w["contributionDays"]] for w in cal["weeks"]],
        "days": days,
        "pinned": [n for n in (user.get("pinnedItems") or {}).get("nodes", []) if n],
    }


def stats(days: list[tuple[dt.date, int]]) -> dict:
    days = days[-365:]
    counts = [c for _, c in days]
    # Current streak: may end today or, if today is still empty, yesterday.
    i = len(days) - 1
    if i >= 0 and counts[i] == 0:
        i -= 1
    cur = 0
    while i >= 0 and counts[i] > 0:
        cur, i = cur + 1, i - 1
    best = run = 0
    for c in counts:
        run = run + 1 if c else 0
        best = max(best, run)
    busiest = max(days, key=lambda d: d[1]) if days else (dt.date.today(), 0)
    months: OrderedDict[str, int] = OrderedDict()
    for d, c in days:
        months[d.strftime("%Y-%m")] = months.get(d.strftime("%Y-%m"), 0) + c
    keys = list(months)[-12:]
    return {
        "streak": cur, "best": best,
        "active": sum(1 for c in counts if c), "span": len(days),
        "busiest": busiest,
        "months": [(dt.date.fromisoformat(k + "-01"), months[k]) for k in keys],
    }


def mix(a: str, b: str, t: float) -> str:
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(pa, pb))


def n(v: int) -> str:
    return f"{v:,}"


def md(d: dt.date) -> str:
    return f"{d.strftime('%b')} {d.day}"


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #
def hero(data: dict, delay: float) -> Card:
    T = THEME
    portrait = next((ROOT / p for p in PORTRAITS if (ROOT / p).exists()), None)
    name_words = data["name"].split()
    col_w = 520
    # One line if it fits at a good size, otherwise one word per line.
    one_line = FONT.width("display", data["name"], 54) <= col_w
    lines = [data["name"]] if one_line else name_words
    size = 54 if one_line else min(FONT.fit("display", w, 62, col_w, 36) for w in lines)
    lh = size * 1.02
    h = int(108 + lh * len(lines) + 92)
    c = Card("hero", W, h, f"{data['name']} — {PROFILE['tagline']}", delay)

    # dotted field behind the portrait
    c.add('<defs><pattern id="dots" width="18" height="18" patternUnits="userSpaceOnUse">'
          f'<circle cx="2" cy="2" r="1.2" fill="{T["line"]}"/></pattern>'
          f'<clipPath id="pc"><circle cx="0" cy="0" r="84"/></clipPath></defs>',
          f'<rect x="560" y="1" width="279" height="{h - 2}" fill="url(#dots)"'
          f'{c.anim("fade", 0.0, 1.6)}/>')

    # kicker with a live dot
    y = 62
    dot = f'<circle cx="50" cy="{y - 4.5}" r="4" fill="{T["brand"]}"/>'
    c.smil(dot + f'<circle cx="50" cy="{y - 4.5}" r="4" fill="none" stroke="{T["brand"]}">'
           f'<animate attributeName="r" values="4;11" dur="2s" begin="{c.begin(0.4)}" repeatCount="indefinite"/>'
           f'<animate attributeName="opacity" values=".8;0" dur="2s" begin="{c.begin(0.4)}" repeatCount="indefinite"/>'
           '</circle>', dot)
    c.add(c.text(64, y, PROFILE["kicker"], "monob", 12.5, T["muted"], spacing=1.6,
                 upper=True, anim=("fade", 0.05)))

    # name, one line per row, each rising in turn
    y = 108 + size * 0.78
    for i, line in enumerate(lines):
        c.add(c.text(44, y, line, "display", size, T["text"], spacing=-1,
                     anim=("rise", 0.15 + i * 0.14)))
        y += lh
    y += -lh + 26
    c.add(f'<rect x="46" y="{y:.1f}" width="56" height="4" rx="2" fill="{T["brand"]}"'
          f'{c.anim("growx", 0.15 + len(lines) * 0.14 + 0.1, 0.7)}/>')
    ts = FONT.fit("medium", PROFILE["tagline"], 19, col_w - 8, 14)
    c.add(c.text(44, y + 46, PROFILE["tagline"], "medium", ts, T["muted"],
                 anim=("rise", 0.25 + len(lines) * 0.14 + 0.15)))

    # portrait or monogram, inside a slowly turning dashed ring
    cx, cy = 700, h / 2
    ring = (f'<circle r="100" fill="none" stroke="{T["brand"]}" stroke-opacity=".55" '
            'stroke-width="1.5" stroke-dasharray="2 9" stroke-linecap="round"/>')
    c.add(f'<g transform="translate({cx},{cy:.1f})"><g{c.anim("pop", 0.35, 0.9)}>')
    c.smil(f'<g>{ring}<animateTransform attributeName="transform" type="rotate" '
           f'from="0" to="360" dur="40s" begin="{c.begin(0.4)}" repeatCount="indefinite"/></g>',
           ring)
    if portrait:
        c.add(f'<circle r="86" fill="{T["line"]}"/>'
              f'<image href="{image_uri(portrait)}" x="-84" y="-84" width="168" height="168" '
              'preserveAspectRatio="xMidYMid slice" clip-path="url(#pc)"/>')
    else:
        c.add(f'<circle r="84" fill="{T["empty"]}" stroke="{T["line"]}"/>',
              c.text(0, 22, PROFILE["monogram"], "display", 64, T["brand"], "middle", -2))
    c.add("</g></g>")
    return c


def image_uri(path: Path) -> str:
    raw, mime = path.read_bytes(), "image/png" if path.suffix == ".png" else "image/jpeg"
    try:  # keep the hero light: shrink big photos if Pillow is around
        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        im.thumbnail((360, 360))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        raw, mime = buf.getvalue(), "image/jpeg"
    except ImportError:
        pass
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def arrow(x: float, y: float, color: str) -> str:
    return (f'<path d="M{x},{y + 10}L{x + 10},{y}M{x + 3},{y}H{x + 10}V{y + 7}" fill="none" '
            f'stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>')


def tile(i: int, spec: dict, data: dict, delay: float) -> Card:
    T = THEME
    value = spec["value"].format(repos=n(data["repos"]))
    c = Card(f"tile-{i + 1}", TILE_W, 150, f"{spec['label']}: {value} — {spec['sub']}", delay)
    c.add(f'<g{c.anim("rise", 0)}>',
          c.text(24, 40, spec["label"], "monob", 11.5, T["muted"], spacing=1.5, upper=True),
          arrow(TILE_W - 36, 28, T["muted"]))
    vs = FONT.fit("display", value, 32, TILE_W - 48, 20)
    c.add(c.text(24, 92, value, "display", vs, T["text"], spacing=-0.5),
          c.text(24, 120, spec["sub"], "mono", 12.5, T["muted"]), "</g>",
          f'<rect x="24" y="134" width="28" height="3" rx="1.5" fill="{T["brand"]}"'
          f'{c.anim("growx", 0.3, 0.6)}/>')
    return c


def bar_path(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    r = min(r, h, w / 2)
    return (f"M{x:.1f},{y + h:.1f}V{y + r:.1f}Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f}"
            f"H{x + w - r:.1f}Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f}V{y + h:.1f}Z")


def activity(data: dict, st: dict, delay: float) -> Card:
    T = THEME
    months = st["months"]
    title = ("Contributions per month: " + ", ".join(f"{d.strftime('%b %Y')} {v}" for d, v in months)
             + f". Current streak {st['streak']} days, {st['active']} active days, "
             f"busiest day {md(st['busiest'][0])} with {st['busiest'][1]}.")
    c = Card("activity", W, 340, title, delay)
    c.add(c.text(40, 48, "Contributions · last 12 months", "monob", 11.5, T["muted"],
                 spacing=1.5, upper=True, anim=("fade", 0)))
    year_total = sum(v for _, v in months)
    c.add(c.text(40, 100, n(year_total), "display", 46, T["text"], spacing=-1, anim=("rise", 0.05)))

    # three stats, right side
    b = st["busiest"]
    cols = [
        ("Streak", f"{st['streak']} day{'s' * (st['streak'] != 1)}", f"best {st['best']}"),
        ("Active days", n(st["active"]), f"of {n(st['span'])}"),
        ("Busiest day", md(b[0]) if b[1] else "—", f"{b[1]} contributions"),
    ]
    for i, (label, val, sub) in enumerate(cols):
        x = 404 + i * 136
        at = 0.15 + i * 0.1
        c.add(f'<g{c.anim("rise", at)}>',
              c.text(x, 48, label, "monob", 11.5, T["muted"], spacing=1.5, upper=True),
              c.text(x, 84, val, "display", 26, T["text"], spacing=-0.5),
              c.text(x, 106, sub, "mono", 12, T["muted"]), "</g>")
        if i:
            c.add(f'<line x1="{x - 18}" y1="34" x2="{x - 18}" y2="108" stroke="{T["line"]}"'
                  f'{c.anim("fade", at)}/>')
    if st["streak"]:
        dot = f'<circle cx="{404 + FONT.width("monob", "STREAK", 11.5, 1.5) + 10:.1f}" cy="44" r="3" fill="{T["brand"]}"/>'
        c.add(f'<g{c.anim("fade", 0.15)}>')
        c.smil(dot.replace("/>", f'><animate attributeName="opacity" values="1;.25;1" dur="1.6s" '
                                 f'begin="{c.begin(0.9)}" repeatCount="indefinite"/></circle>'), dot)
        c.add("</g>")

    # bars
    x0, x1, top, base = 40, 800, 152, 290
    slot = (x1 - x0) / len(months)
    bw = min(30, slot * 0.46)
    peak = max((v for _, v in months), default=0) or 1
    c.add(f'<line x1="{x0}" y1="{base + .5}" x2="{x1}" y2="{base + .5}" stroke="{T["line"]}"/>')
    imax = max(range(len(months)), key=lambda k: months[k][1]) if months else 0
    for k, (d, v) in enumerate(months):
        cx = x0 + slot * (k + 0.5)
        h = (base - top) * v / peak
        at = 0.35 + k * 0.06
        if v:
            c.add(f'<path d="{bar_path(cx - bw / 2, base - h, bw, h)}" fill="{T["brand"]}"'
                  f'{c.anim("growy", at, 0.9)}/>')
        if k in (imax, len(months) - 1):  # label only the peak and the latest month
            c.add(c.text(cx, base - h - 9, n(v), "monob", 12, T["text"], "middle",
                         anim=("fade", at + 0.5)))
        c.add(c.text(cx, base + 22, d.strftime("%b"), "mono", 11.5, T["muted"], "middle",
                     anim=("fade", at)))
    return c


def star(x: float, y: float, color: str) -> str:
    import math
    pts = []
    for i in range(10):
        r = 6 if i % 2 == 0 else 2.6
        a = math.pi / 2 + i * math.pi / 5
        pts.append(f"{x + r * math.cos(a):.2f},{y - r * math.sin(a):.2f}")
    return f'<polygon points="{" ".join(pts)}" fill="{color}"/>'


def fork(x: float, y: float, color: str) -> str:
    return (f'<g fill="none" stroke="{color}" stroke-width="1.5" stroke-linecap="round">'
            f'<circle cx="{x - 4}" cy="{y - 5}" r="1.8"/><circle cx="{x + 4}" cy="{y - 5}" r="1.8"/>'
            f'<circle cx="{x}" cy="{y + 5}" r="1.8"/>'
            f'<path d="M{x - 4},{y - 3}V{y - 1}Q{x - 4},{y + 1} {x},{y + 1}Q{x + 4},{y + 1} {x + 4},{y - 1}V{y - 3}M{x},{y + 1}V{y + 3}"/></g>')


def repo(i: int, r: dict, delay: float) -> Card:
    T = THEME
    desc = r.get("description") or "No description yet."
    lines = FONT.wrap("body", desc, 15.5, 660, 2)
    h = 112 + 22 * len(lines)
    lang = r.get("primaryLanguage") or {}
    c = Card(f"repo-{r['name']}", W, h, f"{r['name']}: {desc}", delay)
    c.add(f'<rect x="1" y="22" width="4" height="{h - 44}" rx="2" fill="{T["brand"]}"'
          f'{c.anim("growy", 0, 0.6, "down")}/>')
    c.add(f'<g{c.anim("slide", 0.08)}>',
          c.text(40, 54, r["name"], "medium", 24, T["text"], spacing=-0.4),
          arrow(W - 50, 38, T["muted"]))
    y = 86
    for line in lines:
        c.add(c.text(40, y, line, "body", 15.5, T["muted"]))
        y += 22
    y += 8
    x = 40
    if lang.get("name"):
        c.add(f'<circle cx="{x + 5}" cy="{y - 4}" r="5" fill="{lang.get("color") or T["muted"]}"/>',
              c.text(x + 16, y, lang["name"], "mono", 12.5, T["muted"]))
        x += 16 + FONT.width("mono", lang["name"], 12.5) + 24
    c.add(star(x + 6, y - 4.5, T["muted"]), c.text(x + 18, y, n(r.get("stargazerCount", 0)), "mono", 12.5, T["muted"]))
    x += 18 + FONT.width("mono", n(r.get("stargazerCount", 0)), 12.5) + 22
    c.add(fork(x + 5, y - 4.5, T["muted"]), c.text(x + 16, y, n(r.get("forkCount", 0)), "mono", 12.5, T["muted"]))
    c.add("</g>")
    return c


def graph(data: dict, delay: float) -> Card:
    T = THEME
    weeks = data["weeks"][-53:]
    cell, gap = 11, 3
    step = cell + gap
    x0, y0 = W - 40 - (len(weeks) * step - gap), 84
    c = Card("contributions", W, 222,
             f"Contribution graph: {n(data['total'])} contributions in the last year", delay)
    c.add(c.text(40, 44, "Contribution graph", "monob", 11.5, T["muted"], spacing=1.5,
                 upper=True, anim=("fade", 0)),
          c.text(W - 40, 44, f"{n(data['total'])} in the last year", "mono", 12.5, T["muted"],
                 "end", anim=("fade", 0.05)))

    nz = sorted(cnt for w in weeks for _, cnt in w if cnt)
    q = [nz[int(len(nz) * f)] for f in (.25, .5, .75)] if nz else [1, 2, 3]
    ramp = [T["empty"]] + [mix(T["empty"], T["brand"], t) for t in (.32, .55, .78, 1)]

    def level(v: int) -> int:
        return 0 if not v else 1 + sum(v > t for t in q)

    for k, d in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        c.add(c.text(x0 - 10, y0 + k * step + 9, d, "mono", 10.5, T["muted"], "end",
                     anim=("fade", 0.05)))
    last_month = None
    for wi, week in enumerate(weeks):
        first = week[0][0]
        if first.month != last_month and first.day <= 7 and wi < len(weeks) - 2:
            c.add(c.text(x0 + wi * step, y0 - 10, first.strftime("%b"), "mono", 10.5, T["muted"],
                         anim=("fade", 0.1 + wi * 0.018)))
            last_month = first.month
        cells = "".join(
            f'<rect x="{x0 + wi * step}" y="{y0 + ((d.weekday() + 1) % 7) * step}" width="{cell}" '
            f'height="{cell}" rx="2.5" fill="{ramp[level(v)]}"/>' for d, v in week)
        c.add(f'<g{c.anim("rise", 0.1 + wi * 0.018, 0.5)}>{cells}</g>')

    # pulse on today's square
    td, tv = data["days"][-1]
    tx = x0 + (len(weeks) - 1) * step + cell / 2
    ty = y0 + ((td.weekday() + 1) % 7) * step + cell / 2
    c.smil(f'<rect x="{tx - 7.5}" y="{ty - 7.5}" width="15" height="15" rx="4" fill="none" '
           f'stroke="{T["brand"]}" stroke-width="1.5" opacity="0"><animate attributeName="opacity" '
           f'values="0;1;0" dur="2.4s" begin="{c.begin(0.1 + len(weeks) * 0.018 + 0.4)}" '
           'repeatCount="indefinite"/></rect>')

    # legend
    lx = W - 40 - 5 * step + gap
    ly = y0 + 7 * step + 16
    c.add(f'<g{c.anim("fade", 0.1 + len(weeks) * 0.018)}>',
          c.text(lx - 8, ly + 9.5, "Less", "mono", 10.5, T["muted"], "end"),
          *(f'<rect x="{lx + i * step}" y="{ly}" width="{cell}" height="{cell}" rx="2.5" fill="{col}"/>'
            for i, col in enumerate(ramp)),
          c.text(lx + 5 * step + 5, ly + 9.5, "More", "mono", 10.5, T["muted"]), "</g>")
    c.h = int(ly + cell + 22)
    return c


# --------------------------------------------------------------------------- #
# README block
# --------------------------------------------------------------------------- #
def readme_block(data: dict, st: dict, tiles: list[dict], repos: list[dict]) -> str:
    def alt(s: str) -> str:
        return esc(s)
    vals = [t["value"].format(repos=n(data["repos"])) for t in tiles]
    rows = [
        f'<p><img src="cards/hero.svg" width="100%" alt="{alt(data["name"] + " — " + PROFILE["tagline"])}"></p>',
        "",
        "<p>\n" + "\n".join(
            f'  <a href="{esc(t["href"])}"><img src="cards/tile-{i + 1}.svg" width="32.5%" '
            f'alt="{alt(t["label"] + ": " + v)}"></a>' for i, (t, v) in enumerate(zip(tiles, vals)))
        + "\n</p>",
        "",
        f'<p><img src="cards/activity.svg" width="100%" alt="{alt(activity_alt(st))}"></p>',
        "",
    ]
    for r in repos:
        rows += [f'<p><a href="{esc(r["url"])}"><img src="cards/repo-{esc(r["name"])}.svg" '
                 f'width="100%" alt="{alt(r["name"] + ": " + (r.get("description") or ""))}"></a></p>', ""]
    rows.append(f'<p><img src="cards/contributions.svg" width="100%" '
                f'alt="{alt(n(data["total"]) + " contributions in the last year")}"></p>')
    return "\n".join(rows)


def activity_alt(st: dict) -> str:
    return (f"{n(sum(v for _, v in st['months']))} contributions in 12 months; "
            f"streak {st['streak']} days; {st['active']} active days; "
            f"busiest day {md(st['busiest'][0])}")


def update_readme(block: str) -> None:
    path = ROOT / "README.md"
    start, end = "<!-- cards:start -->", "<!-- cards:end -->"
    text = path.read_text(encoding="utf-8") if path.exists() else f"{start}\n{end}\n"
    if start not in text or end not in text:
        print("README.md has no cards markers; leaving it alone", file=sys.stderr)
        return
    head, rest = text.split(start, 1)
    _, tail = rest.split(end, 1)
    path.write_text(f"{head}{start}\n{block}\n{end}{tail}", encoding="utf-8")


# --------------------------------------------------------------------------- #
def main() -> None:
    data = load()
    st = stats(data["days"])
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("repo-*.svg"):
        old.unlink()

    # Delays stack so the page plays top to bottom.
    t = 0.0
    cards = [hero(data, t)]
    t += 0.7
    for i, spec in enumerate(TILES):
        cards.append(tile(i, spec, data, t + i * 0.12))
    t += 0.6
    cards.append(activity(data, st, t))
    t += 1.0
    for i, r in enumerate(data["pinned"]):
        cards.append(repo(i, r, t + i * 0.22))
    t += 0.22 * len(data["pinned"]) + 0.2
    cards.append(graph(data, t))

    for card in cards:
        card.save()
    update_readme(readme_block(data, st, TILES, data["pinned"]))
    print(f"wrote {len(cards)} cards to {OUT.relative_to(ROOT)}/"
          + (" (static)" if STATIC else ""))


if __name__ == "__main__":
    main()
