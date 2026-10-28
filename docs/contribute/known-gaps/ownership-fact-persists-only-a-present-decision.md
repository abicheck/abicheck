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

**Status (2026-10-27): OPEN, deliberate.** The round trip is pinned, not fixed.

**What is lost.** A declaration's `ownership_fact` (ADR-075) is written only inside the `extraction_scope` block, as one interned `(owner, contract, rule_id)` triple per decision. The decision table has no slot for a status, so:

- a `PRESENT` fact round-trips;
- every other status (`PARTIAL`, `NOT_COLLECTED`, `UNSUPPORTED`, `FAILED`, `NOT_APPLICABLE`) is written as unclassified and reads back as `None`;
- a `PRESENT` fact on a snapshot with no `extraction_scope` is dropped entirely, because `encode_extraction_scope` removes the per-entity fact and writes no block, and the decoder then leaves `extraction_scope=None` with no decisions (ADR-075: an unrecorded scope is never read as recorded).

**Why no verdict changes.** The only reader is `model.extraction_scope.ownership_of`, which answers `None` for every non-`PRESENT` fact and for a missing one. A decoded `None` and a decoded `NOT_APPLICABLE` therefore mean the same thing to every consumer in the tree. The loss is in the `Fact` object a direct reader could inspect, not in any decision.

**Why the producer never hits it.** `extract/ownership_stamp.stamp_ownership` is the only writer of `ownership_fact`. It writes `Fact.present` only, and it always sets `extraction_scope` in the same call. The no-scope and non-`PRESENT` states are reachable only from hand-built objects.

**How it is pinned.** `tests/unit/storage/test_snapshot_field_roundtrip.py` enumerates each `ownership_fact` field inside a scope, runs one case per `FactStatus` member, and names each lossy case (`ownership_fact[<status>]`, `ownership_fact[no_extraction_scope]`) in `KNOWN_LOSSY` with this reason. `test_every_known_lossy_entry_is_lossy` fails when one of those cases stops being lossy, so the entry cannot outlive the defect.

**What closing it would take.** A per-entry status in the decision table (a new encoding for a non-`PRESENT` triple) or a per-entry status key. Both are snapshot-schema changes that need a `SCHEMA_VERSION` bump with a history line and a reader-compatibility decision, following the storage bump procedure in `abicheck/storage/AGENTS.md`. Writing a scope for a scope-less snapshot is not an option: it would turn "unrecorded" into "recorded" without a classifier having run.

**Related.** [Advanced DWARF extraction status](advanced-facts-collected-infers-advanced-dwarf-extraction-ran.md) is the same pattern for `dwarf_advanced.extraction`, where the loss does change a predicate's inputs.
