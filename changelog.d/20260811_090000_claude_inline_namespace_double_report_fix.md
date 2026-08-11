### Fixed

- **Fixed double-reporting of `experimental_removed_without_replacement` for
  functions reachable via a versioned inline namespace.** A versioned inline
  namespace (`detail::v1::x`) makes the same declaration reachable under two
  qualified spellings — the full path and the version-elided path
  (`detail::x`) that unqualified lookup from the enclosing scope also
  resolves to. When a header-AST producer surfaces both spellings as separate
  top-level declarations, `diff_namespaces.py`'s experimental-namespace
  detector indexed them under two different keys (only the experimental
  segment was stripped, not the version segment), reporting the same removal
  twice. The two indices are now built *jointly* from both snapshots and
  merge two spellings only when the snapshot's own extraction data
  corroborates they're the same declaration — for a function, a shared
  mangled name (the ABI mangles an inline namespace's segment either way, so
  a true alias's two spellings still share one symbol; an empty mangled name
  never counts as a match). A version-shaped segment name alone (`v1` is a
  legal name for an ordinary, non-inline namespace too) is never treated as
  proof by itself. The merge is computed once over both snapshots' pooled
  declarations so the same real-world entity always resolves to the same key
  regardless of which side has both spellings or which order they were
  declared in — an independent per-snapshot merge could otherwise misreport
  a harmless reordering, or an extractor starting/stopping the duplicate
  emission, as a spurious removal.

- **The same double-reporting for types and for `constant_changed` was
  investigated and is a known, documented limitation, not fixed.** Unlike a
  function's mangled name, no reliable alias-identity evidence exists in the
  current snapshot format for a type or a header constant:
  - Two candidate type identities were tried and falsified by concrete
    review counterexamples — a structural (kind/size/alignment/fields/bases)
    fingerprint (coincides routinely between genuinely unrelated
    declarations, both for empty tag/marker types and for non-trivial types
    that merely share a field layout), and the type's declaring
    `source_location` (`dumper_clang.py`'s and `dwarf_snapshot.py`'s own
    extractors can legitimately return a *bare filename* with no line when
    the line is unavailable, so two unrelated types in the same file both
    missing line info would collide).
  - A value-equality-based merge for constants was similarly tried and
    reverted: it can merge two unrelated constants that never even coexist
    in the same snapshot (each present on only one side), and it can hide a
    real, isolated removal behind an unrelated constant that coincidentally
    started with the same value.

  See `diff_namespaces.py`'s `_type_index_items` and
  `diff_symbols._diff_constants` docstrings for the full reasoning; closing
  either needs real per-declaration identity threaded through the snapshot
  format (`RecordType`/`AbiSnapshot.constants` carry none today), which is a
  schema change out of scope here.

- Fixed a pre-existing bug in the shared `version_strip_segments` helper
  (used by the above and by the existing `INLINE_NAMESPACE_VERSION_BUMPED`
  detector): it scanned every segment of a qualified name for a
  version-shaped tag, including the last one — the declaration's own leaf
  name, never a namespace. A constant or type literally named `v1`/`v2`
  could have its own name stripped, or collide with an unrelated
  same-scope declaration also named like a version tag.

- Fixed a further bug in the same merge primitive: a raw key whose
  aggregated identity set spanned more than one distinct value (an
  overloaded function whose header-derived qualified name omits the
  parameter-list signature, so two distinct overloads land in the same
  layer-1 bucket) could still be used as version-segment alias-merge
  evidence via a non-empty intersection with another raw key's identity
  set. That's unsound: it could merge an unrelated raw key on an identity
  that isn't uniquely this raw key's own, silently absorbing a removed
  overload's alias into a surviving sibling overload's bucket and
  dropping the removal entirely. `_paired_stable_indices` now requires
  each side of a merge to be an *unambiguous* singleton identity set, not
  merely a non-empty intersection.

- Fixed a third bug in the same identity check: `Function.mangled` is not
  always a real ABI-mangled name. Both header-AST producers fall back to
  the bare, unqualified declaration name when no real linkage name is
  available (`dwarf_snapshot.py`'s `mangled = linkage_name or name`,
  `dumper_clang.py`'s `mangled = ... or name`), so two structurally
  unrelated declarations in different scopes can coincidentally share the
  same bare-name fallback value and be mistaken for a genuine alias pair.
  `_func_index_items` now only trusts `f.mangled` as identity evidence
  when it carries a recognized ABI name-mangling prefix (Itanium `_Z`
  and its Mach-O `__Z` variant, or MSVC `?`), which a bare-name fallback
  never does.

- **Test infrastructure**: added `tests/test_diff_namespaces.py::TestPairedStableIndicesProperties`,
  a Hypothesis property-test suite testing the merge primitive
  (`_paired_stable_indices`) directly, stating its contract as invariants
  (no merge without shared identity evidence, order-independence, a real
  alias always merges regardless of which side holds which spelling,
  parameter-signature text never leaks into the grouping key) rather than
  pinning individual examples. Added because five of the six review-round
  findings behind this fix were bugs in that reusable primitive itself, not
  in the domain-specific identity logic layered on top, and none were
  caught by any hand-written example test. See `AGENTS.md`'s "Test-quality
  gates" section (new "Primitive-level property tests" entry) for the full
  retrospective and the general practice this establishes for future
  merge/dedupe primitives.
