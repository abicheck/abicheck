---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Opaque-type suppression still falls back to bare RecordType.name when stable identities are incomplete

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** compare/opaque_types.py:74-130 now uses OpaqueTypeIndex carrying stable EntityIds plus a bare-name 'local' tier. contains(strict=True) narrows the bare-name collision only when intersect() proves the pairing is complete, and otherwise falls back to the old bare RecordType.name matching. diff_filtering.py:854 _root_type_name now prefers Change.qualified_name.

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
