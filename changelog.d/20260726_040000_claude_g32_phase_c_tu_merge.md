<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **Real compatible merge across translation units (ADR-050 D4, G32 Phase
  C)**: a manifest-driven `dump` (ADR-050 D3, G32 Phase B) now reconciles a
  declaration repeated across two translation units instead of hard-failing
  on any repeat. A new `abicheck/tu_merge.py` classifies each cross-TU
  redeclaration as a trivial merge — a forward declaration paired with its
  full definition, a plain redeclaration, or a difference confined to an
  added default argument/initializer — or a genuine conflict, raised as a
  new `TuMergeError` (`code="INCONSISTENT_DECLARATION"`) when two TUs
  disagree on anything else (return type, layout, member list, a typedef's
  or constant's value, ...). `TuMergeError` is an extraction-time failure
  (a `SnapshotError` subclass), not a `ChangeKind` — it fires before a
  manifest-driven dump ever produces a snapshot to diff. `TuFragment`/
  `MergedTuFragments`/`entity_key` move to a new leaf module,
  `abicheck/tu_fragment.py`, re-exported from `dumper_manifest.py`
  unchanged for backward compatibility.
