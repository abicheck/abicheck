### Changed

- **`appcompat.py` split into facts, evaluation and workflow (ADR-061)** —
  application/plugin compatibility checking (`compare --used-by`,
  `--required-symbol`) now reads consumer imports and library exports as typed
  facts (`extract.consumer_imports`, `extract.library_export_facts`,
  `model.consumer_requirements`), evaluates them in the pure
  `policy.consumer_requirements`, and wires them in
  `workflows.consumer_scope`/`workflows.consumer_scope_standalone`. A consumer
  that cannot be read is now recorded as an explicit `FAILED` fact with a
  reason. `abicheck.appcompat` stays as the documented public import path
  (re-export only); CLI output, exit codes and report schemas are unchanged.
