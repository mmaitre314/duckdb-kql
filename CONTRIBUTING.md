# Contributing

Thanks for looking. This project has one unusual rule, and most of what follows
is a consequence of it.

> **A wrong answer is worse than no answer.** KQL and SQL look alike in places
> where they behave differently, so a mapping that is *nearly* right runs
> cleanly and returns numbers nobody questions. Every mapping here is verified
> against the real KQL engine, and anything that cannot be verified raises
> instead.

## Getting set up

```bash
git clone https://github.com/mmaitre314/duckdb-kql
cd duckdb-kql
pip install -e ".[dev]"
pytest
```

A dev container is included (`.devcontainer/`) if you would rather not install
Docker, DuckDB and the emulator yourself.

Before opening a pull request:

```bash
ruff check src tools tests
mypy
pytest
```

All three run in CI on every pull request.

## Adding a mapping

Most contributions are one KQL function or operator. The shape is the same each
time:

1. **Find the ground truth.** Run the construct on the Kusto Emulator and record
   what it actually returns — including the edge cases (null input, empty
   string, out-of-range index, negative number). `docs/oracle-harness.md`
   explains how to run it. Do not infer behaviour from Microsoft's docs; several
   of the divergences already recorded here contradict them.
2. **Add a registry row.** Mappings are data, not code:
   `src/duckdb_kql/translate/functions.py` holds one row per construct, citing
   the `R`-rules it must honour. If your mapping needs a shape a `{0}`-style
   template cannot express, add a special form in `translate/__init__.py` and
   say why in a comment.
3. **Write the trap test.** Not "it returns something" — the specific case where
   the obvious mapping would be wrong. If you cannot think of one, look harder:
   `docs/TRANSLATION.md` §4 lists twelve places the two languages diverge.
4. **Regenerate the support matrix.**

   ```bash
   python tools/gen_support_matrix.py
   ```

   `docs/kql-support.md` is generated and CI fails if it is stale. Fill in the
   `Limitations and gotchas` cell with what you learned in step 1 — that column
   is the point of the document.
5. **Update the baselines** if coverage moved:
   `tests/test_behavior.py::BASELINE_PASSING` and
   `tests/test_profile_azure_monitor.py::BASELINE_SUPPORTED` may only go up.

## When *not* to add a mapping

Refusing is a valid, and sometimes the correct, outcome. Add the construct to
the refusal list in `tools/gen_support_matrix.py` with a reason if:

- the nearest DuckDB function is a *different* function that returns
  plausible-looking output (`hash_xxhash64` → DuckDB's `hash`);
- the precision or algorithm cannot be matched (`datetime_part('nanosecond')`,
  the `hll` and `tdigest` sketch formats);
- it would require network or code execution (`geo_location`, the plugins).

A refusal with a reason is a contribution. A mapping that is 95% right is a bug
report waiting to be filed against someone else's report.

## Things CI will check that are easy to miss

| Check | Why |
|---|---|
| `docs/kql-support.md` regenerates identically | The support table must not be able to say something the code does not do. |
| The documented request-option table matches `OPTION_SUPPORT` | Same reason, for the Kusto client. |
| Layer 0 installs and works with `duckdb` absent | The layering claim is only true if it is tested. |
| The CLI does not import `duckdb` | Build-time translation is meant to run in a minimal CI image. |
| Links in the docs resolve | Including the absolute ones the README uses for PyPI. |
| A consumer's type checker sees real types | The package ships `py.typed`, so `Any` in a public signature is a promise broken silently. `tests/test_typing.py` checks from the outside; `mypy` alone cannot. |
| The committed parser matches `grammar/Kql.g4` | The generated parser is committed so installs need no Java. |

## The emulator

The acceptance suite compares against the Kusto Emulator, which runs in Docker.
It is a **development and CI tool only**: never a runtime dependency, never
redistributed, never exposed as a service. Per its licence, do not publish
timing or performance numbers measured against it. See `docs/licensing.md` §5.

Contributions that only touch translation logic do not need it — the frozen
corpus in `tests/cases/` carries the expectations it produced. You need the
emulator when you are adding a mapping and have to *establish* an expectation.

## Reviewing a change

Reviews here are checklist-led and organized by area, because the dangerous
change in this project is the one that runs cleanly and returns a *plausible
wrong answer*. [`docs/code-review/`](docs/code-review/README.md) is the
framework: a charter (severity scale, reporting format, and the small-diff /
checklist discipline the literature backs) plus one checklist per area of the
codebase — translation correctness, public API & typing, security & injection,
the Kusto client, testing & the oracle, and tooling/packaging/CI/docs. Reviewing
a change means picking the area(s) it touches and working its list; the
[`adversarial-reviewer`](.claude/agents/adversarial-reviewer.md) agent automates
the translation-correctness pass over a mapping diff.

## Style

Match the surrounding code. Two habits worth copying:

- **Comments explain the trap, not the mechanism.** `# KQL weeks start Sunday;
  date_trunc('week') starts Monday` is worth writing. `# truncate the date` is
  not.
- **Tests are named after what would break.** `test_payload_never_reaches_the_sql_text`
  says what is being protected; `test_parameters` does not.
- **`Any` is declared, not defaulted.** The package is fully typed and ships
  `py.typed`. Where a value genuinely has no type — an ANTLR tree node, a
  `dynamic` document — say `Any` and say why in a comment. An unannotated
  function silently becomes `Any` for every caller, which is the same failure
  mode as a wrong answer: it looks fine.

## Reporting a wrong answer

The most valuable bug report for this project is a query that runs and returns
something different from Kusto. Please include the KQL, the result you got, the
result Kusto gave, and — if you have it — the output of:

```python
duckdb_kql.to_sql(your_query)
```

Security issues go through [SECURITY.md](SECURITY.md) instead, not the public
tracker.

## Releasing (maintainers)

Publishing is automated, but deliberately not automatic: a tag builds and checks,
and only a **published GitHub Release** uploads to PyPI.

1. Move `CHANGELOG.md`'s `Unreleased` section under the new version, with the
   date.
2. Bump the version in **both** `pyproject.toml` and
   `src/duckdb_kql/__init__.py`. CI compares them against the tag and fails the
   release if any of the three disagree, so there is no silent mismatch — but
   it is quicker to get right the first time.
3. Optionally dry-run: **Actions → Release → Run workflow**, with
   *Publish to TestPyPI* checked. Then
   `pip install -i https://test.pypi.org/simple/ duckdb-kql` in a clean
   environment.
4. Tag and push: `git tag v0.1.0 && git push origin v0.1.0`. This builds and
   runs the version check; it does not publish, so a bad tag costs nothing.
5. Publish the GitHub Release for that tag. That is what uploads to PyPI, via
   [Trusted Publishing][oidc] — there is no API token stored in this repository.
   The built artifacts are attached to the release afterwards.

The first release needs the publisher registered on PyPI first
(`owner: mmaitre314`, `repository: duckdb-kql`, `workflow: release.yml`,
`environment: pypi`), and a `pypi` environment configured on the repository.

[oidc]: https://docs.pypi.org/trusted-publishers/

## Licensing

Contributions are accepted under the MIT licence in [LICENSE](LICENSE). If you
vendor anything third-party, add it to
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) and check
[`docs/licensing.md`](docs/licensing.md) for how the existing entries are
handled.
