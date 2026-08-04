"""``python -m duckdb_kql`` — the same command as the ``duckdb-kql`` script.

Present so the CLI is reachable when the console script is not on PATH, which
is the normal situation inside a virtualenv a CI job did not activate.
"""

from __future__ import annotations

from .cli import main

raise SystemExit(main())
