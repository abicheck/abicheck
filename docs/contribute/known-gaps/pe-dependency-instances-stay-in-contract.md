---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# On PE/COFF a header-only dependency's exported template instances stay in contract (2026-10-26)

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-26): OPEN.** A limit of ADR-089's 2026-10-25 amendment.

`workflows/dump/dependency_internals.py` moves a header-only dependency's
functions and variables out of contract only when the library's binary does
not define them as its own. The binary's export table supplies that answer:

| Format | Library's own | Coalesced copy (not the library's) |
|---|---|---|
| ELF | `GLOBAL` binding | `WEAK`, `GNU_UNIQUE` |
| Mach-O | an export without the weak-definition flag | `N_WEAK_DEF` / `EXPORT_SYMBOL_FLAGS_WEAK_DEFINITION` |
| PE/COFF | every export | none recorded |

A linked DLL keeps no COMDAT evidence. `IMAGE_COMDAT_SELECT_*` is an
auxiliary record of an object file's section symbol, and the linker resolves
it. A `.drectve` `-export:` directive is also object-file only. The export
directory holds a name, an ordinal and an optional forwarder per row (`PeExport`),
and every row is a plain RVA into a merged section. mingw's auto-export
(`--export-all-symbols`, the default with no `dllexport`) lists the
dependency's template instances and hidden friends next to the library's own
functions. Nothing in the DLL tells them apart.

`false-positive` — On Windows, the `-mavx2` against `-mavx512f` rebuild of
`tests/field_shapes/header_only_dependency_internals` reports the
dependency's `hidden_friend_*`, `func_added`/`func_removed` and
`exported_not_public` findings in contract, and the verdict is `BREAKING`
instead of `COMPATIBLE_WITH_RISK`. That shape runs on `linux` and `darwin`
only for this reason. Records, typedefs and an inline variable the DLL does not export are
still classified on PE, because they do not depend on the export table.

Not done, deliberately: guessing coalescing from the name (a template
argument list, a dependency namespace). An explicit instantiation or an
out-of-line function the library really exports would read as a dependency
copy, and the finding would leave the contract on no evidence.

Closing it needs build evidence: the library's object files (L3) read for
COFF COMDAT sections, the way `buildsource/comdat_groups.py` reads ELF
`SHT_GROUP`. That module states the PE/COFF reader as a separate extractor.
Feed its symbols in as the coalesced set when a dump has them.
