### Added

- **One Semantic Pipeline plan, "PR 1"**: a new
  `workflows.resolved_execution_context.ResolvedExecutionContext` composes the
  resolved-configuration pieces a run already produces separately --
  the ADR-049 D7 `CompatibilityEvaluationConfig`, each side's resolved L2
  `CompileContext`, and `AnalysisPlan`'s pre-flight operation/requested-depth
  pair -- into one typed, immutable container, plus a `resolution_digest()`
  fingerprint of that resolved input (deliberately distinct from
  `effective_config_digest`'s own outcome-aware digest, which needs a
  completed comparison's own facts). Pure, additive infrastructure: it
  re-derives nothing, and no command builds or consumes one yet -- landed
  first, with its own primitive-level test suite, so the follow-on consumer
  migration this enables has a concrete, already-tested target to converge
  on. No behavior change.
