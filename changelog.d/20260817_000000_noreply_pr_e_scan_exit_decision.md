### Added

- **`scan --against --format json` now persists the same canonical
  `ExitDecision` `compare` has carried at its top-level `exit` key since PR
  G1 (#789).** Nested at `diff.exit` (schema 1.18) — matching where its own
  constituent `analysis_assurance_exit_contribution`/`contract_coverage_
  exit_contribution` fields already live — it names which axis
  (`compatibility_gate`, `contract_coverage`, `analysis_assurance`, or
  `clean`) actually decided the baseline comparison's exit code, instead of
  leaving a reader to re-derive it from the separately-emitted `severity`/
  contribution fields. A maintainer-promoted `--crosscheck KEY=error`
  finding still raises the process exit past this block's own code, the
  same way it already raises the persisted `severity` block — `diff.exit`
  is kept consistent by re-stamping to `promoted_crosscheck` when that
  happens, rather than silently disagreeing with the real exit. Part of CLI
  cleanup phase two's PR E (Action machine-report) — see the plan doc for
  what of PR E remains open (the Action's own renderer, and deleting
  `--annotate`/`--annotate-additions`).
