"""Error types for duckdb-kql.

The taxonomy is normative — see ``docs/TRANSLATION.md`` §8. The governing
principle is that a clear refusal always beats a silently wrong answer.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceSpan:
    """A 1-based line / 0-based column position in the original KQL text."""

    line: int
    column: int

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.line}:{self.column}"


class KqlError(Exception):
    """Base class for every error raised by this package."""


class KqlSyntaxError(KqlError):
    """The query could not be parsed.

    ``diagnostics`` holds every syntax error the parser reported, not just the
    first, so callers can surface the full picture.
    """

    def __init__(self, message: str, diagnostics: list[Diagnostic] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or []


class KqlUnsupportedError(KqlError):
    """A recognized KQL construct that this version does not translate.

    Raised deliberately and loudly. Partial coverage must fail visibly rather
    than produce a plausible-looking wrong answer.
    """

    def __init__(self, construct: str, span: SourceSpan | None = None, hint: str | None = None):
        self.construct = construct
        self.span = span
        self.hint = hint
        where = f" at {span}" if span else ""
        because = f" ({hint})" if hint else ""
        super().__init__(f"unsupported KQL construct {construct!r}{where}{because}")


class KqlSchemaError(KqlError):
    """An unknown table or column, or an identifier collision.

    KQL identifiers are case-sensitive while DuckDB folds case, so two distinct
    KQL columns can collide once quoted (``docs/TRANSLATION.md`` R7). That is a
    schema error, not something to resolve arbitrarily.

    Also raised when translation needs a schema and none was supplied — ``join``
    must know both sides' columns to reproduce KQL's column renaming.
    """

    def __init__(self, name: str, hint: str | None = None):
        self.name = name
        self.hint = hint
        because = f" ({hint})" if hint else ""
        super().__init__(f"schema error for {name!r}{because}")


class KqlScriptError(KqlError):
    """One statement of a script failed, named by its position in the script.

    Wraps rather than propagates, because the thing a caller of
    :func:`duckdb_kql.script` needs first is *which* of forty statements broke
    — and the underlying error, raised against one statement, cannot say. The
    original is chained on ``__cause__`` and still appears in the traceback, so
    nothing is lost; a caller that wants to discriminate on it can, and
    ``except KqlError`` catches this as it catches everything else here.
    """

    def __init__(self, message: str, index: int, line: int, statement: str):
        #: 1-based position in the script — the *n*th statement, not the *n*th line.
        self.index = index
        #: 1-based line in the script text where the statement starts.
        self.line = line
        #: The statement as written.
        self.statement = statement
        super().__init__(
            f"script statement {index} (line {line}) failed: {message}"
        )


@dataclass(frozen=True)
class Diagnostic:
    """One parser diagnostic."""

    span: SourceSpan
    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.span}: {self.message}"
