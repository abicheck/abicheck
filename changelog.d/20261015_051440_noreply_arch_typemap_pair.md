<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **Old/new type matching now lives in `compare/type_map_pair.py`** —
  `TypeMap` and `type_map_key` moved there from `diff_helpers`, and the new
  `TypeMapPair.counterpart(side, t)` owns the qualified-then-bare fallback
  that `lookup_matched_type` used to hold. Callers name the side a type came
  from instead of passing the probing map first. The `diff_layout` layout
  pass is the first caller on the pair; `lookup_matched_type` still serves
  the rest. Matching results are unchanged. The private `TransitionSpec`
  alias was removed, with its tuple inlined into `bool_transition`.
