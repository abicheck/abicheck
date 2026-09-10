### Fixed

- **Internal: the mutation lane now also mutates `policy/evidence_status.py`,
  `checker_policy.py`'s second ADR-061 gap B owner.** The prior fix retargeted
  `[tool.mutmut].only_mutate`/`.github/workflows/mutation.yml` from
  `checker_policy.py` to `policy/classification.py`, but `checker_policy.py`
  actually split into two owners — `policy/classification.py` (verdict
  classification) and `policy/evidence_status.py` (`EvidenceTier.rank`,
  `is_cross_source_resolved`, `has_binary_evidence` — per-finding epistemic
  status and cross-comparison evolution, which feed reporting and gate
  contributions). The second owner was missed, so that logic was silently no
  longer mutation-tested at all. Added `abicheck/policy/evidence_status.py`
  to `only_mutate` and the matching workflow path filters, with
  `tests/test_mutation_workflow_contract.py`'s `_ACCEPTED_KILL_LOSS`
  bookkeeping updated to match. Checked every other facade this PR touched
  (`contract_gating.py`, `reclassify.py`, `contract_coverage_ledger.py`,
  `qualified_name_segments.py`, `api_types.py`) against the mutation
  config: none of them was ever in `only_mutate` before this PR, so
  `checker_policy.py`'s two-owner split was the only gap of this kind.
