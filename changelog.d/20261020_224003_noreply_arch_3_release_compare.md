<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **Release finding projection has one owner** — the per-library finding list, the `show=` view, the impact table, the uncapped annotations and the verdict fold every release renderer reads moved from the release CLI's `cli_compare_release_matrix` module to `report.release_findings` (ADR-061: a report projection belongs to `report/`). The names change (`project_release_library_results`, `release_finding_dicts`, `release_display_buckets`) and the function-local `workflows.gate` imports become module-level ones; the output is unchanged.
