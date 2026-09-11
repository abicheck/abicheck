### Fixed

- **The composite Action's assurance-floor enforcement no longer silently
  no-ops when `assurance.require_complete: true` is set only through
  `.abicheck.yml`.** `action/run.sh`'s `_assurance_gated()` now reads the
  JSON report's own `analysis_assurance_exit_contribution` field instead of
  the retired `require-complete-analysis` Action input, so the belt-and-
  suspenders exit-1 floor still fires independently of `fail-on-breaking`/
  `fail-on-api-break`.
- **`check-project.yml`'s `checks[].analysis.assurance: complete` is
  enforced again.** `actions/check-target/action.yml` gained an
  `analysis-assurance-complete` input that merges an
  `assurance: {require_complete: true}` fragment into the `build-config`
  the internal analysis step reads — the config-overlay replacement for the
  retired `require-complete-analysis` input/flag.
- **A `--contract` compare's persisted receipt no longer disagrees with
  itself under `assurance.require_complete: true`.**
  `contract_context.with_resolved_gate()` now accepts the resolved
  `require_complete_analysis` value, so `effective_config_fields["gate.
  require_complete_analysis"]` and `contract_context.evaluation_context.
  resolved_config.gate.require_complete_analysis` agree.
