# ADR-079: Recording a Partial Export-Table Read in the Stored Snapshot

**Date:** 2026-10-12
**Status:** Accepted — implemented (accepted 2026-11-02; amended 2026-11-03: stored as an ADR-062 D19 extension fact on the `binary` section, superseding D5's global bump). Written for the known gap
[`a-stored-snapshot-keeps-no-record-of-a-partial-export-table-read`](../known-gaps/a-stored-snapshot-keeps-no-record-of-a-partial-export-table-read.md).
Accepts one snapshot field with D4 as proposed (a `partial` read lowers
scope completeness, no new exit code). D2, D3 and D5 are read through the
[amendment](#amendment-2026-11-03-stored-as-an-extension-fact-adr-062-d19):
no `SCHEMA_VERSION` bump. See [Implementation](#implementation).

## Context

A library's export table is read in two places that can each fail without
raising: `parse_{elf,pe,macho}_metadata` keep going past a malformed section
and return what they read. `extract/parse_failures.py` records each swallowed
failure inside a `recording_export_read_failures()` block, and
`model.export_index.read_export_platforms` reads a platform as:

- **complete** when it holds at least one export, or its header fields show
  the binary was parsed (`platform_block_parsed`);
- **FAILED** otherwise, so a default or parse-failed block never reads as
  "exports nothing".

That rule covers the *total* failure. It does not cover the *partial* one: a
`.dynsym` that is skipped after the header parsed. The header fields are set,
so `platform_block_parsed` is true, and the surviving entries look like a
complete table. A stored snapshot records only the symbol list and the header
fields. Nothing in the document says the table was cut short.

Consequences, in both directions:

- A declaration whose export is in the skipped part reads as **unexported**.
  Public-surface scoping (ADR-065, ADR-068) can then exclude a real public
  declaration, or report a symbol as removed when it was only unread.
- The live pipeline is mostly protected: `dump` rejects every binary it tried
  with a failed export read (ELF, and whole-file PE/Mach-O). So in practice
  only a snapshot produced by another tool can carry a partial table, and it
  is loaded as if complete.

Today the only record of a partial read is the process log, which a stored
snapshot does not keep. That is a false-clean path, and AGENTS.md's "weaker
evidence narrows conclusions" rule requires it to be closed.

## Options considered

1. **Prose note in the known-gaps entry only.** Rejected. It is not
   machine-checkable, and a reader of the stored snapshot cannot see it.
2. **Record the partial read in the run's report only.** Rejected. A later
   `compare` of a stored snapshot would still read it as complete, and the
   report of the dump run is not part of the stored evidence.
3. **Treat any platform with a skipped section as FAILED at dump time.**
   Rejected for now. It throws away the entries that were read, and `dump`
   already rejects such binaries, so the remaining gap is only the
   externally-produced snapshot case.
4. **A per-platform export-read state persisted on the platform block, with a
   schema bump.** Proposed. It is the only option that lets a stored snapshot
   carry the fact, and it reuses the existing `FAILED`/`PARTIAL` vocabulary.

## Decision

**D1. One field, per platform.** Each stored platform block (`elf`, `pe`,
`macho`) gains `export_read`, one of `complete`, `partial`, `failed`. The
writer sets it from the recording sink in `extract/parse_failures.py`:
`partial` when at least one export was read and a failure was noted, `failed`
when none was read, `complete` otherwise.

**D2. Encoding follows the existing "encodes exactly as the prior version"
pattern.** In a v59 document an absent `export_read` means `complete`. Only a
non-complete value is written, so a snapshot with no read fault encodes the
same as v58 apart from the version number.

**D3. Pre-v59 documents read as `unrecorded`, never `complete`.** A v58 or
older document has no record of the read, so the reader must not claim one.
For such a document the reader keeps today's rule (a header-parsed block is
usable, an empty unparsed one is FAILED). Nothing about an old snapshot
changes.

**D4. Consumers.** `read_library_export_facts` maps a
stored `partial` to a `PARTIAL` fact: usable, with reduced confidence and the
unread part named as unknown, not absent. A `failed` maps to `FAILED`, as the
import path already does. A `partial` export read also lowers scope
completeness (ADR-065), so a comparison that relied on it reports incomplete
coverage. No new exit code is added.

**D5. Old-reader safety.** A reader older than the schema version this
change allocates refuses the snapshot before decoding `export_read` when that
version is at or above `_MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION`
(currently 14), which the allocated version is. Only a reader that predates
the hard-rejection gate itself warns and continues, and so could read a
partial snapshot as complete. The bump relies on this existing version gate,
the same mechanism v42 used for the envelope change, and does not raise
`_MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION`.

## Migration and compatibility

- Schema v58 to v59. The bump is shared with ADR-080 (the advanced-DWARF
  status) so that one reader-compatibility decision covers both fields.
- No default, exit code, CLI flag or report field changes with this ADR.
  D4 changes a coverage row because it touches scope completeness.
- A stored snapshot written before v59 is read as `unrecorded`. It is not
  re-classified. Re-dumping it with the current build gives the same
  `complete`/`failed` the dump already produces.

## What this ADR does not decide

- How to detect a partial read on PE and Mach-O beyond the existing
  `note_export_read_failure` sites. Those sites are the input to D1, and
  each new site must register itself there.
- Whether `dump` should keep accepting a binary with a partial read rather
  than rejecting it. That is a behaviour choice and is out of scope here.

## Verification plan

- A regression test per platform: a stored block with `export_read` set to
  each of the three values reads as the matching fact, and a v58 block reads
  as `unrecorded`, across several synthetic blocks, not one fixed example.
- A round-trip test: a v59 snapshot with `partial` survives encode and decode
  unchanged, and a `complete` snapshot encodes byte-identical to v58 apart
  from the version number.
- A mutation test on the reader's mapping, so that flipping `partial` to
  `complete` fails a test.

## Implementation

- **Model.** `model/export_read.ExportReadState` (`complete`/`partial`/
  `failed`/`unrecorded`) is the `export_read` field of `ElfMetadata`,
  `PeMetadata` and `MachoMetadata` (`compare=False`: provenance, not table
  content). `export_read_state` is the D1 writer rule.
- **Writer.** `extract/export_table_read.read_exports_or_fail` stamps
  `complete` on the block it returns; a read that noted a failure still
  raises there (`dump` keeps rejecting it). `read_library_export_facts`
  applies the same D1 rule to a raw binary's recorded failures, so a library
  read live and its stored copy carry the same fact.
- **Encoding (D2/D3, as amended).** `storage/extension_fact_catalog.EXPORT_READ`
  (`abicheck.export_read`, `binary`, `SECTION_MUST_UNDERSTAND`,
  `must_understand_when` a value is `partial`/`failed`). Its value lists
  every platform block that is not `complete` (`{}`: all complete), so D2's
  "a complete read is not written" holds per block, and an absent fact is
  D3's `unrecorded`. `storage/extension_facts.sync_export_read_fact` writes
  it from the model field before every write path and the evidence digest
  (a no-op while every block is `unrecorded`, so a loaded fact is kept);
  `apply_export_read_fact` sets the blocks from it on load and refuses a
  value that does not decode. The block itself never carries the key
  (`storage/enum_codec.py` drops it), so a flat document reads `unrecorded`.
- **Readers (D4).** `model/export_index.platform_export_read` is the one
  effective-state rule: a recorded `partial`/`failed` is taken as recorded,
  anything else keeps the pre-ADR-079 rule. `ExportTableState.PARTIAL` withdraws
  every stored "not exported" answer (at dump and at load), the `exports`
  coverage record reads `failed` (`export_table_partially_read`),
  `read_default_export_names` answers unknown, and consumer scoping refuses
  a `PARTIAL` library (it reads absence).
- **Comparison, assurance, report.** `policy/partial_export_read.py` keeps
  each removal a recorded `partial`/`failed` NEW table cannot establish and
  annotates it `COMPATIBLE_WITH_RISK` under the `partial_export_table_read`
  rule (a removal the NEW side's parsed headers establish stands).
  `analysis_assurance.export_read` reports each side's recorded states and a
  gap makes the status `partial` (so `assurance.require_complete` exits 1);
  `run_outcome.scope` reads `incomplete`; the Markdown report lists the gap
  as a coverage row. Report schema 7.2.
- **Tests.** `tests/unit/storage/test_export_read_persisted.py` (property
  over the read outcomes × live/stored × ELF/PE/Mach-O, a document without
  the fact, re-save, an older reader, the D1 table) and `tests/test_cli_compare_partial_export_read.py`
  (the public `compare` path, with a complete-table control).

## Amendment (2026-11-03): stored as an extension fact (ADR-062 D19)

[ADR-062 D19](062-project-snapshot-storage-v2.md#d19-an-optional-new-fact-is-a-declared-extension-fact)
reserves the global `SCHEMA_VERSION` for envelope and wire changes. Under it,
`export_read` is declared in `storage/extension_fact_catalog.py` as an
extension fact on the `binary` section, keyed by platform, with
`SECTION_MUST_UNDERSTAND` semantics and `must_understand_when` true for any
non-`complete` value. This replaces D5's global bump: a dump whose reads were
complete is readable by every older reader, and one with a `partial` or
`failed` read blocks only the analyses that read `binary` in an older reader
instead of refusing the document. D2 still holds (a `complete` read is not
written), D3's `unrecorded` is the fact's `NOT_COLLECTED`, and D1 and D4 are
unchanged.
