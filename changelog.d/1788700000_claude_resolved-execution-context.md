### Added

- **One Semantic Pipeline plan, "PR 1"**: a new
  `workflows.resolved_execution_context.ResolvedExecutionContext` composes the
  resolved-configuration pieces a run already produces separately --
  the ADR-049 D7 `CompatibilityEvaluationConfig`, each side's resolved L2
  `CompileContext`, `AnalysisPlan`'s pre-flight operation/requested-depth
  pair, and a new `EvidenceView` for the coarse `--depth` ladder -- into one
  typed, immutable container, plus a `resolution_digest()` fingerprint of
  that resolved input (deliberately distinct from `effective_config_digest`'s
  own outcome-aware digest, which needs a completed comparison's own facts).
  `EvidenceView` closes the "requested/effective/available depth" axis
  without duplicating its one existing authority
  (`analysis_assurance.AnalysisAssurance`): it always carries
  `requested_depth` (knowable pre-execution) and `available_depths` (the
  static four-rung `--depth` ladder); `effective_depth`/`depth_satisfied`
  stay `None` until `EvidenceView.from_assurance()` copies them verbatim off
  a real, already-computed `AnalysisAssurance` -- never re-derived.
  `ResolvedExecutionContext.with_assurance()` returns a new context (frozen
  dataclasses don't mutate) carrying the completed view. Pure, additive
  infrastructure: it re-derives nothing, and no command builds or consumes
  one yet -- landed first, with its own primitive-level test suite, so the
  follow-on consumer migration this enables has a concrete, already-tested
  target to converge on. No behavior change.
