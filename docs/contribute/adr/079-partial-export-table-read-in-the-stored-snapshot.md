# ADR-079: Recording a Partial Export-Table Read in the Stored Snapshot

**Date:** 2026-10-12
**Status:** Proposed — not implemented. Drafted for the known gap
[`a-stored-snapshot-keeps-no-record-of-a-partial-export-table-read`](../known-gaps/a-stored-snapshot-keeps-no-record-of-a-partial-export-table-read.md).
Proposes one snapshot field, a schema bump from v58 to v59, and two decisions
the maintainer must confirm (D4, D5). No code, schema or default changes with
this draft; the schema stays at v58 until the decisions are accepted.

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

## Decision (proposed)

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

**D4. Consumers (maintainer decision).** `read_library_export_facts` maps a
stored `partial` to a `PARTIAL` fact: usable, with reduced confidence and the
unread part named as unknown, not absent. A `failed` maps to `FAILED`, as the
import path already does. The open question is whether a `partial` export
read should also lower scope completeness (ADR-065), so that a comparison
which relied on it reports incomplete coverage. This draft proposes yes, as
incomplete coverage, and no new exit code.

**D5. Old-reader safety (maintainer decision).** A pre-v59 reader drops the
key without error, so it would read a partial snapshot as complete. The
proposal is to rely on the existing version gate, the same mechanism v42 used
for the envelope change. The maintainer must decide whether v59 should also
raise `_MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION`, which would make an old
reader refuse a v59 document outright. Hard rejection is the safer choice, and
it is a visible behaviour change for anyone on an old build.

## Migration and compatibility

- Schema v58 to v59. The bump is shared with ADR-080 (the advanced-DWARF
  status) so that one reader-compatibility decision covers both fields.
- No default, exit code, CLI flag or report field changes with this ADR.
  D4 changes a coverage row and is listed for review because it touches
  scope completeness.
- A stored snapshot written before v59 is read as `unrecorded`. It is not
  re-classified. Re-dumping it with the current build gives the same
  `complete`/`failed` the dump already produces.

## What this ADR does not decide

- How to detect a partial read on PE and Mach-O beyond the existing
  `note_export_read_failure` sites. Those sites are the input to D1, and
  each new site must register itself there.
- Whether `dump` should keep accepting a binary with a partial read rather
  than rejecting it. That is a behaviour choice and is out of scope here.

## Verification plan (once accepted)

- A regression test per platform: a stored block with `export_read` set to
  each of the three values reads as the matching fact, and a v58 block reads
  as `unrecorded`, across several synthetic blocks, not one fixed example.
- A round-trip test: a v59 snapshot with `partial` survives encode and decode
  unchanged, and a `complete` snapshot encodes byte-identical to v58 apart
  from the version number.
- A mutation test on the reader's mapping, so that flipping `partial` to
  `complete` fails a test.
