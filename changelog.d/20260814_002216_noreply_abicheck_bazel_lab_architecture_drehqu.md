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
  field, so the exact digest as written is always preserved — including
  through a YAML merge key (`<<: *anchor`), which is resolved the same way
  PyYAML itself does (a direct key always wins over a merged one).
- **`--strict-suppressions`'s expired-rule diagnostic renders a
  `finding_id`-only rule's own identity too** — a third, independent
  selector-rendering chain (`cli_params.py`) had the same bare `"?"`
  fallback gap already fixed in `cli_compare_fold.py`/`post_processing.py`.
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
- **`canonical_finding_id` now canonicalizes a type spelling embedded
  mid-sentence in a finding's description** — `struct_field_type_changed`/
  `template_param_type_changed`/`template_return_type_changed` embed
  `{old}`/`{new}` after other text, where `canonicalize_type_name`'s
  anchored struct-prefix/const-reorder passes never reach them; the raw
  spelling stayed uncanonicalized in `description` even after `old_value`/
  `new_value` themselves were fixed, still breaking the fold across
  producers for these three kinds. `finding_identity.py` now substitutes
  the already-canonicalized `old_value`/`new_value` text for their known
  raw substrings within the description, rather than canonicalizing the
  sentence as one opaque blob.
- **`suppression_yaml.py`'s raw `finding_id` scalar lookup now matches
  `yaml.safe_load`'s own duplicate-key semantics, and rejects an empty
  digest** — an earlier revision returned on the first direct
  `finding_id:` match, disagreeing with `yaml.safe_load`'s own
  last-key-wins behavior for a duplicate mapping key (now fixed: the last
  direct occurrence wins, matching PyYAML). Separately, `parse_finding_id`
  now normalizes an empty string to `None`: an explicit `finding_id: ""`
  previously passed `Suppression.__post_init__`'s selector check as a
  real, standalone-sufficient selector that could never match any actual
  finding — a rule that loaded successfully but was permanently dead.
