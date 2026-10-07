### Removed

- The flat re-export modules `abicheck.severity`, `abicheck.exit_decision`,
  `abicheck.contract_coverage_exit`, `abicheck.service_input_resolution`,
  `abicheck.contract_gating` and `abicheck.reclassify` are deleted. Import
  the owners instead: `abicheck.policy.severity`,
  `abicheck.policy.exit_decision` (and `abicheck.policy.exit_decision_precedence`
  for `resolve_scan_exit_decision`/`resolve_release_exit_decision`/
  `resolve_compare_exit_decision_with_abort_axes`),
  `abicheck.policy.contract_coverage_exit`, `abicheck.workflows.artifact.execute`
  and `abicheck.workflows.artifact.resolve`,
  `abicheck.model.contract_finding_relevance`, and `abicheck.policy.reclassify`.
