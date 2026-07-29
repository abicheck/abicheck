### Fixed

- **`scan --against --policy-file` now actually applies evidence-policy
  overrides** — `_run_baseline_compare` forwarded `policy_file` to
  `compare_snapshots` but not to `prepare_embedded_build_source`, which is
  what applies `require_evidence`/evidence-verdict overrides (ADR-033 D7)
  and emits `evidence_required_missing`; a policy file requiring evidence
  silently had no effect on `scan --against` (Codex review, PR #657).
- **`scan --against --env-matrix` with malformed YAML now exits 64 (usage
  error)** instead of an uncaught traceback, matching `compare`'s existing
  `AbicheckError` → `click.UsageError` handling for the same input (Codex
  review, PR #657).
