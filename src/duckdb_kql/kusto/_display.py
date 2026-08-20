"""Notebook rendering for results — ``_repr_html_`` and friends.

``duckdb_kql.kql()`` returns a DuckDB relation, which Jupyter renders as a
table. ``client.execute()`` returned a ``KustoResponseDataSet``, which Jupyter
rendered as ``<...KustoResponseDataSet object at 0x...>`` — the same data, one
of them invisible. This module closes that gap.

Display only: nothing here is consulted when reading values, and ``__str__`` on
a result table still produces the SDK's JSON. The real ``azure-kusto-data``
has no ``_repr_html_``, so this is an addition rather than a divergence — but
``__repr__`` on the two container types is a small, deliberate one, in the same
spirit as the ``__repr__`` the SDK already puts on rows and columns.

Written by hand rather than through pandas: pandas is an optional extra (only
``dataframe_from_result_table`` needs it), and a response should not become
unprintable for want of it.
"""

from __future__ import annotations

import html
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._models import KustoResultTable
    from .response import KustoResponseDataSet

__all__ = ["response_html", "table_html"]

#: Rows rendered before the table is cut short. A notebook cell holding a
#: million rows of markup helps nobody, and the count in the caption stays
#: honest about what was left out.
MAX_ROWS = 100

#: Characters of one cell before it is elided. Long `dynamic` blobs otherwise
#: stretch a column past the width of the screen.
MAX_CELL = 200

#: Kept theme-neutral on purpose. A notebook may be light or dark, and a
#: hard-coded white background turns into an unreadable stripe under the other
#: one, so colour comes from `currentColor` and greys that read on both.
_STYLE = """
<style>
.dkql-result { margin: 0 0 0.75em 0; font-size: 0.9em; }
.dkql-result .dkql-caption {
  opacity: 0.65; margin-bottom: 0.3em; font-size: 0.92em;
}
.dkql-scroll { overflow-x: auto; max-width: 100%; }
.dkql-result table {
  border-collapse: collapse; border: none; margin: 0;
}
.dkql-result th, .dkql-result td {
  border: 1px solid rgba(128, 128, 128, 0.35);
  padding: 0.2em 0.55em; text-align: left; vertical-align: top;
  white-space: nowrap;
}
.dkql-result th {
  background: rgba(128, 128, 128, 0.10); font-weight: 600;
}
.dkql-result th .dkql-type {
  display: block; font-weight: 400; opacity: 0.6; font-size: 0.85em;
}
.dkql-result td.dkql-null { opacity: 0.45; font-style: italic; }
.dkql-result td.dkql-num { text-align: right; font-variant-numeric: tabular-nums; }
.dkql-more { opacity: 0.65; font-size: 0.85em; margin-top: 0.3em; }
.dkql-extra { margin-top: 0.5em; }
.dkql-extra > summary { cursor: pointer; opacity: 0.7; font-size: 0.9em; }
</style>
"""


def _cell(value: Any) -> tuple[str, str]:
    """One cell as ``(css_class, escaped_html)``.

    Escaping is not cosmetic: a `string` column holding ``<script>`` is ordinary
    data, and interpolating it raw would let stored data execute in the reader's
    notebook.
    """
    if value is None:
        return "dkql-null", "null"
    if isinstance(value, str) and not value:
        # Shown rather than left blank. KQL's `isempty` and `isnull` are
        # different questions (R4), and a null and an empty string that both
        # render as whitespace put the reader on the wrong side of that.
        return "dkql-null", "&quot;&quot;"
    if isinstance(value, bool):
        # Before the numeric check — bool is an int, and `True` should not be
        # right-aligned with the numbers.
        return "", html.escape(str(value))
    css = "dkql-num" if isinstance(value, (int, float)) else ""
    if isinstance(value, (dict, list)):
        # A `dynamic` value arrives parsed. Python's repr would print it with
        # single quotes and `True`/`None`, which is not JSON and not what Kusto
        # shows; `json.dumps` puts it back in the form it was sent in.
        text = json.dumps(value, default=str)
    else:
        text = str(value)
    if len(text) > MAX_CELL:
        text = text[:MAX_CELL] + "…"
    return css, html.escape(text)


def _caption(table: KustoResultTable) -> str:
    name = table.table_name or "(unnamed)"
    rows = table.rows_count
    cols = table.columns_count
    return html.escape(
        f"{name} — {rows} row{'' if rows == 1 else 's'} × "
        f"{cols} column{'' if cols == 1 else 's'}"
    )


def table_html(table: KustoResultTable, *, caption: bool = True) -> str:
    """One result table as an HTML fragment (no ``<style>``; see `response_html`)."""
    head = "".join(
        f"<th>{html.escape(str(c.column_name))}"
        f'<span class="dkql-type">{html.escape(str(c.column_type))}</span></th>'
        for c in table.columns
    )

    body = []
    for row in list(table)[:MAX_ROWS]:
        cells = []
        for index in range(table.columns_count):
            css, text = _cell(row[index])
            cells.append(f'<td class="{css}">{text}</td>' if css else f"<td>{text}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")

    parts = ['<div class="dkql-result">']
    if caption:
        parts.append(f'<div class="dkql-caption">{_caption(table)}</div>')
    parts.append('<div class="dkql-scroll"><table>')
    parts.append(f"<thead><tr>{head}</tr></thead>")
    parts.append("<tbody>" + "".join(body) + "</tbody>")
    parts.append("</table></div>")
    if table.rows_count > MAX_ROWS:
        hidden = table.rows_count - MAX_ROWS
        parts.append(
            f'<div class="dkql-more">… {hidden} more row'
            f"{'' if hidden == 1 else 's'} not shown</div>"
        )
    parts.append("</div>")
    return "".join(parts)


def response_html(response: KustoResponseDataSet) -> str:
    """A whole response as HTML.

    The query's own output is shown directly; `@ExtendedProperties` and
    `QueryCompletionInformation` go inside a collapsed ``<details>``. Rendering
    all three inline would bury the answer under two tables of metadata that
    exist only because real Kusto sends them.
    """
    primary = response.primary_results
    primary_ids = {id(t) for t in primary}
    extra = [t for t in response.tables if id(t) not in primary_ids]

    parts = [_STYLE]
    if not primary:
        parts.append('<div class="dkql-result"><div class="dkql-caption">'
                     "no result table</div></div>")
    for table in primary:
        parts.append(table_html(table, caption=len(primary) > 1 or bool(extra)))

    if extra:
        names = ", ".join(html.escape(t.table_name or "(unnamed)") for t in extra)
        parts.append(f'<details class="dkql-extra"><summary>{names}</summary>')
        parts.extend(table_html(t) for t in extra)
        parts.append("</details>")
    return "".join(parts)
