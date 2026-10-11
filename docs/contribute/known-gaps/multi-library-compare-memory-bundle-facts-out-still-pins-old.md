---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Multi-library compare memory: --bundle-facts-out still pins OLD snapshots per member

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** Closed: JUnit uses SymbolInventory, compressed baseline streaming, and the attrs aliasing. Still open: OLD-side retention for --bundle-facts-out (no spooling handle). Graph sharing and Fact pooling were rejected as negative results, and the surface_graph ordering question is unverified.


Three retention defects were measured and closed — per-side/per-consumer
member retention, the unbounded content-keyed half of the AST acquisition
table, and the baseline write's three simultaneous full-size copies (see
[`docs/contribute/memory.md`](../memory.md) for the numbers and the harness).
This entry records, deliberately, what is **not** closed, so the next
attempt starts from the measurement rather than from the same three places.

**The OLD side is still retained in full, per member — but only for
`--bundle-facts-out` now.** *Half of this is closed:* JUnit was the second
consumer and turned out to read four attributes off the snapshot, so it now
takes the compact `model.symbol_inventory.SymbolInventory`
and the snapshot is released when the member's comparison finishes.
`--bundle-facts-out` genuinely needs the whole document and still pins it.
Bounding *that* means *spooling completed members* — writing each member's
OLD snapshot to the storage codec as its comparison finishes and reading it
back when the folds run — rather than deciding retention. What makes it a
real change and not a tweak: `--bundle-facts-out` additionally resolves
*stranded* libraries that never produced a pair. The archive writer
(`storage/bundle_facts_archive.py`) already spools encoded blobs and is the
mechanism to reuse; what is missing is a bounded, lazy per-member handle
that consumer can take instead of a live object.

**A compressed baseline write no longer joins.** *Closed.*
`storage/incremental_encode.py` compresses the same fragment stream chunk
by chunk, and the determinism story the previous note asked for was
supplied rather than assumed: gzip's frame is assembled here with `mtime=0`
and `OS=0xFF` pinned explicitly and a raw-deflate payload from one
`zlib.compressobj`, which is **byte-identical** to the previous
`gzip.compress` output for every chunking down to one byte; zstd is
byte-identical whenever the decoded size is known up front. One residual:
the bundle-facts producer cannot state that size (it is streaming), so its
zstd frames omit the declared content size. The frame is legal, round-trips,
and `validate_zstd_frame_completeness` already handles `CONTENTSIZE_UNKNOWN`
— but it loses the declared-size cross-check that catches a frame truncated
mid-header, and its bytes differ from the one-shot encoder's for the same
content. A caller that *can* cheaply state the total should pass
`decoded_size=`.

**Three attrs dictionaries per graph entity: closed, and worth reading for
the shape of the next one.** Every `GraphNode`/`GraphEdge` held
`facts[0].attrs`, `resolved` and `attrs` with equal contents in the
single-producer shape a real graph is almost entirely made of (a measured
six-library release: 10,277 producer facts across 2,771 nodes and 7,506
edges, exactly one fact each). `model.graph_facts.resolve_entity_attrs`
returns the fact's own dict for that case and `ensure_facts_and_resolve`
aliases `attrs` to it — measured at −2.43 MiB and −13,851 objects on a
graph of that shape, and multiplied by the member count while
`--bundle-facts-out` retention holds. The aliasing is sound only because
`attrs`/`resolved` were *already* derived views that
`ensure_facts_and_resolve` overwrites on every call, which is why a direct
`entity.attrs[k] = v` was already documented as silently dropped and no
production call site performs one. **Do not extend the aliasing to the
multi-fact case**: that dict is a genuine merge result and sharing it would
make one producer's backfill rewrite another's evidence.

**Whole-graph sharing across release members was investigated and not
shipped.** The audit's observation — that all 10,277 serialized node/edge
payloads matched by content hash across six OLD-side member graphs — is a
statement about *that fixture's* shared headers, not a licence to share the
objects. `build_header_only_graph(snap, ast_root, ...)` takes the member's
own snapshot, so a correct sharing key would have to cover every
declaration that snapshot contributes, not just the header/compiler/macro/
target context the brief lists; and the graph is *mutated* after
construction (`augment_graph_with_includes`, `degraded_passes`,
`extractor_passes`, `finalize()`), so a shared instance would need a
copy-on-write or freeze boundary that does not exist today. The raw AST
*is* already shared (`dumper_cache`'s in-process memo plus the disk cache),
so what would be saved is construction time and per-member graph storage,
not re-parsing. Reducing the per-entity storage was the sound half and is
what shipped; sharing the graph itself needs the same
inventory-of-every-mutation work the shared-header-declaration item below
already calls for, and must not be justified by one fixture's content-hash
coincidence.

**Scalar `Fact` pooling was measured and deliberately not shipped.**
Pooling immutable scalar `Fact` values on `Function`/`Param` after
construction reduced a whole six-member scan's parent peak by **4.4%**
(127.1 MiB against 132.9 MiB on a small fixture) — a poor return for a
change that must preserve the `UNKNOWN`/`ABSENT`/`PARTIAL`/`FAILED`/
`PRESENT` distinctions exactly and must never share a mutable list/dict
payload. In a *narrower* controlled experiment (twelve 5,000-function
models, 116.37 MiB of Python allocations) the same pooling reached 57.77
MiB, which is the reason not to drop the idea: the gap between the two says
the win is real in the model objects and is being swamped by everything
else resident during a real scan. The right next step is therefore **not**
to pool after construction but to avoid the duplicate construction — and
per `AGENTS.md`'s own guidance, the larger follow-on (shared immutable
header declarations plus per-member bindings; two shared surfaces plus
member overlays measured 22.89 MiB in the same experiment) requires an
inventory of every mutation of a `Function` after construction and an
explicit ownership boundary first. Sharing today's mutable `Function`
objects by reference is not that work and must not be presented as it.

**Concurrency was not re-tuned.** The per-worker memory clamp
(`workflows/release_jobs.py`) sizes off a budget described as "each holding
up to two full snapshots resident". That description is still accurate:
retention now keeps at most one full snapshot per member *after* a
comparison, but both sides are live *during* one, which is what the clamp
budgets. Changing the constant without measuring a real fan-out's
co-resident set would repeat the gap that constant already carries (see
`perf.release_admission_ignores_co_resident_set`'s own known gaps). A
process pool is separately ruled out on evidence: four processes on a fixed
Python-heavy workload took process-tree PSS from ~57 MiB to ~226 MiB.

**The matcher work is a CPU change, not a memory one, and is out of scope
here.** Token-indexed and trie prototypes preserved nesting, ordering and
range semantics and cut synthetic scanning time substantially, but a Python
trie *retained* 6.91 MiB against a regex's 1.39 MiB. If it is taken up, it
needs adversarial common-prefix and randomized differential tests against
the current matcher as oracle, and it must not be justified as a memory
fix. `id()`-keyed match caching was measured and rejected: warm production
lookups are ~0.60 µs, stable string keys already benefit from cached
hashes, and interning inside each lookup was worse.

**No oneDAL operand was available.** `/mnt/cached_oses/napetrov/tmp-abi/l2b6/`
does not exist in this workspace, so the 35:47.83 / 17.82 GiB six-library
figures could not be reproduced, the exact command/frontend/cache state
behind them could not be established, and **none of the reductions recorded
here is a measured oneDAL reduction**. The fixture used is a real compiled
six-member C++ release, which is the right *shape* and a far smaller
*scale*. Whether the whole job fits a nominal-16-GB runner is therefore
untested; the next experiment is to run
`scripts/bench_release_memory.py --keep` against the real oneDAL tree with
`--trace`, and read `release.ast_scope` and `release.member.retained`
against the sampled cgroup peak to see which of the two remaining owners
(OLD-side member retention, or one member's own working set) dominates
there.

**Unverified: `surface_graph` member ordering across runs.** The
before/after baseline documents in the measurement above were equal on
every field except the ordering within `surface_graph.nodes`/`edges` (equal
multisets, identical `graph_id`). Nothing on that branch touches
surface-graph construction and the streaming encoder is byte-identical to
the eager one over a fixed document, so pre-existing run-to-run
nondeterminism is the likely cause — but the check that settles it (run the
*unchanged* base revision twice, diff the two baselines) was started and
lost when the workspace's `/tmp` was cleared, and is therefore **not**
claimed. Anyone touching baseline determinism should run it first:
`python scripts/bench_release_memory.py --members 3 --apis 120 --records 10
--variants bundle-facts --repeat 2` and `cmp` the two `baseline.json`
files. If they differ, the ordering is genuinely unstable and that is its
own (pre-existing) gap, not a property of the streaming write.
