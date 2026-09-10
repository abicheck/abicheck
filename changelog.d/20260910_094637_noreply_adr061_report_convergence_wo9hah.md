### Fixed

- **`build_report_envelope`'s JSON document now shares the envelope's own
  frozen `today` with every date-sensitive resolution in its build** — the
  envelope already threaded its captured date into `gate_decision_for_result`
  and `build_report_findings`, but its `build_report_document` call, which
  builds the frozen JSON document during that same construction, received
  only `gate`. `_add_changes_block`, `_add_policy_overrides`, and
  `_build_severity_json` (via `_change_to_dict`/`active_reclassify_rules`/
  `categorize_changes`) still resolved a fresh `date.today()`. If envelope
  construction straddled midnight on a dated `reclassify:` rule's expiry, the
  JSON document could report a finding as breaking and omit the
  still-active rule while `envelope.findings`/`envelope.gate` (resolved a
  moment earlier, against the pre-midnight date) stayed compatible. `today`
  is now threaded through the whole document build, including the
  scoped-gate (`--used-by`/`--required-symbol`) fold-in.
