---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# An unreadable NEW-only release member is reported unmatched, not unreadable

Index: [Known gaps — closed and moot](../archive/known-gaps-closed.md)

**Status (2026-11-01): CLOSED.** Unmatched members are probed by content after pairing (`workflows/release_member_readability`) and an unreadable one is `failed`, on both routes and both sides. Residual: a damaged native body with valid magic, recorded separately.

## Evidence

A release `compare` extracts only the members that pair. A file that discovery
accepts as a member on its name alone (`notes.so`, a text file; see
`classify.BinaryExtensionClassifier`, which keeps linker-script stubs
discoverable) and that has no OLD counterpart is never opened. It reads as an
unmatched NEW member (`not_supplied`, listed in `unmatched_new`), never as
`failed`. Its unreadability goes unreported. This holds identically on
`compare OLD_DIR NEW_DIR` and `compare OLD.bundle-facts.json NEW_DIR`, and
neither route reads it as a clean comparison.

When the same file *does* pair, both routes report it as an explicit `failed`
member with an extraction error and exit 4.

## Intended resolution

Probe each unmatched member's format during discovery or scope
classification, and record a member that no extractor can read as `failed`
(ADR-065 D1) rather than as a plain one-sided member. Do this once, in the
shared scope builder, so both routes keep agreeing.

## Test that pins it

`tests/test_cli_compare_stored_versioned_member_pairing.py::test_an_unreadable_new_only_member_is_unmatched_on_both_routes`
pins the current, agreeing behaviour on both routes. Flip its expected state
to `failed` when this is fixed.
