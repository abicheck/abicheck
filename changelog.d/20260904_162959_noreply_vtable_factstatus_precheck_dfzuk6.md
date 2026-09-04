<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Documentation

- **`TYPE_VTABLE_CHANGED` evidence-gating cluster: `FactStatus` pre-check
  investigated across three rounds, ultimately declined.** ADR-063 Track
  4's 5B closure found a real, reachable fabrication risk (a
  `TYPE_VTABLE_CHANGED` finding wrongly reported against a PDB-derived
  snapshot, whose real extractor never captures vtable data at all) and
  landed a `FactStatus`-based decline for it — but that decline also
  regressed a real, previously-passing detection scenario, because a
  hand-constructed/typed-API `RecordType` omitting `vtable=` (meaning "no
  virtuals") resolves to the identical `NOT_COLLECTED` status PDB's own
  non-evidence does, and `FactStatus` alone cannot tell the two apart. The
  fix was reverted before merge; `diff_types_vtable.py`'s
  `TYPE_VTABLE_CHANGED` cluster's evidence heuristic is unchanged. No
  behavior change ships from this PR. The PDB fabrication remains a real,
  open, documented gap — closing it needs a snapshot/producer-level
  signal (analogous to `AbiSnapshot.clang_vtable_facts_reliable`), not a
  per-record `FactStatus` branch. Full investigation recorded in both
  touched modules' docstrings, the plan, and the status ledger, with a
  regression-locking test
  (`tests/test_vtable_evidence_guard.py::TestOmittedVtableStillDetectsARealAddition`)
  pinning the scenario the reverted fix broke.
