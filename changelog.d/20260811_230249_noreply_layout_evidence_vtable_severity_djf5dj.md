### Changed

- **`checker_types.Change` is now keyword-only from `old_value` onward.**
  Every production and test call site already constructs `Change` with its
  first three fields (`kind`/`symbol`/`description`) positional and every
  other field by keyword, so this changes nothing for existing callers, but
  it permanently closes the bug class where a new field inserted into the
  class silently shifts a later positional constructor argument for any
  future caller — the same class of bug fixed for `AbiSnapshot` per-field in
  PR #582, generalized here via the `dataclasses.KW_ONLY` sentinel so every
  future field is protected automatically.
