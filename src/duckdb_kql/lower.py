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
from typing import Any

from . import ir
from ._antlr.KqlParser import KqlParser
from .errors import KqlUnsupportedError, SourceSpan
from .parser import parse

__all__ = ["lower"]

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

    if kind == "IdentifierName":
        return ir.ColumnRef(node.getText())

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

    raise _unsupported(node, f"expression:{kind}")


def _literal_text(node: Any) -> str:
    return node.getText().strip()


def _typed_literal(node: Any, kind: str, convert) -> ir.Literal:
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
    """The operator spelling, from a token or an ``*Operator`` rule node."""
    return node.getText().strip().lower()


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
    result = None
    pending_op: str | None = None
    for child in kids:
        if type(child).__name__.endswith("Context"):
            operand = _lower_expr(child)
            if result is None:
                result = operand
            elif pending_op is not None:
                result = ir.BinaryOp(pending_op, result, operand)
                pending_op = None
            else:
                raise _unsupported(node, "binary-expression")
        else:
            pending_op = _op_text(child)
            if pending_op not in _BINARY_TEXT_OPS:
                raise _unsupported(node, f"operator:{pending_op}")

    if result is None:
        raise _unsupported(node, "binary-expression")
    return result


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
            names = _find_all(kids[0], "IdentifierName")
            name = names[0].getText() if names else kids[0].getText().rstrip("= ")
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

    if kind == "IdentifierName":
        return ir.TableRef(node.getText())

    if kind == "DataTableExpression":
        cols, values = [], []
        for decl in _find_all(node, "RowSchemaColumnDeclaration"):
            parts = _rule_children(decl)
            cols.append((parts[0].getText(), parts[1].getText().lower()))
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


def _lower_operator(node: Any) -> ir.Operator:
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

    if kind == "CountOperator":
        if kids:  # `count as Name`
            return ir.Count(kids[-1].getText())
        return ir.Count()

    if kind == "JoinOperator":
        return _lower_join(node, kids)

    if kind == "SummarizeOperator":
        aggregates, by = [], []
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
        names: list[str] = []
        for k in kids:
            if _cls(k) == "DistinctOperatorStarTarget":
                raise _unsupported(node, "distinct *")
            # The column list arrives as a single wrapper node, so taking its
            # raw text yields one bogus column literally named "State,EventType".
            inner = _find_all(k, "IdentifierName") or _rule_children(k)
            names.extend(c.getText() for c in inner) if inner else names.append(k.getText())
        return ir.Distinct(tuple(names))

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
        return ir.Query(_lower_source(parts[0]), [_lower_operator(p) for p in parts[1:]])
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
        ("DeclareQueryParametersStatement", "declare query_parameters"),
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

    scalars, tabulars = _lower_lets(tree)

    pipe = _collapse(statements[0])
    if _cls(pipe) != "PipeExpression":
        # A source with no pipeline at all, e.g. `print 1` or `datatable(...)[]`.
        query = ir.Query(_lower_source(pipe))
    else:
        parts = _rule_children(pipe)
        query = ir.Query(
            _lower_source(parts[0]), [_lower_operator(p) for p in parts[1:]]
        )

    query = _substitute_query(query, scalars)
    query.lets.extend(tabulars)
    return query


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
# let bindings
# ---------------------------------------------------------------------------

#: Node kinds that make a `let` value tabular rather than scalar.
_TABULAR_VALUE = {
    "PipeExpression", "DataTableExpression", "RangeExpression", "PrintOperator",
}


def _is_tabular_value(node: Any, scalars: dict) -> bool:
    """Whether a ``let`` binds a table or a scalar.

    A pipeline or an inline table is unambiguous. A bare identifier is an alias
    for another table — unless it names a scalar `let` already in scope, in
    which case it is that scalar.
    """
    kind = _cls(node)
    if kind in _TABULAR_VALUE:
        return True
    if kind == "IdentifierName":
        return node.getText() not in scalars
    return False


def _lower_query_node(node: Any) -> ir.Query:
    """Lower a tabular expression node into a Query."""
    node = _collapse(node)
    if _cls(node) == "PipeExpression":
        parts = _rule_children(node)
        return ir.Query(_lower_source(parts[0]), [_lower_operator(p) for p in parts[1:]])
    return ir.Query(_lower_source(node))


def _substitute(node: Any, scalars: dict) -> Any:
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
    if isinstance(node, ir.SortKey):
        return dataclasses.replace(node, expr=_substitute(node.expr, scalars))
    return node


def _substitute_query(query: ir.Query, scalars: dict) -> ir.Query:
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
    return ir.Query(
        source,
        [_substitute_operator(op, scalars) for op in query.operators],
        list(query.lets),
    )


def _substitute_operator(op: ir.Operator, scalars: dict) -> ir.Operator:
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
    if isinstance(op, ir.Join):
        return dataclasses.replace(op, right=_substitute_query(op.right, scalars))
    return op


def _lower_lets(tree: Any) -> tuple[dict, list]:
    """Collect ``let`` bindings in declaration order.

    Returns ``(scalars, tabulars)``. Later bindings may reference earlier ones,
    so scalars are substituted as they are collected.
    """
    scalars: dict = {}
    tabulars: list = []

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
        name = kids[0].getText()
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
