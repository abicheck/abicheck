### Fixed

- **Internal: three ADR-061 gap B follow-up gaps found by automated review, all closed.**
  1. The remaining internal (non-test) callers of the `abicheck.api_types`
     compatibility facade — `service.py`, both `service_compare_pipeline.py`
     import sites, `service_dump_pipeline.py`, `service_compare_evidence.py`,
     `service_scan.py`, `scan_engine.py`, `dependency_info.py`,
     `compatibility_evaluation_frontend.py`, and five `cli_*.py` modules —
     now import `workflows.contracts`/`workflows.request_inputs`/
     `model.header_ast_frontends` directly, so `api_types.py`'s own claim
     ("every physically-migrated internal caller imports the owner
     directly") is actually true.
  2. `[tool.mutmut].only_mutate` and `.github/workflows/mutation.yml`'s path
     filters named `abicheck/checker_policy.py`, which the earlier gap B
     landing had already turned into a re-export facade with no verdict
     logic left in it — the weekly mutation lane was silently no longer
     exercising the real classification logic in `policy/classification.py`.
     Repointed both, plus `tests/test_mutation_workflow_contract.py`'s
     `_ACCEPTED_KILL_LOSS` bookkeeping for the four action/workflow tests
     that transitively reach it.
  3. `scripts/check_ai_readiness.py`'s orphan-`ChangeKind` scan
     (`changekind-detector`) excluded only `checker_policy.py` by name to
     avoid a "the definition file trivially mentions every kind" false
     positive — a rationale that predates ADR-061 gap B moving the real
     per-kind policy exception lists into `policy/classification.py`.
     Excluded that file too, so a policy-table mention of a kind can no
     longer stand in for a real detector reference.
