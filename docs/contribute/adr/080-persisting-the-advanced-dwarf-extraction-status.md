# ADR-080: Persisting the Advanced-DWARF Extraction Status

**Date:** 2026-10-12
**Status:** Proposed — the in-memory status is implemented; persistence is not.
Drafted for the known gap
[`advanced-facts-collected-infers-advanced-dwarf-extraction-ran`](../known-gaps/advanced-facts-collected-infers-advanced-dwarf-extraction-ran.md).
Proposes one snapshot field, shared with ADR-079 in a single schema bump from
v58 to v59, and two decisions for the maintainer (D3, D4). Nothing in the
stored format changes until these are accepted.

## Context

`AdvancedDwarfMetadata.extraction` (`not_run`, `completed`, `failed`, plus
`unrecorded` for objects that never recorded an outcome) is set by the
extraction itself. `model.dwarf_facts.advanced_facts_collected` reads it, so a
completed parse that found nothing is distinguishable from a run that never
happened, and a failed read is never reported as an empty surface. That part is
live for every snapshot built in the same process as its compare.

It is not persisted. `storage/snapshot_encode.py` drops the key
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

## Decision (proposed)

**D1. One persisted key.** `dwarf_advanced` gains `extraction`, written only
when the status is not `unrecorded`. A DWARF dump always records `completed`
or `failed`, and a presence-only dump records `not_run`, so in practice the key
appears on every DWARF-derived snapshot. BTF, CTF and PDB objects stay
`unrecorded` and write no key.

**D2. Absent means `unrecorded`.** A pre-v59 document, and any v59 document
without the key, decodes to `unrecorded`. The reader then uses the existing
inference, so a stored snapshot changes its reading only when it carries a
recorded status. This is the same convention ADR-079 uses for `export_read`.

**D3. Snapshot digests change once (maintainer decision).** The content digest
covers the encoded document, so every DWARF snapshot's digest changes at the
v59 bump. Snapshot caches are keyed by schema version, so this invalidates them
once rather than producing a silent mismatch. The maintainer must confirm that
a one-time digest change at the bump is acceptable.

**D4. Comparability when the two sides disagree (maintainer decision).** A
stored `completed` side paired with a legacy `unrecorded` side is compared the
same way the detector compares them now: both must be collected for the
detector to run. The open question is whether a pair where one side is
`unrecorded` and the other is `completed` should be flagged in the assurance
output as a mixed-provenance comparison. The proposal is a note, not a gate and
not a new exit code.

## Migration and compatibility

- Shared with ADR-079: schema v58 to v59, one reader-compatibility decision,
  one `_MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION` decision.
- A pre-v59 reader drops the `extraction` key silently and reads the
  inference, the same result as today. So an old reader is not made less
  correct by the new key.
- No default, exit code, CLI flag or report field changes. The reading change
  is limited to a stored snapshot whose recorded status differs from the
  inference, and the change is toward the status the extraction actually
  recorded.

## What this ADR does not decide

- Whether `unrecorded` should be retired entirely. It stays for hand-built
  objects and for BTF/CTF/PDB until those producers record their own status.
- Per-compile-unit failures inside a completed parse. A skipped CU still reads
  `completed` today, because the parse walked the file. Recording a partial
  parse would be a fourth status and is a separate decision.

## Verification plan (once accepted)

- Round-trip per status across several payload shapes: `completed` with an
  empty payload and an empty arch, `completed` with a populated payload,
  `failed`, `not_run`. Each must decode to the status it was encoded with, and
  `unrecorded` must write no key.
- A digest test: a v59 `completed` snapshot and the same snapshot with
  `unrecorded` differ, while two identical `completed` snapshots agree.
- A reader test: a v58 document decodes to `unrecorded` and reads the same
  collected/not-collected answer as before the bump, over the set of payload
  shapes already covered by `tests/test_debug_evidence_presence.py`.
