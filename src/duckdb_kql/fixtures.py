"""StormEvents fixture — the sample table 251 corpus cases query.

Dev/CI only, like :mod:`oracle` and :mod:`comparison`; never imported by the
translation path.

Why the data is synthetic
-------------------------
``StormEvents`` is ADX's standard sample table. The real CSV lives behind
``kustosamples.blob.core.windows.net`` (unreachable from some environments,
including the one this was built in), and vendoring NOAA/Microsoft sample data
would add a licensing question we do not need.

We do not need the authentic rows. Microsoft's published outputs were never our
ground truth — the **emulator** produces expectations from whatever data it is
given (``docs/licensing.md`` §3). Load identical rows into the emulator and into
DuckDB and the freeze-and-compare loop proves precisely what it should: that our
translation agrees with the real KQL engine.

The cost, stated plainly: results here will **not** match the numbers printed in
the Microsoft docs.

What does matter is that the data is *non-vacuous*. ``State == "FLORIDA"``
against a fixture with no Florida rows returns empty on both sides and passes
while proving nothing — the most expensive kind of green test. So the
vocabularies are the real ones (actual US states, actual NOAA event types, 2007
dates), chosen to cover every literal the corpus filters on, and
``tests/test_fixtures.py`` fails if a fixture-backed case degenerates to empty.

Determinism is the contract: the emulator and DuckDB must see byte-identical
rows, and frozen expectations are reproducible only if this always emits the
same file. Hence the fixed seed and the committed checksum.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import random
from pathlib import Path

OUT = Path("tests/fixtures/kusto/StormEvents.csv")

#: Row count. Big enough that `summarize`/`top`/percentile cases have something
#: to chew on, small enough to commit and to ingest in seconds.
ROWS = 5000

SEED = 20070101

# The real StormEvents schema: (column, kql_type, duckdb_type).
SCHEMA: tuple[tuple[str, str, str], ...] = (
    ("StartTime", "datetime", "TIMESTAMP"),
    ("EndTime", "datetime", "TIMESTAMP"),
    ("EpisodeId", "int", "INTEGER"),
    ("EventId", "int", "INTEGER"),
    ("State", "string", "VARCHAR"),
    ("EventType", "string", "VARCHAR"),
    ("InjuriesDirect", "int", "INTEGER"),
    ("InjuriesIndirect", "int", "INTEGER"),
    ("DeathsDirect", "int", "INTEGER"),
    ("DeathsIndirect", "int", "INTEGER"),
    ("DamageProperty", "int", "INTEGER"),
    ("DamageCrops", "int", "INTEGER"),
    ("Source", "string", "VARCHAR"),
    ("BeginLocation", "string", "VARCHAR"),
    ("EndLocation", "string", "VARCHAR"),
    ("BeginLat", "real", "DOUBLE"),
    ("BeginLon", "real", "DOUBLE"),
    ("EndLat", "real", "DOUBLE"),
    ("EndLon", "real", "DOUBLE"),
    ("EpisodeNarrative", "string", "VARCHAR"),
    ("EventNarrative", "string", "VARCHAR"),
    ("StormSummary", "dynamic", "JSON"),
)

STATES = [
    "ALABAMA", "ALASKA", "ARIZONA", "ARKANSAS", "CALIFORNIA", "COLORADO",
    "CONNECTICUT", "DELAWARE", "FLORIDA", "GEORGIA", "GUAM", "HAWAII", "IDAHO",
    "ILLINOIS", "INDIANA", "IOWA", "KANSAS", "KENTUCKY", "LOUISIANA", "MAINE",
    "MARYLAND", "MASSACHUSETTS", "MICHIGAN", "MINNESOTA", "MISSISSIPPI",
    "MISSOURI", "MONTANA", "NEBRASKA", "NEVADA", "NEW HAMPSHIRE", "NEW JERSEY",
    "NEW MEXICO", "NEW YORK", "NORTH CAROLINA", "NORTH DAKOTA", "OHIO",
    "OKLAHOMA", "OREGON", "PENNSYLVANIA", "PUERTO RICO", "RHODE ISLAND",
    "SOUTH CAROLINA", "SOUTH DAKOTA", "TENNESSEE", "TEXAS", "UTAH", "VERMONT",
    "VIRGINIA", "WASHINGTON", "WEST VIRGINIA", "WISCONSIN", "WYOMING",
]

EVENT_TYPES = [
    "Astronomical Low Tide", "Avalanche", "Blizzard", "Coastal Flood",
    "Cold/Wind Chill", "Debris Flow", "Dense Fog", "Drought", "Dust Devil",
    "Dust Storm", "Excessive Heat", "Extreme Cold/Wind Chill", "Flash Flood",
    "Flood", "Frost/Freeze", "Funnel Cloud", "Hail", "Heat", "Heavy Rain",
    "Heavy Snow", "High Surf", "High Wind", "Ice Storm", "Lake-Effect Snow",
    "Lightning", "Marine Hail", "Marine High Wind", "Marine Strong Wind",
    "Marine Thunderstorm Wind", "Rip Current", "Seiche", "Sleet",
    "Storm Surge/Tide", "Strong Wind", "Thunderstorm Wind", "Tornado",
    "Tropical Storm", "Waterspout", "Wildfire", "Winter Storm", "Winter Weather",
]

SOURCES = [
    "Public", "Trained Spotter", "Law Enforcement", "Emergency Manager",
    "Broadcast Media", "Newspaper", "COOP Observer", "Storm Chaser",
    "Department of Highways", "Amateur Radio",
]

LOCATION_WORDS = [
    "SPRINGFIELD", "FRANKLIN", "CLINTON", "GREENVILLE", "SALEM", "FAIRVIEW",
    "MADISON", "GEORGETOWN", "ARLINGTON", "ASHLAND", "OXFORD", "BURLINGTON",
    "MANCHESTER", "CLAYTON", "MILTON", "AUBURN", "BRISTOL", "DOVER",
]


def _narrative(rng: random.Random, event: str, state: str) -> str:
    templates = [
        "A {e} was reported across parts of {s}.",
        "Widespread {e} caused damage in several counties of {s}.",
        "Trained spotters reported {e} in {s}.",
        "{e} produced minor damage in {s}. No injuries were reported.",
        "Severe weather including {e} moved through {s} during the afternoon.",
    ]
    return rng.choice(templates).format(e=event.lower(), s=state.title())


def generate() -> list[list[object]]:
    rng = random.Random(SEED)
    start_of_year = dt.datetime(2007, 1, 1)
    rows: list[list[object]] = []

    # StartTime must be UNIQUE. `sort by ... StartTime` and `top N by` break ties
    # arbitrarily, so duplicate sort keys make a deterministic query produce
    # engine-specific output and the comparison reports a divergence that is
    # really just a tie. Sampling without replacement removes the ambiguity
    # instead of teaching the comparator to ignore it.
    seconds_in_year = 365 * 24 * 60 * 60
    offsets = rng.sample(range(seconds_in_year), ROWS)

    for i in range(ROWS):
        state = rng.choice(STATES)
        event = rng.choice(EVENT_TYPES)

        start = start_of_year + dt.timedelta(seconds=offsets[i])
        end = start + dt.timedelta(minutes=rng.choice([0, 5, 15, 30, 60, 180, 720]))

        # Most events are harmless; a long tail does damage. A uniform
        # distribution would make every percentile/top-N case uninteresting.
        severe = rng.random() < 0.18
        deaths_direct = rng.choice([0, 0, 0, 1, 2]) if severe else 0
        injuries_direct = rng.choice([0, 1, 3, 12]) if severe else 0
        # Varied rather than drawn from a handful of values: `top N by
        # DamageProperty` over five distinct amounts is almost all ties, which
        # makes the "top 10" an arbitrary choice among hundreds of equals.
        damage_property = rng.randrange(1, 5_000_000) if severe else 0
        damage_crops = rng.randrange(1, 500_000) if severe and rng.random() < 0.5 else 0

        lat = round(rng.uniform(18.0, 65.0), 4)
        lon = round(rng.uniform(-160.0, -66.0), 4)

        rows.append([
            start.strftime("%Y-%m-%d %H:%M:%S"),
            end.strftime("%Y-%m-%d %H:%M:%S"),
            10000 + (i // 3),                 # episodes group a few events
            20000 + i,
            state,
            event,
            injuries_direct,
            rng.choice([0, 0, 0, 2]) if severe else 0,
            deaths_direct,
            rng.choice([0, 0, 1]) if severe else 0,
            damage_property,
            damage_crops,
            rng.choice(SOURCES),
            rng.choice(LOCATION_WORDS),
            rng.choice(LOCATION_WORDS),
            lat,
            lon,
            round(lat + rng.uniform(-0.5, 0.5), 4),
            round(lon + rng.uniform(-0.5, 0.5), 4),
            _narrative(rng, event, state),
            _narrative(rng, event, state),
            json.dumps({
                "TotalDamages": damage_property + damage_crops,
                "StartTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "Details": {"Description": event, "Location": state},
            }, separators=(",", ":"), sort_keys=True),
        ])

    return rows


def write(path: Path, rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="" + \n keeps the file byte-identical on every platform; CRLF would
    # change the checksum and, worse, the ingested string values.
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
        w.writerow([c for c, _, _ in SCHEMA])
        w.writerows(rows)


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()




# ---------------------------------------------------------------------------
# Loading — the emulator and DuckDB must end up with identical rows
# ---------------------------------------------------------------------------

TABLE = "StormEvents"

#: A companion table the corpus joins against (`StormEvents | join PopulationData
#: on State`). Small, one row per state, and the second table the `join` work
#: needs — a join cannot be tested against a single table.
POPULATION_TABLE = "PopulationData"
POPULATION_OUT = Path("tests/fixtures/kusto/PopulationData.csv")
POPULATION_SCHEMA: tuple[tuple[str, str, str], ...] = (
    ("State", "string", "VARCHAR"),
    ("Population", "long", "BIGINT"),
)


def generate_population() -> list[list[object]]:
    """One row per state, with a spread that makes `Population > 5000000` select
    a meaningful subset rather than all or nothing.

    Populations are synthetic like the rest of the fixture (see module docstring)
    but derived from a fixed seed, so they are identical in both engines.
    """
    rng = random.Random(SEED + 1)
    return [[s, rng.randrange(500_000, 30_000_000)] for s in STATES]


def write_population(path: Path = POPULATION_OUT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
        w.writerow([c for c, _, _ in POPULATION_SCHEMA])
        w.writerows(generate_population())


def ensure_csv(path: Path = OUT) -> Path:
    """Write the fixture if it is missing. Never overwrites a committed file."""
    if not path.is_file():
        write(path, generate())
    if not POPULATION_OUT.is_file():
        write_population()
    return path


#: Everything a fixture-backed case may reference: (table, csv, schema).
TABLES: tuple[tuple[str, Path, tuple[tuple[str, str, str], ...]], ...] = (
    (TABLE, OUT, SCHEMA),
    (POPULATION_TABLE, POPULATION_OUT, POPULATION_SCHEMA),
)


def load_duckdb(con: object, path: Path = OUT) -> None:
    """Load every fixture table into a DuckDB connection.

    Types are declared explicitly rather than sniffed: letting ``read_csv``
    guess would make our side's schema depend on the data sample, so a column
    could silently become BIGINT here and int there and turn a type mismatch
    into a false divergence.
    """
    ensure_csv(path)
    for table, csv_path, schema in TABLES:
        columns = "{" + ", ".join(f"'{c}': '{t}'" for c, _, t in schema) + "}"
        con.execute(  # type: ignore[attr-defined]
            f'CREATE OR REPLACE TABLE "{table}" AS '
            f"SELECT * FROM read_csv('{csv_path.as_posix()}', header=true, "
            f"columns={columns}, timestampformat='%Y-%m-%d %H:%M:%S')"
        )


def kusto_create_command(table: str, schema: tuple[tuple[str, str, str], ...]) -> str:
    cols = ", ".join(f"{c}:{t}" for c, t, _ in schema)
    return f".create table {table} ({cols})"


def kusto_ingest_command(
    table: str, csv_path: Path, container_path: str = "/kustodata"
) -> str:
    """Ingest a CSV the compose file mounts read-only at ``/kustodata``.

    ``ignoreFirstRecord`` skips the header row; without it the header ingests as
    a data row and every string column silently gains a bogus value.
    """
    return (
        f".ingest into table {table} "
        f"(@'{container_path}/{csv_path.name}') "
        f'with (format="csv", ignoreFirstRecord=true)'
    )


def load_emulator(kusto: object) -> dict[str, int]:
    """Create and populate every fixture table, returning row counts.

    Idempotent: each table is dropped first, so a re-run cannot double-ingest and
    quietly double every count() in the frozen expectations.
    """
    counts: dict[str, int] = {}
    for table, csv_path, schema in TABLES:
        kusto.command(f".drop table {table} ifexists")            # type: ignore[attr-defined]
        kusto.command(kusto_create_command(table, schema))        # type: ignore[attr-defined]
        kusto.command(kusto_ingest_command(table, csv_path))      # type: ignore[attr-defined]
        result = kusto.query(f"{table} | count")                  # type: ignore[attr-defined]
        counts[table] = int(result.rows[0][0])
    return counts
