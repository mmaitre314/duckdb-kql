# Code review — the framework

> **Status: normative process doc.** This is *how* we review `duckdb-kql`, and
> *what* every review must cover. It sits beside [`../TRANSLATION.md`](../TRANSLATION.md)
> (what the code must *do*) and [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md)
> (how a change gets in). Where a review needs a semantic ruling, TRANSLATION.md
> wins and this doc defers to it.

This project's charter is one sentence: **a wrong answer is worse than no
answer.** That changes what a review is *for*. In most codebases the review's
job is to catch the change that crashes or regresses. Here the dangerous change
is the one that *runs cleanly and returns a plausible wrong number* — KQL and
SQL look alike exactly where they behave differently. A review that only checks
"does it work" passes that change every time. So our reviews are checklist-led
and trap-oriented, and the checklist is organized by area.

## Why checklists, and why by area

The literature is consistent on three points, and the framework is built on
them:

- **Review small, review slowly.** The SmartBear/Cisco study — still the largest
  on record — found defect-discovery drops sharply past **200–400 changed lines**
  per sitting and past **~60 minutes** or **~500 LOC/hour**. A 900-line diff does
  not get reviewed; it gets rubber-stamped. Split large changes and review each
  piece at reading pace. ([SmartBear best practices](https://smartbear.com/learn/code-review/best-practices-for-peer-code-review/),
  [Cisco case study](https://static1.smartbear.co/support/media/resources/cc/book/code-review-cisco-case-study.pdf))
- **A checklist beats ad-hoc reading.** Controlled experiments repeatedly find
  checklist-based reading finds more real defects with *fewer* false positives
  than reading with no list — the checklist fights **errors of omission**, the
  thing you forget to look for. That is the whole reason the area files below
  exist. ([CBR vs ad-hoc comparison](https://arxiv.org/pdf/0909.4260))
- **Review is also understanding and knowledge transfer, not only defects.**
  Bacchelli & Bird's study of modern review at Microsoft found the primary
  *value* is often shared understanding of the change; and that the reviewer's
  central need — understanding the change — is the thing tooling serves worst.
  So a review here explains *why* something is wrong, not just *that* it is.
  ([Bacchelli & Bird, ICSE 2013](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/ICSE202013-codereview.pdf))

Google's reviewer guide supplies the baseline every area inherits (design,
functionality, complexity, tests, naming, comments, consistency, docs) and the
governing standard: **approve when the change definitely improves overall code
health — not when it is perfect.** ([Google eng-practices](https://google.github.io/eng-practices/review/reviewer/looking-for.html))

## The universal checklist (every area, every review)

Before the area-specific list, every reviewer confirms:

1. **Understanding first.** State in one sentence what the change does and why.
   If you can't, you can't review it — ask, don't guess. (Bacchelli & Bird.)
2. **Design & altitude.** Does it belong here, in this layer, at this level of
   abstraction? Does it fix the *class* or special-case one instance?
   (TRANSLATION.md principle 3.)
3. **Complexity & over-engineering.** Is it more complex than the problem needs?
   Speculative generality is a defect, not foresight. (Google.)
4. **Correctness at the edges.** Null, empty, single-row, out-of-range, negative,
   overflow, non-ASCII, empty table. This is where wrong answers hide.
5. **Fails loud, never silently wrong.** An unsupported or unverifiable path
   raises (`KqlUnsupportedError` / `KqlSchemaError` / `KqlSyntaxError`) with a
   name and span — it does not emit SQL that "probably works." (Principle 5.)
6. **Tests prove the trap, not the happy path.** A green test proves only the
   case someone thought of; see the testing area file for what "proves
   correctness" means here.
7. **Comments explain the trap, not the mechanism.** `# KQL weeks start Sunday`
   earns its place; `# truncate the date` does not. (CONTRIBUTING Style.)
8. **Types are honest.** The package ships `py.typed`; an `Any` in a public
   signature is a promise broken silently — it must be *declared* and explained,
   never defaulted.

## Severity — rank every finding

Reviews here use a fixed scale so a report can be triaged without re-reading it:

| Severity | Meaning | Examples |
|---|---|---|
| **S1 — wrong answer** | Runs and returns something different from Kusto. The project's defining bug. | `has` as `LIKE '%x%'`; bare `join` as `INNER JOIN`; `CAST` where KQL returns null; `sort` defaulting to `ASC`. |
| **S2 — safety / contract** | Injection surface, a broken layering/type promise, an unhandled error that should raise cleanly, data-loss or resource leak. | Caller bytes reaching SQL text; `Any` in a public signature; a UDF double-registered. |
| **S3 — correctness-adjacent** | Wrong only on an edge not yet reached, or a latent trap the tests don't pin. | An un-pinned null-ordering default; a missing overflow test. |
| **S4 — maintainability** | Complexity, naming, dead code, comment-explains-what, avoidable duplication. | — |
| **Nit** | Non-blocking preference. Prefix `Nit:` and never block on it. (Google.) | — |

An **S1 or S2 blocks**; the change does not merge until it is fixed or
consciously converted into a loud refusal. S3/S4/Nit are reported and negotiated
against overall code-health improvement, not held to perfection.

## Reporting format (what a reviewer returns)

One entry per finding, most-severe first:

```
[S1] <file>:<line> — <one-line claim>
  What breaks: <the concrete input and the wrong output vs. Kusto>
  Rule/contract: <R-rule ID, invariant, or the promise violated>
  Fix direction: <the class-level fix, not a query special-case>
```

End with **what you checked and found clean** — an enumerated attack surface.
"Looks good" with nothing enumerated is not a review. If you found nothing,
prove you looked.

## The areas

Each file is a self-contained checklist for one slice of the codebase. A
reviewer (human or subagent) takes one area, reads its file plus this charter
and TRANSLATION.md §4, and reports in the format above.

| Area | File | Owns |
|---|---|---|
| Translation correctness | [`translation-correctness.md`](translation-correctness.md) | `parser.py`, `lower.py`, `ir.py`, `translate/`, `schema.py`, `engine.py` — the transpiler and its semantic fidelity |
| Public API, layering & typing | [`public-api-and-typing.md`](public-api-and-typing.md) | `__init__.py`, the three-layer boundary, `py.typed`, error surface |
| Security & injection safety | [`security-and-injection.md`](security-and-injection.md) | `params.py`, identifier/literal emission, the "no caller bytes in SQL" invariant, CLI input |
| Kusto client compatibility | [`kusto-client-compat.md`](kusto-client-compat.md) | `kusto/` — the `azure-kusto-data` drop-in and its fidelity |
| Testing, oracle & fixtures | [`testing-oracle-and-fixtures.md`](testing-oracle-and-fixtures.md) | `tests/`, `oracle.py`, `comparison.py`, `fixtures.py` — does the verification actually verify? |
| Tooling, packaging, CI & docs | [`tooling-packaging-ci-docs.md`](tooling-packaging-ci-docs.md) | `cli.py`, `tools/`, `pyproject.toml`, `.github/workflows/`, generated-doc staleness |

## Past reviews

- [`review-2026-08-04.md`](review-2026-08-04.md) — the first full pass of this
  framework over the whole repo (at `728fb55`): 1 S1, 3 S2, 4 S3, 1 S4.

Boundaries overlap on purpose at one seam: **identifier/literal quoting** is a
translation concern (is it *correct*?) and a security concern (is it *safe*?).
Translation owns correctness of the emitted form; security owns whether any
untrusted byte can reach it. When in doubt, both look.

The generated ANTLR parser under `src/duckdb_kql/_antlr/` is **out of scope for
all areas** — it is vendored verbatim and regenerated, never edited
(`grammar/UPSTREAM.md`). Review the grammar (`grammar/*.g4`), not its output.
