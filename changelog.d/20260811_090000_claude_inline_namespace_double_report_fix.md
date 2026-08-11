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
  spellings really are the same declaration (a shared mangled name for a
  function, a matching structural fingerprint for a type, or an identical
  value on both sides for a constant, via the new
  `qualified_name_segments.dedupe_versioned_spellings` helper) — a
  version-shaped segment name alone (`v1` is a legal name for an ordinary,
  non-inline namespace too) is not treated as proof, so two genuinely
  distinct declarations that happen to share a leaf name are never merged.
