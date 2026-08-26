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
- **Follow-up (Codex review): a key a symbol raised at a locally-ambiguous
  masking position was invisible to the same tie-break, so a genuine tie
  could be missed.** The global-support check above only compared a
  symbol's competing keys among the ones that had already survived the
  *local* one-to-many filter -- but a key rejected by that local filter
  (e.g. a masking position matching two distinct added symbols) can still
  be independently, unambiguously corroborated by a *different* removed
  symbol at that same key, which is real evidence of a genuine tie. The
  competing-keys set now comes from every raw candidacy a symbol proposed,
  not only the ones that produced their own resolved entry, so this case
  correctly rejects both competing candidates instead of admitting one.
- **Second follow-up (Codex review): a repeated bare segment could make
  two genuinely different added declarations key identically for the
  same removed symbol, defeating the tie-break's own corroboration
  check.** Removing `old::old::{f,g}` while adding both `new::old::{f,g}`
  (a position-0 substitution) and `old::new::{f,g}` (a position-1
  substitution) makes every removed symbol resolve to two distinct added
  targets that both happen to key as the identical `(old, new)` text --
  the corroboration check saw no *competing* key at all (both candidacies
  share one key string) and let the first-seen candidacy through,
  silently dropping the other target and fabricating a 2-pair
  `SYMBOL_RENAMED_BATCH`. The tie-break now also checks, per symbol and
  per key, whether that key itself resolves to more than one distinct
  added declaration -- rejecting before corroboration is even
  considered, since two different targets sharing identical key text
  can never be told apart by which other symbol supports that text.
