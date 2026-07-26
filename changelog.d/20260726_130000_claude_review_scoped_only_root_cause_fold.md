### Fixed

- **`impact_assessment.root_cause_id` silently missing for a finding whose
  only correlation was a scoped-only overlay** (`reporter.py`, PR review
  finding): `--report-mode root-cause` JSON, SARIF, and JUnit all fold
  `DiffResult.scoped_only_changes`' `caused_by_type` values into their
  root-cause grouping (the `--used-by`/`--required-symbol` gate appends
  these findings after the main report is otherwise built), but the default
  full-mode JSON (`_add_changes_block`) and `--report-mode leaf`
  (`_to_json_leaf`) did not — a finding in `result.changes` that only
  correlates via one of those later-appended scoped-only findings silently
  lost its `impact_assessment.root_cause_id`/`root_cause_display`/
  `impact_group_id` in those two modes, disagreeing with the same finding's
  root-cause-mode/SARIF/JUnit rendering. Factored the correct fold-in logic
  (previously only in `_to_json_root_cause`) into a shared
  `_scoped_only_extra_causes` helper and wired it into all three call sites.
