### Fixed

- **The `include_sequence` slot-index validation wrongly rejected the
  ordinary trailing system-header bucket.** `_slot_indices_match_position`
  required every slot's index prefix to equal its literal list position —
  but the real `compute_extraction_contract` construction appends an
  unnumbered `sys:...` entry for any depfile header outside every declared
  include root (system headers, the C standard library, ...), which has no
  owning `IncludeDir` and thus no position to number. A pure header
  addition with an unchanged system bucket present therefore wrongly
  raised `ProfileMismatchError` instead of being recognized as
  comparable. `_slot_indices_match_position` now excludes a single
  trailing `sys:`-prefixed entry from the position check — a `sys:` token
  anywhere but the last position is still rejected, since that isn't a
  shape the real construction produces.
