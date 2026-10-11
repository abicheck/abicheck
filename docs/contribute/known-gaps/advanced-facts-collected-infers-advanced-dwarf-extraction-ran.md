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

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** model/dwarf_facts.py:181 advanced_facts_collected still infers that extraction ran. The comment at line 229 says an explicit 'advanced extraction completed' status is still future work.

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
