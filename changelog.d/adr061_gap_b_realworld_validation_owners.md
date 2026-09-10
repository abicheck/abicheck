### Fixed

- **Internal: `.github/workflows/realworld-validation.yml`'s real-package
  `--contract` filter now covers all four `checker_policy.py`/
  `contract_*.py` gap-B owners, not just one.** A prior round added
  `policy/classification.py` but missed `policy/evidence_status.py`
  (checker_policy.py's second owner — per-finding evidence status/
  cross-source evolution, which feeds gate decisions),
  `policy/contract_finding_relevance.py` (contract_gating.py's real owner),
  and `policy/coverage_ledger.py` (contract_coverage_ledger.py's real
  owner) — all three feed contract/gating behavior this lane's real-package
  `--contract` step explicitly exercises, and the existing
  `abicheck/contract_*.py` glob only matches the flat facades, not their
  `policy/`-nested owners. Cross-checked the workflow against every owner
  module this PR created (`policy/classification.py`,
  `policy/contract_finding_relevance.py`, `policy/coverage_ledger.py`,
  `policy/evidence_status.py`, `policy/reclassify.py`,
  `workflows/request_inputs.py`, `workflows/contracts.py`,
  `compare/qualified_name_normalization.py`,
  `storage/closure_identity.py`) — the other five were never in this
  workflow's scope before this PR either (no `reclassify.py`/`api_types.py`/
  `qualified_name_segments.py` reference existed), so adding them now would
  be new scope, not a regression fix. `tests/test_realworld_validation_workflow.py`
  gained assertions proving all three new paths are covered.
