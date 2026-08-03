<!--
Thanks for contributing. CONTRIBUTING.md has the detail; this is the short list.
Delete the sections that do not apply.
-->

## What this changes

<!-- One or two sentences. -->

## Ground truth

<!--
For a new or changed mapping: where the expected behaviour came from. The
emulator, a frozen corpus case, or a documented divergence — not the docs alone,
which have been wrong before.
-->

## The trap

<!--
For a new or changed mapping: the specific case where the obvious mapping would
be wrong, and the test that covers it. If there is genuinely no trap, say so.
-->

## Checklist

- [ ] `pytest` passes
- [ ] `ruff check src tests tools` passes
- [ ] `python tools/gen_support_matrix.py` produces no diff (regenerate if the
      supported surface moved)
- [ ] Coverage baselines updated if they went up
      (`BASELINE_PASSING`, `BASELINE_SUPPORTED`)
- [ ] `CHANGELOG.md` updated under `Unreleased` for anything user-visible
