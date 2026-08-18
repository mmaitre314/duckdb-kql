"""Lowering — concrete syntax tree to IR (pipeline stage 2).

This is the only module that knows about ANTLR. It walks the generated tree and
produces ``ir`` nodes, raising ``KqlUnsupportedError`` for anything outside the
current wave so partial coverage fails loudly rather than silently.

Dispatch is by context **class name** rather than by visitor subclassing: the
generated visitor has hundreds of methods, most irrelevant, and a name-keyed
table keeps the supported surface readable and greppable.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from . import ir
from ._antlr.KqlParser import KqlParser
from .errors import KqlSchemaError, KqlUnsupportedError, SourceSpan
from .params import ParameterDeclaration, normalize_type
from .parser import parse

__all__ = ["lower", "qualify", "query_parameters"]

#: Query-scope scalar bindings — `let` values and query parameters — keyed by
#: the KQL name they were declared under, substituted into the IR before
#: translation.
Scalars = dict[str, ir.Expr]

# KQL binary operator spellings preserved verbatim into the IR (see ir.BinaryOp).
_BINARY_TEXT_OPS = {
    "==", "!=", "<>", "=~", "!~", "<", "<=", ">", ">=",
    "+", "-", "*", "/", "%",
    "and", "or",
    "has", "!has", "has_cs", "!has_cs",
    "contains", "!contains", "contains_cs", "!contains_cs",
    "startswith", "!startswith", "startswith_cs", "!startswith_cs",
    "endswith", "!endswith", "endswith_cs", "!endswith_cs",
    "hasprefix", "!hasprefix", "hassuffix", "!hassuffix",
    "matches regex", "matchesregex",
}


def _cls(node: Any) -> str:
    return type(node).__name__.removesuffix("Context")


def _span(node: Any) -> SourceSpan | None:
    tok = getattr(node, "start", None)
    return SourceSpan(tok.line, tok.column) if tok is not None else None


def _unsupported(node: Any, what: str | None = None) -> KqlUnsupportedError:
    name = what or _cls(node)
    text = node.getText()[:60] if hasattr(node, "getText") else ""
    return KqlUnsupportedError(
        name,
        span=_span(node),
        hint=f"not implemented in this wave; near {text!r}" if text else None,
    )


def _children(node: Any) -> list[Any]:
    return [c for c in (getattr(node, "children", None) or []) if hasattr(c, "getText")]


def _rule_children(node: Any) -> list[Any]:
    return [c for c in _children(node) if type(c).__name__.endswith("Context")]


#: The three shapes a *name* takes in the grammar.
#:
#: `identifierOrKeywordOrEscapedName: identifierName | keywordName | escapedName`
#: — so most of KQL's keywords are legal names, and the parser only builds one of
#: these where a name is allowed. Seeing one means the grammar has already
#: decided it is a name, which is what makes reading it as one safe.
#:
#: Before this, a column called `id`, `count`, `by` or `range` reached the
#: lowerer as `KeywordName` and was reported as an unsupported *construct* — a
#: message about the language when the problem was one column's name. Kusto
#: accepts all of them, and `['...']` exists precisely to name things a plain
#: identifier cannot.
_NAME_KINDS = ("IdentifierName", "KeywordName", "EscapedName")


def _name_text(node: Any) -> str | None:
    """The name a node spells, or ``None`` if it does not spell one."""
    kind = _cls(node)
    if kind in ("IdentifierName", "KeywordName"):
        text: str = node.getText()
        return text
    if kind == "EscapedName":
        # `['my column']` — the name is the string's *value*, not its source
        # text, so quoting and escaping are resolved in exactly one place. Using
        # getText() here would produce a column literally called `['my column']`.
        literals = _find_all(node, "StringLiteralExpression")
        return _literal_string(literals[0]) if literals else None
    return None


def _find_name_nodes(node: Any) -> list[Any]:
    """Every name node under *node*, in source order, without descending into one.

    Deliberately **not** a blanket replacement for searching `IdentifierName`:
    a function name is a `KeywordName` too — `summarize ... by bin(t, 1h)` has
    one — so this is only used where the grammar allows nothing but names.
    """
    if _cls(node) in _NAME_KINDS:
        return [node]
    found: list[Any] = []
    for child in _rule_children(node):
        found.extend(_find_name_nodes(child))
    return found


def _find_names(node: Any) -> list[str]:
    return [text for n in _find_name_nodes(node) if (text := _name_text(n)) is not None]


def _collapse(node: Any) -> Any:
    """Skip pass-through rules that wrap exactly one child rule.

    The grammar has deep chains such as ``expression -> pipeExpression ->
    beforePipeExpression -> unnamedExpression -> ...``. Collapsing them keeps the
    lowering code focused on nodes that actually carry meaning.
    """
    while True:
        kids = _rule_children(node)
        if len(kids) == 1 and len(_children(node)) == 1:
            node = kids[0]
        else:
            return node


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------


def _lower_expr(node: Any) -> ir.Expr:
    node = _collapse(node)
    kind = _cls(node)

    if kind in _NAME_KINDS:
        name = _name_text(node)
        if name is not None:
            return ir.ColumnRef(name)

    if kind in ("LongLiteralExpression", "IntLiteralExpression"):
        return _typed_literal(node, "long" if kind[0] == "L" else "int", int)

    if kind in ("RealLiteralExpression", "DecimalLiteralExpression"):
        return _typed_literal(node, "real", float)

    if kind == "StringLiteralExpression":
        return ir.Literal(_string_value(node.getText()), "string")

    if kind == "BooleanLiteralExpression":
        return _typed_literal(node, "bool", lambda t: t.strip().lower() == "true")

    if kind == "DateTimeLiteralExpression":
        return _typed_literal(node, "datetime", str)

    if kind == "TimeSpanLiteralExpression":
        return _typed_literal(node, "timespan", str)

    if kind == "GuidLiteralExpression":
        return _typed_literal(node, "guid", str)

    if kind in (
        "LiteralExpression", "NumericLiteralExpression", "NumberLikeLiteralExpression",
        # Sign/unsigned wrappers carry the real literal as their only child.
        "SignedLiteralExpression", "UnsignedLiteralExpression",
        "SignedLongLiteralExpression", "SignedRealLiteralExpression",
    ):
        inner = _rule_children(node)
        if inner:
            return _lower_expr(inner[0])
        return _lower_bare_literal(node)

    if kind in (
        "AdditiveExpression", "MultiplicativeExpression", "RelationalExpression",
        "EqualsEqualityExpression", "LogicalAndExpression", "LogicalOrExpression",
        "EqualityExpression", "StringOperatorExpression",
        "StringBinaryOperatorExpression", "StringBinaryExpression",
        "StringEqualityExpression",
    ):
        return _lower_binary(node)

    if kind == "DynamicLiteralExpression":
        return _lower_dynamic_literal(node)

    if kind == "FunctionCallOrPathPathExpression":
        return _lower_path(node)

    if kind == "ListEqualityExpression":
        return _lower_in_list(node)

    if kind == "ParenthesizedExpression":
        inner = _rule_children(node)
        if inner:
            return _lower_expr(inner[0])

    if kind in ("NamedFunctionCallExpression", "FunctionCallExpression", "CountExpression"):
        return _lower_function_call(node)

    if kind in ("NamedExpression", "ArgumentExpression"):
        # A named expression in scalar position: take the value side.
        kids = _rule_children(node)
        if len(kids) == 2:
            return _lower_expr(kids[1])
        if len(kids) == 1:
            return _lower_expr(kids[0])

    if kind == "UnaryMinusExpression":
        return ir.UnaryOp("-", _lower_expr(_rule_children(node)[0]))
    if kind == "UnaryPlusExpression":
        return _lower_expr(_rule_children(node)[0])

    if kind == "InvocationExpression":
        # The grammar spells a unary operator as a leading token on
        # `invocationExpression`, so `-1` never reaches UnaryMinusExpression.
        # Without this, `d[-1]` was reported unsupported.
        kids = _children(node)
        rules = _rule_children(node)
        if len(kids) == 2 and len(rules) == 1:
            op = kids[0].getText().strip().lower()
            operand = _lower_expr(rules[0])
            if op == "-":
                if isinstance(operand, ir.Literal) and isinstance(
                    operand.value, (int, float)
                ):
                    return ir.Literal(-operand.value, operand.kind)
                return ir.UnaryOp("-", operand)
            if op == "+":
                return operand
            if op in ("not", "!"):
                return ir.UnaryOp("not", operand)

    raise _unsupported(node, f"expression:{kind}")


def _literal_text(node: Any) -> str:
    text: str = node.getText().strip()
    return text


def _typed_literal(
    node: Any, kind: str, convert: Callable[[str], ir.LiteralValue]
) -> ir.Literal:
    """Lower a literal, unwrapping the ``type(value)`` form.

    KQL allows an explicitly-typed literal — ``long(5)``, ``datetime(2015-01-01)``
    — and, importantly, a *typed null*: ``int(null)``. Feeding that raw text to
    ``int()`` used to raise a bare ValueError out of the public API.
    """
    text = _literal_text(node)

    # Unwrap `typename(...)`, e.g. int(null) / long(5) / datetime(2015-01-01).
    if text.endswith(")") and "(" in text:
        head, _, inner = text.partition("(")
        if head.strip().isidentifier():
            text = inner[:-1].strip()

    # The argument may itself be quoted -- `datetime('2015-01-01')` is as legal
    # as `datetime(2015-01-01)`. Keeping the quotes produced `TIMESTAMP
    # '''2015-01-01'''`, which DuckDB reads as a different string entirely.
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = _string_value(text)

    if text.lower() in ("null", ""):
        return ir.Literal(None, "null")

    try:
        return ir.Literal(convert(text), kind)
    except (ValueError, TypeError) as e:
        raise _unsupported(node, f"literal:{kind}") from e


def _lower_bare_literal(node: Any) -> ir.Expr:
    text = node.getText().strip()
    if text.lower() in ("true", "false"):
        return ir.Literal(text.lower() == "true", "bool")
    try:
        return ir.Literal(int(text), "long")
    except ValueError:
        pass
    try:
        return ir.Literal(float(text), "real")
    except ValueError:
        pass
    if text[:1] in ("'", '"'):
        return ir.Literal(_string_value(text), "string")
    raise _unsupported(node, "literal")


def _string_value(text: str) -> str:
    """Decode a KQL string literal, including the ``@`` verbatim form."""
    verbatim = text.startswith("@")
    if verbatim:
        text = text[1:]
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        body = text[1:-1]
    else:
        body = text
    if verbatim:
        return body
    out, i = [], 0
    escapes = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"', "0": "\0"}
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            out.append(escapes.get(body[i + 1], body[i + 1]))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _op_text(node: Any) -> str:
    """The operator spelling, from a token or an ``*Operator`` rule node.

    `getText()` drops whitespace, so the two-word `matches regex` arrives
    glued together.
    """
    text = node.getText().strip().lower()
    return "matches regex" if text == "matchesregex" else text


def _lower_binary(node: Any) -> ir.Expr:
    """Fold a binary chain left-associatively.

    The grammar uses two different shapes for binary expressions, and both
    occur in Wave 1:

    * **flat** — ``left '==' right`` (equality), where the operator is a bare
      token sitting between two operand rules;
    * **nested** — ``left (Operation)*`` (arithmetic, string operators), where
      each ``*Operation`` node holds *both* the operator and its right operand.
      The operator inside may itself be a rule (``StringBinaryOperator``) rather
      than a token, which is why operators are read via ``_op_text``.

    Handling only one shape silently returns the left operand alone — which is
    how ``where s == "abc"`` once became ``where s``.
    """
    kids = _children(node)
    rules = [k for k in kids if type(k).__name__.endswith("Context")]
    if not rules:
        raise _unsupported(node, "binary-expression")

    operations = [k for k in rules if _cls(k).endswith("Operation")]

    if operations:
        result = _lower_expr(rules[0])
        for part in operations:
            inner = _children(part)
            if len(inner) < 2:
                raise _unsupported(part, "binary-operation")
            op = _op_text(inner[0])
            right = [c for c in inner[1:] if type(c).__name__.endswith("Context")]
            if not right:
                raise _unsupported(part, "binary-operation")
            if op not in _BINARY_TEXT_OPS:
                raise _unsupported(part, f"operator:{op}")
            result = ir.BinaryOp(op, result, _lower_expr(right[-1]))
        return result

    # Flat shape: operand (token operand)*
    folded: ir.Expr | None = None
    pending_op: str | None = None
    for child in kids:
        if type(child).__name__.endswith("Context"):
            operand = _lower_expr(child)
            if folded is None:
                folded = operand
            elif pending_op is not None:
                folded = ir.BinaryOp(pending_op, folded, operand)
                pending_op = None
            else:
                raise _unsupported(node, "binary-expression")
        else:
            pending_op = _op_text(child)
            if pending_op not in _BINARY_TEXT_OPS:
                raise _unsupported(node, f"operator:{pending_op}")

    if folded is None:
        raise _unsupported(node, "binary-expression")
    return folded


def _lower_function_call(node: Any) -> ir.Expr:
    """Lower ``name(arg, ...)``.

    Shape: ``NamedFunctionCallExpression -> SimpleNameReference '(' Argument* ')'``.
    """
    kids = _rule_children(node)
    if not kids:
        return ir.FunctionCall(node.getText().rstrip("()"))
    name = kids[0].getText()
    args = tuple(_lower_expr(k) for k in kids[1:])
    return ir.FunctionCall(name, args)


def _lower_named(node: Any) -> ir.NamedExpr:
    """Lower ``name = expr`` (or a bare expression).

    The name arrives as a ``NamedExpressionNameClause`` that *includes* the
    trailing ``=`` token, so take the identifier out of it rather than the
    clause's raw text.
    """
    node = _collapse(node)
    if _cls(node) in ("NamedExpression", "ArgumentExpression"):
        kids = _rule_children(node)
        if len(kids) == 2 and _cls(kids[0]) == "NamedExpressionNameClause":
            names = _find_names(kids[0])
            name = names[0] if names else kids[0].getText().rstrip("= ")
            return ir.NamedExpr(_lower_expr(kids[1]), name)
        if len(kids) == 1:
            return ir.NamedExpr(_lower_expr(kids[0]))
    return ir.NamedExpr(_lower_expr(node))


# ---------------------------------------------------------------------------
# Sources and operators
# ---------------------------------------------------------------------------


def _lower_source(node: Any) -> ir.Source:
    node = _collapse(node)
    kind = _cls(node)

    if kind in _NAME_KINDS:
        name = _name_text(node)
        if name is not None:
            return ir.TableRef(name)

    if kind == "FunctionCallOrPathPathExpression":
        qualified = _lower_qualified_table(node)
        if qualified is not None:
            return qualified

    if kind == "RangeExpression":
        kids = _rule_children(node)
        if len(kids) != 4:
            raise _unsupported(node, "range")
        names = _find_names(kids[0])
        return ir.RangeSource(
            names[0] if names else kids[0].getText(),
            _lower_expr(kids[1]),
            _lower_expr(kids[2]),
            _lower_expr(kids[3]),
        )

    if kind == "DataTableExpression":
        cols, values = [], []
        for decl in _find_all(node, "RowSchemaColumnDeclaration"):
            parts = _rule_children(decl)
            names = _find_names(parts[0])
            cols.append((names[0] if names else parts[0].getText(),
                         parts[1].getText().lower()))
        for child in _rule_children(node):
            # Values are everything outside the schema declaration.
            if _cls(child).startswith("RowSchema"):
                continue
            values.append(_lower_expr(child))
        return ir.DataTable(tuple(cols), tuple(values))

    if kind == "PrintOperator":
        exprs = [_lower_named(c) for c in _rule_children(node)]
        return ir.PrintSource(tuple(exprs))

    # A bare scalar expression is a legal query (an implicit single-row print).
    try:
        return ir.PrintSource((ir.NamedExpr(_lower_expr(node)),))
    except KqlUnsupportedError:
        raise _unsupported(node, f"source:{kind}") from None


def _lower_qualified_table(node: Any) -> ir.TableRef | None:
    """``database("Sales").Orders`` -> ``TableRef("Orders", database="Sales")``.

    Returns ``None`` when the path is not a cross-database table reference, so
    the caller falls through to its own error rather than this one guessing.

    `cluster(...)` is refused rather than ignored. A cross-*cluster* reference
    names a service that does not exist here; quietly treating
    ``cluster("prod").database("Sales").Orders`` as the local `Sales` would
    answer a question about production with local data, which is the one failure
    this package exists to prevent.
    """
    root, *operations = _rule_children(node)
    call = _find_all(root, "NamedFunctionCallExpression")
    if not call:
        return None
    name_nodes = _rule_children(call[0])
    function = name_nodes[0].getText().lower() if name_nodes else ""

    if function == "cluster":
        raise _unsupported(
            node,
            "cluster() — there is no cluster here; attach the database locally "
            'and reference it as database("Name").Table',
        )
    if function != "database":
        return None

    literals = _find_all(call[0], "StringLiteralExpression")
    if len(literals) != 1:
        return None
    database = _literal_string(literals[0])
    if database is None:
        return None

    # Exactly one `.Name` after it. `database("X").Y.Z` is not a table.
    names = [n for op in operations for n in _find_names(op)]
    if len(names) != 1:
        return None
    return ir.TableRef(names[0], database=database)


def _literal_string(node: Any) -> str | None:
    """The value of a string-literal node, or ``None`` if it is not one.

    Routed through :func:`_lower_expr` rather than reading the text, so quoting
    and escaping are decided in exactly one place — `database('a\\'b')` cannot
    mean one thing here and another everywhere else.
    """
    lowered = _lower_expr(node)
    if isinstance(lowered, ir.Literal) and isinstance(lowered.value, str):
        return lowered.value
    return None


def _lower_operator(node: Any) -> ir.Operator | None:
    # `pipedOperator` is `'|' afterPipeOperator`, so it has a token child
    # alongside the rule child and the generic collapse won't descend into it.
    while _cls(node) in ("PipedOperator", "AfterPipeOperator"):
        rules = _rule_children(node)
        if not rules:
            raise _unsupported(node)
        node = rules[0]
    node = _collapse(node)
    kind = _cls(node)
    kids = _rule_children(node)

    if kind == "WhereOperator":
        return ir.Where(_lower_expr(kids[-1]))

    if kind == "ProjectOperator":
        return ir.Project(tuple(_lower_named(k) for k in kids))

    if kind == "ExtendOperator":
        return ir.Extend(tuple(_lower_named(k) for k in kids))

    if kind == "TakeOperator":
        # The count is an expression, not a bare token: `take int(10)` is legal
        # and used to reach int() as the literal text "int(10)", leaking a
        # ValueError straight out of the public API.
        count = _lower_expr(kids[-1])
        if not isinstance(count, ir.Literal) or not isinstance(count.value, int):
            raise _unsupported(node, "take", )
        return ir.Take(int(count.value))

    if kind == "GetSchemaOperator":
        return ir.GetSchema()

    if kind == "CountOperator":
        if kids:  # `count as Name`
            names = _find_names(kids[-1])
            return ir.Count(names[0] if names else kids[-1].getText())
        return ir.Count()

    if kind in ("MvexpandOperator", "MvExpandOperator"):
        return _lower_mv_expand(node, kids)

    if kind == "RenderOperator":
        # `render` is a *visualization* directive. The emulator returns the
        # primary result table unchanged and puts the chart hint in a separate
        # metadata table, so dropping it is correct, not a shortcut.
        return None

    if kind == "ProjectAwayOperator":
        names = _find_names(node)
        if not names:
            raise _unsupported(node, "project-away")
        return ir.ProjectAway(tuple(names))

    if kind == "ProjectRenameOperator":
        renames = []
        for k in kids:
            named = _lower_named(k)
            if not named.name or not isinstance(named.expr, ir.ColumnRef):
                raise _unsupported(node, "project-rename")
            renames.append((named.expr.name, named.name))
        if not renames:
            raise _unsupported(node, "project-rename")
        return ir.ProjectRename(tuple(renames))

    if kind == "JoinOperator":
        return _lower_join(node, kids)

    if kind == "LookupOperator":
        return _lower_lookup(node, kids)

    if kind == "SummarizeOperator":
        aggregates: list[ir.NamedExpr] = []
        by: list[ir.NamedExpr] = []
        for k in kids:
            if _cls(k) == "SummarizeOperatorByClause":
                by.extend(_lower_named(c) for c in _rule_children(k))
            elif _cls(k) == "SummarizeOperatorParameters":
                raise _unsupported(k, "summarize hint")
            else:
                aggregates.append(_lower_named(k))
        return ir.Summarize(tuple(aggregates), tuple(by))

    if kind == "SortOperator":
        return ir.Sort(tuple(_lower_sort_key(k) for k in kids))

    if kind == "DistinctOperator":
        distinct_on: list[str] = []
        for k in kids:
            if _cls(k) == "DistinctOperatorStarTarget":
                raise _unsupported(node, "distinct *")
            # The column list arrives as a single wrapper node, so taking its
            # raw text yields one bogus column literally named "State,EventType".
            found = _find_names(k)
            if found:
                distinct_on.extend(found)
            elif children := _rule_children(k):
                distinct_on.extend(c.getText() for c in children)
            else:
                distinct_on.append(k.getText())
        return ir.Distinct(tuple(distinct_on))

    raise _unsupported(node, kind)


#: Join kinds we implement, KQL spelling -> canonical (docs/TRANSLATION.md R5).
_JOIN_KINDS = {
    "innerunique": "innerunique",
    "inner": "inner",
    "leftouter": "leftouter",
    "rightouter": "rightouter",
    "fullouter": "fullouter",
    "leftsemi": "leftsemi",
    "rightsemi": "rightsemi",
    "leftanti": "leftanti",
    "rightanti": "rightanti",
    "anti": "leftanti",          # documented alias
    "leftantisemi": "leftanti",
    "rightantisemi": "rightanti",
}


def _lower_join(node: Any, kids: list[Any]) -> ir.Join:
    """Lower ``join kind=... (right) on keys``.

    The **default kind is innerunique**, not inner — the single most dangerous
    default in KQL, because the SQL that looks equivalent silently returns more
    rows (R5).
    """
    kind = "innerunique"
    right_node = None
    keys: list[ir.JoinKey] = []

    for k in kids:
        cls = _cls(k)
        if cls in ("RelaxedQueryOperatorParameter", "QueryOperatorParameter"):
            text = k.getText()
            name, _, value = text.partition("=")
            key = name.strip().lower()
            if key.startswith("hint."):
                # Distribution hints (hint.strategy, hint.shufflekey, ...) tune
                # how a *cluster* executes the join. They cannot change the
                # result, and DuckDB is single-node, so honouring them by
                # ignoring them is correct rather than a shortcut.
                continue
            if key != "kind":
                raise _unsupported(k, f"join parameter:{name.strip()}")
            canonical = _JOIN_KINDS.get(value.strip().lower())
            if canonical is None:
                raise _unsupported(k, f"join kind:{value.strip()}")
            kind = canonical
        elif cls == "JoinOperatorOnClause":
            keys.extend(_lower_join_key(c) for c in _rule_children(k))
        elif right_node is None:
            right_node = k

    if right_node is None:
        raise _unsupported(node, "join", )
    if not keys:
        raise _unsupported(node, "join", )

    return ir.Join(_lower_join_right(right_node), tuple(keys), kind)


#: The only two kinds `lookup` has. Measured: the emulator rejects every other
#: spelling outright, including ones `join` accepts (`innerunique`, `fullouter`,
#: `leftanti`, ...), so accepting them here would let a query pass locally and
#: fail against a real cluster.
_LOOKUP_KINDS = ("leftouter", "inner")

#: Distribution hints `lookup` tolerates. `join` ignores every `hint.*`, but
#: `lookup` is narrower: the emulator accepts `hint.remote` and `hint.strategy`
#: and *rejects* `hint.shufflekey`. Mirroring that keeps us from being more
#: permissive than the engine we translate for.
_LOOKUP_HINTS = ("hint.remote", "hint.strategy")


def _lower_lookup(node: Any, kids: list[Any]) -> ir.Lookup:
    """Lower ``lookup kind=... (right) on keys`` (docs/TRANSLATION.md R14).

    Shares `join`'s `on` clause — the grammar reuses `joinOperatorOnClause` — so
    key parsing is shared too. What differs is the default kind (**leftouter**,
    not `innerunique`) and the output columns, which are decided later.
    """
    kind = "leftouter"
    right_node = None
    keys: list[ir.JoinKey] = []

    for k in kids:
        cls = _cls(k)
        if cls in ("RelaxedQueryOperatorParameter", "QueryOperatorParameter"):
            text = k.getText()
            name, _, value = text.partition("=")
            key = name.strip().lower()
            if key in _LOOKUP_HINTS:
                # Cluster distribution only; cannot change the result, and
                # DuckDB is single-node.
                continue
            if key != "kind":
                raise _unsupported(k, f"lookup parameter:{name.strip()}")
            spelling = value.strip().lower()
            if spelling not in _LOOKUP_KINDS:
                raise _unsupported(k, f"lookup kind:{value.strip()}")
            kind = spelling
        elif cls == "JoinOperatorOnClause":
            keys.extend(_lower_join_key(c) for c in _rule_children(k))
        elif right_node is None:
            right_node = k

    if right_node is None:
        raise _unsupported(node, "lookup")
    # The grammar makes `on` mandatory for lookup (unlike join, where it is
    # optional), so an empty key list means `on` with nothing after it.
    if not keys:
        raise _unsupported(node, "lookup")

    return ir.Lookup(_lower_join_right(right_node), tuple(keys), kind)


def _lower_join_right(node: Any) -> ir.Query:
    """The joined side is a parenthesised tabular expression."""
    inner = _collapse(node)
    while _cls(inner) == "ParenthesizedExpression":
        rules = _rule_children(inner)
        if not rules:
            break
        inner = _collapse(rules[0])

    if _cls(inner) == "PipeExpression":
        parts = _rule_children(inner)
        return ir.Query(_lower_source(parts[0]), _lower_operators(parts[1:]))
    return ir.Query(_lower_source(inner))


def _lower_join_key(node: Any) -> ir.JoinKey:
    """One ``on`` entry: ``k`` or ``$left.a == $right.b``.

    ``on k`` is shorthand for the same column on both sides.
    """
    text = node.getText()
    if "$left" in text.lower() or "$right" in text.lower():
        # `$left.x` parses as a *path* expression, which is a dynamic/JSON
        # feature we do not lower yet. In a join's `on` clause it is only ever a
        # side-qualified column name, so read it off the text rather than
        # pulling all of path lowering forward.
        lhs, sep, rhs = text.partition("==")
        if not sep:
            raise _unsupported(node, "join key")
        return ir.JoinKey(
            _strip_side(lhs.strip(), "left"), _strip_side(rhs.strip(), "right")
        )

    expr = _lower_expr(node)
    if isinstance(expr, ir.ColumnRef):
        return ir.JoinKey(expr.name, expr.name)
    if isinstance(expr, ir.BinaryOp) and expr.op == "==":
        left, right = expr.left, expr.right
        if isinstance(left, ir.ColumnRef) and isinstance(right, ir.ColumnRef):
            return ir.JoinKey(_strip_side(left.name, "left"), _strip_side(right.name, "right"))
    raise _unsupported(node, "join key")


def _strip_side(name: str, side: str) -> str:
    """Drop a ``$left.`` / ``$right.`` qualifier."""
    prefix = f"${side}."
    lowered = name.lower()
    if lowered.startswith(prefix):
        return name[len(prefix):]
    # The lexer may deliver the qualifier without the '$'.
    if lowered.startswith(f"{side}."):
        return name[len(side) + 1:]
    return name


def _lower_sort_key(node: Any) -> ir.SortKey:
    node = _collapse(node)
    text = node.getText().lower()
    kids = _rule_children(node)
    expr_node = kids[0] if kids else node

    # KQL defaults to DESC (R6) — only an explicit asc/ascending flips it.
    ascending = "asc" in text and "desc" not in text
    nulls_first: bool | None = None
    if "nullsfirst" in text.replace(" ", ""):
        nulls_first = True
    elif "nullslast" in text.replace(" ", ""):
        nulls_first = False

    return ir.SortKey(_lower_expr(expr_node), ascending=ascending, nulls_first=nulls_first)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def query_parameters(kql: str) -> list[ParameterDeclaration]:
    """The parameters *kql* declares, in declaration order.

    Cheaper than a full translation and useful on its own: a caller can ask what
    a query expects before deciding what to supply.
    """
    return _lower_query_parameters(parse(kql).tree)


def lower(kql: str) -> ir.Query:
    """Parse *kql* and lower it to IR.

    Raises:
        KqlSyntaxError: the query does not parse.
        KqlUnsupportedError: it parses but uses a construct outside this wave.
    """
    tree = parse(kql).tree

    # These are NOT QueryStatements, so counting query statements alone would
    # silently DROP them. Refuse loudly instead. (`let` used to be in this list
    # for exactly that reason; it is now implemented below.)
    for stmt_kind, label in (
        ("SetStatement", "set"),
        ("AliasDatabaseStatement", "alias database"),
        ("DeclarePatternStatement", "declare pattern"),
        ("RestrictAccessStatement", "restrict"),
    ):
        if _find_all(tree, stmt_kind):
            raise KqlUnsupportedError(
                label, hint="statements other than a single query are Wave 1+"
            )

    statements = _find_all(tree, "QueryStatement")
    if not statements:
        raise KqlUnsupportedError("statement", hint="no query statement found")
    if len(statements) > 1:
        raise KqlUnsupportedError(
            "multi-statement", hint="only a single query statement is supported"
        )

    declarations = _lower_query_parameters(tree)
    # A parameter is in scope for the whole query, `let` bindings included, so it
    # seeds the same substitution pass those use.
    seed: Scalars = {
        d.name: ir.Parameter(d.name, d.type, d.slot) for d in declarations
    }
    scalars, tabulars = _lower_lets(tree, seed)

    pipe = _collapse(statements[0])
    if _cls(pipe) != "PipeExpression":
        # A source with no pipeline at all, e.g. `print 1` or `datatable(...)[]`.
        query = ir.Query(_lower_source(pipe))
    else:
        parts = _rule_children(pipe)
        query = ir.Query(_lower_source(parts[0]), _lower_operators(parts[1:]))

    query = _substitute_query(query, scalars)
    query = _resolve_in_subqueries(query, {name for name, _ in tabulars})
    query.lets.extend(tabulars)
    query.parameters.extend(declarations)
    return query


def _resolve_in_subqueries(query: ir.Query, tabular_names: set[str]) -> ir.Query:
    """Rewrite ``x in (T)`` where T is a tabular ``let`` into a subquery.

    At lowering time a bare `T` is indistinguishable from a column reference;
    only once the `let` bindings are known can it be resolved. Left as a
    ColumnRef it would compare against a column that does not exist.
    """
    if not tabular_names:
        return query

    def fix(node: ir.Expr) -> ir.Expr:
        # `has_any` / `has_all` take the same shape of right-hand side as `in`,
        # so a tabular `let` on the right resolves the same way.
        if isinstance(node, (ir.InList, ir.HasList)):
            if (
                node.subquery is None
                and len(node.items) == 1
                and isinstance(node.items[0], ir.ColumnRef)
                and node.items[0].name in tabular_names
            ):
                return dataclasses.replace(
                    node,
                    items=(),
                    subquery=ir.Query(ir.TableRef(node.items[0].name)),
                )
            return node
        if isinstance(node, ir.BinaryOp):
            return dataclasses.replace(node, left=fix(node.left), right=fix(node.right))
        if isinstance(node, ir.UnaryOp):
            return dataclasses.replace(node, operand=fix(node.operand))
        if isinstance(node, ir.FunctionCall):
            return dataclasses.replace(node, args=tuple(fix(a) for a in node.args))
        return node

    ops: list[ir.Operator] = []
    for op in query.operators:
        if isinstance(op, ir.Where):
            ops.append(dataclasses.replace(op, predicate=fix(op.predicate)))
        elif isinstance(op, (ir.Project, ir.Extend)):
            ops.append(dataclasses.replace(
                op,
                expressions=tuple(
                    dataclasses.replace(e, expr=fix(e.expr)) for e in op.expressions
                ),
            ))
        else:
            ops.append(op)
    return ir.Query(query.source, ops, list(query.lets))


def _find_all(node: Any, class_name: str) -> list[Any]:
    found: list[Any] = []
    if _cls(node) == class_name:
        found.append(node)
        return found
    for child in _rule_children(node):
        found.extend(_find_all(child, class_name))
    return found


# Re-exported for callers that want the raw parser type.
_ = KqlParser


# ---------------------------------------------------------------------------
# declare query_parameters
# ---------------------------------------------------------------------------


def _lower_query_parameters(tree: Any) -> list[ParameterDeclaration]:
    """Collect ``declare query_parameters(...)`` declarations, in order.

    Slots are positional (``kqlp0``, ``kqlp1``, …) rather than derived from the
    declared name. A KQL identifier can be an escaped name holding arbitrary
    text; generating the slot keeps every byte of the emitted SQL ours.
    """
    declarations: list[ParameterDeclaration] = []
    seen: set[str] = set()

    for statement in _find_all(tree, "DeclareQueryParametersStatement"):
        for node in _find_all(statement, "DeclareQueryParametersStatementParameter"):
            kids = _rule_children(node)
            if len(kids) < 2:
                raise _unsupported(node, "query_parameters declaration")

            name = _parameter_name(kids[0])
            if name in seen:
                raise KqlSchemaError(name, hint="declared as a query parameter twice")
            seen.add(name)

            kind = normalize_type(kids[1].getText())
            default = None
            for extra in kids[2:]:
                if _cls(extra) == "ScalarParameterDefault":
                    default = _parameter_default(extra, kind)

            declarations.append(
                ParameterDeclaration(
                    name=name,
                    type=kind,
                    slot=f"kqlp{len(declarations)}",
                    default=default,
                )
            )

    return declarations


def _parameter_name(node: Any) -> str:
    """The declared name, with any ``['...']`` escaping removed."""
    text: str = node.getText()
    if text.startswith("['") and text.endswith("']"):
        return text[2:-2]
    if text.startswith('["') and text.endswith('"]'):
        return text[2:-2]
    return text


def _parameter_default(node: Any, kind: str) -> Any:
    """Evaluate a declaration's ``= <literal>`` default to a Python value.

    Only literals are accepted. A default that could *compute* would need the
    full expression machinery at bind time, and a parameter default is not worth
    that: declare it required instead.
    """
    from .params import coerce

    literals = _find_all(node, "LiteralExpression")
    if not literals:
        raise _unsupported(node, "query_parameters default")
    expr = _lower_expr(_collapse(literals[0]))
    if not isinstance(expr, ir.Literal):
        raise _unsupported(node, "query_parameters default")
    return coerce(expr.value, kind, "<default>")


# ---------------------------------------------------------------------------
# let bindings
# ---------------------------------------------------------------------------

#: Node kinds that make a `let` value tabular rather than scalar.
_TABULAR_VALUE = {
    "PipeExpression", "DataTableExpression", "RangeExpression", "PrintOperator",
}


def _is_tabular_value(node: Any, scalars: Scalars) -> bool:
    """Whether a ``let`` binds a table or a scalar.

    A pipeline or an inline table is unambiguous. A bare identifier is an alias
    for another table — unless it names a scalar `let` already in scope, in
    which case it is that scalar.
    """
    kind = _cls(node)
    if kind in _TABULAR_VALUE:
        return True
    if kind in _NAME_KINDS:
        name = _name_text(node)
        return name is not None and name not in scalars
    return False


def _lower_query_node(node: Any) -> ir.Query:
    """Lower a tabular expression node into a Query."""
    node = _collapse(node)
    if _cls(node) == "PipeExpression":
        parts = _rule_children(node)
        return ir.Query(_lower_source(parts[0]), _lower_operators(parts[1:]))
    return ir.Query(_lower_source(node))


def _lower_operators(nodes: list[Any]) -> list[ir.Operator]:
    """Lower a run of piped operators, dropping the ones that are no-ops.

    `render` lowers to None: it is a visualization directive that leaves the
    result table untouched.
    """
    out: list[ir.Operator] = []
    for node in nodes:
        op = _lower_operator(node)
        if op is not None:
            out.append(op)
    return out


def _substitute(node: Any, scalars: Scalars) -> Any:
    """Replace scalar ``let`` references inside an expression.

    A `let` is a query-scope binding, not a column, so this runs as a pass over
    the IR rather than being threaded through every lowering function.
    """
    if isinstance(node, ir.ColumnRef):
        return scalars.get(node.name, node)
    if isinstance(node, ir.BinaryOp):
        return dataclasses.replace(
            node,
            left=_substitute(node.left, scalars),
            right=_substitute(node.right, scalars),
        )
    if isinstance(node, ir.UnaryOp):
        return dataclasses.replace(node, operand=_substitute(node.operand, scalars))
    if isinstance(node, ir.FunctionCall):
        return dataclasses.replace(
            node, args=tuple(_substitute(a, scalars) for a in node.args)
        )
    if isinstance(node, ir.NamedExpr):
        return dataclasses.replace(node, expr=_substitute(node.expr, scalars))
    if isinstance(node, ir.PathAccess):
        # `let d = dynamic({...}); print d.a` — without this the base stays an
        # unbound column reference and the query fails to bind.
        return dataclasses.replace(
            node,
            base=_substitute(node.base, scalars),
            steps=tuple(
                s if s.index is None
                else dataclasses.replace(s, index=_substitute(s.index, scalars))
                for s in node.steps
            ),
        )
    if isinstance(node, (ir.InList, ir.HasList)):
        # `let areas = dynamic(['a','b']); T | where State has_any (areas)` is
        # real corpus KQL. Without HasList here the name stayed a ColumnRef and
        # the generated SQL referenced a column that does not exist.
        return dataclasses.replace(
            node,
            value=_substitute(node.value, scalars),
            items=tuple(_substitute(i, scalars) for i in node.items),
        )
    if isinstance(node, ir.SortKey):
        return dataclasses.replace(node, expr=_substitute(node.expr, scalars))
    return node


def _substitute_query(query: ir.Query, scalars: Scalars) -> ir.Query:
    if not scalars:
        return query
    source = query.source
    if isinstance(source, ir.DataTable):
        source = dataclasses.replace(
            source, values=tuple(_substitute(v, scalars) for v in source.values)
        )
    elif isinstance(source, ir.PrintSource):
        source = dataclasses.replace(
            source,
            expressions=tuple(_substitute(e, scalars) for e in source.expressions),
        )
    elif isinstance(source, ir.RangeSource):
        # `let n = 20; range y from 0 to n step 5`. Without this the bound stays
        # an unbound column reference and DuckDB rejects the query — a loud
        # failure, but for a query Kusto runs.
        source = dataclasses.replace(
            source,
            start=_substitute(source.start, scalars),
            stop=_substitute(source.stop, scalars),
            step=_substitute(source.step, scalars),
        )
    return ir.Query(
        source,
        [_substitute_operator(op, scalars) for op in query.operators],
        list(query.lets),
    )


def _substitute_operator(op: ir.Operator, scalars: Scalars) -> ir.Operator:
    if isinstance(op, ir.Where):
        return dataclasses.replace(op, predicate=_substitute(op.predicate, scalars))
    if isinstance(op, (ir.Project, ir.Extend)):
        return dataclasses.replace(
            op, expressions=tuple(_substitute(e, scalars) for e in op.expressions)
        )
    if isinstance(op, ir.Sort):
        return dataclasses.replace(
            op, keys=tuple(_substitute(k, scalars) for k in op.keys)
        )
    if isinstance(op, ir.Summarize):
        return dataclasses.replace(
            op,
            aggregates=tuple(_substitute(a, scalars) for a in op.aggregates),
            by=tuple(_substitute(k, scalars) for k in op.by),
        )
    if isinstance(op, (ir.Join, ir.Lookup)):
        return dataclasses.replace(op, right=_substitute_query(op.right, scalars))
    return op


def _lower_lets(
    tree: Any, seed: Scalars | None = None
) -> tuple[Scalars, list[tuple[str, ir.Query]]]:
    """Collect ``let`` bindings in declaration order.

    Returns ``(scalars, tabulars)``. Later bindings may reference earlier ones,
    so scalars are substituted as they are collected. *seed* pre-populates the
    scalar scope — query parameters live there, since a ``let`` may read one.
    """
    scalars: Scalars = dict(seed or {})
    tabulars: list[tuple[str, ir.Query]] = []

    for statement in _find_all(tree, "LetStatement"):
        decls = _rule_children(statement)
        if not decls:
            continue
        decl = decls[0]
        kind = _cls(decl)

        if kind == "LetFunctionDeclaration":
            raise _unsupported(decl, "let function")

        kids = _rule_children(decl)
        if len(kids) < 2:
            raise _unsupported(decl, "let")
        let_names = _find_names(kids[0])
        name = let_names[0] if let_names else kids[0].getText()
        value = _collapse(kids[-1])

        if kind == "LetMaterializeDeclaration":
            # `materialize()` is a caching hint for a distributed engine; it
            # cannot change the result, so unwrapping it is correct.
            tabulars.append((name, _substitute_query(_lower_query_node(value), scalars)))
            continue

        if _is_tabular_value(value, scalars):
            tabulars.append((name, _substitute_query(_lower_query_node(value), scalars)))
        else:
            scalars[name] = _substitute(_lower_expr(value), scalars)

    return scalars, tabulars


#: `in` family, KQL spelling -> (negated, case_insensitive).
_IN_OPERATORS = {
    "in": (False, False),
    "!in": (True, False),
    "in~": (False, True),
    "!in~": (True, True),
}


#: `has_any` / `has_all` share the `in` family's grammar rule
#: (`listEqualityExpression`) but not its semantics — see ir.HasList.
_HAS_LIST_OPERATORS = {"has_any": False, "has_all": True}


def _lower_in_list(node: Any) -> ir.Expr:
    """Lower ``x in (a, b, ...)``, its ``!in`` / ``in~`` variants, and the
    ``has_any`` / ``has_all`` forms that share the same grammar rule.

    The operator is a bare token between the value and the parenthesised list,
    and the list items arrive as separate rule children.
    """
    op = None
    for child in _children(node):
        if not type(child).__name__.endswith("Context"):
            text = child.getText().strip().lower()
            if text in _IN_OPERATORS or text in _HAS_LIST_OPERATORS:
                op = text
                break
    if op is None:
        # Name the operator that is actually there. This used to say "in"
        # unconditionally — the handler's name, not the query's — so an
        # unsupported `has_any` was reported as an unsupported `in`, pointing
        # at the wrong half of the expression.
        raise _unsupported(node, _list_operator_text(node) or "in")

    rules = _rule_children(node)
    if len(rules) < 2:
        raise _unsupported(node, op)

    if op in _HAS_LIST_OPERATORS:
        return _lower_has_list(node, rules, op)

    value = _lower_expr(rules[0])
    negated, case_insensitive = _IN_OPERATORS[op]

    items = []
    for r in rules[1:]:
        try:
            items.append(_lower_expr(r))
        except KqlUnsupportedError:
            # The right-hand side may be a whole tabular expression rather than
            # a value list -- `x in (T | project col)`. That is what the vendored
            # grammar patch (grammar/UPSTREAM.md, PATCH 001) exists to accept.
            if len(rules) != 2:
                raise
            return ir.InList(
                value, (), negated, case_insensitive, _lower_query_node(r)
            )
    return ir.InList(value, tuple(items), negated, case_insensitive)


def _list_operator_text(node: Any) -> str | None:
    """The operator token of a ``listEqualityExpression``, as written.

    Used only to name the construct in an error. Everything between the value
    and the opening parenthesis is the operator, so the first bare token that is
    not punctuation is it.
    """
    for child in _children(node):
        if type(child).__name__.endswith("Context"):
            continue
        text: str = child.getText().strip()
        if text and text not in ("(", ")", ","):
            return text.lower()
    return None


def _lower_has_list(node: Any, rules: list[Any], op: str) -> ir.Expr:
    """Lower ``x has_any (...)`` / ``x has_all (...)`` (R3).

    Term matching, not equality — the items are `has` needles. The right-hand
    side may be a value list, a `dynamic` array, or a tabular subquery; the
    emulator accepts all three.
    """
    value = _lower_expr(rules[0])
    require_all = _HAS_LIST_OPERATORS[op]

    items = []
    for r in rules[1:]:
        try:
            items.append(_lower_expr(r))
        except KqlUnsupportedError:
            # Same shape as the `in` case: a tabular right-hand side rather than
            # a value list. `x has_any (T | project col)` is accepted by Kusto.
            if len(rules) != 2:
                raise
            return ir.HasList(value, (), require_all, _lower_query_node(r))
    return ir.HasList(value, tuple(items), require_all)


# ---------------------------------------------------------------------------
# dynamic / JSON
# ---------------------------------------------------------------------------


def _lower_dynamic_literal(node: Any) -> ir.Expr:
    """``dynamic(<json>)`` — keep the JSON text verbatim.

    KQL's JSON dialect accepts single quotes where strict JSON requires double,
    so the payload is normalised rather than passed straight through.
    """
    values = [c for c in _rule_children(node) if _cls(c) == "JsonValue"]
    if not values:
        return ir.Literal(None, "null")
    text = values[0].getText().strip()
    if text.lower() == "null":
        return ir.Literal(None, "null")
    return ir.Literal(_normalize_json(text), "dynamic")


def _normalize_json(text: str) -> str:
    """Rewrite KQL's JSON spelling into strict JSON.

    `dynamic({'a':1})` is legal KQL but not legal JSON, and DuckDB's parser
    rejects it. Converting quote by quote (rather than a blind replace) keeps
    apostrophes inside double-quoted strings intact.
    """
    out: list[str] = []
    in_double = in_single = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            out.append(text[i : i + 2])
            i += 2
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
        elif ch == "'" and not in_double:
            in_single = not in_single
            out.append('"')
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _lower_path(node: Any) -> ir.Expr:
    """``d.a``, ``d[0]``, ``d['a']`` and chains of them."""
    kids = _rule_children(node)
    if not kids:
        raise _unsupported(node, "path")

    base = _lower_expr(kids[0])
    steps: list[ir.PathStep] = []
    for op in kids[1:]:
        inner = _collapse(op)
        cls = _cls(inner)
        if cls == "FunctionCallOrPathPathOperation":
            names = _find_names(inner)
            if not names:
                raise _unsupported(inner, "path step")
            steps.append(ir.PathStep(name=names[0]))
        elif cls == "FunctionCallOrPathElementOperation":
            exprs = _rule_children(inner)
            if not exprs:
                raise _unsupported(inner, "path step")
            index = _lower_expr(exprs[0])
            # `d['a']` is property access spelled with brackets, not an index.
            if isinstance(index, ir.Literal) and index.kind == "string":
                steps.append(ir.PathStep(name=str(index.value)))
            else:
                steps.append(ir.PathStep(index=index))
        else:
            raise _unsupported(inner, "path step")

    if not steps:
        return base
    return ir.PathAccess(base, tuple(steps))


def _lower_mv_expand(node: Any, kids: list[Any]) -> ir.Operator:
    """``mv-expand col`` — one output row per array element."""
    item_index = None
    named = []
    for k in kids:
        text = k.getText()
        if text.lower().startswith("with_itemindex"):
            item_index = text.split("=", 1)[1].strip()
            continue
        if "Parameter" in _cls(k):
            raise _unsupported(k, f"mv-expand parameter:{text}")
        named.append(k)

    if not named:
        raise _unsupported(node, "mv-expand")
    if len(named) > 1:
        # Expanding several columns in lockstep is a different operation from
        # expanding one, and getting it wrong silently changes the row count.
        raise _unsupported(node, "mv-expand", )
    target = _lower_named(named[-1])
    if not isinstance(target.expr, ir.ColumnRef):
        raise _unsupported(node, "mv-expand", )
    return ir.MvExpand(target.expr.name, target.name, item_index)


# ---------------------------------------------------------------------------
# Default database qualification
# ---------------------------------------------------------------------------


def qualify(query: ir.Query, database: str | None) -> ir.Query:
    """Give every unqualified table reference *database* as its database.

    This is how ``kql(con, q, database="sales")`` targets a database: the name
    is baked into the SQL as ``"sales"."T"`` at translate time, rather than the
    connection being switched to it and switched back.

    Switching was the obvious design and it cannot be made correct here. A
    relation from :func:`duckdb_kql.kql` is **lazy**, and DuckDB resolves an
    unqualified table name when the relation is *fetched* — so restoring the
    previous database before the caller fetches makes the query read the wrong
    one, silently. Two threads sharing a connection make it worse: measured, one
    query in 144 answered from the wrong database with no error. See
    ``docs/session-state-proposal.md`` §1-§2.

    Qualifying at translate time has none of that: nothing mutates, nothing to
    restore, and the answer cannot drift between building and fetching.

    Two kinds of name are deliberately left alone:

    * one that already names a database — ``database("other").T`` wins, as it
      does in Kusto;
    * one bound by a tabular ``let``, which is a CTE in the generated SQL and
      not a table at all. Qualifying it would produce SQL referring to a table
      that does not exist.
    """
    if database is None:
        return query
    return _qualify_query(query, database, frozenset())


def _qualify_query(
    query: ir.Query, database: str, bound: frozenset[str]
) -> ir.Query:
    # A `let` may refer to an earlier one, so names accumulate in order.
    lets: list[tuple[str, ir.Query | ir.Expr]] = []
    seen = set(bound)
    for name, value in query.lets:
        if isinstance(value, ir.Query):
            lets.append((name, _qualify_query(value, database, frozenset(seen))))
        else:
            lets.append((name, value))
        seen.add(name)
    scope = frozenset(seen)

    source = query.source
    if isinstance(source, ir.TableRef) and source.database is None:
        if source.name not in scope:
            source = dataclasses.replace(source, database=database)

    operators = [_qualify_operator(op, database, scope) for op in query.operators]

    out = ir.Query(source, operators, lets, list(query.parameters))
    return out


def _qualify_operator(
    op: ir.Operator, database: str, scope: frozenset[str]
) -> ir.Operator:
    if isinstance(op, (ir.Join, ir.Lookup)):
        return dataclasses.replace(op, right=_qualify_query(op.right, database, scope))
    if isinstance(op, ir.Where):
        return dataclasses.replace(op, predicate=_qualify_expr(op.predicate, database, scope))
    if isinstance(op, (ir.Project, ir.Extend)):
        return dataclasses.replace(
            op,
            expressions=tuple(
                dataclasses.replace(e, expr=_qualify_expr(e.expr, database, scope))
                for e in op.expressions
            ),
        )
    if isinstance(op, ir.Summarize):
        return dataclasses.replace(
            op,
            aggregates=tuple(
                dataclasses.replace(a, expr=_qualify_expr(a.expr, database, scope))
                for a in op.aggregates
            ),
            by=tuple(
                dataclasses.replace(b, expr=_qualify_expr(b.expr, database, scope))
                for b in op.by
            ),
        )
    return op


def _qualify_expr(expr: ir.Expr, database: str, scope: frozenset[str]) -> ir.Expr:
    """Only subqueries carry table references; everything else is passed through."""
    if isinstance(expr, (ir.InList, ir.HasList)):
        if expr.subquery is not None:
            return dataclasses.replace(
                expr, subquery=_qualify_query(expr.subquery, database, scope)
            )
        return expr
    if isinstance(expr, ir.BinaryOp):
        return dataclasses.replace(
            expr,
            left=_qualify_expr(expr.left, database, scope),
            right=_qualify_expr(expr.right, database, scope),
        )
    if isinstance(expr, ir.UnaryOp):
        return dataclasses.replace(
            expr, operand=_qualify_expr(expr.operand, database, scope)
        )
    if isinstance(expr, ir.FunctionCall):
        return dataclasses.replace(
            expr, args=tuple(_qualify_expr(a, database, scope) for a in expr.args)
        )
    return expr
