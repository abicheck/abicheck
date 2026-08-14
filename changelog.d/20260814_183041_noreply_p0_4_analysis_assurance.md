### Added

- **`compare` now reports `analysis_assurance`** — a new, always-present,
  orthogonal answer to "how complete and trustworthy was the evidence behind
  this comparison", independent of the compatibility verdict and the
  policy/severity gate (P0.4, `abicheck/analysis_assurance.py`). Every
  `--format json` report gains a top-level `analysis_assurance` object
  (report schema 2.38 -- renumbered from 2.37 during the `origin/main`
  rebase, since P0.2's `layer_coverage` root-target keys claimed 2.37 first)
  with a `status` of `complete`/`partial`/`failed`/
  `not_comparable`/`not_requested`, requested-vs-effective depth (reusing the
  existing `binary`/`headers`/`build`/`source` vocabulary), translation-unit
  and export accounting, header-parse-context and fact-set-comparability
  status, and source-graph completeness — rolled up from evidence the
  pipeline already computes. A new `--require-complete-analysis` flag on
  `compare` (single-pair only) makes an incomplete status contribute exit
  `1`, folded with the same `max` discipline `--contract-evaluation`'s
  coverage axis already uses: it raises a clean `0` to `1` and never lowers a
  `2`/`4`. Purely additive — every existing invocation's exit code and
  report shape are unchanged unless the new flag is passed. See
  `docs/reference/exit-codes.md`'s new "Analysis-assurance contribution"
  section.

### Fixed

- **`analysis_assurance` now reflects an out-of-band `--old/new-build-info`/
  `--old/new-sources` pack, not just each snapshot's own embedded evidence**
  (P1 review). `compare` resolves such a pack separately from the snapshot
  and uses it for the run's real findings/coverage without ever attaching it
  back onto the snapshot; `analysis_assurance` previously never saw that
  pack, so a genuinely partial or failed out-of-band pack could still read
  `status="complete"` and let `--require-complete-analysis` exit `0` despite
  the real evidence being incomplete. `analysis_assurance` is now recomputed
  once the real pack is resolved, closing the gap `--require-complete-analysis`
  exists to guard against.
- **`graph_completeness` now accounts for a narrowed-scope source-graph
  pass, absent pass-coverage bookkeeping, and old/new graph asymmetry**
  (P1 review), instead of only checking `degraded_passes` and defaulting to
  `"complete"` for every other state. Two new values, `"narrowed"` and
  `"unknown"`, join the existing `"complete"`/`"degraded"`/`"not_collected"`.
- **`compare_report.schema.json`'s `analysis_assurance` key is now
  `required`** (schema 2.37, unchanged) alongside the report's other
  unconditional fields, matching how it is actually always emitted (P2
  review) — a 2.37 report missing the key now fails schema validation
  instead of silently passing.

