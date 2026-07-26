### Fixed

- **The `include_sequence` slot-index validation accepted a malformed slot
  with no delimiter at all.** `_slot_indices_match_position` only compared
  `slot.partition(":")[0]` against the slot's position — but a bare,
  delimiter-less string like `"0"` trivially satisfies that (it partitions
  to itself), and if such a slot happens to be byte-identical on both
  sides, `_include_sequence_is_additive_owned_growth`'s per-slot equality
  short-circuit never re-examines it, letting it ride alongside a
  genuinely-growing slot and still return additive-safe. Every numbered
  slot must now also carry a real `":"` delimiter and one of the three
  token shapes `compute_extraction_contract` actually produces
  (`hdrs:`/`ext:`/`label:`) before any owned-header growth is trusted.
