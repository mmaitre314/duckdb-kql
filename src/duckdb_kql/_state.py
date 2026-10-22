"""Per-statement translator state, one value per thread and per asyncio task.

The translator keeps a statement's state beside the code that reads it rather
than threading it through every call: the CTE-name table, the clock, the
sharing scopes and the graph match in `translate`, and the `let` functions,
entity groups, macro scope and stored-function scope in `lower`. As module
globals that state was one per *process*, so a statement translated on one
thread read another's — measured, four threads over the corpus gave SQL that
differed from the serial translation with no error, and crashed outright on
other queries (tests/test_concurrent_translation.py).

A `ContextVar` holds one value per context instead: every thread starts with
its own, and an asyncio task works on a copy of its creator's.
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
        """The value in force. The first read in a context creates the default,
        so a mutable one is that context's own rather than shared."""
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
