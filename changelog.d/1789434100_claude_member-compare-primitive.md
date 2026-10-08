### Changed

- **Internal: one per-member compare primitive.** A directory/package `compare` now runs each matched library pair through `abicheck.workflows.member_compare.compare_member`, the same `run_compare_request` path the typed API uses; `record_release_resolved_config` moved there from `abicheck.cli_compare_receipt`. Output, exit codes and report schemas are unchanged, and a new test pins that each member's findings, disposition audit and verdict match a scalar `compare` of the same pair at every release size from 1 to 4.
