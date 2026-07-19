<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Documentation

- **ADR-046 (proposed): Source Graph Identity v2** — records the
  design for evidence-preserving node/edge merge (replacing today's
  silent first-writer-wins), a USR-based `EntityResolver` centralizing
  the identity-fragmentation fallbacks scattered across `MarkReachability`,
  a per-`(kind, role)` graph coverage matrix, a named `TraversalPolicy`,
  and a proof-path selection preference order — the impact-analysis-layer
  plan's Phase 2 (`docs/development/plans/g29-impact-analysis-layer.md`).
  Documentation only; no code changes in this fragment.
