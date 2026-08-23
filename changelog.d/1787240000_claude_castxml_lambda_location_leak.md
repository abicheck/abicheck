### Fixed

- **The castxml AST frontend embedded an absolute source path/line/column
  in a lambda closure type's (and any anonymous struct/union/enum's) own
  name** — e.g. `raii_guard<(lambda at <checkout-dir>/include/foo.h:522:26)>`
  — so identical headers compiled from two different checkout directories
  produced two different type identities for the same declaration, and
  old/new type matching (keyed on the raw, unstripped name) manufactured a
  spurious `type_removed`/`type_added` pair — plus, when the closure type
  appeared in a public function's return type, a fabricated
  `template_return_type_changed` break on an otherwise-unchanged function.
  The clang JSON-AST frontend already normalized this away
  (`dumper_clang_expr._normalize_qual_type`); castxml had no equivalent.
  `name_classification.strip_anonymous_type_location` (a new, shared helper
  reusing the same location-matching regex `canonicalize_type_name` already
  applies for comparisons) is now applied by `dumper_castxml.py` at the
  point a `RecordType`/`EnumType`'s own identity is extracted — a lambda
  closure type or anonymous struct/union/enum's name is stripped down to
  its bare `(lambda)`/`(unnamed <kind>)` marker before it ever reaches type
  matching, not left to be normalized only downstream in one comparison
  path. Reproduced against a real oneTBB 2021.13.0→2022.3.0 header
  comparison, where 27 breaking/27 compatible finding pairs (94 of 225
  total findings) were pure path artifacts of this leak.
