---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# An `ownership_fact` is persisted only as a PRESENT decision

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-29): FIXED (schema v67).** The round trip is pinned, and every `FactStatus` member now survives a dump and load.

**What was lost, and the fix.** A declaration's `ownership_fact` (ADR-075) was written only as a `PRESENT` triple inside the `extraction_scope` block. Schema v67 adds two keys: `entity_ownership.statuses` carries each non-`PRESENT` fact as `[position, status, producer, diagnostics]` beside the interned decision table, and `ownership_without_scope` carries the decisions of a snapshot with no recorded scope, without writing a scope (ADR-075 reads an unrecorded scope as unrecorded). A `PRESENT` decision encodes exactly as before.

**Why no verdict changes.** The only reader is `model.extraction_scope.ownership_of`, which answers `None` for every non-`PRESENT` fact and for a missing one. A decoded `None` and a decoded `NOT_APPLICABLE` therefore mean the same thing to every consumer in the tree. The loss is in the `Fact` object a direct reader could inspect, not in any decision.

**Why the producer never hits it.** `extract/ownership_stamp.stamp_ownership` is the only writer of `ownership_fact`. It writes `Fact.present` only, and it always sets `extraction_scope` in the same call. The no-scope and non-`PRESENT` states are reachable only from hand-built objects.

**How it is pinned.** `tests/unit/storage/test_snapshot_field_roundtrip.py` enumerates each `ownership_fact` field inside a scope, runs one case per `FactStatus` member, and names each lossy case (`ownership_fact[<status>]`, `ownership_fact[no_extraction_scope]`) in `KNOWN_LOSSY` with this reason. `test_every_known_lossy_entry_is_lossy` fails when one of those cases stops being lossy, so the entry cannot outlive the defect.

**Related.** [Advanced DWARF extraction status](advanced-facts-collected-infers-advanced-dwarf-extraction-ran.md) is the same pattern for `dwarf_advanced.extraction`, where the loss does change a predicate's inputs.
