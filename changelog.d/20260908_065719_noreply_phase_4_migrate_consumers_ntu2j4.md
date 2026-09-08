<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **The GitHub Action's `mode: scan` request routing was investigated for
  Phase 4 of `docs/contribute/plans/one-comparison-product.md`** (ADR-068
  D2), but the internal translation to `compare` is **disabled**, not
  active: `action/run.sh` builds the translated `abicheck compare AGAINST
  ARTIFACT` command behind `_SCAN_NEEDS_LEGACY_CLI`, and that predicate is
  now unconditionally `true` for every `mode: scan` request, baseline
  comparisons included. Live verification across several review rounds
  found `compare`'s automatic cross-source-checks stage has no way to
  reproduce `scan`'s own advisory-only stripping of single-version hygiene
  findings (`_strip_automatic_cross_source_findings()`) for a baseline
  comparison, at any depth or evidence combination — not a narrow gap a
  routing condition can work around. So every `mode: scan` request,
  baseline or audit-only, still runs through the unmodified legacy `scan`
  CLI branch, with unchanged observable verdict/exit-code/report-JSON/
  PR-comment-body output. The translated-command code path remains in
  `action/run.sh`, unreachable and clearly marked as such, documenting the
  narrower divergences (evidence-contract strictness, header/include
  resolution, report schema, dependency-scope tagging, exit-code mapping)
  a future fix would need to close before it could be safely re-enabled.
