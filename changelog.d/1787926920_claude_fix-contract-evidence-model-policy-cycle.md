### Fixed

- **ADR-061**: reclassifying `compatibility_evaluation_config.py` into `policy`
  (an already-merged sibling PR) exposed a pre-existing `model -> policy`
  forbidden import: `contract_evidence.py` (classified `model`) imports
  `CompatibilityEvaluationConfig` from it as a real dataclass field type
  (`EvaluationContextBlock`), not a type-only reference — `contract_evidence.py`
  is itself ADR-049 Phase 4's persisted contract-relevance evidence/decision
  shape, which genuinely depends on the policy-layer resolved-configuration
  type it records, so `model` (whose `may_import` is `[]` by design) was
  always the wrong classification for it, not a fixable import direction.
  Removed `contract_evidence.py` from `model`'s `legacy_paths`, leaving it
  unclassified — the same "leave it, document why" pattern this migration
  already uses for several other genuinely cross-layer-conflicted modules
  (see `AGENTS.md`'s and recent ADR-061 PRs' own "deliberately left
  unclassified" sections). `python scripts/check_architecture.py` returns to
  0 errors; this was blocking every other in-flight ADR-061 classification PR
  whose merge with `main` surfaced the same pre-existing violation. The
  identical one-line change was also ported directly onto two other
  in-flight PRs (#784, #922) that hit this same failure before this fix
  merged, so neither had to wait on merge order; both ports become no-ops
  once this PR merges and those branches re-merge `main`.
