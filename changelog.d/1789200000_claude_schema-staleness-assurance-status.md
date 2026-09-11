### Fixed

- **A snapshot carrying schema-vintage-degraded facts (e.g. an unreliable
  `param_kind_facts_reliable`) no longer reports `run_outcome.assurance.
  status` as a silently overclaiming `"complete"`.** Loading a snapshot
  whose `schema_version` predates this abicheck's, or one that was re-saved
  since without ever being regenerated, already emitted a load-time
  `UserWarning` naming the stale fact — but that warning was stderr-only,
  invisible to any programmatic JSON consumer of the report, and
  `analysis_assurance`'s own completeness rollup had no signal for it at
  all. A new `analysis_assurance.schema_staleness_status` context field
  (`"clean"`/`"degraded"`) now folds into the existing `status` rollup the
  same way every other evidence-completeness axis does, naming the exact
  degraded field(s) in `notes`. The load-time warning and this new status
  now share one implementation
  (`policy.analysis_assurance_degraded_facts.degraded_reliability_facts`) so
  they can never independently drift on what counts as degraded. This changes only
  `analysis_assurance.status`/`notes` — never a compatibility verdict or
  finding; a run under `--require-complete-analysis` can now correctly exit
  non-zero for a schema-stale snapshot that previously read as complete,
  but every other invocation's exit code is unaffected.
