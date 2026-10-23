---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A large export shrink with no declared surface emits no finding in header mode (2026-10-22)

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-22): OPEN, deliberate follow-up — product decision needed.**

`abi_surface_explosion` used to fire for a large export-count shrink (a 281 to
138 shrink was the field report), under the growth kind and with the growth
advice. It is now growth-only (`diff_platform._diff_abi_surface`), so a shrink
is no longer reported under that kind.

For an ELF-only dump the removals are still reported one by one: the dumper
builds an export-table `Function` for each symbol, and the functions detector
classifies each removal as `func_removed_elf_only`
(`tests/test_abi_surface_direction.py` pins 143 such findings for 281 to 138).

For a header-mode comparison, the exports that no header declares are not
`functions` entries. Removing them emits no change, so a shrink of undeclared
exports now reads as no finding at all. The old aggregate was the only signal
for that case, and it is gone.

Whether undeclared exports belong in the compatibility contract is a question
for the surface-declaration model (`surface_facts`), not for this detector. The
choice is one of: report the shrink under a direction-correct kind, or record
undeclared-export removals as their own evidence. Neither is implemented.

Do not restore the shrink under `ABI_SURFACE_EXPLOSION` with a different
description without an ADR: that renames the meaning of a public kind.
