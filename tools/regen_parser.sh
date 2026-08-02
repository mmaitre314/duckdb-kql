#!/usr/bin/env bash
# Regenerate the vendored ANTLR parser from grammar/*.g4.
#
# Only maintainers run this — the generated Python is committed so that
# installing duckdb-kql never requires Java. See grammar/UPSTREAM.md.
set -euo pipefail

ANTLR_VERSION="4.13.2"   # must match antlr4-python3-runtime in pyproject.toml
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/src/duckdb_kql/_antlr"
JAR="${ANTLR_JAR:-$ROOT/.antlr/antlr-$ANTLR_VERSION-complete.jar}"

command -v java >/dev/null || { echo "error: java is required" >&2; exit 1; }

if [[ ! -f "$JAR" ]]; then
  echo "downloading ANTLR $ANTLR_VERSION ..."
  mkdir -p "$(dirname "$JAR")"
  curl -fsSL -o "$JAR" \
    "https://repo1.maven.org/maven2/org/antlr/antlr4/$ANTLR_VERSION/antlr4-$ANTLR_VERSION-complete.jar"
fi

echo "generating Python target from grammar/Kql.g4 ..."
rm -rf "$OUT"
mkdir -p "$OUT"
( cd "$ROOT/grammar" && java -jar "$JAR" -Dlanguage=Python3 -visitor -o "$OUT" Kql.g4 )
touch "$OUT/__init__.py"

echo "done. Now run the L1 corpus test — the parsed-block count must not go down."
