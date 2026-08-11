### Fixed

- **Fixed double-reporting of `experimental_removed_without_replacement` for
  functions/types reachable via a versioned inline namespace.** A versioned
  inline namespace (`detail::v1::x`) makes the same declaration reachable
  under two qualified spellings — the full path and the version-elided path
  (`detail::x`) that unqualified lookup from the enclosing scope also
  resolves to. When a header-AST producer surfaces both spellings as separate
  top-level declarations, `diff_namespaces.py`'s experimental-namespace
  detector indexed them under two different keys (only the experimental
  segment was stripped, not the version segment), reporting the same removal
  twice. The two indices are now built *jointly* from both snapshots and
  merge two spellings only when the snapshot's own extraction data
  corroborates they're the same declaration — a shared mangled name for a
  function (the ABI mangles an inline namespace's segment either way, so a
  true alias's two spellings still share one symbol) or a shared declaring
  source location for a type (two spellings of one physical declaration
  resolve to the same AST node and so share this by construction, unlike a
  structural fingerprint, which coincides routinely between genuinely
  unrelated declarations — verified across several review rounds for both
  empty and non-trivial records). A version-shaped segment name alone (`v1`
  is a legal name for an ordinary, non-inline namespace too) is never treated
  as proof by itself. The merge is computed once over both snapshots' pooled
  declarations so the same real-world entity always resolves to the same key
  regardless of which side has both spellings or which order they were
  declared in — an independent per-snapshot merge could otherwise misreport
  a harmless reordering, or an extractor starting/stopping the duplicate
  emission, as a spurious removal.

- **`constant_changed` double-reporting for the same versioned-inline-
  namespace pattern was investigated and is a known, documented limitation,
  not fixed.** Unlike a function's mangled name or a type's source location,
  `AbiSnapshot.constants` carries no identity for a header constant beyond
  its own value, and a value-equality-based merge — attempted and reverted
  during review — was shown to be unsound in both directions: it can merge
  two unrelated constants that never even coexist in the same snapshot (each
  present on only one side), and it can hide a real, isolated removal behind
  an unrelated constant that coincidentally started with the same value. See
  `qualified_name_segments`'s module docstring and `diff_symbols._diff_constants`'s
  docstring for the full reasoning; closing this needs real per-constant
  identity (e.g. a declaring source location) threaded through
  `AbiSnapshot.constants`, which is a schema change out of scope here.

- Fixed a pre-existing bug in the shared `version_strip_segments` helper
  (used by the above and by the existing `INLINE_NAMESPACE_VERSION_BUMPED`
  detector): it scanned every segment of a qualified name for a
  version-shaped tag, including the last one — the declaration's own leaf
  name, never a namespace. A constant or type literally named `v1`/`v2`
  could have its own name stripped, or collide with an unrelated
  same-scope declaration also named like a version tag.
