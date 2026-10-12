# ADR-079: Inline-Fact Reliability for `FUNC_BECAME_INLINE` / `FUNC_LOST_INLINE`

**Date:** 2026-10-12
**Status:** Proposed — implemented in PR #1570 (D1, D2, D3′, D4′, D6); D5 rejected. Not yet accepted by a maintainer. The branch was implemented at the user's direction before acceptance. No schema version bump was required.
**Governs:** the gap in [known-gaps/a-pre-fix-clang-backend-baseline-compares-against-a-post-fix.md](../known-gaps/a-pre-fix-clang-backend-baseline-compares-against-a-post-fix.md).
**Relates to:** [062](062-project-snapshot-storage-v2.md) (storage version axes), [063](063-one-semantic-pipeline.md) (retirement of the `*_facts_reliable` booleans in favour of `stale_fact_families`).

## Context

`FUNC_BECAME_INLINE` and `FUNC_LOST_INLINE` are produced by
`compare/function_lifecycle.inline_changes`, called from
`diff_symbols._check_inline_transitions`. The detector reads a present
`is_inline` value on both sides and emits a finding when the two differ. A
missing value already declines (`function_lifecycle._value` returns `None`).

The implicit-inline fix (commit `5ba6a5c84`) corrected what the clang backend
records for a `constexpr`, in-class or `= default` declaration, and added
castxml recovery of out-of-line inline member definitions. A snapshot written
before the fix carries a wrong but *collected* `is_inline` value for both
producers. A pre-fix baseline compared with a post-fix candidate therefore
reports a spurious wave of `FUNC_BECAME_INLINE` (RISK).

### Verified 2026-10-12

- `SCHEMA_VERSION` is **52** at the parent of `5ba6a5c84` and at `5ba6a5c84`
  itself. The clang inline-semantics commits (`b47d3cb30`, `c571e3336`) landed
  at **46**, so v46 to v52 writes cover both sides of the fix. A cutoff of 53
  covers all of them.
- `EXTRACTOR_GENERATION` is still `1`. The fix did not bump it, and
  `StorageVersions` lives on the package manifest, not on `AbiSnapshot`. The
  producer generation stamps cannot separate the two sides, so this design
  does not use them.
- The persisted reliability keys are written generically from
  `FACT_FAMILIES` (`storage/snapshot_encode._expand_stale_fact_families`).
  A new family therefore persists without a schema change.

## Decision

**D1. A new fact family, `inline`.** `model/snapshot_reliability.FACT_FAMILIES`
gains `"inline"`. It covers `is_inline` for header-derived snapshots from the
clang and castxml producers (hybrid included).

**D2. Load-time staleness by version threshold.**
`storage/fact_schema_versions._MIN_SCHEMA_VERSION_FOR_INLINE_FACTS = 53`. A
header-derived snapshot below 53 is stale unless its document persists
`inline_facts_reliable`. DWARF and ELF-only snapshots are never stale for this
family. A v52 post-fix snapshot is also marked stale: the conservative
direction, which only declines a comparison.

**D3′. Decline in the detector, not by demoting the stored fact.** This
supersedes the earlier plan to demote each `is_inline` fact in
`storage/fact_backfill.py`. The canonical IR is a frozen dataclass, and
rebuilding it on load was disproportionate. Instead,
`diff_symbols._check_inline_transitions` returns no changes when either side
is stale for `inline`. The coverage record is the assurance status from
`policy/analysis_assurance_schema_staleness.schema_staleness_status`, which
names `inline_facts_reliable` through `analysis_assurance_degraded_facts`.

**D4′. Persisting the marker needs no schema bump.** The encoder already writes
`inline_facts_reliable` from the family set. The decoder prefers an explicit
key over the version rule, so a stale baseline loaded and re-saved at the
current version stays stale. The key is registered in the section allowlists
(`storage/legacy_sections._SECTION_FIELDS`, `storage/sparse_section_codec`).
The earlier D4 claim that a v59 bump was required was wrong: it came from an
unverified assumption, and the code now confirms it is not needed.

**D5. Rejected alternative: gate on `extractor_generation`.** Propagating
`EXTRACTOR_GENERATION` onto the snapshot needs a new model field. It also
cannot distinguish the two sides, since the fix did not bump the stamp.

**D6. Tests.** `tests/test_inline_fact_reliability.py`:
(a) a pre-fix side (stale) against a post-fix side with the truth yields zero
`FUNC_BECAME_INLINE` or `FUNC_LOST_INLINE`, for both producers and both sides
being the stale one;
(b) a real inline change with both sides reliable is still reported;
(c) a header snapshot at or above the cutoff is never marked stale;
(d) a stale legacy document stays stale after a re-save at the current version;
(e) the coverage record is `degraded` and names `inline_facts_reliable`.

## Known limits (not fixed here)

- **Re-saved by a pre-change build.** A baseline loaded and saved by a build
  from before this change already carries the current schema version and no
  marker. It reads as reliable. No version rule can recover this, because the
  original snapshot's provenance is gone. The remedy is to re-dump the baseline.
- **`Function.is_inline` is still a plain `bool`.** It is listed in
  `model/fact_registry_schema._CASE_A_UNCONVERTED` as a reviewed deferral.
  Converting it to a `Fact[bool]` needs a persisted codec and a schema
  decision, which is out of scope.
- **Conservative over-declining.** Every header snapshot below 53 is declined
  for inline transitions, including ones written after the fix. That costs
  comparisons, never correctness.

## Open questions for the maintainers

1. Accept the case (a) deferral for `Function.is_inline`, or convert it to a
   `Fact[bool]` in a follow-up with its own schema decision.
2. Whether a re-save warning should be emitted when a stale legacy document is
   written back out, so the loss in the first limit is visible.

## Implementation status

Done in PR #1570: D1, D2, D3′, D4′, D5 (rejected), and the tests in D6.
Not done: the conversion of `Function.is_inline` (deferred above), and the
re-save warning (open question 2).
