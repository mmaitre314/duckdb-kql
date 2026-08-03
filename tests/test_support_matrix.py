"""``docs/kql-support.md`` is generated. These checks keep it that way.

A support table is the document readers trust most and maintainers update least.
The committed copy is therefore regenerated here and compared byte for byte: a
mapping cannot be added without the table moving, and the table cannot be edited
into saying something the code does not do.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TOOLS = Path("tools")
DOC = Path("docs/kql-support.md")

pytestmark = pytest.mark.skipif(
    not DOC.is_file(), reason="run from the repo root"
)


def _generator():
    sys.path.insert(0, str(TOOLS))
    import gen_support_matrix  # noqa: PLC0415

    return gen_support_matrix


def test_committed_file_is_up_to_date() -> None:
    """Regenerating must produce exactly what is committed."""
    gen = _generator()
    assert DOC.read_text(encoding="utf-8") == gen.build(), (
        "docs/kql-support.md is stale — run `python tools/gen_support_matrix.py`"
    )


def test_every_registry_entry_appears() -> None:
    """The tables cannot under-report the surface.

    A mapping that exists but is undocumented is the failure this guards: a
    reader checks the table, does not find the function, and assumes it raises.
    """
    gen = _generator()
    from duckdb_kql.translate.functions import (  # noqa: PLC0415
        AGGREGATE_FUNCTIONS,
        BINARY_OPERATORS,
        SCALAR_FUNCTIONS,
    )

    doc = DOC.read_text(encoding="utf-8")
    missing = [
        name
        for registry in (SCALAR_FUNCTIONS, AGGREGATE_FUNCTIONS, BINARY_OPERATORS)
        for name in registry
        if f"`{name}`" not in doc
    ]
    assert not missing, f"registry entries missing from the support matrix: {missing}"
    assert gen  # the generator is what produced it


def test_every_probe_is_a_real_query() -> None:
    """A probe that no longer parses would silently demote a supported row.

    ``KqlSyntaxError`` and ``KqlUnsupportedError`` mean different things: the
    first says the probe is broken, the second says the feature is missing. Only
    the second belongs in the "not supported" column.
    """
    gen = _generator()
    import duckdb_kql  # noqa: PLC0415
    from duckdb_kql.errors import KqlSyntaxError  # noqa: PLC0415

    broken = []
    for _, kql, _ in gen.OPERATORS + gen.SOURCES:
        try:
            duckdb_kql.to_sql(kql, schema=gen.PROBE_SCHEMA)
        except KqlSyntaxError:
            broken.append(kql)
        except Exception:  # noqa: BLE001, S110 - unsupported is the expected outcome
            pass
    assert not broken, (
        "these probes no longer parse, so their rows report 'not supported' for "
        f"the wrong reason: {broken}"
    )


def test_every_entry_has_a_note_or_an_explicit_dash() -> None:
    """No blank cells. A dash says "nothing to watch for" on purpose."""
    rows = [
        line
        for line in DOC.read_text(encoding="utf-8").splitlines()
        if line.startswith("| `") or line.startswith("| table ")
    ]
    assert rows, "the support matrix has no table rows"
    empty = [r for r in rows if r.rstrip().endswith("|  |")]
    assert not empty, f"rows with an empty gotcha cell: {empty[:5]}"


def test_refusals_all_explain_themselves() -> None:
    gen = _generator()
    for what, why in gen.REFUSALS:
        assert len(why) > 60, f"{what} needs a real reason, not a placeholder"


def test_divergences_match_the_enforced_list() -> None:
    """The doc's divergence list and the test suite's must not drift apart."""
    gen = _generator()
    behaviour = Path("tests/test_behavior.py").read_text(encoding="utf-8")
    enforced = behaviour.count('": (\n', behaviour.index("KNOWN_DIVERGENCES"))
    assert len(gen.DIVERGENCES) == enforced, (
        f"docs/kql-support.md lists {len(gen.DIVERGENCES)} known divergences but "
        f"tests/test_behavior.py enforces {enforced}"
    )
