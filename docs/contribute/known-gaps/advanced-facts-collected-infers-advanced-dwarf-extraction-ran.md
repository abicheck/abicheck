---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `advanced_facts_collected` infers "advanced DWARF extraction ran" from its output rather than recording it

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-12): PARTIAL.** The in-memory half is fixed; the persisted half is open.

**Landed (2026-10-12).** `AdvancedDwarfMetadata.extraction` (`AdvancedExtractionStatus`: `not_run`, `completed`, `failed`, and `unrecorded` for an object that never recorded one) is set by the extraction itself: `dwarf_unified.parse_dwarf` records `completed` after a walk, `failed` when the file cannot be opened or parsed, and `completed` with `has_dwarf=False` when the ELF carries no DWARF. The presence-only paths (`dwarf_presence`) record `not_run`. `advanced_facts_collected` reads the status: `completed` is collected when DWARF was found, `not_run` and `failed` never are, and only `unrecorded` falls back to the payload inference. The `advanced_dwarf` detector's support gate names the state in its not-evaluated reason, and `analysis_assurance._dwarf_context_status` reports a failed side as incomplete coverage. Regression tests: `tests/test_advanced_extraction_status.py`.

**Still open.** The status is not persisted. `storage/snapshot_encode.py` drops it, so the stored document is byte-identical to v58 and a snapshot decoded from a file reads `unrecorded`, which keeps the inference. Persisting it adds a snapshot-schema key and needs [ADR-080](../adr/080-persisting-the-advanced-dwarf-extraction-status.md) (drafted, Proposed). The inference's one unsound shape, a completed empty parse with an empty `target_arch`, therefore still applies to stored snapshots only.

**Measured (2026-10-27).** A round trip through `snapshot_to_json` / `snapshot_from_dict` reads every status back as `unrecorded`: `not_run`, `completed` and `failed` alike. So a `FAILED` extraction is no longer distinguishable from a parse that never ran once the snapshot is written to a file, and `advanced_facts_collected` answers from the payload alone. The loss is pinned, not hidden: `tests/unit/storage/test_snapshot_field_roundtrip.py` has one case per status, and its `KNOWN_LOSSY` entries `AdvancedDwarfMetadata.extraction[not_run|completed|failed]` and `Declarations.debug_advanced` name it, with the same reason, until ADR-080 is decided.

**Historical note (2026-10-09, `456989f`):** the paragraphs below describe the state before the in-memory fix.

The predicate answers by checking
whether any field `dwarf_advanced.diff_advanced_dwarf` consumes is
non-empty, plus `target_arch` as a discriminator for a parse that completed
but established nothing (`dwarf_advanced` sets it from the ELF header;
the presence-only helpers leave it `""`). That is sound for every shape
reachable today, and it is mutation-checked per consumed family in
`tests/test_debug_evidence_presence.py`. But it is still an inference: the
honest signal is a **status**, not a reconstruction from findings.

The reason it matters is asymmetric. `checker.py` requires the predicate on
*both* sides, so any shape the inference gets wrong does not merely mislabel
coverage — it disables the `advanced_dwarf` detector wholesale and the run
silently misses real drift. That is a false negative, the worse direction,
and two such shapes were already found by review during one PR (the three
`toolchain` flag sets, then the successful-but-empty parse).

**The durable fix is an explicit "advanced extraction completed" field on
`AdvancedDwarfMetadata`**, set where the parse succeeds and persisted, with
the predicate reading it instead of inferring. Not folded into the fix PR
because it is a persisted-model change: a new field means a
`SCHEMA_VERSION` bump and a decision about how a pre-bump snapshot reads
(almost certainly "unknown", resolved conservatively), which this file's own
contract says gets its own ADR and migration rather than riding along with a
behavior fix. Until then the inference stands, and any new consumer of
`AdvancedDwarfMetadata` must be added to `_CONSUMED_ADVANCED_FIELDS` in that
test or it will silently disable the detector again.
