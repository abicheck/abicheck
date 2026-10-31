---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A snapshot is not byte-reproducible across processes

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-31): NARROWED.** `SourceGraphSummary.finalize()` (live and decoded graphs alike) and `encode_graph_table` now order node rows
sorted by node id and edge rows sorted by `relation_key()` (both unique), so
the interner indices and rows no longer follow in-memory insertion order. The
producer of the insertion-order drift is still unidentified, but it can no
longer reach the stored bytes. `tests/test_snapshot_byte_reproducibility.py`
(bug class `serialization.stored_bytes_depend_on_producer_iteration_order`, to be registered in `tests/regressions` once a PR number exists)
checks every insertion permutation in-process and dumps a compiled corpus in
three processes (`PYTHONHASHSEED` 1/4242/random, different cwd,
`SOURCE_DATE_EPOCH` pinned) expecting identical bytes. **Residual:** the
cross-process check runs on a generated ~300-entity corpus, not a
100k-node library like oneDAL; a different section with the same defect
would only be caught at that scale. `created_at` differs by design unless
`SOURCE_DATE_EPOCH` is set.


**What was observed.** Dumping the *same* library twice, with the *same*
code, in two separate processes produces snapshots whose
`surface_graph.nodes` differ in order: 9 positions out of 108,142 on oneDAL's
`libonedal_parameters`. The node *sets* are identical (0 added, 0 removed),
so no ABI conclusion moves; only the ordering does.

**Why it matters anyway.** It defeats comparing two snapshots by digest, and
any content-addressed caching or reproducibility claim built on one. It is
also a trap for anyone measuring a change by hashing output: a cross-process
A/B will report a difference that the change did not cause. The
haystack-deduplication measurement hit exactly that and had to be re-checked
in one process (where the snapshot *is* byte-identical) before the "findings
unchanged" claim could be made honestly.

**Not investigated further here.** The shape — a small number of adjacent
positions permuted, stable within a process — points at set/dict iteration
over values whose hashes vary with `PYTHONHASHSEED`, but the specific
producer was not identified, and this is not a defect the surrounding
performance work introduced: it reproduces with that work reverted.
