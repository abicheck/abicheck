### Changed

- **`scan --artifact-set` now reports a member's evidence-contract abort
  (ADR-037 D5) at the same dedicated process exit code `7` a single-artifact
  `scan --against` already uses, instead of flooring the whole set's exit at
  the generic `1`.** This closes the last `--artifact-set`/`format: text`
  signal gap left open in the cli-cleanup-phase-two plan's PR G2 section: a
  `format: text` Action step previously had no way to distinguish this abort
  from a genuine CLI usage error or crash (both also exit `1`), since
  `--artifact-set` categorically rejects a JSON secondary output.
  `service_scan._aggregate_scan_set_verdict` now returns exit `7` (not `1`)
  whenever an `EVIDENCE_CONTRACT_ERROR` member is the worst signal in the
  set, matching what each member's own `ScanResult.exit_code` already
  reported; `action/run.sh`'s exit-`7` dispatch arm now fires unconditionally
  for `--artifact-set` too, with an artifact-set-specific error message.
  `docs/reference/exit-codes.md` and `docs/use/github-action.md` updated;
  `docs/contribute/plans/cli-cleanup-phase-two.md`'s PR G2 section records
  the design decision and why the exit-code approach turned out to
  generalize after all.
