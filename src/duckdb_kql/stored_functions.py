"""Stored functions, registered locally.

A stored function is database-side state: `.create-or-alter function ReadEvents()
{ Events }` on a cluster, and a query calling `ReadEvents()`. There is no cluster
here, so the definitions are supplied — the same design as named entity groups
(`duckdb_kql.entity_groups`) and for the same reason: answering an unknown
function from a table of the same name, or from nothing, would return plausible
rows for a question nobody asked. An unregistered call is refused.

**Entries are Kusto's own export form**, verbatim::

    .create-or-alter function with (folder = "Tests", docstring = "…") ReadEvents() { Events }

— which is what `.show database D schema as csl script` emits for each function,
so a production database's definitions paste across unchanged. One string may
hold several, one per command, as a database script does.

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

#: One command, a script of several, or a list of either.
FunctionScript = str | Sequence[str]

#: What the ``functions=`` parameters accept: definitions for the current
#: database, a mapping of database to definitions (None for the current one),
#: or an already-parsed registry — so a component holding a parsed one can
#: forward it, as `KustoClient` does.
FunctionArg = FunctionScript | Mapping[str | None, FunctionScript] | ResolvedFunctions


def parse_functions(functions: FunctionArg | None) -> ResolvedFunctions | None:
    """Parse and check *functions*, or return None.

    Here rather than at the call, so a malformed definition fails where it was
    written: a fixture's stack trace points at the fixture.
    """
    if functions is None:
        return None
    if isinstance(functions, (str, list, tuple)):
        return {None: _parse_script(functions, None)}
    if not isinstance(functions, Mapping):
        raise TypeError(
            "functions must be a string of `.create-or-alter function` commands, a "
            f"list of them, or a dict of database -> those; got {type(functions).__name__}"
        )
    out: ResolvedFunctions = {}
    for database, definitions in functions.items():
        if database is not None and (not isinstance(database, str) or not database):
            raise TypeError(
                f"functions: a database must be a non-empty str, or None for the "
                f"current one; got {database!r}"
            )
        if isinstance(definitions, Mapping):
            # Already parsed — accepted as-is, which makes this idempotent.
            parsed = dict(definitions)
            if not all(isinstance(f, StoredFunction) for f in parsed.values()):
                raise TypeError(
                    f"functions[{database!r}]: expected command text, got a mapping"
                )
            out[database] = parsed
        else:
            out[database] = _parse_script(definitions, database)
    return out


def _parse_script(definitions: FunctionScript, database: str | None) -> dict[str, StoredFunction]:
    if isinstance(definitions, str):
        texts = _split_commands(definitions)
    elif isinstance(definitions, (list, tuple)):
        texts = [c for d in definitions for c in _split_commands(_expect_str(d))]
    else:
        raise TypeError(
            "functions: expected command text or a list of it, got "
            f"{type(definitions).__name__}"
        )
    out: dict[str, StoredFunction] = {}
    for text in texts:
        function = _parse_command(text, database)
        if function.name in out:
            raise ValueError(
                f"function {function.name!r} is defined twice"
                + (f" for database {database!r}" if database else "")
                + "; one registry holds one definition per name"
            )
        out[function.name] = function
    return out


def _expect_str(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"functions: expected a command string, got {type(value).__name__}")
    return value


#: A line that starts a control command. Specific verbs rather than any leading
#: dot, because a function body is KQL and a path expression may continue on a
#: line of its own — `.field` — where no command verb can.
_COMMAND_START = re.compile(
    r"^\s*\.(create-or-alter|create-merge|create|alter-merge|alter|drop|add|show|"
    r"set-or-append|set-or-replace|set|append|delete|ingest|execute)\b",
    re.IGNORECASE,
)
_COMMENT_LINE = re.compile(r"^\s*(//.*)?$")


def _split_commands(text: str) -> list[str]:
    """One command per chunk: each starts on a line with a command verb.

    Not on blank lines, as a database script is split: a function body may hold
    one. Comment lines outside a command are dropped; inside one they are the
    body's, and the parser reads them.
    """
    chunks: list[list[str]] = []
    for line in text.splitlines():
        if _COMMAND_START.match(line):
            chunks.append([line])
        elif chunks:
            chunks[-1].append(line)
        elif not _COMMENT_LINE.match(line):
            raise ValueError(
                f"functions: expected a `.create-or-alter function` command, got {line.strip()!r}"
            )
    out = []
    for lines in chunks:
        while lines and _COMMENT_LINE.match(lines[-1]):
            lines.pop()
        out.append("\n".join(lines))
    if not out:
        raise ValueError("functions: no `.create-or-alter function` command found")
    return out


_DEFINE = re.compile(r"\s*\.(create-or-alter|create|alter)\s+function\b", re.IGNORECASE)
_NAME = re.compile(
    r"\s*(\[\s*(?:'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")\s*\]|[A-Za-z_][A-Za-z0-9_]*)"
)
_PROPERTIES = frozenset({"docstring", "folder", "skipvalidation", "view"})


def _parse_command(text: str, database: str | None) -> StoredFunction:
    head = _DEFINE.match(text)
    if head is None:
        first = text.strip().splitlines()[0]
        raise ValueError(
            f"functions: {first!r} is not a function definition. functions= takes "
            "`.create-or-alter function Name(params) { body }` commands — the "
            "function lines of `.show database D schema as csl script`"
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

        duckdb_kql.set_functions(Path("schema.csl").read_text())

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
