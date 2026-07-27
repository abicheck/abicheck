<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **ADR-049 Phase 3: shadow contract-relevance evaluator** (no behavior
  change): `abicheck/contract_evaluation.py` adds
  `evaluate_change_contract_relevance()`/
  `evaluate_snapshot_pair_contract_relevance()`, computing a
  `ContractEvaluationDecision` (relevance/reason/assurance) for one finding
  from evidence that already exists — `surface.py`'s public-surface
  resolution (ADR-024) and `finding_identity.py`'s identity tiers (Phase 2).
  It is a true shadow module: not called from `checker.compare`, the CLI,
  or any report path, so no verdict, exit code, or report output changes.
  Only `ContractMode.PUBLIC`/`ALL` are implemented (`EXPORTS` raises
  `NotImplementedError` — no export-root-closure evidence provider exists
  yet), and `ContractRelevance.UNKNOWN_UNPROVEN` is never emitted (every
  case that would need it degrades to the weaker `UNKNOWN_UNRESOLVED` with
  reason `required_evidence_incomplete`, since this module cannot verify
  the closed-world completeness claim `UNKNOWN_UNPROVEN` requires with
  today's evidence providers).
