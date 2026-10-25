---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A binary-only dump loses exports with no debug subprogram, so a still-exported symbol reads as removed (2026-10-25)

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-25): OPEN.** Found by
`tests/test_depth_projection_matches_shallow_dump.py`, where it is excused as
an oracle defect when the projection proves the symbol is exported on both
sides.

A binary-only dump (no `-H`) of a library with DWARF builds its declarations
from the debug-info subprograms (`workflows/dump/elf_fallback._try_dwarf_snapshot`).
When that yields at least one function, it uses the DWARF snapshot directly.
Exports with no matching subprogram do not become declarations.
When it yields none, the dump falls back to one export-only record per
export (`_build_symbol_only_snapshot`). The two versions of one library can
take different paths, so the same export is a declaration on one side and
absent on the other.

Reproduction: catalog `case38_virtual_methods`, built with the catalog
CMake. `nm -D libv2.so` lists `_ZN9Processor8validateEi`,
`_ZN9Processor9transformEi` and `_ZTI9Processor`, but
`abicheck compare libv1.so libv2.so` reports each as "dynamic export removed"
(`func_removed_elf_only`, `var_removed`). The v2 dump's declarations hold a
single `_ZN9ProcessorC4ERKS_`. The same mechanism reports `crypto_hash` as
added in case65, where both versions export `crypto_hash@@CRYPTO_2.0`. It
also reports every v1 export of case80 as added.

`false-positive` — a binary-only comparison reports still-exported symbols as
removed or added. The verdict in these catalog cases is already `BREAKING`
from real changes, so no exit code changed there. A library whose only
change is unrelated would still get false removal findings.

Fix direction: when the DWARF snapshot is used, add an export-only record for
every ABI-relevant export it does not declare, as the symbol-only fallback
does. Then check that `undeclared_exports` does not report those symbols a
second time. This changes what a dump records: it needs an
`EXTRACTOR_GENERATION` bump, a migration row and a corpus freeze.
