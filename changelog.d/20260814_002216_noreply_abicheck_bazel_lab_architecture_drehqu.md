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
- **A `finding_id:` suppression selector loaded from YAML no longer breaks
  when its digest looks numeric** — `report_canonical_finding_id` truncates
  a sha256 digest to 16 hex characters, which has a real chance of landing
  entirely within `0`-`9` (plain decimal) or `0`-`7` with a leading zero
  (parsed by PyYAML's YAML 1.1 resolver as an *octal* literal, silently
  becoming a completely different number). Either way the value previously
  reached `Suppression.finding_id` as the wrong Python value and silently
  matched nothing. `SuppressionList.load` now reads each entry's raw,
  unresolved `finding_id` scalar text directly from the YAML node tree
  (`suppression_yaml.py`, a new leaf module split out to stay under the
  file-size cap) instead of trusting PyYAML's type resolution for this one
  field, so the exact digest as written is always preserved.
- **`canonical_finding_id` now canonicalizes each `func_params_changed`
  parameter individually, not the whole comma-joined list as one type
  string** — `canonicalize_type_name`'s const-reorder/struct-prefix passes
  are anchored to the start of the string, so calling it on the full
  `_format_params()`-joined parameter list only ever fixed the first
  parameter's spelling. A real change on a later parameter, plus an
  unrelated const-spelling difference on an earlier one, previously hashed
  to two different canonical ids across producers for the identical
  function-wide event. `finding_identity.py` now splits the list at
  top-level commas (respecting `<>`/`()`/`[]` nesting, so a template
  argument's own comma doesn't misalign the split) and canonicalizes each
  parameter before rejoining.
