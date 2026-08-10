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
ruff check src tools tests demo
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
| The demo notebook still executes | `demo/` ships with its outputs, so a broken demo keeps looking authoritative. |

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

**The git tag is the version.** There is no file to bump: `hatch-vcs` reads the
tag at build time and writes it into the distribution, so the tag and the package
cannot disagree.

Publishing is automated but deliberately not automatic — only a **published
GitHub Release** uploads to PyPI. A tag on its own builds and checks; it does
not publish, so a bad tag costs nothing.

The whole procedure:

1. Go to **Releases → Draft a new release**, create the tag `vX.Y.Z` on `main`,
   click **Generate release notes**, and publish.

**Tag plain versions only** — `v0.2.0`, or `v0.2.0rc1` for a pre-release. A tag
ending in `.devN` or `.postN` cannot be built forward from: `setuptools_scm` uses
those fields itself to express distance from the last tag, so it has nothing left
to bump and *every subsequent commit* fails to build. `v0.0.1.dev1` did exactly
that. The release workflow now rejects such a tag up front, with a message naming
the tag to delete.

That is it. The release workflow builds the sdist and wheel, verifies the built
version equals the tag, smoke-tests the wheel in a clean environment, uploads to
PyPI via [Trusted Publishing][oidc], and attaches the artifacts to the release.

Two things worth doing around it:

- **Rehearse with a manual run** for a first release, or after any packaging
  change: *Actions → Release → Run workflow*. That does everything except
  publish — build, metadata check, and the clean-environment smoke test of the
  wheel — so a packaging break surfaces without spending a version number. The
  same build also runs on every push and pull request.

- **Edit the generated notes** before publishing. There is no `CHANGELOG.md`;
  the release notes and the git history are the record. *Generate release notes*
  gives you the list of commits, which is a fine skeleton and a poor explanation
  — so lead with a short paragraph on what changed for the people using the
  package, especially anything that returns a different answer than it did
  before. This is the reason commit messages here are written to be read.

**Before the very first release**, two one-time setup steps that only an owner
can do:

1. Register the PyPI publisher (a *pending* publisher, since the project does not
   exist yet) at <https://pypi.org/manage/account/publishing/>: owner
   `mmaitre314`, repository `duckdb-kql`, workflow `release.yml`, environment
   `pypi`.
2. Create the `pypi` environment under **Settings → Environments**. The name
   must match the publisher registration, because PyPI checks the environment in
   the OIDC claim. Adding yourself as a required reviewer on it makes every
   upload a deliberate two-step action.

## Licensing

Contributions are accepted under the MIT licence in [LICENSE](LICENSE). If you
vendor anything third-party, add it to
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) and check
[`docs/licensing.md`](docs/licensing.md) for how the existing entries are
handled.
