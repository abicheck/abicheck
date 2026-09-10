### Fixed

- **Internal: full repository sweep for ADR-061 gap B's "stale reference to
  the old facade path, not the new owner" bug class.** Automated review had
  found this class trickling in one surface at a time (mutation config
  twice, four path-routing scripts, the bugfix-contract trigger); this round
  grepped the whole repository for every old facade name/path this PR moved
  logic out of (`checker_policy`, `contract_gating`, `reclassify`,
  `contract_coverage_ledger`, `api_types`, `qualified_name_segments`) across
  `.github/workflows/*.yml`, `scripts/*.py`, `docs/**/*.md`,
  `docs/**/*.yaml`, and `pyproject.toml`, and classified every hit as either
  a legitimate compatibility-facade reference (left as-is: ordinary code
  importing through the supported facade path, the ADR's own historical
  narrative, schema prose) or stale routing/trigger metadata that needed the
  real owner added alongside it. Two more real gaps found and fixed:
  - `scripts/check_bugfix_test_contract.py`'s `verdict-gate-exit` trigger
    was still missing `policy/evidence_status` — `has_binary_evidence()`/
    `is_cross_source_resolved()` feed gate decisions the same way
    `policy/classification.py` does.
  - `docs/_meta/topics.yaml`'s `verdicts`/`change-kinds`/`python-api`/
    `policies`/`contract-relevance-and-coverage` topics, plus the matching
    per-page `depends_on` front matter on `docs/learn/verdicts.md`,
    `docs/reference/change-kinds.md`, and
    `docs/learn/contract-aware-compatibility.md`, still named only
    `checker_policy.py`/`api_types.py`/`reclassify.py`/
    `contract_coverage_ledger.py` — an edit to the real owner
    (`policy/classification.py`, `policy/evidence_status.py`,
    `workflows.contracts`/`workflows.request_inputs`/
    `model.header_ast_frontends`, `policy/reclassify.py`,
    `policy/coverage_ledger.py`) wouldn't have surfaced the docs-review
    notice `scripts/check_docs_review_triggers.py` exists to give.

  Deliberately left as-is: `docs/contribute/usecase-registry.yaml`'s
  `modules:` evidence lists (checked for existence only, by
  `tests/test_usecase_registry.py`, not used as a change-trigger the way
  `depends_on`/the mutation config/the path classifiers are — a different
  bug class, not this one) and every plain import of the facade in
  generator/analysis scripts (`gen_detector_spec.py`,
  `measure_contract_shadow.py`, `benchmark_comparison.py`,
  `check_fp_rate.py`), which resolve to the identical real objects either
  way and lose nothing by going through the facade.

  New test coverage: `tests/test_docs_review_triggers.py` gained a
  real-repo parametrized test proving `find_triggers()` fires on the new
  owner paths, not just the facades; `tests/test_bugfix_test_contract.py`
  gained the matching `verdict-gate-exit` case for
  `policy/evidence_status.py`.
