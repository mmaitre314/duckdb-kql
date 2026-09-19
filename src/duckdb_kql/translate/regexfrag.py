"""Rewriting a user-supplied regex fragment so it can be spliced into a larger one.

`parse kind=regex` lets the user write regex *between* the columns it declares,
and the whole thing is assembled into one pattern with a named group per column::

    parse kind=regex s with "a(b)" v "cd" w
        ->  a(?:b)(?P<v>.*)cd(?P<w>.*)
                ^^^ the user's group, made non-capturing

The rewrite is not cosmetic. DuckDB's ``regexp_extract(s, pattern, names)``
maps the name list onto groups **by position, not by name** — measured,
``['q','z']`` against a pattern whose groups are named `v` and `w` happily
returns ``{'q': …, 'z': …}``. So a capturing group anywhere in a user fragment
shifts every column that follows it. Kusto is immune, because it matches by
name.

An **unbalanced** parenthesis is a literal, not an error. Measured on the
emulator, each answering `'b'`::

    parse kind=regex s with ")"    v      over  'a)b'
    parse kind=regex s with "(a"   v      over  '(ab'
    parse kind=regex s with "(a))" v      over  'a)b'

RE2 rejects all three outright (*"unexpected )"*), so the stray parenthesis has
to be escaped here or the user meets a DuckDB binder error quoting a pattern
they did not write. Finding which parentheses are stray is the reason this is a
two-pass scan rather than a `str.replace`.

Two constructs are refused rather than rewritten, and both are refused
*somewhere* by Kusto too:

* **lookaround** — Kusto's own pattern analysis rejects `(?=…)`, `(?!…)`,
  `(?<=…)` and `(?<!…)` with SEM0476, which is convenient because RE2 cannot
  execute them either;
* **backreferences** — Kusto accepts `(a)\\1` and RE2 refuses the pattern
  outright (*"invalid escape sequence"*), so without a check here the user
  meets a raw DuckDB binder error instead of a KQL one.

This module knows nothing about `parse`; it is a string-to-string rewrite with
its own tests (`tests/test_regexfrag.py`).
"""

from __future__ import annotations

from ..errors import KqlUnsupportedError

__all__ = ["neutralise_groups", "to_re2_named_groups", "find_lookaround"]

#: `(?` followed by one of these opens a **lookaround**, which RE2 cannot run.
_LOOKAROUND = ("=", "!", "<=", "<!")

#: `(?` followed by one of these opens a **named capturing** group. RE2's
#: `(?P<n>` and .NET's `(?<n>` and `(?'n'` all exist in the wild; Kusto accepts
#: the first two, and recognising the third costs nothing.
_NAMED_OPENERS = ("P<", "<", "'")


def find_lookaround(pattern: str) -> str | None:
    r"""The first lookaround opener in *pattern*, or None.

    RE2 cannot execute `(?=…)`, `(?!…)`, `(?<=…)` or `(?<!…)` and rejects the
    whole pattern, so without this the caller meets a DuckDB exception quoting a
    construct rather than a KQL error. Kusto refuses all four too — measured,
    SEM0420 "Regex pattern is ill-formed", for `extract`, `extract_all`,
    `matches regex` and `replace_regex` alike — so refusing here loses nothing
    and `neutralise_groups` has said the same about `parse kind=regex` all
    along.

    Escapes and character classes are skipped, so an escaped `\(?=` and a `(`
    inside `[...]` are the literals they are.
    """
    scratch: list[_Piece] = []
    i, n = 0, len(pattern)
    while i < n:
        char = pattern[i]
        if char == "\\":
            i += 2 if i + 1 < n else 1
        elif char == "[":
            scratch.clear()
            i = _copy_character_class(pattern, i, scratch)
        elif pattern.startswith("(?", i):
            rest = pattern[i + 2 :]
            for opener in _LOOKAROUND:
                if rest.startswith(opener):
                    return f"(?{opener}"
            i += 2
        else:
            i += 1
    return None


def to_re2_named_groups(pattern: str) -> str:
    """`(?<name>…)` -> `(?P<name>…)`, the spelling RE2 understands.

    Kusto accepts both spellings of a named capturing group — measured, `(?<w>…)`
    and `(?P<w>…)` answer identically for `extract`, `extract_all`,
    `matches regex` and `replace_regex`. DuckDB's RE2 accepts only the second
    and rejects the first outright, *"invalid perl operator: (?<"*, so a pattern
    Kusto runs happily reached the caller as a raw DuckDB exception rather than
    an answer or a KQL error.

    Only the spelling changes; the group still captures, in the same position,
    under the same name.

    **Lookbehind is not touched.** `(?<=` and `(?<!` start with the same three
    characters and are a different construct: RE2 cannot execute either, and
    Kusto refuses them too (SEM0420), so rewriting one into a named group would
    turn a shared refusal into a silently different pattern.

    Escapes and character classes are skipped rather than scanned, because both
    can contain a `(` that opens nothing — the same reason
    :func:`neutralise_groups` is a scan and not a `str.replace`.
    """
    out: list[str] = []
    scratch: list[_Piece] = []
    i, n = 0, len(pattern)
    while i < n:
        char = pattern[i]
        if char == "\\":
            # Copied without `_copy_escape`, which refuses backreferences: that
            # is `parse kind=regex`'s rule, and this rewrite has no opinion.
            out.append(pattern[i : i + 2] if i + 1 < n else char)
            i += 2 if i + 1 < n else 1
        elif char == "[":
            scratch.clear()
            end = _copy_character_class(pattern, i, scratch)
            out.append(pattern[i:end])
            i = end
        elif pattern.startswith("(?<", i) and pattern[i + 3 : i + 4] not in ("=", "!"):
            out.append("(?P<")
            i += 3
        else:
            out.append(char)
            i += 1
    return "".join(out)


def neutralise_groups(fragment: str) -> str:
    """Make every capturing group in *fragment* non-capturing.

    Leaves alone anything that does not capture: `(?:…)`, an inline flag group
    like `(?i)` or `(?is:…)`, and every escape or character class that merely
    *contains* a parenthesis. Escapes any parenthesis that has no partner, which
    is how Kusto reads one.

    Raises:
        KqlUnsupportedError: for lookaround or a backreference.
    """
    pieces = _scan(fragment)
    _mark_unbalanced(pieces)
    return "".join(text for _kind, text in pieces)


#: One piece of a scanned fragment: ``(kind, text)``.
#:
#: * ``"open"`` — a plain ``(``, already rewritten to ``(?:``. Left unpaired it
#:   becomes a literal ``\(``, because that is how Kusto reads it.
#: * ``"group"`` — a ``(?…`` opener. Left unpaired it is malformed rather than
#:   literal, so it is passed through for RE2 to name.
#: * ``"close"`` — a ``)`` that may close either.
#: * ``"other"`` — everything that cannot pair with anything.
_Piece = tuple[str, str]


def _scan(fragment: str) -> list[_Piece]:
    """Split *fragment* into pieces, rewriting each group opener as we meet it.

    Everything that can *hide* a parenthesis — an escape, a character class — is
    consumed whole here, so the pairing pass that follows sees only parentheses
    that are really parentheses.
    """
    pieces: list[_Piece] = []
    i, n = 0, len(fragment)
    while i < n:
        char = fragment[i]

        if char == "\\":
            i = _copy_escape(fragment, i, pieces)
        elif char == "[":
            i = _copy_character_class(fragment, i, pieces)
        elif char == "(":
            i = _copy_group_opener(fragment, i, pieces)
        elif char == ")":
            pieces.append(("close", ")"))
            i += 1
        else:
            pieces.append(("other", char))
            i += 1
    return pieces


def _mark_unbalanced(pieces: list[_Piece]) -> None:
    """Escape every parenthesis with no partner, in place.

    A stack, not a counter: a counter would pair the `)` in ``)(`` with the `(`
    that comes after it.
    """
    open_stack: list[int] = []
    for index, (kind, _text) in enumerate(pieces):
        if kind in ("open", "group"):
            open_stack.append(index)
        elif kind == "close":
            if open_stack:
                open_stack.pop()
            else:
                pieces[index] = ("other", "\\)")
    for index in open_stack:
        if pieces[index][0] == "open":
            pieces[index] = ("other", "\\(")


def _copy_escape(fragment: str, i: int, out: list[_Piece]) -> int:
    """Copy ``\\x``, refusing ``\\1``–``\\9``.

    A lone trailing backslash is copied as-is and left for RE2 to complain
    about: inventing a meaning for it here would be guessing, and DuckDB's
    message names the real problem.
    """
    nxt = fragment[i + 1] if i + 1 < len(fragment) else ""
    if nxt.isdigit() and nxt != "0":
        raise KqlUnsupportedError(
            f"parse kind=regex backreference:\\{nxt}",
            hint="RE2 has no backreferences, and DuckDB would reject the whole "
            "pattern rather than this one construct",
        )
    out.append(("other", fragment[i : i + 2] if nxt else fragment[i]))
    return i + 2 if nxt else i + 1


def _copy_character_class(fragment: str, i: int, out: list[_Piece]) -> int:
    """Copy a ``[...]`` class verbatim; a `(` inside one is a literal.

    Three details make this more than "find the next `]`": a `]` immediately
    after the opening bracket (or after a `^`) is a **literal** `]` rather than
    the terminator, an escape inside the class can hide one, and a POSIX class
    like ``[[:alpha:]]`` contains a `]` of its own.

    An unterminated class is copied as-is and left to RE2, for the same reason
    as a trailing backslash.
    """
    j = i + 1
    if j < len(fragment) and fragment[j] == "^":
        j += 1
    if j < len(fragment) and fragment[j] == "]":  # `[]]` — a literal `]`
        j += 1
    while j < len(fragment) and fragment[j] != "]":
        if fragment[j] == "\\":
            j += 2
        elif fragment.startswith("[:", j):
            end = fragment.find(":]", j)
            j = len(fragment) if end == -1 else end + 2
        else:
            j += 1
    if j >= len(fragment):  # unterminated — RE2 will say so
        out.append(("other", fragment[i:]))
        return len(fragment)
    out.append(("other", fragment[i : j + 1]))
    return j + 1


def _copy_group_opener(fragment: str, i: int, out: list[_Piece]) -> int:
    """Copy one ``(``, defusing it if it captures."""
    rest = fragment[i + 1 :]

    if not rest.startswith("?"):
        out.append(("open", "(?:"))  # a plain capturing group
        return i + 1

    body = rest[1:]

    for form in _LOOKAROUND:
        if body.startswith(form):
            raise KqlUnsupportedError(
                f"parse kind=regex lookaround:(?{form}",
                hint="RE2 has no lookaround; Kusto's own pattern analysis "
                "refuses it too (SEM0476)",
            )

    if body.startswith("P="):
        raise KqlUnsupportedError(
            "parse kind=regex backreference:(?P=",
            hint="RE2 has no backreferences",
        )

    for opener in _NAMED_OPENERS:
        if body.startswith(opener):
            # A *named* group still captures, and its name could even collide
            # with a column's. Replace the whole `(?P<name>` with `(?:`.
            close = ">" if opener.endswith("<") else "'"
            end = fragment.find(close, i + 2 + len(opener))
            if end == -1:  # malformed — leave it for RE2 to report
                out.append(("group", fragment[i]))
                return i + 1
            out.append(("group", "(?:"))
            return end + 1

    out.append(("group", "("))  # `(?:`, `(?i)`, `(?is:` — none of them capture
    return i + 1
