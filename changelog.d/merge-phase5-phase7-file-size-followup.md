<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **Internal: `compare --dry-run`'s report builder moved to
  `frontends/cli/compare_dry_run.py`.** No user-facing change —
  `abicheck/cli_compare_helpers.py`'s own `_render_compare_dry_run` moved to
  its already-existing `frontends/cli` sibling (as
  `build_compare_dry_run_result`) purely to keep `cli_compare_helpers.py`
  under the AI-readiness file-size hard cap after merging two
  independently-landed CLI-consolidation phases (`--view` consolidation and
  hidden-flags/config demotion) whose combined growth crossed it.
