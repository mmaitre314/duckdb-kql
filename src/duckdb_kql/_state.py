"""Per-statement translator state, one value per thread and per asyncio task.

The translator keeps a statement's state beside the code that reads it rather
than threading it through every call: the CTE-name table, the clock, the
sharing scopes and the graph match in `translate`, and the `let` functions,
entity groups, macro scope and stored-function scope in `lower`. As module
globals that state was one per *process*, so a statement translated on one
thread read another's — measured, four threads over the corpus gave SQL that
differed from the serial translation with no error, and crashed outright on
other queries (tests/test_concurrent_translation.py).

A `ContextVar` holds one value per context instead. A thread on standard
CPython starts with an empty context; an asyncio task, an `asyncio.to_thread`
call, and every thread on a free-threaded build start with a *copy* of their
creator's. A copy is harmless because a statement only ever uses values it bound
itself with :meth:`State.bound` — with one exception, the default
:meth:`State.get` creates and caches, which a copy shares with its original.
Hence the rule: **never mutate what `get` returns outside the `bound` that
supplied it.** Nothing breaks it today, and nothing enforces it:
`tests/test_concurrent_translation.py` runs translations in copied contexts,
and fails on per-statement state kept the old way, as a module global.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from typing import Generic, TypeVar

T = TypeVar("T")


class State(Generic[T]):
    """One piece of per-statement state."""

    def __init__(self, name: str, default: Callable[[], T]) -> None:
        self._var: ContextVar[T] = ContextVar(name)
        self._default = default

    def get(self) -> T:
        """The value in force, or the default, created by the first read and
        cached in that context — and shared with any context copied from it."""
        try:
            return self._var.get()
        except LookupError:
            value = self._default()
            self._var.set(value)
            return value

    @contextlib.contextmanager
    def bound(self, value: T) -> Iterator[T]:
        """*value* in force for the duration; the previous value after."""
        token = self._var.set(value)
        try:
            yield value
        finally:
            self._var.reset(token)
