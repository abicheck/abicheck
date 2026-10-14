---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: historical
generated: false
---

# Opaque-type suppression still falls back to bare RecordType.name when stable identities are incomplete

Index: [Known gaps — closed](../archive/known-gaps-closed.md)

**Status (2026-10-14): CLOSED.** Both remaining mechanical parts are fixed. `find_by_value_types`'s leaf widening now applies C++ unqualified lookup (`compare/opaque_leaf_lookup.py`): a bare leaf reference written inside a known scope does not expose `ns::Handle` when the snapshot declares a same-leaf type in a scope that lookup reaches first; with no such declaration, or no known scope, the reference stays exposed. `find_opaque_struct_types`' asymmetric-existence rule now pairs a forward declaration with a definition under either spelling (`name` or `qualified_name`), so a DWARF forward declaration keyed `ns::Handle` and a header definition keyed `Handle`/`ns::Handle` (or the reverse) are no longer read as "absent", and a visible declaration's `qualified_name` also blocks the spelling tier. Accepted by design, not a gap: a change with no identity whose spelling is shared only by opaque declarations is suppressed by spelling, since no identity or visible namesake contradicts it. Regression: `tests/test_opaque_leaf_lookup.py`.

**Previous status (2026-10-13): PARTIAL.** Fixed: a spelling that also names a visible declaration is never used to suppress through the spelling tier (`OpaqueTypeIndex.shared_with_visible`, both opaque-type and opaque-struct paths, complete or incomplete pairing); a stable id that a visible declaration also carries no longer proves opacity. Still open: (1) a change with no identity whose spelling is shared only by opaque declarations is still suppressed by spelling, by design, because no identity or visible namesake contradicts it; (2) the exposure-detection mirror, `find_by_value_types` leaf widening, which pins `test_find_by_value_types_leaf_widening_bare_reference_in_another_scope_is_a_documented_gap` in `tests/test_cov95_diff_filtering.py`; (3) a DWARF-only forward declaration paired with a full definition under the same name keeps the name-based criterion of `find_opaque_struct_types`, since DWARF resolves no identity to tell the two apart.

**Previous status (re-verified 2026-10-09 at `456989f`): PARTIAL.** compare/opaque_types.py:74-130 now uses OpaqueTypeIndex carrying stable EntityIds plus a bare-name 'local' tier. contains(strict=True) narrows the bare-name collision only when intersect() proves the pairing is complete, and otherwise falls back to the old bare RecordType.name matching. diff_filtering.py:854 _root_type_name now prefers Change.qualified_name.

`diff_filtering._find_opaque_types()` (and its
siblings `_find_by_value_types()`/`_downgrade_opaque_type_changes()`) index
a snapshot's opaque/impl-private types by `t.name` alone, and
`_root_type_name()` derives a `Change`'s matching key from `Change.symbol`
the same way `diff_types.py` stamps it — also the bare name for a
top-level type change (`name = t_old.name`), never `qualified_name`. Two
distinct records sharing a bare name in different namespaces (a complete,
genuinely public `api::Foo` and an unrelated forward-only `impl::Foo`)
therefore collide in the same `opaque: set[str]`: if `impl::Foo` is opaque,
`_downgrade_opaque_type_changes()` silently suppresses a real
`TYPE_SIZE_CHANGED`/field-change finding on the unrelated, complete,
public `api::Foo` too, since both match the bare key `"Foo"`. Confirmed
by reading the code (no live repro run); this predates PR #719 and
already applied identically to castxml's own `is_opaque=True` types — the
PR's clang-backend opaque-stub fix (previously clang silently dropped
every forward-decl-only type instead of emitting a stub) makes this
reachable from a new source, not a new bug class. **Not fixed here**: a
correct fix needs qualified identity threaded consistently through
`_find_opaque_types`/`_find_by_value_types`/`_downgrade_opaque_type_changes`
and `_root_type_name`'s several call sites in `diff_filtering.py` (at
least five, by grep), none of which currently have test coverage for the
cross-namespace-collision case to validate a change against — a
systematic, cross-cutting rework, not a scoped fix reactive to one review
comment. Filed here rather than attempted under this PR's time budget,
per this file's own "known gaps over risky reactive patches" convention.
