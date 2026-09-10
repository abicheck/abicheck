### Fixed

- **Internal: reconciled two concurrent ADR-061 tracks on merge.** Merged
  `main` into this branch, which had landed a second, independent ADR-061
  track (module dispositions and `architecture/dispositions.yaml`) since
  this PR was opened. Real, non-textual conflicts resolved:
  - `architecture/modules.yaml`'s `facades` list: union of both branches'
    additions (this branch's `checker_policy`/`contract_coverage_ledger`/
    `contract_gating`/`qualified_name_segments`/`reclassify`/`api_types`
    alongside `main`'s independently-added `bundle_facts`/
    `bundle_facts_serialization`/`bundle_facts_store`).
  - Two test files' mutation-lane reachability records
    (`tests/test_mutation_workflow_contract.py`'s `_ACCEPTED_KILL_LOSS`,
    `tests/unit/storage/adr062_scope.py`'s exclusion comment): took the
    narrower, already-verified reachability `main`'s own bundle_facts-split
    fix recorded, layered this branch's `checker_policy` →
    `policy.classification`/`policy.evidence_status` renaming on top where
    that chain is still genuinely reached.
  - A real architecture-gate regression the merge itself introduced:
    `main`'s module-dispositions track classified `qualified_name_segments.py`/
    `_walk.py` into `model`, unaware of this branch's already-landed gap B
    split — the facade imports both `compare` and `storage`, which a
    `model` classification cannot do without a real dependency-direction
    violation. Removed both from `model`'s `legacy_paths`; added a
    `retain` disposition entry for the facade in the newly-merged-in
    `architecture/dispositions.yaml`, matching `api_types.py`'s own
    precedent for a cross-layer compatibility shim that cannot have a
    single owning layer.
  - Raised `abicheck/schemas/__init__.py`'s `no_growth` debt baseline
    1290 → 1293: two independently-merged, non-overlapping additions
    landed on the same file (this branch's own three-line ADR-061 gap B
    module-docstring note, plus `main`'s `REPORT_SCHEMA_VERSION`/
    `SCAN_SCHEMA_VERSION` bump for the additive
    `gate.fail_on_removed_library` field).

  Verified post-merge: `check_architecture.py` 0 errors, `mypy`/`ruff`
  clean, the full fast unit suite (42390 passed) and this PR's own facade/
  mutation-contract/docs-trigger test files all green.
