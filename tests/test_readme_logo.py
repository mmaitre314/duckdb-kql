"""The README's logo has to render on **two** surfaces with different rules.

GitHub and PyPI disagree about this markup in ways that fail silently — a broken
image, not an error — so each rule is pinned here rather than left to whoever
edits the file next.

Measured, not assumed:

* `readme_renderer` is the library PyPI itself renders long descriptions with.
  Running it locally shows `<source>` stripped and `<img src alt width>` kept,
  which is why one `<picture>` block can serve both surfaces.
* PyPI's own CSP is `img-src 'self' https://pypi-camo.freetls.fastly.net/ …` —
  it proxies description images and does not resolve repo-relative paths.
  `polars` and `uv` both ship SVG logos from `raw.githubusercontent.com` through
  exactly that path today.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

README = Path("README.md")
ASSETS = Path("docs/assets")

pytestmark = pytest.mark.skipif(not README.is_file(), reason="run from the repo root")

RAW_PREFIX = "https://raw.githubusercontent.com/mmaitre314/duckdb-kql/main/"


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def _body() -> str:
    """The README with HTML comments removed.

    The comment above the logo explains the markup and therefore *mentions*
    `<img>` and `<source>`; scanning the raw text finds those and reports two of
    each. Neither surface renders a comment, so neither should these checks.
    """
    return re.sub(r"<!--.*?-->", "", _readme(), flags=re.DOTALL)


def _logo_block() -> str:
    return _body().split("</picture>", 1)[0]


def _logo_sources() -> list[str]:
    """Every asset URL the logo block references, `src` and `srcset` alike."""
    return re.findall(r'(?:src|srcset)="([^"]+)"', _logo_block())


def test_the_readme_opens_with_the_logo() -> None:
    """Before the badges and before the prose — it replaces the H1."""
    body = _body().lstrip()
    assert body.startswith('<p align="center">'), "the logo is no longer the first thing"
    assert "<picture>" in _body().split("[![CI]", 1)[0]


def test_the_logo_files_exist() -> None:
    for name in ("logo-horizontal-light.svg", "logo-horizontal-dark.svg"):
        assert (ASSETS / name).is_file(), f"{ASSETS / name} is missing"


def test_the_logo_urls_are_absolute() -> None:
    """A relative path is a broken image on PyPI, which renders this file on its
    own domain and resolves nothing against the repository."""
    sources = _logo_sources()
    assert sources, "no logo image found in the README"
    for url in sources:
        assert url.startswith(RAW_PREFIX), f"{url} is not an absolute raw URL"


def test_every_logo_url_points_at_a_file_that_exists() -> None:
    """An absolute URL cannot be checked by the relative-link test in
    test_docs.py, so a renamed asset would 404 silently on both surfaces."""
    for url in _logo_sources():
        path = Path(url[len(RAW_PREFIX) :])
        assert path.is_file(), f"{url} points at {path}, which is not in the repo"


def test_dark_mode_is_offered_and_light_is_the_fallback() -> None:
    block = _logo_block()
    (source,) = re.findall(r"<source[^>]*>", block)
    assert "prefers-color-scheme: dark" in source
    assert "logo-horizontal-dark.svg" in source
    # The <img> is what PyPI keeps, so it must be the light one.
    (img,) = re.findall(r"<img[^>]*>", block)
    assert "logo-horizontal-light.svg" in img


def test_the_image_carries_the_project_name_as_alt_text() -> None:
    """The README no longer has an H1; if the image fails, this is the title."""
    (img,) = re.findall(r"<img[^>]*>", _logo_block())
    assert 'alt="duckdb-kql"' in img


def test_the_declared_width_matches_the_artwork() -> None:
    """A width that disagrees with the viewBox scales the wordmark."""
    (img,) = re.findall(r"<img[^>]*>", _logo_block())
    declared = int(re.search(r'width="(\d+)"', img).group(1))
    svg = (ASSETS / "logo-horizontal-light.svg").read_text(encoding="utf-8")
    view_box = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    assert view_box, "the logo has no viewBox to check against"
    assert abs(declared - float(view_box.group(1))) < 1, (
        f'width="{declared}" does not match the artwork\'s {view_box.group(1)}'
    )


def test_the_two_variants_are_the_same_size() -> None:
    """`<picture>` swaps them in place; different geometry would shift the page."""
    boxes = {
        name: re.search(
            r'viewBox="([^"]+)"', (ASSETS / name).read_text(encoding="utf-8")
        ).group(1)
        for name in ("logo-horizontal-light.svg", "logo-horizontal-dark.svg")
    }
    assert len(set(boxes.values())) == 1, boxes


def test_the_svg_has_no_font_dependency() -> None:
    """The wordmark is outlined on purpose — a `<text>` element would render in
    whatever font the reader happens to have, and PyPI's camo serves the file
    with no webfont available at all."""
    for name in ("logo-horizontal-light.svg", "logo-horizontal-dark.svg"):
        svg = (ASSETS / name).read_text(encoding="utf-8")
        assert "<text" not in svg, f"{name} draws text with a font"
        assert "@font-face" not in svg


def test_pypis_own_renderer_keeps_the_image() -> None:
    """The decisive check: run PyPI's sanitizer and look at what survives.

    `<source>` is dropped — that is the reason the fallback `<img>` has to be
    the light variant and has to carry an absolute URL.
    """
    render = pytest.importorskip(
        "readme_renderer.markdown", reason="readme_renderer is PyPI's own renderer"
    ).render
    html = render(_readme())
    assert html is not None, "PyPI would fail to render this README at all"

    logo = html.split("</picture>", 1)[0]
    assert "<img" in logo, "PyPI's sanitizer dropped the logo entirely"
    assert RAW_PREFIX in logo, "the surviving image is not absolute; PyPI shows a 404"
    assert "<source" not in logo, (
        "readme_renderer now keeps <source>; the fallback assumption needs rechecking"
    )
