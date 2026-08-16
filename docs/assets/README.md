# Logos

## Regenerating

`generate_logo.py` derives the whole layout from the font's own metrics, so
swapping the typeface re-lays-out correctly with no hand-tuning:

```bash
pip install fonttools
python generate_logo.py --font /path/to/JetBrainsMono-Regular.ttf --out docs/assets/
```

The wordmark is converted to outlines, so the SVGs have no font dependency and
render identically everywhere — no webfont request, no fallback to whatever
monospace the reader happens to have.

Colours and geometry are the constants at the top of the script:

| Constant | Value | What it is |
| --- | --- | --- |
| `AMBER` | `#F0A11E` | icon field |
| `GLYPH` | `#3A2405` | bar and chevron |
| `INK` / `PAPER` | `#1F1F1E` / `#FFFFFF` | wordmark, light / dark |
| `PIPE` | `#F0A11E` | the `\|` — same amber as the icon field |
| `PIPE_ALT` | `#C4780C` | darker gold, if you ever need more contrast |
| `PIPE_W` | `9` | pipe bar width |
| `ICON` | `64` | everything scales off this |
| `GAP` | `14` | icon to wordmark |
| `FONT_SIZE` | `44` | wordmark |

The pipe occupies one full character cell, with `(advance - PIPE_W) / 2` of
space on each side, so the gaps stay symmetric whatever font you use.

The pipe uses the icon's exact amber on both light and dark backgrounds, so the
mark and the wordmark share one brand colour. The two horizontal files therefore
differ only in the colour of the letterforms.

## GitHub

GitHub honours `prefers-color-scheme` inside `<picture>`, so this swaps the
wordmark colour between light and dark mode. Relative paths work in GitHub
READMEs.

```html
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-horizontal-dark.svg">
    <img src="docs/assets/logo-horizontal-light.svg" alt="duckdb-kql" width="343">
  </picture>
</p>
```

## PyPI

PyPI renders the long description in isolation and does **not** resolve relative
paths, so it needs absolute raw URLs. It also ignores `<picture>`, so point it at
the light variant only.

```html
<p align="center">
  <img src="https://raw.githubusercontent.com/mmaitre314/duckdb-kql/main/docs/assets/logo-horizontal-light.svg" alt="duckdb-kql" width="343">
</p>
```

Alternatively set `project.urls` / use the absolute form in `pyproject.toml`'s
readme so both surfaces stay in sync.

## Favicon / docs site

`icon.svg` is the standard mark. `icon-small.svg` is the same mark with a
heavier bar, a wider chevron and tighter corners — use it below about 32px,
where the standard strokes start to close up.

```html
<link rel="icon" href="/assets/icon-small.svg" type="image/svg+xml">
```

## Social preview

`social-preview.svg` is 1280x640, the size GitHub wants under
Settings → General → Social preview. That slot only accepts PNG/JPG/GIF, so
export it first:

```bash
rsvg-convert -w 1280 -h 640 social-preview.svg -o social-preview.png
# or: inkscape social-preview.svg -o social-preview.png -w 1280
```
