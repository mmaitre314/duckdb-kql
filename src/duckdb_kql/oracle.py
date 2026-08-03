"""Kusto Emulator client — the ground-truth oracle.

The emulator runs Microsoft's real KQL engine locally and, per Microsoft,
"understands KQL the same way the Azure service does". That makes it ground
truth, unlike a translator or a reimplementation. See ``docs/test-plan.md`` §5.1
and the licensing review in ``docs/licensing.md`` §5.

**This module is dev/CI only.** It is never imported by the translation path and
is never a runtime dependency of the shipped library — a constraint that comes
straight from the emulator's licence terms.

Talks plain HTTP with no auth (the emulator supports neither HTTPS nor Entra),
so it needs nothing beyond the standard library.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

DEFAULT_ENDPOINT = "http://localhost:8080"
DEFAULT_DATABASE = "NetDefaultDB"

__all__ = ["KustoEmulator", "QueryResult", "EmulatorError"]


class EmulatorError(RuntimeError):
    """The emulator rejected a request or could not be reached."""


@dataclass(frozen=True)
class QueryResult:
    """A materialized result table from the emulator."""

    columns: list[str]
    column_types: list[str]
    rows: list[list[Any]]

    @property
    def shape(self) -> tuple[int, int]:
        return len(self.rows), len(self.columns)

    def to_dict(self) -> dict:
        """The frozen-expectation form stored in case files."""
        return {
            "columns": self.columns,
            "column_types": self.column_types,
            "rows": self.rows,
        }


@dataclass
class KustoEmulator:
    """Minimal client for a running Kusto Emulator.

    Example::

        kusto = KustoEmulator()
        kusto.wait_until_ready()
        result = kusto.query("print x = 1 + 1")
    """

    endpoint: str = DEFAULT_ENDPOINT
    database: str = DEFAULT_DATABASE
    timeout: float = 60.0
    _headers: dict = field(
        default_factory=lambda: {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        },
        repr=False,
    )

    # -- transport ---------------------------------------------------------

    def _post(self, path: str, payload: dict, timeout: float | None = None) -> dict:
        req = urllib.request.Request(
            f"{self.endpoint}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:800]
            raise EmulatorError(f"HTTP {e.code} from {path}: {detail}") from e
        except urllib.error.URLError as e:
            raise EmulatorError(f"cannot reach emulator at {self.endpoint}: {e.reason}") from e
        except TimeoutError as e:
            # A read timeout surfaces as a bare TimeoutError, NOT as URLError, so
            # it used to escape this handler and abort whole sweeps. One slow
            # query is information about that query, not a transport failure.
            raise EmulatorError(
                f"emulator timed out after {timeout or self.timeout}s"
            ) from e
        except OSError as e:  # connection reset, broken pipe, ...
            raise EmulatorError(f"emulator transport error: {e}") from e

    # -- API ---------------------------------------------------------------

    def query(self, kql: str, database: str | None = None) -> QueryResult:
        """Run a KQL query and return its primary result table."""
        raw = self._post(
            "/v1/rest/query", {"db": database or self.database, "csl": kql}
        )
        return self._primary_table(raw)

    def command(self, csl: str, database: str | None = None) -> QueryResult:
        """Run a control command (``.create table``, ``.ingest``, …)."""
        raw = self._post(
            "/v1/rest/mgmt", {"db": database or self.database, "csl": csl}
        )
        return self._primary_table(raw)

    def is_ready(self) -> bool:
        try:
            self.query("print ready = 1", database=self.database)
            return True
        except EmulatorError:
            return False

    def wait_until_ready(self, timeout: float = 300.0, interval: float = 3.0) -> None:
        """Block until the emulator answers a trivial query.

        Raises:
            EmulatorError: if it never becomes ready. First boot pulls a large
                image and initializes storage, so allow several minutes.
        """
        import time

        deadline = time.monotonic() + timeout
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self.query("print ready = 1")
                return
            except EmulatorError as e:  # noqa: PERF203 - retry loop
                last = e
                time.sleep(interval)
        raise EmulatorError(
            f"emulator not ready after {timeout:.0f}s at {self.endpoint} (last: {last})"
        )

    # -- response parsing --------------------------------------------------

    @staticmethod
    def _primary_table(raw: dict) -> QueryResult:
        """Extract the primary result from a v1 response envelope.

        The v1 shape is ``{"Tables": [{"TableName", "Columns", "Rows"}, ...]}``.
        The first table is the query's own result; later tables are
        QueryStatus/QueryProperties metadata that we ignore.
        """
        tables = raw.get("Tables")
        if not tables:
            raise EmulatorError(f"unexpected response shape: {list(raw)[:6]}")

        primary = None
        for t in tables:
            if t.get("TableName") in (None, "Table_0", "PrimaryResult"):
                primary = t
                break
        primary = primary or tables[0]

        cols = primary.get("Columns", [])
        rows = primary.get("Rows", [])

        # A *partial* query failure comes back as HTTP 200 with the error
        # embedded in the row list as an object, e.g.
        #   {"Exceptions": ["Partial query failure: ..."]}
        # instead of a normal array row. Freezing that as an expectation would
        # silently bake a failed query into the corpus as if it had succeeded,
        # so treat it as an error.
        for row in rows:
            if isinstance(row, dict):
                detail = row.get("Exceptions") or row
                raise EmulatorError(f"partial query failure: {str(detail)[:400]}")
            if not isinstance(row, (list, tuple)):
                raise EmulatorError(f"unexpected row shape {type(row).__name__}: {str(row)[:200]}")
            if len(row) != len(cols):
                raise EmulatorError(
                    f"ragged row: {len(row)} values for {len(cols)} columns"
                )

        return QueryResult(
            columns=[c.get("ColumnName", c.get("Name", "")) for c in cols],
            column_types=[c.get("DataType", c.get("ColumnType", "")) for c in cols],
            rows=[list(r) for r in rows],
        )
