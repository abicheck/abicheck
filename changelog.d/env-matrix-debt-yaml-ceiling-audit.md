### Fixed

- **`architecture/debt.yaml` no-growth ceilings audited per entry
  (Codex P1 finding, PR #1221).** A repo-wide audit of every `no_growth`
  baseline this PR's `compare --env-matrix` demotion had raised, done
  individually rather than by blanket-reverting the finding. One genuine
  instance of the "raise the ceiling to fit new code" anti-pattern was
  found and fixed: `tests/test_environment_drift.py`'s baseline (raised
  1639 -> 1998, +359 lines) had folded in three test classes covering
  `diff_versioning.promote_baseline_violation_findings` -- a self-contained
  unit for one new function, not the drift-report/DT_RELR/hash-style/
  time64 behavior that module's own docstring scopes it to. Those three
  classes (`TestPromoteBaselineViolationFindings`,
  `TestWheelRpathNotPortableStaysAtRisk`,
  `TestPromotionRunsBeforeSuppressionRecording`) moved verbatim to a new
  sibling file, `tests/test_diff_versioning_baseline_promotion.py`, and the
  ceiling was restored to the actual measured post-extraction line count
  (1750) rather than trimmed to fit. The other ten raised ceilings
  (`checker.py`, `diff_versioning.py`, `cli_compare_receipt.py`,
  `cli_compare_release_matrix.py`, `cli_helpers_compare.py`,
  `frontends/cli/commands/compare_bundle_facts.py`, `reporter_markdown.py`,
  `service_compare_pipeline.py`, `report/render_markdown.py`,
  `report/render_markdown_document.py`) were individually re-verified
  against their recorded rationale text and the actual diff each
  represents: each is a small (2-40 line), irreducible call-site or
  field-threading cost for a genuinely new capability -- the `deployment:`
  config key's resolved value reaching a dataclass/shim that already
  carries every sibling config-only field the same way, or a shared
  promotion/digest helper whose logic itself already lives in a sibling
  module -- and were kept as-is, since each already matches this file's
  own "irreducible cost, logic lives elsewhere" convention rather than
  restating unrelated code inline.
