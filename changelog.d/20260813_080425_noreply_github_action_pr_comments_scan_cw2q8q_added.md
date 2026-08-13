### Added

- **The GitHub Action's sticky PR comment now supports `mode: scan`** (single
  artifact, not `new-library-set`) — `pr-comment`/`pr-comment-on`/
  `pr-comment-mode`/`pr-comment-detail` all work the same way they already do
  for `compare`, rendering `scan`'s own verdict, breaking/needs-review
  findings, a green "Public API additions" section, and a short risk/coverage
  summary line, without a second `compare` run. `scan --against`'s JSON gained
  an always-on `additions` array and its complement `quality` array (schema
  `1.13`, `cli_scan_baseline.py`) so the comment can itemize every compatible
  finding the same way `compare`'s own report already does via its full
  `changes` list; `NOT_COMPARABLE` (a scope/profile mismatch) is now its own
  Action verdict — and unconditionally fails the step, since no `fail-on-*`
  input governs a run in which no comparison happened — and header counts
  stay exact even when a large diff's `findings`/`additions`/`quality` were
  truncated below the report cap.
- **`scan` gained `--secondary-format`/`--secondary-output`**, mirroring
  `compare`'s own flags: render a second output format (typically JSON)
  from the same scan run without re-running it. The GitHub Action's own
  PR-comment renderer uses this to avoid a second, potentially
  `--depth build/source`-expensive scan when the primary step output stays
  the documented default `--format text`, and now also reuses the
  already-materialized JSON for `--format json` with no `--output` (the
  CLI's stdout mode) instead of falling through to a rerun there too, and
  skips entirely (rather than rerunning) after a `BUDGET_OVERFLOW`, which
  a rerun could only reproduce. `compare` and `scan` now share one
  `--secondary-format`/`--secondary-output` decorator and coherence
  validator (`cli_secondary_output.py`) instead of two independently
  drifting inline copies.
- **`scan --against`'s JSON gained `diff.policy`**, the resolved
  compatibility policy name, so the comment footer reports the policy that
  actually classified the run instead of always the `strict_abi` fallback.
