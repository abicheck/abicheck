<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **Operation and entity resolvers come from their owner** — `operation_for_kind` and `entity_for_change` are now imported from `abicheck/report/change_operation.py`, where they are defined, by the report modules and tests that used them. `abicheck/reporter_markdown.py` no longer re-exports them, so that module no longer carries a second import path for them. The public `abicheck.reporter` names are unchanged. Rendered output is byte-identical.
