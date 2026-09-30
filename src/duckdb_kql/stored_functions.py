"""Stored functions, registered locally.

A stored function is database-side state: `.create-or-alter function ReadEvents()
{ Events }` on a cluster, and a query calling `ReadEvents()`. There is no cluster
here, so the definitions are supplied — the same design as named entity groups
(`duckdb_kql.entity_groups`) and for the same reason: answering an unknown
function from a table of the same name, or from nothing, would return plausible
rows for a question nobody asked. An unregistered call is refused.

**A list of definitions**, one per item, the way `set_entity_groups` takes a
list of entities::

    duckdb_kql.set_functions([
        'function with (folder = "Tests", docstring = "Reads it") ReadEvents() { Events }',
        'function Above(x:long = 5) { Events | where Value > x }',
    ])

— or a dict of database -> such a list, with None for the database a query runs
in. An item is Kusto's own definition without its command verb; the verb is
accepted too (`.create-or-alter function …`), so the function lines of
`.show database D schema as csl script` paste in unchanged.

What a definition *means* is decided at each call, not here: Kusto expands a
stored function where it is called, so a caller's `let` captures a name inside
its body (measured, docs/stored-functions-proposal.md §2). This module parses
and checks the text; `duckdb_kql.lower` expands it.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "FunctionArg",
    "ResolvedFunctions",
    "StoredFunction",
    "effective_functions",
    "get_functions",
    "lookup",
    "parse_functions",
    "registered_names",
    "set_functions",
    "split_definitions",
]


@dataclasses.dataclass(frozen=True)
class StoredFunction:
    """One parsed definition."""

    name: str
    #: The database it was registered for, or None for "whichever database the
    #: query runs in".
    database: str | None
    #: As `.show functions` reports them: `(x:long=5)`, `{ Events }`.
    parameters: str
    body: str
    folder: str
    docstring: str
    #: The command it came from, verbatim.
    command: str
    #: The parsed ``let Name = (params) { body }`` declaration, which lowering
    #: reads at every call site. Not compared: two parses of one text are equal.
    declaration: Any = dataclasses.field(compare=False, repr=False)


#: Database (None = the current one) -> function name -> definition.
ResolvedFunctions = dict[str | None, dict[str, StoredFunction]]

#: What the ``functions=`` parameters accept: a list of definitions for the
#: database the query runs in, a dict of database -> such a list (None for that
#: database), or an already-parsed registry — so a component holding a parsed
#: one can forward it, as `KustoClient` does.
FunctionArg = Sequence[str] | Mapping[str | None, Sequence[str]] | ResolvedFunctions

_SHAPE = (
    "functions takes a list of definitions, one per item — "
    "['function F() { T }', ...] — or a dict of database -> such a list, with "
    "None for the database the query runs in"
)


def parse_functions(functions: FunctionArg | None) -> ResolvedFunctions | None:
    """Parse and check *functions*, or return None.

    Here rather than at the call, so a malformed definition fails where it was
    written: a fixture's stack trace points at the fixture.
    """
    if functions is None:
        return None
    if isinstance(functions, Mapping):
        out: ResolvedFunctions = {}
        for database, definitions in functions.items():
            if database is not None and (not isinstance(database, str) or not database):
                raise TypeError(
                    "functions: a database must be a non-empty str, or None for the "
                    f"one the query runs in; got {database!r}"
                )
            out[database] = _parse_definitions(definitions, database)
        return out
    return {None: _parse_definitions(functions, None)}


def _parse_definitions(definitions: object, database: str | None) -> dict[str, StoredFunction]:
    where = f"functions[{database!r}]" if database is not None else "functions"
    if isinstance(definitions, Mapping):
        # Already parsed — accepted as-is, which makes this idempotent.
        parsed = dict(definitions)
        if not all(isinstance(f, StoredFunction) for f in parsed.values()):
            raise TypeError(f"{where}: expected a list of definitions, got a mapping")
        return parsed
    if isinstance(definitions, str):
        # Refused rather than iterated: a string is a sequence of one-character
        # strings, and "not a function definition: 'f'" would say nothing useful.
        raise TypeError(f"{_SHAPE}; got a str — wrap it in a list: [{definitions[:40]!r}…]")
    if not isinstance(definitions, (list, tuple)):
        raise TypeError(f"{_SHAPE}; got {type(definitions).__name__}")
    out: dict[str, StoredFunction] = {}
    for text in definitions:
        if not isinstance(text, str):
            raise TypeError(f"{where}: a definition is a str, got {type(text).__name__}")
        function = _parse_definition(text, database)
        if function.name in out:
            raise ValueError(
                f"function {function.name!r} is defined twice"
                + (f" for database {database!r}" if database else "")
                + "; one registry holds one definition per name"
            )
        out[function.name] = function
    return out


#: Where a definition starts: `function`, or the whole exported command.
_DEFINE = re.compile(
    r"\s*(?:\.(?:create-or-alter|create|alter)\s+)?function\b", re.IGNORECASE
)
_DEFINITION_LINE = re.compile(
    r"^\s*(?:\.(?:create-or-alter|create|alter)\s+)?function\b", re.IGNORECASE
)
#: Any other command, which a file of definitions may not hold.
_COMMAND_LINE = re.compile(r"^\s*\.[A-Za-z]")
_COMMENT_LINE = re.compile(r"^\s*(//.*)?$")


def split_definitions(text: str) -> list[str]:
    """A file of definitions, one per item: what ``serve --functions FILE`` reads.

    Each starts on a line of its own with `function` (or the exported
    `.create-or-alter function`). Not split on blank lines, as a database script
    is: a function body may hold one. Comment lines outside a definition are
    dropped; inside one they are the body's, and the parser reads them.
    """
    chunks: list[list[str]] = []
    for line in text.splitlines():
        if _DEFINITION_LINE.match(line):
            chunks.append([line])
        elif _COMMAND_LINE.match(line) or (not chunks and not _COMMENT_LINE.match(line)):
            raise ValueError(
                f"functions: {line.strip()!r} is not a function definition; a file of "
                "them holds `function Name(params) { body }` items only"
            )
        elif chunks:
            chunks[-1].append(line)
    out = []
    for lines in chunks:
        while lines and _COMMENT_LINE.match(lines[-1]):
            lines.pop()
        out.append("\n".join(lines))
    if not out:
        raise ValueError("functions: no `function Name(params) { body }` definition found")
    return out


_NAME = re.compile(
    r"\s*(\[\s*(?:'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")\s*\]|[A-Za-z_][A-Za-z0-9_]*)"
)
_PROPERTIES = frozenset({"docstring", "folder", "skipvalidation", "view"})


def _without_leading_comments(text: str) -> str:
    lines = text.splitlines()
    while lines and _COMMENT_LINE.match(lines[0]):
        lines.pop(0)
    return "\n".join(lines)


def _parse_definition(text: str, database: str | None) -> StoredFunction:
    text = _without_leading_comments(text)
    head = _DEFINE.match(text)
    if head is None:
        first = (text.strip().splitlines() or [""])[0]
        raise ValueError(
            f"functions: {first!r} is not a function definition; expected "
            "`function Name(params) { body }`"
        )
    rest = text[head.end():]
    properties: dict[str, str] = {}
    while True:
        rest = rest.lstrip()
        if re.match(r"ifnotexists\b", rest, re.IGNORECASE):
            rest = rest[len("ifnotexists"):]
            continue
        opening = re.match(r"with\s*\(", rest, re.IGNORECASE)
        if opening is None:
            break
        inside, rest = _parenthesized(rest, opening.end() - 1)
        properties.update(_parse_properties(inside))

    named = _NAME.match(rest)
    after = rest[named.end():].lstrip() if named else ""
    if named is None or not after.startswith("("):
        raise ValueError(
            f"functions: cannot read the name and parameters in {text.strip()!r}; "
            "expected `Name(params) { body }`"
        )
    return _parse_declaration(text, named.group(1), after, properties, database)


def _parse_declaration(
    text: str, name_text: str, after: str, properties: dict[str, str], database: str | None
) -> StoredFunction:
    """Parse ``Name(params) { body }`` by the grammar's own `let` function rule."""
    from .errors import KqlError
    from .lower import _cls, _find_all, _find_names, _rule_children
    from .parser import parse

    # The newline before `;` keeps a trailing `// comment` on the brace's line
    # from swallowing it.
    wrapper = f"let {name_text} = {after}\n;\nprint 0"
    later = [line for line in text.splitlines()[1:] if _DEFINITION_LINE.match(line)]
    if later:
        raise ValueError(
            f"function {name_text}: one definition per item — {later[0].strip()!r} "
            "starts another; give it an item of its own"
        )
    try:
        tree = parse(wrapper).tree
    except KqlError as exc:
        raise ValueError(f"function {name_text}: the definition does not parse ({exc})") from None
    declarations = _find_all(tree, "LetFunctionDeclaration")
    if not declarations:
        raise ValueError(
            f"function {name_text}: expected `{name_text}(params) {{ body }}`, with the "
            "body in braces"
        )
    declaration = declarations[0]
    kids = _rule_children(declaration)
    names = _find_names(kids[0])
    name = names[0] if names else kids[0].getText()
    _refuse_reserved(name)

    parameters: list[Any] = []
    body = None
    for kid in kids[1:]:
        if _cls(kid) == "LetFunctionParameterList":
            parameters = _rule_children(kid)
        elif _cls(kid) == "LetFunctionBody":
            body = kid
    if body is None:
        raise ValueError(f"function {name}: no body")
    for parameter in parameters:
        if _cls(parameter) != "ScalarParameter":
            raise ValueError(
                f"function {name}: a tabular parameter ({parameter.getText()}) is not "
                "supported yet — only scalar parameters, as `(x:long, s:string = 'a')`"
            )
    _refuse_scalar_body(name, parameters, body)
    if properties.get("view", "false").lower() == "true":
        raise ValueError(
            f"function {name}: `view = true` is not supported yet. A view joins "
            "wildcard unions (`union Prefix*`), which a plain function does not, so "
            "accepting it as a plain function would change what those return"
        )

    source = body.start.getInputStream()
    return StoredFunction(
        name=name,
        database=database,
        parameters="(" + ", ".join(p.getText() for p in parameters) + ")",
        body=source.getText(body.start.start, body.stop.stop),
        folder=properties.get("folder", ""),
        docstring=properties.get("docstring", ""),
        command=text.strip(),
        declaration=declaration,
    )


def _refuse_reserved(name: str) -> None:
    """Kusto refuses a built-in's name: SEM0515, "reserved for internal provider"."""
    from .translate import _SPECIAL_FORMS
    from .translate.functions import AGGREGATE_FUNCTIONS, SCALAR_FUNCTIONS

    if name in SCALAR_FUNCTIONS or name in AGGREGATE_FUNCTIONS or name in _SPECIAL_FORMS:
        raise ValueError(
            f"function {name}: is invalid callable name (reserved for internal "
            "provider) — it names a built-in function (Kusto: SEM0515)"
        )


def _refuse_scalar_body(name: str, parameters: list[Any], body: Any) -> None:
    """A body must end in a tabular expression; scalar functions come later."""
    from . import ir
    from .lower import _cls, _collapse, _find_all, _find_names, _is_tabular_value, _rule_children

    scope: dict[str, ir.Expr] = {}
    for parameter in parameters:
        for kid in _rule_children(parameter):
            if _cls(kid) == "ParameterName":
                found = _find_names(kid)
                scope[found[0] if found else kid.getText()] = ir.ColumnRef("")
    final = [k for k in _rule_children(body) if _cls(k) != "LetFunctionBodyStatement"]
    for statement in _rule_children(body):
        if _cls(statement) != "LetFunctionBodyStatement":
            continue
        # In order, and by value: `let t = FpT | where …; t` binds a *table*,
        # and counting every local as a scalar called that body scalar.
        for declaration in _find_all(statement, "LetVariableDeclaration"):
            kids = _rule_children(declaration)
            names = _find_names(kids[0]) if kids else []
            if names and len(kids) >= 2 and not _is_tabular_value(_collapse(kids[-1]), scope):
                scope[names[0]] = ir.ColumnRef("")
            elif names:
                scope.pop(names[0], None)
    if len(final) != 1:
        raise ValueError(f"function {name}: the body must end in exactly one expression")
    value = _collapse(final[0])
    if not _is_tabular_value(value, scope) and not _calls_a_non_builtin(value):
        raise ValueError(
            f"function {name}: a scalar stored function is not supported yet — only "
            "a tabular body, one that can start a query"
        )


def _calls_a_non_builtin(node: Any) -> bool:
    """``{ OtherFunction() }`` — a body that is a call to another function.

    No registry is in scope while registering, so whether the callee is a
    stored function is not known yet. A built-in makes it a scalar body; any
    other name is taken as a tabular call, and a wrong guess fails at the call
    as an unknown function — loudly, never as an answer.
    """
    from .lower import _cls, _find_names, _is_builtin, _rule_children

    if _cls(node) != "NamedFunctionCallExpression":
        return False
    kids = _rule_children(node)
    found = _find_names(kids[0]) if kids else []
    return bool(found) and not _is_builtin(found[0])


def _parenthesized(text: str, opening: int) -> tuple[str, str]:
    """The text inside the parenthesis at *opening*, and what follows it."""
    depth = 0
    quote = ""
    i = opening
    while i < len(text):
        char = text[i]
        if quote:
            if char == "\\" and quote != "@":
                i += 2
                continue
            if char == quote[-1]:
                quote = ""
        elif char in "'\"":
            verbatim = i > 0 and text[i - 1] == "@"
            quote = ("@" if verbatim else "") + char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[opening + 1 : i], text[i + 1 :]
        i += 1
    raise ValueError(f"functions: unbalanced parenthesis in {text.strip()!r}")


_PROPERTY = re.compile(
    r"\s*([A-Za-z_]+)\s*=\s*(@?'(?:[^'\\]|\\.)*'|@?\"(?:[^\"\\]|\\.)*\"|[A-Za-z0-9_.]+)\s*(,|$)"
)


def _parse_properties(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    position = 0
    while position < len(text) and text[position:].strip():
        found = _PROPERTY.match(text, position)
        if found is None:
            raise ValueError(f"functions: cannot read the properties `with ({text})`")
        key = found.group(1).lower()
        if key not in _PROPERTIES:
            raise ValueError(
                f"functions: unknown property {found.group(1)!r}; accepted: "
                + ", ".join(sorted(_PROPERTIES))
            )
        out[key] = _unquote(found.group(2))
        position = found.end()
    return out


def _unquote(value: str) -> str:
    if value.startswith("@"):
        return value[2:-1]
    if value[:1] in "'\"":
        return re.sub(r"\\(.)", r"\1", value[1:-1])
    return value


# ---------------------------------------------------------------------------
# Looking a call up
# ---------------------------------------------------------------------------


def lookup(
    functions: ResolvedFunctions | None,
    database: str | None,
    name: str,
    current: str | None,
) -> StoredFunction | None:
    """The definition a call to *name* in *database* resolves to, or None.

    *database* None means an unqualified call. *current* is the database the
    query runs in when a caller named it (`database=`), so a definition
    registered under that name and one registered under None are both "the
    current database" — and one name defined in both is refused rather than
    picked between.
    """
    if not functions:
        return None
    if database is not None and database != current:
        return functions.get(database, {}).get(name)
    found = functions.get(None, {}).get(name)
    named = functions.get(current, {}).get(name) if current is not None else None
    if found is not None and named is not None and found is not named:
        raise ValueError(
            f"function {name!r} is registered both for the current database and for "
            f"{current!r}, which is the current database here; register it once"
        )
    return found or named


def registered_names(
    functions: ResolvedFunctions | None, database: str | None, current: str | None
) -> list[str]:
    """What *database* has registered, for an error message."""
    if not functions:
        return []
    if database is not None and database != current:
        return sorted(functions.get(database, {}))
    names = set(functions.get(None, {}))
    if current is not None:
        names |= set(functions.get(current, {}))
    return sorted(names)


# ---------------------------------------------------------------------------
# The process-wide default
# ---------------------------------------------------------------------------

_DEFAULT: ResolvedFunctions | None = None


def set_functions(functions: FunctionArg | None) -> None:
    """Set the definitions every later call uses when it passes no ``functions=``.

    Meant for a test fixture or start-up::

        duckdb_kql.set_functions([
            "function ReadEvents() { Events }",
            "function Above(x:long = 5) { Events | where Value > x }",
        ])

    ``None`` clears it. **Process-wide, not thread-local**, like
    :func:`duckdb_kql.set_entity_groups`; :func:`get_functions` exists so a
    fixture can save and restore.
    """
    global _DEFAULT
    _DEFAULT = parse_functions(functions)


def get_functions() -> ResolvedFunctions | None:
    """The current default, parsed, or ``None``. A copy."""
    return None if _DEFAULT is None else {k: dict(v) for k, v in _DEFAULT.items()}


def effective_functions(functions: FunctionArg | None) -> ResolvedFunctions | None:
    """What a call resolves against: its own definitions, or the default.

    A call's ``functions=`` **replaces** the default rather than merging, as
    ``entity_groups=`` does — so ``{}`` is how a call says "none at all".
    """
    if functions is None:
        return _DEFAULT
    return parse_functions(functions)
