# ADR-080: Persisting the Advanced-DWARF Extraction Status

**Date:** 2026-10-12
**Status:** Accepted — implemented. The in-memory status was implemented
earlier; the status is now persisted as `dwarf_advanced.extraction` in schema
v68 (from v67), with the maintainer's decisions D3 (one-time digest change at
the bump) and D4 (a mixed-provenance note, not a gate) recorded below.
Drafted for the known gap
[`advanced-facts-collected-infers-advanced-dwarf-extraction-ran`](../known-gaps/advanced-facts-collected-infers-advanced-dwarf-extraction-ran.md),
which is now FIXED.

## Context

`AdvancedDwarfMetadata.extraction` (`not_run`, `completed`, `failed`, plus
`unrecorded` for objects that never recorded an outcome) is set by the
extraction itself. `model.dwarf_facts.advanced_facts_collected` reads it, so a
completed parse that found nothing is distinguishable from a run that never
happened, and a failed read is never reported as an empty surface. That part is
live for every snapshot built in the same process as its compare.

It was not persisted as of 2026-10-12 (schema v67): `storage/snapshot_encode.py` dropped the key
(`_drop_advanced_extraction_status`). The reason is the rule in AGENTS.md and
the known-gaps write-up: the encoder walks every dataclass field, so adding the
field without dropping it would add a key to every stored document, and that is
a snapshot-schema change, which needs an ADR and a migration. This keeps the
stored document byte-identical to v58.

The cost is real. A snapshot that goes through `dump` to a file and is later
loaded by `compare` decodes with `unrecorded`, so the predicate falls back to
the payload inference the known gap describes. That inference is sound for
every shape reachable from a stored snapshot today, except one: a successful
parse that found nothing and whose `target_arch` is empty. For that shape the
stored snapshot still reads "not collected", so the detector is skipped on both
sides without saying why.

## Options considered

1. **Keep the drop; accept the stored-snapshot gap.** Current state. It is
   correct and changes nothing, but it leaves the failure mode the known-gap
   entry names in place for every file-based compare.
2. **Persist `dwarf_advanced.extraction` with a schema bump.** Proposed. It
   closes the gap for stored snapshots, and it is the only option that does.
3. **Derive the status at decode time from a new `target_arch` sentinel.**
   Rejected. It would make `target_arch` carry a second meaning, which is the
   inference the known gap exists to remove, moved to a different field.

## Decision (accepted 2026-10-29)

**D1. One persisted key.** `dwarf_advanced` gains `extraction`, written only
when the status is not `unrecorded`. A DWARF dump always records `completed`
or `failed`, and a presence-only dump records `not_run`, so in practice the key
appears on every DWARF-derived snapshot. BTF, CTF and PDB objects stay
`unrecorded` and write no key.

**D2. Absent means `unrecorded`.** A pre-v59 document, and any v59 document
without the key, decodes to `unrecorded`. The reader then uses the existing
inference, so a stored snapshot changes its reading only when it carries a
recorded status. This is the same convention ADR-079 uses for `export_read`.

**D3. Snapshot digests change once (accepted).** The content digest covers the
encoded document, so every DWARF snapshot's digest changes at the bump.
Snapshot caches are keyed by schema version, so this invalidates them once
rather than producing a silent mismatch. **Maintainer decision: accepted.** The
bump is v67 to v68 (the schema version this repository is at; the v58 to v59
numbering in the draft is stale).

**D4. Comparability when the two sides disagree (maintainer decision).** A
stored `completed` side paired with a legacy `unrecorded` side is compared the
same way the detector compares them now: both must be collected for the
detector to run. The open question is whether a pair where one side is
`unrecorded` and the other is `completed` should be flagged in the assurance
output as a mixed-provenance comparison. The proposal is a note, not a gate and
not a new exit code.

**Chosen: the note.** The recommended option is taken as proposed. A pair with
one side `unrecorded` and the other `completed`, with both sides carrying
advanced DWARF, keeps the detector running on both sides (no gate, no exit
code change). `analysis_assurance`'s DWARF-context notes name the
`unrecorded` side in a `mixed provenance` note, and the context status stays
`clean`, since nothing was skipped. Implemented in
`policy/analysis_assurance/_context.py` (`_advanced_provenance_notes`).

## Migration and compatibility

- Schema v67 to v68, one additive nested key (`dwarf_advanced.extraction`). No
  top-level key is added, so the section allowlists (`legacy_sections`,
  `sparse_section_codec`) do not change. ADR-079, which drafted a shared v59
  bump, is still Proposed and did not take a bump, so v68 carries only this
  field.
- A pre-v68 reader drops the `extraction` key silently and reads the inference,
  the same result as before. So an old reader is not made less correct, and the
  key needs no `_MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION` entry.
- No default, exit code, CLI flag or report field changes. The one report
  change is the D4 note, which only appears for a mixed-provenance pair.

## What this ADR does not decide

- Whether `unrecorded` should be retired entirely. It stays for hand-built
  objects and for BTF/CTF/PDB until those producers record their own status.
- Per-compile-unit failures inside a completed parse. A skipped CU still reads
  `completed` today, because the parse walked the file. Recording a partial
  parse would be a fourth status and is a separate decision.

## Verification (done)

- Round trip per status, `completed`/`failed`/`not_run`, as a case of
  `tests/unit/storage/test_snapshot_field_roundtrip.py` (`KNOWN_LOSSY` is now
  empty, ceiling 0); `unrecorded` writes no key
  (`tests/test_advanced_extraction_status.py`).
- Digest: a recorded `completed` snapshot and its `unrecorded` twin differ;
  two identical `completed` snapshots agree
  (`tests/test_advanced_extraction_status.py`).
- Reader: a pre-v68 document (no key, schema 67) decodes to `unrecorded`, and
  a garbled status is refused (`tests/test_advanced_extraction_status.py`).
- Live versus stored: `tests/test_live_stored_advanced_dwarf_parity.py`, a
  real `gcc -g` pair.
