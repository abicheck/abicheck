### Fixed

- **`exported_object_alignment_reduced` false positive on plain global variables** — the CastXML
  dumper only populated a `Variable`'s declared-alignment evidence from an *explicit*
  `alignas`/`__attribute__((aligned))` override, never from the variable's own type's natural
  alignment, so the address-derived alignment heuristic in `_check_object_alignment_reduced` had
  no declared-alignment evidence to corroborate against for the overwhelming majority of exported
  globals (any plain `int`, `long`, etc.) — letting an unrelated neighbouring global's addition
  shift an existing variable's link-time address low bits and get misread as a real alignment
  reduction (e.g. `case61_var_added` under `release-headers`). `dumper_castxml.py` now resolves a
  variable's own natural type alignment (`_type_alignment_bits`) as a fallback declared-alignment
  source when no explicit override is present.
