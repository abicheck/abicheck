---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A member's peak memory is the clang JSON AST, not the header graph (2026-09-19)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The 'currently ungated' clause is closed: scripts/check_header_graph_perf.py:267 defines MEMORY_METRICS attach_peak_rss_mib/attach_end_rss_mib. The peak from materialising the clang JSON AST is still open, and the empty-list reduction is a recorded negative result.


Full measurement, with the per-point RSS table and the method:
[`measurements/header-graph-attach-memory.md`](../measurements/header-graph-attach-memory.md).
Recorded here because it refutes two plausible fixes that were each tried and
reverted, and because the obvious reading of the numbers is wrong.

On `libonedal_core.so.3` (default `castxml` backend), one `run_dump` reaches
**1968 MiB before the header graph is built at all** — the clang AST parse
alone is ~1.4-1.5 GiB, and the graph build adds 271 MiB on top. The attach
disables the streaming pruner on purpose (`suppress_streaming_prune()` in
`service_header_graph_attach`, because `parse_clang_ast_calls` walks the raw
AST for `DECL_CALLS_DECL` edges, Codex review PR #840), so the whole tree is
materialised; the cached document for these headers is 1.3 GiB of JSON.

**Do not read "dropping the graph frees 1.2 GiB" as "the graph costs
1.2 GiB".** The graph's own objects are 251.7 MiB across 2,830,703 objects by
a full `gc.get_referents` walk. Dropping the *AST* returns only 222 MiB; the
rest comes back only when the graph is dropped, because the graph's
long-lived objects sit in pymalloc arenas the AST parse dirtied and an arena
cannot be returned while anything in it is live. The graph pins that memory,
it does not spend it.

**Attempted and reverted: making the graph's objects cheaper.** The graph
allocates 959,016 lists of which ~958,000 are empty (711,436 `conflicts`
lists hold **6** `FactConflict` objects in total; node `attrs` averages 0.0
entries). Defaulting the empty ones to the singleton empty tuple removed
603,290 objects — 21% of the graph's entire object count — and saved
**40 MiB**. The prediction had been ~240 MiB, from dividing RSS by object
count and applying that average as a marginal cost; empty lists are among the
cheapest objects in the population, and 603,290 x ~64 B is exactly the 40 MiB
observed. Reverted: not worth changing a model field's type and seven test
expectations for 3.7% of the graph's retention. The same average-as-marginal
error is what produced the withdrawn "empty-result prefilter" estimate
recorded earlier in this file — check a marginal cost against the specific
population being removed, never against a population-wide average.

**Also ruled out, each by its own measurement:** the allocator is not holding
it (`malloc_trim(0)` returns 96 MiB of 2138), it is not file-backed or shared
(99.4% of RSS at peak is anonymous private-dirty), there are no garbage cycles
(`gc.collect()` frees exactly zero), the attrs mapping is not duplicated
(clearing `facts` and `resolved` frees zero — `attrs`/`resolved`/
`facts[0].attrs` are one object), and the `BuildSourcePack` holds nothing
besides the graph (35 objects, 0.0 MiB).

**A model stated and refuted.** The six-member release peak was explained
during this work as `admitted workers x per-member peak`; the arithmetic fit
(2 x 5.7 GiB plus the parent is ~7 GiB, against 7077.8 MiB measured). Running
the same comparison at **one** worker peaks at **7080.6 MiB** -- unchanged, so
concurrency is not what produces it. Whether it is accumulation across
members, one large member costing it alone, or the once-per-side acquired
public surface held for the run is not established. A fitting arithmetic is
not a mechanism.

**Still open.** Reducing a member's peak means not materialising the AST as a
Python dict tree — either building the graph from a streaming parse, or
pruning the AST to what the graph needs first. Neither is attempted here, and
the existing `check_header_graph_perf.py` gate measures the attach's *time*
only, so this dimension is currently ungated.
