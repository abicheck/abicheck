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

**Status (2026-11-03): FIXED for persistence and consumers; narrowed.** [ADR-079](../adr/079-partial-export-table-read-in-the-stored-snapshot.md) is implemented, as its 2026-11-03 amendment specifies: the read is the `binary` section's `abicheck.export_read` extension fact (ADR-062 D19, no `SCHEMA_VERSION` bump), listing each platform block that was not read completely (`partial`, `failed`; `{}` for all complete), and must-understand for that section only when a value is `partial` or `failed`. A document without the fact reads `unrecorded` and keeps the older rule, never `complete`. The readers follow one effective-state rule (`model.export_index.platform_export_read`): a `partial` block withdraws its stored "not exported" answers at load, reads as a `PARTIAL` export fact (consumer scoping refuses it), makes the `exports` coverage record `failed` (`export_table_partially_read`), keeps each removal it cannot establish as `COMPATIBLE_WITH_RISK` under the `partial_export_table_read` rule, and lowers `analysis_assurance.status` to `partial` (with `analysis_assurance.export_read` naming the side, and a coverage row) and `run_outcome.scope` to `incomplete`. A live library read applies the same D1 rule, so live and stored operands agree. Regression tests: `tests/unit/storage/test_export_read_persisted.py` (property over the read outcomes, live and stored, ELF, PE and Mach-O; an older reader) and `tests/test_cli_compare_partial_export_read.py` (public `compare`, JSON and Markdown, exit under `assurance.require_complete`). Still open: (1) a baseline written before the fact whose read was cut short has no record and still reads by the older rule -- re-dump it; (2) in a release/package comparison a member's partial read lowers that member's assurance and annotates its removals, but does not mark the release's `comparison_scope` record incomplete; (3) `dump` still rejects a live partial read (ADR-079 leaves that choice open), so only an externally produced snapshot carries `partial` today; (4) a flat `snapshot_to_dict` document has no section header, so it carries no fact and reads `unrecorded`.


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
