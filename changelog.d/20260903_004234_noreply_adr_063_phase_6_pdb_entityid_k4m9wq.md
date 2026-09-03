### Added

- **PDB record/enum types now get a real `entity_id` and populate
  `AbiSnapshot.semantic_ir`** — `extract/pdb_scope.py` parses CodeView's
  flat, already-`"::"`-qualified type names into typed `ScopePath`
  segments (the reverse of DWARF's/the header-AST backends' own
  tree-walk approach), disambiguating a namespace from a nested class by
  checking against the PDB's own recorded struct/class/union names. Wired
  into the existing PE header-scoping fallback path. PDB function/variable
  identity remains unimplemented (needs new DBI module-symbol-stream
  parsing).

### Changed

- **`qualified_name_segments.raw_segments` now delegates to a new shared
  leaf primitive, `model.qualified_name_split.split_top_level_scopes`** —
  no behavior change; the bracket-depth-aware `"::"`-splitting algorithm
  moved down to `model/` so `extract`-layer code (which may not import
  the `compare`-layer `qualified_name_segments` module) can reuse it
  instead of duplicating it.
- **`qualified_name_segments.version_suffix`/`is_inline_abi_namespace_segment`
  moved down to `model.qualified_name_split` too** (Codex review, PR #1025)
  — same reasoning as `split_top_level_scopes` above, now doubly motivated:
  `extract/headers/scope_segments.py` already wanted this recognizer for
  `InlineNamespace.version_tag` and declined to import it across the
  `compare` boundary, and PDB's own visibility filter (below) needed the
  identical recognition to avoid dropping libc++/libstdc++ ABI-tag types.
  `qualified_name_segments.py` re-exports both names unchanged for every
  existing call site — no behavior change there either.

### Fixed

- **`pdb_metadata._is_user_visible` now rejects a compiler-internal or
  anonymous name embedded anywhere in a qualified spelling, not just as
  the whole string's own prefix** (Codex review) — CodeView can emit a
  fully-qualified name for a nested anonymous aggregate too (e.g.
  `"N::O::<unnamed-tag>"`), which the previous whole-string check let
  through as an ordinary named leaf.
- **That same per-segment `__` check no longer drops a type nested under a
  recognized ABI-tag inline namespace** (Codex review) — `std::__1::vector<int>`
  (libc++) and `std::__cxx11::basic_string<char>` (libstdc++) were being
  filtered out entirely, losing their layout facts, entity ID, and
  SemanticIR occurrence, because the per-segment compiler-internal check
  didn't distinguish a genuinely compiler-internal segment (MSVC's
  `__vc_attributes`) from a legitimate, user-visible ABI-tag namespace. Now
  exempted via the same `is_inline_abi_namespace_segment` recognizer
  `diff_namespaces.py`/`diff_abi_tags.py` already treat as transparent. The exemption applies only to a non-leaf (enclosing-scope) segment (Codex review, second round, fresh evidence): a globally named UDT whose own declaration leaf happens to be spelled `__1`/`__v2`/`__cxx11` is not an inline namespace at all, and must still be rejected as compiler-internal the same way `__vc_attributes` is.
- **`extract/pdb_scope.py` documents a 4th accepted limitation**: it always
  emits an ordinary `Namespace` segment, never `InlineNamespace`, because
  CodeView's flat TPI qualified names carry no source-level `inline`
  marker (unlike DWARF's `DW_AT_export_symbols` or Clang's `isInline`) —
  a live `EntityId` mismatch against a header-AST backend for any
  declaration nested inside a real inline namespace, matching castxml's
  own documented inability to produce `InlineNamespace` at all. Not a
  heuristic fix (no MSVC toolchain available to verify one against);
  pinned with an executable regression test instead.
- **`_SNAPSHOT_CACHE_VERSION` bumped to 29** (Codex review, second round,
  fresh evidence): the PE header-scoping fallback path that now produces
  PDB `entity_id`/`semantic_ir` output (`service_dump_native_pe.py`) is
  itself cacheable, so a snapshot cached by an older abicheck build for
  identical cache-key inputs would otherwise keep serving PDB model types
  with no `entity_id` and `semantic_ir=None` forever.
- **`_is_user_visible`'s ABI-tag exemption is now admit-by-default for a
  non-leaf `__`-prefixed segment, denylist-gated, instead of an
  allowlist gated on a closed set of known-standard tag shapes** (Codex
  review, third round, fresh evidence): `_LIBCPP_ABI_NAMESPACE` is a
  documented, build-configurable macro, so a vendor's own customized
  spelling (e.g. `__vendor`) can never match a closed enumeration of
  known tag shapes (`__1`, `__cxxN`, `__ndkN`) and was being rejected as
  compiler-internal, dropping a real, user-visible declaration's layout
  facts, entity ID, and SemanticIR occurrence entirely. Any non-leaf
  `__`-prefixed segment is now admitted UNLESS it is positively known to
  be compiler-synthesized (`_KNOWN_COMPILER_INTERNAL_NAMESPACES`,
  currently just MSVC's own `__vc_attributes`) -- the deliberately safer
  failure direction, since an extra namespace scope is recoverable noise
  while a silently-dropped declaration is not.
- **`_is_user_visible` no longer drops a named declaration just because an
  ENCLOSING scope segment is anonymous** (Codex review, fourth round, fresh
  evidence): `"N::<unnamed-tag>::Inner"` was being rejected in full,
  losing `Inner`'s real layout facts, even though only the middle segment
  is compiler-synthesized. A `"<...>"`-prefixed non-leaf segment is now
  admitted the same way an unrecognized `__`-prefixed one already is; only
  a `"<...>"`-prefixed LEAF segment (the declaration's own name) still
  means "this declaration has no real name" and is rejected. Since
  `extract/pdb_scope.py` still builds no `Anonymous` scope segment, its new
  `has_anonymous_enclosing_scope` predicate makes `record_entity_id`/
  `enum_entity_id` leave `entity_id` unset for exactly this shape instead
  of guessing a plain `Namespace`/`Record` segment for a scope CodeView
  never gave a real name — the type still reaches the model with its
  layout facts but contributes no `SemanticIR` occurrence, matching
  `extract/semantic_normalizer.py`'s existing "no `entity_id` means no
  occurrence" contract.
