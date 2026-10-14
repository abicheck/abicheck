<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **JSON report no longer imports the legacy `reporter` module** — the per-change projectors and JSON section builders moved from `abicheck/reporter.py` into `abicheck/report/change_json.py` and `abicheck/report/json_sections.py`, and `reporter.to_json` now calls `report.build.build_report_document` directly instead of through `importlib`. The JSON output is unchanged; code that imported the old underscore helpers from `abicheck.reporter` must import the new public names from these modules.
