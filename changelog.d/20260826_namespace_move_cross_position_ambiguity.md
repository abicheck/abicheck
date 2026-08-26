### Fixed

- **A real, multi-symbol namespace move could be silently reduced to
  fewer supporting pairs than it actually had, sometimes dropping the
  `SYMBOL_RENAMED_BATCH` roll-up finding entirely.**
  `diff_symbols_renames.find_namespace_move_groups` rejects a removed
  symbol's candidate rename target when it resolves ambiguously across
  more than one masking position (a real, deliberate safety guard) --
  but it rejected *every* such candidacy outright, even when one of the
  competing substitutions was independently corroborated by a
  *different* removed symbol proposing the identical
  `(old_segment, new_segment)` move, while the other candidate had no
  such corroboration at all. That corroboration is real evidence the
  isolated, single-symbol view can't see: the ambiguity now resolves in
  favor of the substitution other symbols also support, and only stays
  rejected when the ambiguity is a genuine tie (both, or neither,
  candidate independently corroborated) -- the same conservative
  default the existing many-to-many and one-to-many guards already use
  for a genuinely unresolvable case.
