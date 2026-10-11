---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Releasing the clang AST before the graph build does not reduce the peak (2026-09-19)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): NEGATIVE_RESULT.** Records that releasing the AST before the graph build did not move the 2215 MiB peak. Key-whitelist pruning during the parse is measured but not attempted.


The follow-up to the entry above, recorded because it **did not pay** in the
way its own premise predicted, and because measuring it reattributes the peak
one level further down.

`_attach_header_graph` now projects the AST into the four compact values
`build_header_only_graph` actually reads
(`buildsource/header_graph_ast_projection.py`) and releases the tree before
the graph is allocated, so the two are never resident together. That part
works exactly as intended. On the real reference library — oneDAL 2024.7
`libonedal_core.so.2`, conda-forge `dal`/`dal-devel`, default `castxml`
backend, cold AST cache, one `daal.h`, three fresh processes per side — the
graph build's own residency cost falls from **+147/+148/+143 MiB to
+25/+21/+25 MiB**, because the graph now lands in arenas the AST parse has
already freed instead of taking fresh ones. The projection itself is
23.6 MiB against a 1044 MiB tree (2.3%).

**And the member's peak does not move at all: 2215.4 / 2218.0 / 2215.4 MiB
before, 2215.2 / 2215.3 / 2215.3 after — 0.05%.** The graph/AST overlap was
simply never where the peak was.

**The retained figure also comes out slightly worse, and stayed that way.**
Only three runs per side made it visible: every *after* run sat above every
*before* run. One cause was a real bug in the change — the projection is a
local, so holding it to function exit kept its indexes alive past the graph
build, their only consumer; freeing it at the build's end moves the graph
build from *adding* ~24 MiB to *subtracting* ~27 (1302.5 / 1306.9 against a
1332.5 AST-parse level). But the steady-state figure survives that fix at
**~8-12 MiB above baseline** (1289.8 / 1295.2 vs 1275.9 / 1286.1 / 1287.6),
most likely the inverse of the pinning effect above: freeing the AST early
returns its arenas, and the graph then faults in fresh pages rather than
reusing ones the parse had dirtied.

**So on this library the change improves neither number a release fan-out's
per-member budget is sized from** — the peak is unchanged and steady-state
retention is marginally worse. It is kept for the mid-attach residency
(~175 MiB lower at the graph-build point) and because the projection is the
executable statement of what a pruned tree would have to preserve, not
because it reduced oneDAL's memory. It did not. Two method points worth
keeping: a single run per side would have read the regression as noise, and
"free it earlier" is not automatically "hold less" once the allocator is in
the picture.

**Where the peak actually is.** The attach's high-water mark occurs *inside*
`json.load`, before the graph exists: `dump.header_graph.clang_ast` ends at
1332 MiB, while `VmHWM` for the same window is 2215 MiB. The difference is
the JSON document itself, held as one `bytes`/`str` while the tree is built
from it. So the peak is `document + tree`, not `tree + graph`. Checked
rather than assumed that this is reducible by decoding more carefully: on a
247 MB AST, `json.load(fh)` and an explicit read/decode/`del raw`/`loads`
sequence both peak at **640.9 vs 641.0 MiB** — CPython already drops the
source buffer, and there is no stdlib spelling that avoids holding one full
copy of the document during the parse.

**What that leaves, with a number.** The remaining lever is the *tree*, not
the document: prune it during the parse to the keys the four readers touch
(`kind`, `inner`, `name`, `id`, `qualType`, `file`, `type`, `mangledName`,
`range`, `loc`, `referencedDecl`, `ownedTagDecl`, `bases`, … — a bounded,
auditable set). Measured ceiling on the same 247 MB AST: a recursive
key-whitelist copy is **224.9 MiB against 359.8 MiB, i.e. 62%**, so ~38% of
the tree is fields nothing reads. Applied to oneDAL's 1044 MiB tree that is
roughly 400 MiB off a 2215 MiB peak (~18%). Not attempted here: it must run
as an `object_pairs_hook`, which the existing streaming pruner measured at a
13-30% wall-time cost on *every* object in the document, and the gate for
`attach_ms` allows 50%; and a whitelist that is wrong in one key silently
drops edges rather than raising, which is exactly the failure mode
`tests/test_header_graph_ast_projection.py`'s differential invariant would
have to be extended to cover before it could be trusted.

**Shipped anyway, on its own merits, not as a memory fix:** the reordering is
strictly non-worse, evidence-identical (verified through the real `compare`
CLI on a real pair: every finding, section, verdict and exit code byte-equal
apart from one `extractor.duration_seconds` field), and it is the
precondition for any pruning work, since the projection is the thing that
defines what a pruned tree would have to preserve. The `attach_ms` gate is
unchanged by it (13.1s → 13.6s on the STL fixture, within run-to-run
spread). Do not cite it as having reduced oneDAL's memory; it did not.

**Now gated.** `check_header_graph_perf.py` measures `attach_peak_rss_mib`
and `attach_end_rss_mib` alongside the three time metrics, each in a fresh
subprocess over an STL-bearing fixture (the pre-existing fixture retains
0.1-2.8 MiB, far too little to gate on). The `performance.yml` PR-vs-base job
gates both. This closes the "currently ungated" clause of the entry above —
a change that holds one more copy of the AST costs no measurable time and
would have passed every gate that existed before.

One design note worth not relearning, because CI caught it and a unit test
now does: both memory metrics are **absolute** RSS, never a delta against
the pre-attach reading. `perf_measurement.is_gateable` is written for
wall-clock durations and rejects anything `<= 0`, while a retained-memory
delta is legitimately negative whenever the attach releases more than it
allocates — which is exactly what the clang backend does, since its AST
comes from the in-process memo the primary pass wrote. The first version
failed CI with `measured attach_retained_mib=-28.4 is not a gateable
value`, i.e. on a *better* result than the baseline's. A metric whose
domain does not match its gate's predicate cannot be gated at all.
