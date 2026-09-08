<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **The GitHub Action's `mode: scan` now internally routes through
  `compare`** for the baseline-comparison case (`against`/`abi-baseline`
  resolved, not forced audit-only): `action/run.sh` builds a plain
  two-sided `abicheck compare AGAINST ARTIFACT` invocation instead of
  `abicheck scan ARTIFACT --against ...` (ADR-068 D2; plan
  `docs/contribute/plans/one-comparison-product.md` Phase 4 commit 1).
  `mode: scan` stays a fully documented, working Action input with
  identical observable verdict/exit-code/report-JSON/PR-comment-body
  output — only the internal CLI invocation changes. Every scan
  capability `compare` does not yet support (`new-library-set`/
  `--artifact-set`, `budget`, `risk-rules`, `crosscheck`'s `KEY=error`
  promotion syntax, `build-target`, and every audit-only/no-baseline
  invocation, since `compare --no-baseline`'s current CLI slice has no
  `--dry-run`, secondary `--write`, or build/source-evidence support yet)
  still routes through the unchanged legacy `scan` CLI branch, so no
  functionality is lost.
