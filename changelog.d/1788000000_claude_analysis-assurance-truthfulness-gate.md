### Fixed

- **A declared `checks[].analysis.assurance: complete` now actually gates
  `check-project.yml` runs — it previously did nothing at all.**
  `project validate`/`project plan` reject any other `analysis.assurance`
  value (e.g. `partial`) outright, since only `complete` maps onto a real
  mechanism anywhere in this codebase (`compare`/`scan --against
  --require-complete-analysis`); before this fix any structurally-valid
  identifier there passed validation, was forwarded verbatim into the
  generated run plan, and was silently honored by nothing, in either
  direction. `abicheck.buildsource.analysis_assurance_gate` is the new leaf
  module owning the rejection. Separately, `check-project.yml`'s "Run
  check-target" step and `actions/check-target/action.yml`'s new
  `require-complete-analysis` input now actually thread a `complete`
  declaration through to the existing `--require-complete-analysis` gate
  (skipped for `kind: bundle`, which the root Action already rejects it
  for) — closing the plan-to-execution gap for the one supported value.
