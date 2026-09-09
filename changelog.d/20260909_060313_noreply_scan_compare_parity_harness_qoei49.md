<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`scan --against` baseline comparisons now gate cross-source findings the
  same way `compare` does.** Previously, a baseline `scan` silently
  demoted every cross-source finding (`exported_not_public`,
  `private_header_leak`, `unversioned_exported_symbol`, and the rest of
  ADR-068's eleven checks) to advisory-only, so a real accidental-export or
  unversioned-symbol issue could report `NO_CHANGE`/exit 0 under `scan
  --against` while the equivalent `compare` invocation correctly reported
  `COMPATIBLE_WITH_RISK` and, under a stricter severity preset, a non-zero
  exit code. This was a divergence, not an intended `scan`-specific
  behavior — see the 2026-09-09 amendment to ADR-068
  (`docs/contribute/adr/068-one-comparison-product-and-scan-retirement.md`)
  for the full account. **This is a documented breaking change** to
  `scan --against`'s baseline-comparison result and to the GitHub Action's
  `mode: scan` contract: a baseline scan that previously reported
  `NO_CHANGE`/exit 0 for a cross-source-only issue may now report the
  finding and, depending on your severity configuration, a non-zero exit
  code.
