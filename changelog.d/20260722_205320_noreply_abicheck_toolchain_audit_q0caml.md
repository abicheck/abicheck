<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **The trailing-`requires` declarator check no longer treats an
  unrelated `->` earlier in the same expression as proof of a genuine
  C++20 clause.** `_looks_like_genuine_requires_clause`/
  `_looks_like_requires_declarator` (`abicheck/dumper_ast_config.py`)
  required only that `->` appear somewhere in the same-line prefix — a
  plain pre-C++20 declaration like `int requires(int); return p->m +
  requires(1);` was wrongly classified as genuine C++20 syntax because of
  the unrelated `->` in `p->m`, with no statement boundary between it and
  `requires` for the earlier statement-boundary fix to catch. The arrow
  is now required to be declarator-adjacent (directly follow a function
  declarator's closing `)`, after stripping cv/ref/noexcept specifiers).
- **Hidden-friend surface classification's friend-symbol fallback is now
  tolerant of the friend existing on only one side.** When a hidden
  friend's befriending class can't be resolved, `_classify_hidden_friend_
  surface` (`abicheck/surface.py`) falls back to the friend function's own
  recorded origin — but that fallback required *both* snapshots to agree
  the symbol is private/system, so a friend added or removed together
  with the finding (the common case, where the symbol legitimately exists
  in only one snapshot's function map) was never demoted even when the
  one side that has it confidently says private/system-header. The
  fallback now applies the same one-sided-origin relaxation already used
  for the owner-class fallback.
</content>
