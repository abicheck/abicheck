### Fixed

- **Fixed double-reporting of `experimental_removed_without_replacement` and
  `constant_changed` for declarations reachable via a versioned inline
  namespace.** A versioned inline namespace (``detail::v1::x``) makes the
  same declaration reachable under two qualified spellings — the full path
  and the version-elided path (``detail::x``) that unqualified lookup from
  the enclosing scope also resolves to. When a header-AST producer surfaces
  both spellings as separate top-level declarations, `diff_namespaces.py`'s
  experimental-namespace detector indexed them under two different stable
  keys (only the experimental segment was stripped, not the version
  segment), reporting the same removal twice; `diff_symbols._diff_constants`
  compared `AbiSnapshot.constants` by raw qualified-name key, so the same
  value change was reported once per spelling too. Both now canonicalize
  away the versioned inline-namespace segment before indexing/diffing, but
  only when the snapshot's own extraction data corroborates that the two
  spellings really are the same declaration — a shared mangled name for a
  function (the ABI mangles an inline namespace's segment either way, so a
  true alias's two spellings still share one symbol), a matching *non-empty*
  structural fingerprint (kind/size/alignment/fields/bases — an empty record
  carries no distinguishing structure, so it is never used as evidence) for
  a type, or an identical value on *every* side that has more than one
  spelling for a constant (via the new
  `qualified_name_segments.dedupe_versioned_spellings_pair` helper, which
  decides jointly across old and new so a group that agrees on one side but
  diverges on the other is never merged on either). A version-shaped
  segment name alone (`v1` is a legal name for an ordinary, non-inline
  namespace too) is never treated as proof by itself, so two genuinely
  distinct declarations that happen to share a leaf name are never merged.
  A merged group's reported key is also now independent of each snapshot's
  own declaration order, so reordering two alias spellings between old and
  new can't misreport a plain reordering as a removal.
