### Added

- **`compare --budget`** — a wall-clock guard (`15m`/`900s`/`1h`, or a bare
  number of seconds) on a `compare` run's deadline-aware stages (build/source
  evidence collection, the automatic cross-source/pattern/preprocessor
  scans). Overflow reports exit `5` (ADR-064's `BUDGET_OVERFLOW` axis)
  instead of running unbounded. Absorbed from `scan --budget`
  (ADR-068 §3 #19); typed API: `CompareRequest.budget_s`.
- **`compare`'s `--depth build`/`--depth source` live-extraction
  evidence-contract floor** — a pinned depth that a *live* binary side's own
  extraction fails to reach now reports exit `7` (`EVIDENCE_CONTRACT_ERROR`)
  instead of silently degrading to shallower evidence and reporting
  `NO_CHANGE`. Pre-serialized snapshot operands stay exempt (there is
  nothing this run extracted to blame). Closes a real, previously-
  undocumented gap in `compare`'s own native-CLI resolution (ADR-068 §3
  #28).

### Fixed

- **`--depth build`/`--depth source` no longer silently degrades evidence
  on the native `compare` CLI.** `workflows.artifact.execute.
  enforce_requested_depth` already enforced this floor for the typed-API
  resolution path; the native CLI's own resolution never called it, so
  `compare --depth build old.so new.so` with no `--build-info`/`--sources`
  quietly fell back to symbols-only evidence and reported `NO_CHANGE`/exit
  `0` instead of failing loudly. See `docs/contribute/known-gaps.md` and
  ADR-068's 2026-09-09 amendment for the full account.
