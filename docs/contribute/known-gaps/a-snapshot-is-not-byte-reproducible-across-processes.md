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

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** unverified: no cross-process determinism check was run. The entry says the producer of the surface_graph.nodes ordering drift was never identified.


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
