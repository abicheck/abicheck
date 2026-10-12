# ADR-079: Inline-Fact Reliability for `FUNC_BECAME_INLINE` / `FUNC_LOST_INLINE`

**Date:** 2026-10-12
**Status:** Proposed — design only. This ADR changes no behaviour, default, schema or public interface. The schema bump in D4 is blocked on acceptance.
**Governs:** the gap in [known-gaps/a-pre-fix-clang-backend-baseline-compares-against-a-post-fix.md](../known-gaps/a-pre-fix-clang-backend-baseline-compares-against-a-post-fix.md).
**Relates to:** [062](062-project-snapshot-storage-v2.md) (storage version axes), [063](063-one-semantic-pipeline.md) (retirement of the `*_facts_reliable` booleans in favour of `stale_fact_families`).

## Context

`FUNC_BECAME_INLINE` and `FUNC_LOST_INLINE` are produced by
`compare/function_lifecycle.inline_changes`, called from
`diff_symbols._check_inline_transitions`. That function reads the
`is_inline` **`Fact[bool]`** on the SemanticIR `CanonicalEntity`
(`model/semantic_ir.py`). `_value()` returns `None` when the fact is not
present, and `inline_changes` then emits nothing (`function_lifecycle.py`,
the `o is None or n is None` guard). The decline path already exists.

What is missing is a signal that a *present* fact is stale. The implicit-inline
fix (commit `5ba6a5c84`, `extract/headers/clang/inline_semantics.py`) changed
what the clang backend records for a `constexpr`, in-class or `= default`
declaration, from `False` to the correct `True`. The same commit also added
castxml recovery of out-of-line inline member definitions from header text.
A snapshot written before the fix carries a wrong but *collected*
`is_inline` fact for both producers, so a pre-fix baseline compared with a
post-fix candidate reports a spurious wave of `FUNC_BECAME_INLINE` (RISK).

### Verified 2026-10-12

- `SCHEMA_VERSION` is **52** at the parent of `5ba6a5c84` and at `5ba6a5c84`
  itself (`storage/snapshot_schema_versions.py`). The current value is 58.
  A schema threshold therefore cannot tell a pre-fix v52 snapshot from a
  post-fix v52 snapshot. Snapshots at v53 and later were all written after
  the fix.
- Stale families are persisted on save as `<family>_facts_reliable` keys
  (`storage/snapshot_encode.py`). A snapshot loaded from an old version and
  saved again is re-stamped at the current version, so a marker that is
  derived from the version number alone is lost on re-save.
- `EXTRACTOR_GENERATION` (`storage/versioning.py`) is still `1`. The fix did
  not bump it, so the producer generation stamps cannot separate the two sides.
  `StorageVersions` also lives on the package manifest, not on the in-memory
  `AbiSnapshot`.

## Decision

**D1. A new fact family, `inline`.** Add `inline` to `FACT_FAMILIES` in
`model/snapshot_reliability.py`, covering `CanonicalEntity.is_inline` for
clang and castxml producers. Clang and castxml are both affected, so
the family is not limited to one producer. Hybrid snapshots follow whichever
producer recorded the fact.

**D2. Load-time staleness by version threshold.** Add
`_MIN_SCHEMA_VERSION_FOR_INLINE_FACTS = 53` in `storage/fact_schema_versions.py`.
A loaded snapshot below 53 marks `inline` stale in
`storage/snapshot_reliability_flags.py`. Version 52 is the fix's own version,
so v52 snapshots written after the fix are also marked stale. That is the
conservative direction: it only declines a comparison and never invents a
finding. This uses the current version axis and does not bump `SCHEMA_VERSION`.

**D3. Demote the per-declaration fact.** For a stale load, demote each
`is_inline` fact to `NOT_COLLECTED` in `storage/fact_backfill.py`, following
the `param_kind` precedent. The existing detector then declines through the
path already described above. The coverage record names the family, the
producer, the snapshot's version and the number of declined pairs, through
the degraded-facts path in `policy/analysis_assurance_degraded_facts.py`.
That map's every-flag-has-a-key invariant is enforced by `KeyError`, so a key
for `inline` must be added in the same change.

**D4. Persist the marker. This is the schema change and is blocked on
acceptance.** Persist `inline_facts_reliable` in `storage/snapshot_encode.py`
so that a re-saved old baseline keeps its marker. That requires
`SCHEMA_VERSION` 59, a migration note and the existing hard-rejection
handling for readers older than 59. D2 and D3 without D4 still work for a
freshly loaded snapshot. They fail to protect a baseline that is loaded and
re-saved, which is the common case for a stored baseline.

**D5. Rejected alternative: gate on `extractor_generation`.** Propagating
`EXTRACTOR_GENERATION` onto the snapshot needs a new model field. It also
cannot distinguish the two sides, since the fix did not bump the stamp. The
generation stamps belong to the concurrent change and are not touched here.

**D6. Tests, written with the implementation.**
(a) A pre-fix snapshot (`is_inline=False`, stale) against a post-fix snapshot
(`is_inline=True`, the truth) yields zero `FUNC_BECAME_INLINE`, and the coverage
record names the declined pair. Without the stale load the same pair reports
the spurious change.
(b) A genuine inline change with both sides reliable is still reported.
(c) A newly produced current-version snapshot is never marked stale by this family.
(d) A stale old baseline remains stale after it is re-saved at schema v59 (D4).

## Consequences

- A v52 post-fix baseline is declined until it is re-dumped. That is the
  intended conservative cost and matches the one-step remedy in the gap write-up.
- D4 changes every newly written snapshot's schema version. Release notes must
  say so, and it needs its own migration and the ADR-062 handling for older readers.
- Implementation is blocked on accepting D4.

## Open questions for the maintainers

1. Accept the D4 schema bump to `SCHEMA_VERSION` 59 for the persisted marker,
   or accept D2/D3 alone with the known re-save loss.
2. Whether the castxml out-of-line recovery needs a separate threshold,
   since its change may differ from the clang change.

## Implementation slices (after acceptance)

1. Add the `inline` family, its threshold, the demotion and the
   degraded-facts key, with tests (a)–(c).
2. Add the persisted marker and the v59 bump (D4), with its migration note.
3. Move the gap entry to `known-gaps-closed.md` only after 1 and 2 merge.
