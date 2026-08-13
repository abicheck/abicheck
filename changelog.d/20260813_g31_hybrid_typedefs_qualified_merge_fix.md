### Fixed

- **`AbiSnapshot.typedefs_qualified` was not merged in a hybrid
  (`--ast-frontend hybrid`) dump** (Codex review, fresh evidence): unlike
  every other explicitly-merged fact, `merge_snapshots()` left this field
  as an unchanged copy of the castxml leg's own value, the same as bare
  `typedefs`/constants/platform metadata deliberately are. But
  `typedefs_qualified`'s whole purpose (schema v25) is to recover a
  qualified typedef alias `type_reachability.py`'s stdlib-reference scan
  would otherwise miss — leaving it castxml-only meant a declaration only
  clang appended, or an alias only clang's own parse captured under that
  qualified key, was invisible on a hybrid snapshot even though the
  underlying data existed on the clang leg. Fixed by explicitly unioning
  both sides' `typedefs_qualified` (castxml's own value wins on the rare
  key disagreement, matching "castxml remains the base" for every other
  merged fact).
