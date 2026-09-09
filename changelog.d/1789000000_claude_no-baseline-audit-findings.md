### Fixed

- **`abicheck compare --no-baseline` now reports the single-build audit's
  findings.** The audit is implemented as a self-compare, and it used to
  assert the resulting change set was empty — an invariant that held until
  the eleven cross-source hygiene checks and the pattern/preprocessor
  pre-scan moved into `compare()`, where they legitimately fire on a
  self-compared snapshot. Every stored-snapshot candidate aborted with an
  unhandled `AssertionError`, and a live binary rendered a report with
  nothing in it. The change set is now partitioned: the comparison half is
  still provably empty (and still enforced, as a raised error rather than an
  `assert`, so the guard survives `python -O`), while the candidate-side half
  is reported under a new `findings[]` block. Findings carry their evolution
  state, which with no baseline is always `persistent` or `not_evaluated`,
  never `introduced`.
- **`compare --no-baseline` now folds `-H`/`--header` into public-header
  provenance**, as a two-sided `compare` always has. Without it every
  declaration resolved to an unknown scope origin, so `exported_not_public`,
  `public_not_exported`, `rtti_for_internal_type` and
  `public_to_internal_dependency` evidence-gated themselves off and reported
  nothing.
- **`compare --no-baseline --contract` is no longer silently inert.** The
  flag was parsed and documented but never forwarded, so the contract-coverage
  ledger it gates on was never populated: a run against a headerless candidate
  exited `0` where the two-sided equivalent exited `1`. A CI job relying on it
  as a gate got no warning that it never ran.
- **`compare --no-baseline` now reads `--sources`, `--build-info`, `--depth`
  and `--dry-run`.** All four were parsed and ignored. `--depth build`/
  `--depth source` is held to the same evidence-contract floor as the
  two-sided path (exit `7`) instead of silently degrading to symbols-only
  evidence and reporting a clean audit; `--dry-run` reports that condition as
  a blocker before any analysis runs. An `old=`-scoped evidence input
  (`--sources old=…`) is now a usage error rather than a silent drop.
- **`compare --no-baseline` now honours `--write FORMAT=PATH` and
  `--include-system-declarations`.** Both were accepted and silently
  dropped — the same class as the three above. `--write` renders from the
  same analysis rather than re-running it (ADR-068 D4), and a secondary
  format outside the audit's supported set is a usage error naming
  `--write`, not `--format`.

- **An option `compare --no-baseline` does not implement is now a usage
  error, not a silent no-op.** "Accepted but never read" caused four separate
  defects on this path, none of which any test could catch, so the rule is
  inverted: every `compare` option is either wired to the audit or named in
  an explicit unsupported table whose message says why, and a test fails if
  any option is neither. Newly rejected rather than dropped: `--used-by`,
  `--used-by-manifest`, `--required-symbol`, `--use-cases`,
  `--post-manifest`, `--env-matrix`, `--diagnostic-comparison`,
  `--old-variant`/`--new-variant`, `--bundle-facts-*`,
  `--since`/`--changed-path`, `--select`/`--select-required`,
  `--output-dir`, `--abi3`, `--budget`, `--severity-preset`, `--pack`,
  `--config`, `--instantiation-manifest`, `--follow-deps`, `--search-path`,
  `--ld-library-path`, `--debug-info` and `--devel-pkg`.

### Added

- **`compare --no-baseline --format` accepts `sarif`, `junit` and `oneline`**
  alongside `json` and `markdown`. SARIF and JUnit are findings formats with
  no verdict slot to leave empty, which is exactly what an audit produces, and
  both are how a CI job consumes one. `html` and `review` remain a usage
  error by ruling rather than deferral: both render a comparison (verdict
  badge, old-to-new counts, release recommendation) and an audit has none of
  those — the error now says so and points at `oneline`.

### Changed

- **`compare --no-baseline`'s report schema is `2.0`.** `findings[]`,
  `cross_source_evolution` and a candidate-only `pattern_preprocessor_scan`
  block are new. `changes` remains present and always `[]`, so a consumer
  reading it off any abicheck report still finds it — the audit's own content
  is under `findings[]`.
