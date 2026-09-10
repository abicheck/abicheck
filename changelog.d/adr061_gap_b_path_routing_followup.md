### Fixed

- **Internal: four more path-based automation surfaces now route to
  `checker_policy.py`'s/`contract_gating.py`'s real ADR-061 gap B owners,
  not just their re-export facades.** `scripts/classify_perf_paths.py`'s
  performance-sensitivity classifier, `.github/workflows/
  realworld-validation.yml`'s PR trigger, `scripts/skill_eval_surface.py`'s
  consumed-surface roots, and `scripts/check_bugfix_test_contract.py`'s
  `verdict-gate-exit` trigger all still named only the facade path, so a
  future PR touching only `policy/classification.py` (verdict logic) or
  `policy/contract_finding_relevance.py` (contract gating) would have been
  invisible to all four — a perf regression there wouldn't gate CI, the
  real-world validation lane wouldn't run, the skill eval pack wouldn't
  regenerate, and the PR wouldn't be asked the verdict/gate/exit-code
  question a change there warrants. Added the real owners alongside the
  existing facade entries in each, plus test coverage
  (`tests/test_realworld_validation_workflow.py`,
  `tests/test_bugfix_test_contract.py`) proving each new path actually
  triggers.
