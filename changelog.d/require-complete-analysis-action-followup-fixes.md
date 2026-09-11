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
- **A bundle (`kind: bundle`) project check declaring
  `checks[].analysis.assurance: complete` no longer ends the whole
  `check-project.yml` run in a hard CLI usage error.** The directory/
  package release fan-out has no single `analysis_assurance` result to
  gate on, so `_reject_set_input_flags` unconditionally rejects
  `require_complete_analysis=true` for it — `project_targets.py`'s
  `_check_issues` now rejects this combination at run-plan generation
  time instead, with an actionable message, mirroring how
  `allow_new_target` is already rejected for a bundle check.
- **The composite Action's assurance-overlay generation step no longer
  silently discards a malformed `assurance:` block in a project's
  `build-config`.** A wrong-typed value (`assurance: false`, a bare
  string, or a list) — every one already rejected by normal `BuildConfig`
  ingestion — now fails the "Generate assurance-overlay config" step
  loudly instead of being replaced with `{}`; only a missing key or an
  explicit `null` is treated as "safe to overlay onto".
- **A `--contract` compare's persisted receipt now records *why*
  `gate.require_complete_analysis` was enabled, not just that it was.**
  `field_provenance["gate.require_complete_analysis"]` was still omitted
  after the fix above; `contract_context.with_resolved_gate()` now stamps
  a `project_config`-layer provenance entry itself whenever an explicit
  `require_complete_analysis=True` is passed — the field has no CLI
  override and no pack route, so `True` can only ever have come from
  `.abicheck.yml`'s `assurance.require_complete`.
