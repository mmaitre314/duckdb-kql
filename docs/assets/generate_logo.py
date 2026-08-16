#!/usr/bin/env python3
"""Generate the duckdb-kql logo files.

The wordmark is converted to outlines so the SVG renders identically
everywhere, with no font dependency and no webfont request.

Usage:
    python generate_logo.py [--font PATH_TO_MONO_TTF] [--out DIR]

Swapping the font is the only thing you should need to change. Layout is
derived from the font's own metrics, so a different monospace face
re-lays-out correctly without hand-tuning.

Requires: fonttools
"""

import argparse
import os

from fontTools.misc.transform import Transform
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

# ---------------------------------------------------------------- palette

AMBER = "#F0A11E"      # icon field
GLYPH = "#3A2405"      # bar + chevron inside the icon
INK = "#1F1F1E"        # wordmark on light backgrounds
PAPER = "#FFFFFF"      # wordmark on dark backgrounds
PIPE = "#F0A11E"       # the | — the icon's exact amber, so mark and wordmark
                       # share one brand colour. Same value on light and dark.

# Alternative: a darker gold, ~3.5:1 on white against the matched amber's ~2.1:1.
# Higher contrast, but it reads as a second, slightly-off gold rather than as
# the brand colour. Swap into PIPE if you ever need the extra legibility.
PIPE_ALT = "#C4780C"

# ---------------------------------------------------------------- geometry

ICON = 64.0     # icon is a 64x64 square; everything else scales off it
GAP = 14.0      # space between icon and wordmark
FONT_SIZE = 44.0
PIPE_W = 9.0    # widened from 8 to carry visual weight at the lighter amber.
                # Free in layout: the pipe sits in a fixed character cell, so
                # total width is unchanged and the side gaps simply tighten.
PIPE_H = 40.0

ICON_RX = 15.0
BAR = dict(x=16.0, y=16.0, w=7.0, h=32.0, rx=3.5)
CHEVRON_D = "M31 19 L47 32 L31 45"
CHEVRON_W = 7.0

# Bolder variant for 16-24px rendering, where the standard glyph thins out.
ICON_RX_SMALL = 12.0
BAR_SMALL = dict(x=14.0, y=13.0, w=9.0, h=38.0, rx=4.5)
CHEVRON_D_SMALL = "M30 16 L50 32 L30 48"
CHEVRON_W_SMALL = 9.0


def fmt(v):
    """Trim float noise out of the path data."""
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


class Wordmark:
    """Lays out and outlines the wordmark from a font's real metrics."""

    def __init__(self, font_path, size=FONT_SIZE):
        self.font = TTFont(font_path)
        self.glyphs = self.font.getGlyphSet()
        self.cmap = self.font.getBestCmap()
        self.upem = self.font["head"].unitsPerEm
        self.size = size
        self.scale = size / self.upem

        # One character cell. The pipe sits in its own cell so the space
        # either side of it is identical by construction.
        self.advance = self.glyphs[self.cmap[ord("d")]].width * self.scale

        cap = getattr(self.font["OS/2"], "sCapHeight", None)
        if not cap:
            pen = _BoundsPen(self.glyphs)
            self.glyphs[self.cmap[ord("H")]].draw(pen)
            cap = pen.top
        self.cap_height = cap * self.scale

    def outline(self, text, x, y):
        parts, pen_x = [], 0.0
        for ch in text:
            glyph = self.glyphs[self.cmap[ord(ch)]]
            svg_pen = SVGPathPen(self.glyphs, ntos=fmt)
            transform = Transform(
                self.scale, 0, 0, -self.scale, x + pen_x * self.scale, y
            )
            glyph.draw(TransformPen(svg_pen, transform))
            d = svg_pen.getCommands()
            if d:
                parts.append(d)
            pen_x += glyph.width
        return " ".join(parts), pen_x * self.scale


class _BoundsPen:
    """Minimal fallback when the font carries no sCapHeight."""

    def __init__(self, glyph_set):
        self.top = 0

    def moveTo(self, pt):
        self.top = max(self.top, pt[1])

    lineTo = moveTo

    def curveTo(self, *pts):
        for pt in pts:
            self.top = max(self.top, pt[1])

    qCurveTo = curveTo

    def closePath(self):
        pass

    def endPath(self):
        pass

    def addComponent(self, *args):
        pass


def icon_markup(offset_x=0.0, offset_y=0.0, small=False):
    bar = BAR_SMALL if small else BAR
    rx = ICON_RX_SMALL if small else ICON_RX
    chevron_d = CHEVRON_D_SMALL if small else CHEVRON_D
    chevron_w = CHEVRON_W_SMALL if small else CHEVRON_W
    shift = "" if (offset_x, offset_y) == (0.0, 0.0) else (
        f' transform="translate({fmt(offset_x)},{fmt(offset_y)})"'
    )
    return (
        f'  <g{shift}>\n'
        f'    <rect width="{fmt(ICON)}" height="{fmt(ICON)}" rx="{fmt(rx)}" fill="{AMBER}"/>\n'
        f'    <rect x="{fmt(bar["x"])}" y="{fmt(bar["y"])}" width="{fmt(bar["w"])}"'
        f' height="{fmt(bar["h"])}" rx="{fmt(bar["rx"])}" fill="{GLYPH}"/>\n'
        f'    <path d="{chevron_d}" fill="none" stroke="{GLYPH}"'
        f' stroke-width="{fmt(chevron_w)}" stroke-linecap="round" stroke-linejoin="round"/>\n'
        f'  </g>\n'
    )


def horizontal(wordmark, text_color, pipe_color, title, pipe_w=PIPE_W):
    x0 = ICON + GAP
    half_gap = (wordmark.advance - pipe_w) / 2
    baseline = ICON / 2 + wordmark.cap_height / 2

    left_d, left_w = wordmark.outline("duckdb", x0, baseline)
    pipe_x = x0 + left_w + half_gap
    right_x = pipe_x + pipe_w + half_gap
    right_d, right_w = wordmark.outline("kql", right_x, baseline)
    total_w = right_x + right_w

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {fmt(total_w)} {fmt(ICON)}"'
        f' width="{fmt(total_w)}" height="{fmt(ICON)}" role="img" aria-label="duckdb-kql">\n'
        f'  <title>{title}</title>\n'
        + icon_markup()
        + f'  <path d="{left_d}" fill="{text_color}"/>\n'
        f'  <rect x="{fmt(pipe_x)}" y="{fmt(ICON / 2 - PIPE_H / 2)}" width="{fmt(pipe_w)}"'
        f' height="{fmt(PIPE_H)}" rx="{fmt(pipe_w / 2)}" fill="{pipe_color}"/>\n'
        f'  <path d="{right_d}" fill="{text_color}"/>\n'
        f'</svg>\n'
    )


def icon_file(small=False):
    label = "duckdb-kql icon"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"'
        f' width="64" height="64" role="img" aria-label="{label}">\n'
        f'  <title>{label}</title>\n'
        + icon_markup(small=small)
        + '</svg>\n'
    )


def social_preview(wordmark):
    """1280x640 card for the GitHub social preview slot."""
    w, h = 1280, 640
    inner = horizontal(wordmark, INK, PIPE, "duckdb-kql")
    body = inner.split("\n", 2)[2].rsplit("</svg>", 1)[0]

    x0 = ICON + GAP
    half_gap = (wordmark.advance - PIPE_W) / 2
    left_w = wordmark.advance * 6
    right_w = wordmark.advance * 3
    total_w = x0 + left_w + half_gap + PIPE_W + half_gap + right_w

    scale = 3.0
    tx = (w - total_w * scale) / 2
    ty = (h - ICON * scale) / 2 - 30

    tagline_size = 30.0
    tag = Wordmark(wordmark.font.reader.file.name, size=tagline_size)
    tagline = "run kql queries on duckdb, from python"
    tag_w = tag.advance * len(tagline)
    tag_d, _ = tag.outline(tagline, (w - tag_w) / 2, ty + ICON * scale + 78)

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}"'
        f' width="{w}" height="{h}" role="img" aria-label="duckdb-kql">\n'
        f'  <title>duckdb-kql</title>\n'
        f'  <rect width="{w}" height="{h}" fill="#FFFFFF"/>\n'
        f'  <g transform="translate({fmt(tx)},{fmt(ty)}) scale({fmt(scale)})">\n'
        f'{body}'
        f'  </g>\n'
        f'  <path d="{tag_d}" fill="#6E6E6A"/>\n'
        f'</svg>\n'
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--font", default="/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
    )
    parser.add_argument("--out", default=".")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    wordmark = Wordmark(args.font)

    files = {
        "logo-horizontal-light.svg": horizontal(
            wordmark, INK, PIPE, "duckdb-kql"
        ),
        "logo-horizontal-dark.svg": horizontal(
            wordmark, PAPER, PIPE, "duckdb-kql"
        ),
        "icon.svg": icon_file(),
        "icon-small.svg": icon_file(small=True),
        "social-preview.svg": social_preview(wordmark),
    }

    for name, content in files.items():
        path = os.path.join(args.out, name)
        with open(path, "w") as handle:
            handle.write(content)
        print(f"wrote {path} ({len(content)} bytes)")


if __name__ == "__main__":
    main()
