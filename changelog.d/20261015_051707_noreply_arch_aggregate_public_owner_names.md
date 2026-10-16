### Changed

- **Aggregate's sibling modules share public owner names** — `aggregate/load.py`
  now imports `malformed_gate_report` (from `contracts.py`),
  `analysis_assurance_exit` and `contract_coverage_declared` (from `gate.py`)
  under their public names instead of reaching across modules for underscore-
  private helpers. The underscore names remain as aliases in their owning
  modules, so no caller, test or import path changes. Behaviour, verdicts,
  exit codes and output are unchanged.
