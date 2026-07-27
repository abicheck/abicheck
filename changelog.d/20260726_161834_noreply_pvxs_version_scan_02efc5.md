### Fixed

- **The comparability gate's sequence carve-outs could waive a header
  addition that didn't correspond to the header the scope actually grew
  by.** `_scope_growth_corroborated` only proved the declared scope grew
  by *some* header, not that it grew by the *same* header the
  `header_sequence`/`include_sequence` carve-out was waiving. For example,
  a scope growing by a newly-declared `c.h` (never parsed at all) could
  corroborate an unrelated `header_sequence` append of `d.h` (already
  declared identically on both sides via `--public-header`, but fed to the
  L2 frontend only on the new side) — the old snapshot never parsed `d.h`'s
  content, so a real removal inside it would be invisible, and `c.h` was
  never parsed on either side. Added `_scope_newly_added_headers`, and both
  `_header_sequence_is_additive_reorder_free` and
  `_include_sequence_is_additive_owned_growth` now require every appended
  sequence entry / newly-owned pair to be a member of the specific set of
  headers newly added to scope, not just that scope growth happened
  somewhere.
