### Fixed

- **The `include_sequence` owned-header carve-out never validated an
  unchanged `hdrs:` slot's own JSON payload shape.** The `ext:`/`sys:`
  digest-format check added in a previous round only covers those two
  token shapes; an `hdrs:` slot's JSON-list-of-`(identity, relative_path)`-
  pairs payload was previously decoded and validated only inside
  `_include_sequence_is_additive_owned_growth`'s per-slot diff loop, which
  never re-examines a slot that is byte-identical between the old and new
  contract (its `if old_slot == new_slot: continue` short-circuit). A
  malformed, unchanged payload like `"0:hdrs:not-json"` could therefore
  ride alongside a genuinely-growing separate `hdrs:` slot completely
  unexamined, and the carve-out still authorized the waiver.
  `_slot_indices_match_position` now also decodes and validates every
  `hdrs:` slot's payload (accepting either the `<single-header>` sentinel
  or a JSON list of valid owned-header pairs), closing the same
  unverifiable-evidence gap the `ext:`/`sys:` digest check closed for its
  own token shapes.
