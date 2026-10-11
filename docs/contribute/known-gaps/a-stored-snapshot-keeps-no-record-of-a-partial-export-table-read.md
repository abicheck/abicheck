---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A stored snapshot keeps no record of a partial export-table read

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The entry says closed, but it also says the partial read is still open. Fixed: FAILED import reads; tests/unit/workflows/test_failed_consumer_read.py exists. Open: a stored snapshot cannot record a partial export-table read (needs a schema field and ADR).


**Closed (2026-10, Lane C stage 5).** `workflows.consumer_scope.read_consumer_facts`
now treats a `FAILED` consumer-import fact exactly like an unrecognised
format: a REQUIRED consumer raises `ConsumerUnreadableError`, an ADVISORY one
yields `unreadable=True`. Regression tests:
`tests/unit/workflows/test_failed_consumer_read.py`. The library side
followed in Lane C stage 6: `parse_{elf,pe,macho}_metadata` still return what
they read, but each site that loses export-table facts records it
(`extract/parse_failures.py`), and `read_library_export_facts` reads such a
library -- or a stored block that records no parse
(`model.export_index.platform_block_parsed`) -- as `FAILED`. The scoping
workflow then raises `LibraryExportsUnreadableError` (`compare --used-by`:
exit 1, `Error: --used-by library: ...`). Regression tests:
`tests/unit/extract/test_library_export_read_failures.py`. Still open: a
stored snapshot keeps no record of a *partial* read (a skipped `.dynsym` with a
parsed header) -- persisting it needs a snapshot-schema field and its ADR;
`dump` rejected every such binary tried (ELF and whole-file PE/Mach-O
failures), so in practice only a snapshot produced elsewhere can carry one.
