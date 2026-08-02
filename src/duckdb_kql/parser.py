"""KQL parsing — stage 1 of the pipeline (``docs/implementation-plan.md`` §2).

Wraps the ANTLR parser generated from Microsoft's ``Kql.g4`` (vendored and
pinned; see ``grammar/UPSTREAM.md``) behind a small, stable surface so the rest
of the package never imports ANTLR directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from antlr4 import CommonTokenStream, InputStream
from antlr4.error.ErrorListener import ErrorListener

from ._antlr.KqlLexer import KqlLexer
from ._antlr.KqlParser import KqlParser
from .errors import Diagnostic, KqlSyntaxError, SourceSpan

if TYPE_CHECKING:  # pragma: no cover
    from antlr4 import ParserRuleContext

__all__ = ["parse", "validate", "ParseResult"]


class _DiagnosticCollector(ErrorListener):
    """Collects diagnostics instead of printing them to stderr."""

    def __init__(self) -> None:
        self.diagnostics: list[Diagnostic] = []

    def syntaxError(  # noqa: N802 - ANTLR's interface
        self, recognizer, offending, line, column, msg, e
    ) -> None:
        self.diagnostics.append(Diagnostic(SourceSpan(line, column), msg))


class ParseResult:
    """A parsed query: the concrete syntax tree plus any diagnostics."""

    __slots__ = ("tree", "diagnostics", "kql")

    def __init__(self, tree: ParserRuleContext, diagnostics: list[Diagnostic], kql: str):
        self.tree = tree
        self.diagnostics = diagnostics
        self.kql = kql

    @property
    def ok(self) -> bool:
        return not self.diagnostics

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"<ParseResult ok={self.ok} diagnostics={len(self.diagnostics)}>"


def _build(kql: str) -> ParseResult:
    collector = _DiagnosticCollector()

    lexer = KqlLexer(InputStream(kql))
    lexer.removeErrorListeners()
    lexer.addErrorListener(collector)

    parser = KqlParser(CommonTokenStream(lexer))
    parser.removeErrorListeners()
    parser.addErrorListener(collector)

    tree = parser.top()
    return ParseResult(tree, collector.diagnostics, kql)


def parse(kql: str) -> ParseResult:
    """Parse *kql* and return its syntax tree.

    Raises:
        KqlSyntaxError: if the query does not parse. Every diagnostic is
            attached, not only the first.
    """
    if not isinstance(kql, str):
        raise TypeError(f"kql must be str, got {type(kql).__name__}")

    result = _build(kql)
    if not result.ok:
        first = result.diagnostics[0]
        raise KqlSyntaxError(
            f"could not parse KQL at {first.span}: {first.message}",
            diagnostics=result.diagnostics,
        )
    return result


def validate(kql: str) -> list[Diagnostic]:
    """Syntax-check *kql* without raising.

    Returns an empty list when the query is well-formed. This is a *syntax*
    check only — it does not resolve tables or columns, and says nothing about
    whether the query can be translated.
    """
    return _build(kql).diagnostics
