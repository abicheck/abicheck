### Fixed

- **`finding_id`-only suppression rules now render their own identity in
  diagnostic output instead of an ambiguous fallback** — a suppression rule
  matched purely by `finding_id` (no `symbol`/`namespace`/`source_location`)
  previously rendered as a bare `rule#<index>` label in
  `--dry-run`/fold summaries and as `"?"` in the
  `SUPPRESSION_REACHABILITY_UNKNOWN` diagnostic, indistinguishable from any
  other unlabeled rule. `cli_compare_fold.py`'s `_suppression_rule_label`
  and `post_processing.py`'s `_build_suppression_unknown_reachability_change`
  now include `rule.finding_id` in their selector chains.
- **`canonical_finding_id` now actually collapses a `func_removed`/
  `func_added` event across producers** — `diff_symbols.py`'s rich ELF
  detector stamps `old_value`/`new_value` with the function's display name,
  while `diff_platform.py`'s PE/Mach-O export-table detectors leave them
  unset for the identical removed/added-symbol event, so the two producers'
  findings previously hashed to two different canonical ids despite being
  one logical event. `finding_identity.py`'s category-fold discriminator
  now drops old/new for these two categories entirely (there is at most one
  such event per symbol per comparison, so the value carried no
  disambiguating power, only producer-specific noise).
