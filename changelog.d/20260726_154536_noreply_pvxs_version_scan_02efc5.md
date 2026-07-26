### Fixed

- **The comparability gate's `include_sequence` owned-header-growth carve-out
  never validated that a slot's index was a real, well-formed position.**
  `_include_sequence_is_additive_owned_growth` only checked that a slot's
  index was *unchanged* between the old and new side (`old_idx != new_idx`),
  never that the shared index was itself a genuine position rather than an
  arbitrary label — a real `include_sequence` value always numbers its
  slots `"0"`, `"1"`, `"2"`, ... in order (`_slot_token_for_ancestor`'s own
  `enumerate()`-based construction), but an externally-constructed contract
  with a fabricated slot like `"bogus:hdrs:[...]"` on both sides passed the
  equality check trivially and could still reach the owned-header superset
  comparison. Added `_slot_indices_match_position`, which requires every
  slot's index to match its literal list position before any owned-header
  growth is trusted.
