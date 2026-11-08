"""Naming a DuckDB lambda's parameter so that it captures nothing.

Measured on DuckDB 2.0: inside ``lambda t: …`` an *unqualified* ``t`` is the
parameter, whatever column of that name is in scope, and however it is
spelled — ``t``, ``"t"`` and ``"T"`` all resolve to the parameter. So a lambda
whose body embeds rendered user SQL captures any column the user named like
the parameter:

    list_filter(arr, lambda t: <"t" has the needle t>)

tested each needle against *itself* rather than against column ``t``, and
``t has_any (p)`` was true for every row. Nothing failed; the answer was wrong.

:func:`parameter` picks the name: the one asked for, lengthened with leading
underscores until no identifier in the SQL the body embeds resolves to it — the
same move as `sharing._prefix`. The scan is deliberately coarse (a keyword or a
nested lambda's own parameter counts too), because renaming needlessly is
harmless and missing a capture is not.

The opposite resolution holds for a *qualified* reference: ``t.v`` prefers a
column ``t`` in the immediate ``FROM`` over the parameter. A lambda reading a
struct field therefore writes ``t['v']``, never ``t.v``.
"""

from __future__ import annotations

import re

#: One token of rendered SQL that can name something: a string literal (kept
#: only so its contents are skipped), a double-quoted identifier, or a bare
#: word. A `$name` placeholder and the tail of a number like `1e5` are not
#: identifiers, hence the lookbehind.
_TOKEN = re.compile(
    r"'(?:[^']|'')*'"
    r'|"((?:[^"]|"")*)"'
    r"|(?<![\w$])([A-Za-z_][A-Za-z0-9_]*)"
)


def names_in(sql: str) -> set[str]:
    """Every identifier in *sql*, folded: DuckDB matches them case-insensitively."""
    found: set[str] = set()
    for match in _TOKEN.finditer(sql):
        quoted, bare = match.groups()
        if quoted is not None:
            found.add(quoted.replace('""', '"').lower())
        elif bare is not None:
            found.add(bare.lower())
    return found


def parameter(name: str, *captured: str) -> str:
    """*name*, or a longer one, that no identifier in *captured* resolves to.

    *captured* is every piece of SQL the lambda's body embeds that the caller
    did not write — rendered user expressions, above all.
    """
    taken = set().union(*(names_in(sql) for sql in captured))
    while name.lower() in taken:
        name = "_" + name
    return name
