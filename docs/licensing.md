# Licensing & Provenance Review

> Status: reviewed 2026-08-02. Covers every third-party input this project
> depends on — the Kusto documentation corpus we harvest tests from, the
> reference implementations we import test cases from, the vendored ANTLR
> grammar, and the Kusto Emulator we use as a test oracle.
>
> **Not legal advice.** This is a practical working policy; the repo owner signs
> off. Companions: [`test-plan.md`](./test-plan.md) (what we harvest and why),
> [`implementation-plan.md`](./implementation-plan.md) (what we vendor).

## Summary

| Input | License | Verdict |
|---|---|---|
| `dataexplorer-docs` **code samples** (KQL queries) | MIT | ✅ Harvest freely (attribution) |
| `dataexplorer-docs` **prose** (incl. example output tables) | CC-BY-4.0 | ⚠️ Avoid — we generate expectations instead (§3) |
| `microsoft/Kusto-Query-Language` (incl. vendored `Kql.g4`) | Apache-2.0 | ✅ Vendor; ship the license text, state modifications. **No upstream `NOTICE` file exists** (checked at the pinned commit), so §4(d) does not apply |
| `saoc90/kql-to-sql` | MIT | ✅ Import with notice |
| ClickHouse KQL tests | Apache-2.0 | ✅ Import + state modifications |
| KustoLoco / BabyKusto | MIT | ✅ Import with notice |
| **Kusto Emulator** (`kustainer`) | MS Software License Terms | ✅ **Approved** for dev/CI oracle use (§5) |
| **DejaVu Sans Mono** (outlined into `docs/assets/*.svg`) | Bitstream Vera | ✅ Redistributable; notice given. Not shipped as a font — the glyphs are paths (§4) |

Nothing is copyleft; nothing conflicts with shipping `duckdb-kql` under **MIT**.

## 1. The frequency scan is not the risky part

Counting operator/function occurrences across the docs (test-plan §8) yields
**statistics** — facts, not copyrightable expression — and redistributes nothing.
The committed artifact is our own ranking table. **No sign-off needed; it can
proceed immediately.** Risk begins only when we **commit harvested content** as
fixtures, because that is redistribution.

## 2. `dataexplorer-docs` is dual-licensed (verified)

| Part | File | License | Applies to |
|------|------|---------|-----------|
| Prose / doc body | `LICENSE` | **CC-BY-4.0** | narrative text **and the example output tables** |
| Code samples | `LICENSE-CODE` | **MIT** | the ` ```kusto ` query blocks |

The queries are MIT → trivially compatible with this MIT project (attribution
only). The **expected-output tables are prose → CC-BY-4.0**: permissive, but a
*different* license with attribution obligations, and embedding it would mean part
of an MIT repo isn't MIT.

## 3. Policy: harvest queries, generate expectations

The Kusto Emulator is a **licensing win as well as a fidelity win**. We therefore:
- **Take the queries** (MIT) from the docs.
- **Never commit the docs' output tables.** Generate expected results ourselves on
  the emulator (test-plan §5.2) — our own generated data, not Microsoft's prose.
  (A doc table may be used as a transient dev-time cross-check, not committed.)
- Record per-case provenance in the case file (`source`, `source_commit`,
  `oracle`, image digest) — already in the test-plan §4.1 schema.

This shrinks the CC-BY surface to ~zero while *improving* fidelity.

## 4. Imported corpora & vendored code — all permissive

| Source | License | Obligation |
|---|---|---|
| `saoc90/kql-to-sql` | MIT | preserve notice |
| ClickHouse KQL `.sql`/`.reference` | Apache-2.0 | notice + **state modifications** (§4) — we reformat into our case schema, so say so |
| `microsoft/Kusto-Query-Language` (incl. vendored `Kql.g4`) | Apache-2.0 | ship the license text (§4a), mark modified files (§4b), retain attribution (§4c). §4(d) is moot: upstream has no `NOTICE` file |
| **DejaVu Sans Mono** (logo wordmark) | Bitstream Vera | preserve notice; do not redistribute *as a font* under a Bitstream/Vera name — we redistribute outlines, not a font |
| `azure-kusto-data` (a few helper bodies in the compat layer) | MIT | preserve copyright + permission notice — see `docs/kusto-client.md` §Provenance |
| KustoLoco / BabyKusto | MIT (verified) | preserve notice |

None are copyleft; nothing affects our MIT license. Vendoring `Kql.g4` makes the
repo "MIT + an Apache-2.0 subtree" — standard practice, handled by
`THIRD-PARTY-NOTICES.md` plus `grammar/UPSTREAM.md`.

Full license texts live in [`licenses/`](../licenses/) and ship in **both** the
wheel and the sdist. That is not cosmetic: the wheel contains
`src/duckdb_kql/_antlr/`, which is generated from the Apache-2.0 grammar, and
§4(a) requires a recipient of a derivative work to get a copy of the license.
Until `license-files` was set in `pyproject.toml`, `pip install duckdb-kql`
delivered the derived parser with no notice at all.

## 5. Kusto Emulator EULA — review

Reviewed against the actual *Microsoft Software License Terms — Azure Data
Explorer Emulator* (accepted via `ACCEPT_EULA=Y`).

> ### ✅ Owner decision (2026-08-02)
> The repo owner has **reviewed and accepted §2(e)** (see §5.4) and approved
> using the Kusto Emulator as this project's dev/CI test oracle, subject to the
> posture in §5.5.

### 5.1 Clearly permitted / compatible with our design
- **§1(a)** — install and use **any number of copies** on your devices for
  **internal business purposes**. Our use (a dev/CI test oracle) is internal: we
  run it, we never expose it.
- **§1(a)** also bars use in a **"live operating environment"** — we never do; the
  emulator is dev/CI-only and is **never a runtime dependency**.

### 5.2 Prohibitions and how we comply
| Clause | Term | Our compliance |
|---|---|---|
| §2(g) | No **share/publish/distribute/lease** the software, or provide it as a stand-alone offering | We **never redistribute the image** — CI pulls it from MCR, pinned by digest. Referencing an image in CI config is not distribution. |
| §2(d) | No **disclosing benchmark test results** to third parties without written approval | We assert **correctness only** and **never publish performance numbers** derived from it. |
| §2(b) | No reverse engineer / decompile / **derive the source code** | We do **black-box** input→output observation, never derive source. Mitigated further by treating the **public CC-BY docs as the normative spec** and the emulator as a *checker*. |
| §2(c) | No removing notices | N/A — we don't modify the image. |
| §1(d) | Competitive-benchmarking waiver for **direct competitors** | An OSS KQL-to-SQL translator is not a direct competitor of ADX; and this is a waiver clause, not a prohibition. |
| §3 | Feedback grants Microsoft broad rights | Be aware if we file issues/feedback. |
| §5 | Export-control compliance | Standard. |
| §10–11 | As-is, **no warranty**, damages capped at **US $5.00** | Reinforces: the emulator is a convenience oracle, not a guarantee. Our correctness story must not *depend* on it. |

### 5.3 Notable absences (good news)
- **No "competing products" clause** — a worry flagged during earlier research
  that does **not** exist in these terms (§1(d) is only a benchmarking waiver
  aimed at direct competitors).
- **No general restriction on publishing the software's output.** The only
  disclosure restriction is §2(d), specifically **benchmark** results. Microsoft
  called that out explicitly and did *not* restrict functional output — so
  publishing our frozen *expected-result* fixtures is not restricted by these
  terms.

### 5.4 §2(e) — the ambiguity, and the decision
> *"you will not … use the software for **commercial, non-profit, or
> revenue-generating activities**"*

Read literally, "non-profit activities" could sweep in an unpaid open-source
project. That reading sits awkwardly against **§1(a)**, which *expressly permits*
use for "internal business purposes" (most of which are commercial) — so the
sensible reading is that §2(e) bars using the software **as, or as part of, an
offering** (commercial or not), rather than barring incidental dev/test use of it
as a tool.

**Decision: accepted by the repo owner (2026-08-02).** We proceed on the reading
that using the emulator as an internal development and testing tool — never
shipped, never offered to others, never part of a product — is permitted under
§1(a). Revisit if Microsoft updates these terms.

### 5.5 Posture (binding on the implementation)
1. Emulator stays a **dev/CI-only checker**: never shipped, never exposed as a
   service, never a runtime dependency.
2. **Never publish perf numbers** from it (§2(d)).
3. Treat the **public docs (CC-BY) as the normative KQL specification** and the
   emulator as *verification*. This keeps §2(b) comfortably clear and means the
   project is not *dependent* on the emulator.
4. Keep the emulator **optional and replaceable**: the freeze-and-compare design
   (test-plan §5.2) means contributors never need it to run the test suite.
5. **Fallback (retained, not currently needed):** the docs' own published output
   tables (CC-BY — legally fine, just requires attribution and the hygiene of
   §2–§3), and/or `kql-to-sql` / `KustoLoco` (both MIT).

## 6. Checklist before committing harvested data

- [x] **Read the emulator EULA** — reviewed above; §2(e) accepted by the owner
      (§5.4). Emulator approved for dev/CI oracle use.
- [ ] `NOTICE` / `THIRD-PARTY-NOTICES` listing every upstream + license text.
- [ ] Every case file carries `source`, `source_commit`, and upstream license.
- [ ] No doc output tables committed (expectations are emulator-generated).
- [ ] Apache-2.0 imports carry a "modified from" statement.
- [ ] Large sample datasets downloaded/cached in CI, not committed; confirm
      `StormEvents` provenance (NOAA-derived, likely public domain — verify).

## 7. Sources

- `dataexplorer-docs` licenses: [`LICENSE` (CC-BY-4.0)](https://github.com/MicrosoftDocs/dataexplorer-docs/blob/main/LICENSE) · [`LICENSE-CODE` (MIT)](https://github.com/MicrosoftDocs/dataexplorer-docs/blob/main/LICENSE-CODE)
- Kusto Emulator terms: https://aka.ms/adx.emulator.license · overview: https://learn.microsoft.com/en-us/azure/data-explorer/kusto-emulator-overview
- `microsoft/Kusto-Query-Language` (Apache-2.0): https://github.com/microsoft/Kusto-Query-Language
- `saoc90/kql-to-sql` (MIT): https://github.com/saoc90/kql-to-sql
- ClickHouse (Apache-2.0): https://github.com/ClickHouse/ClickHouse
- KustoLoco (MIT): https://github.com/NeilMacMullen/kusto-loco
