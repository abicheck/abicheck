# Comparison Memory

Companion to [Comparison Performance](performance.md), which covers *time*.
This page covers what a header-depth **multi-library** `compare` keeps
resident, how to measure it in a way that attributes a peak to an owner, and
what the current owners are.

## Four numbers, not one

The single most common way to reach a wrong conclusion about this tool's
memory is to treat one figure as "the" memory. There are four, they move
independently, and a fix that moves one may not move another:

| Figure | What it is | What moves it |
|---|---|---|
| **Parent RSS** | this interpreter's resident set — what `/usr/bin/time -v` reports | Python-side retention: snapshots, ASTs, buffers |
| **Process-tree RSS** | parent plus every live child (`castxml`, `clang`, a compiler driver) | how many extractions run at once, and how big each one's compiler is |
| **Process-tree PSS** | the same tree with each shared page divided by its mappers | the *real* incremental cost of concurrency; summed RSS double-counts a fork's shared pages |
| **cgroup `memory.current`** | what the kernel bills the job, page cache included | everything above, plus I/O — and it is the figure an OOM kill and a "nominal 16 GB runner" are decided on |

A fifth, **`tracemalloc` totals**, is not resident memory at all: it counts
Python *allocations*, excludes the allocator's unreturned arenas, and
excludes every byte a child process holds. It is genuinely useful for
attributing Python-side retention, and it perturbs both wall time and RSS —
so it belongs in a separate profiling run, never alongside a timing claim.

`abicheck.workflows.memory_trace` records all of these separately, and records a
missing probe as `null` rather than `0`: a host without `smaps_rollup` or a
cgroup must look *unmeasured*, not idle.

## Measuring a run

```bash
# One trace, with phase boundaries and retention counts
ABICHECK_MEMORY_TRACE=trace.jsonl abicheck compare old/lib new/lib \
    -H old=old/include -H new=new/include -o junit=report.xml

# A separate Python-allocation profiling run
ABICHECK_MEMORY_TRACE=alloc.jsonl ABICHECK_MEMORY_TRACE_TRACEMALLOC=1 abicheck compare ...
```

Both are off by default. With `ABICHECK_MEMORY_TRACE` unset the whole module
is one boolean test per call site: nothing is probed and nothing is written.

The trace records, per release member, a `release.member:enter`/`:exit` pair
of fully-probed samples, a `release.member.retained` record saying which
side's full snapshot that member left resident and why, and a
`release.ast_scope` record with both halves of the AST acquisition table.
That last pairing is the point: a peak is only *attributable* if the trace
says what was being held when it was taken.

## The benchmark harness

`scripts/bench_release_memory.py` builds a **real compiled** two-sided C++
release — one `.so` per member from `g++ -shared -g`, with a changed public
record and a removed export on the NEW side so a real break is detected —
and runs the CLI once per output variant, sampling the whole process tree
throughout:

```bash
python scripts/bench_release_memory.py --members 6 --apis 1800 --records 120 \
    --variants json,junit,bundle-facts,junit+bundle-facts --repeat 2 --out bench.json
```

Three properties make a before/after pair honest:

* **One harness, both revisions.** Its `abicheck.workflows.memory_trace` import is
  optional, so the same script runs at a pre-instrumentation SHA. Two
  scripts could sample differently and the difference would be
  indistinguishable from the change.
* **One fixture.** `--keep` reuses the compiled tree, so both sides compare
  identical operands.
* **Sampled, not final.** A release's peak is *inside* the fan-out, and a
  compiler child that has already exited contributes nothing to a
  post-hoc reading.

Use `--cold` to clear the cache between runs; cold and warm are different
measurements and must not be averaged together.

## What was fixed

All three were found by measurement, all three are invisible in the output
(the reports, the JUnit XML and the baseline document are unchanged, byte
for byte), and all three are registered as bug classes in
`tests/regressions/manifest_performance.py`.

| # | Owner | Was | Fix |
|---|---|---|---|
| 1 | Release member retention (`cli_compare_release_pairwise`) | `need_full_snapshots` was one switch for two consumers and two sides. JUnit and `--bundle-facts-out` each read only the OLD side, so every member's full NEW `AbiSnapshot` was retained for the whole release and never opened. | `workflows.release_snapshot_retention.SnapshotRetention` resolves it per side and per consumer; NEW keeps the compact `BundleSignatureEvidence` the bundle analysis already consumes duck-typed. |
| 2 | Raw AST acquisition (`dumper_cache.AstAcquisitionScope`) | Only the `id()`-keyed groups were bounded and counted. The content-keyed entries — whose `Future` **result is the parsed AST root** — were neither, so a measured six-member release retained 24 raw roots while correctly reporting eight retained groups. | `MAX_RETAINED_RAW_ENTRIES` bounds the ungrouped half by LRU, coordinated with in-flight producers, waiters, failures and group ownership. |
| 3 | Baseline serialization (`storage.bundle_facts_codec`) | `json.dumps(bundle_facts_to_dict(facts), indent=2)` held every member's snapshot dict, the whole string and its UTF-8 encoding at once, on top of the member graph. | `storage.json_stream` streams the document one member at a time; `snapshot_io.write_snapshot_text_stream` writes fragments through the existing atomic writer, generalised to take chunks rather than duplicated. |

Two design notes worth not relearning:

* **The AST bound is an LRU, not a flush.** Clearing acquisition caches
  between members was measured: legacy model constructions rose from 18 to
  28 (i.e. real re-parsing) with *no* peak reduction, because the peak is
  one member's own working set, not the accumulation. An LRU keeps the
  common shared-header case at exactly one resident root while stopping a
  fan-out over distinct header sets from accumulating.
* **Evicting a content-keyed entry cannot reopen the `id()`-reuse hole**
  that `AstAcquisitionScope.retain` exists to prevent, because a group
  holds its own strong reference to the object it is keyed on. That is
  asserted, not argued:
  `test_a_group_holds_its_object_even_when_the_content_entry_goes`.

## Results

Measured on this fixture, at two recorded SHAs, with one harness over one
compiled tree:

* **Before:** `c7c0be257c58c3b986654d5208a89f4e99a15a0b` (`main`)
* **After:** this work's branch (`5739f5f` plus the encoder's delegation
  bound)
* **Fixture:** a real compiled six-member C++ release — 300 APIs, 20
  records/templates/enums per member, `g++ -shared -g -O0 -std=c++17`, with
  a changed public record (inserted field) and a removed export on the NEW
  side
* **Host:** CPython 3.13.12, CastXML 0.7.0 (the pinned conda build, no
  toolchain check bypassed), warm cache, `--repeat 2`, medians reported

| Variant | Parent peak RSS | | Tree peak PSS | | Wall | |
|---|---:|---:|---:|---:|---:|---:|
| | before | after | before | after | before | after |
| `json` (default) | 804.7 MiB | 802.1 MiB (**−0.3%**) | 871.6 | 878.4 | 91.1 s | 92.0 s |
| `+ junit` | 1280.2 MiB | 1022.7 MiB (**−20.1%**) | 1322.5 | 1352.7 | 93.0 s | 93.1 s |
| `+ bundle-facts` | 1962.4 MiB | 1037.7 MiB (**−47.1%**) | 1958.0 | 1096.7 | 108.2 s | 111.0 s |
| `+ junit + bundle-facts` | 1974.5 MiB | 994.8 MiB (**−49.6%**) | 1968.6 | 988.9 | 109.6 s | 110.8 s |

Every run exits 4 (the injected break is detected on both sides), and the
outputs were compared as documents, not by headline verdict: the JSON
report and the JUnit XML are **byte-identical** before and after, and the
bundle-facts baseline is equal as a parsed document on every field except
the *ordering* within `surface_graph.nodes`/`edges`, whose multisets are
equal element for element and whose `graph_id` digest is identical.

That ordering difference is **not attributed** here. It is not produced by
anything on this branch — nothing here touches surface-graph construction,
and the streaming encoder is byte-identical to the eager one over the same
document (`tests/test_json_stream_encoder.py`) — so the likely cause is
pre-existing run-to-run nondeterminism (per-process hash randomisation, or
worker completion order feeding a set). The check that would settle it is
running the *unchanged* base revision twice and diffing its two baselines;
that run was started and lost before it finished, so the claim is left
open rather than asserted. Treat `surface_graph` member ordering as
unverified-stable until someone runs it.

Wall-clock is within noise except `bundle-facts`, which pays ~3% for
streaming the baseline rather than encoding it in one shot.

Read the table by column, not by row: the `json` variant barely moves
because it never retained a snapshot to begin with — its peak is one
member's own extraction working set, which none of these three fixes
touches. That is the honest headline: **what was removed is the retention
that scales with member count and requested outputs, not the cost of
analysing a member.**

### What this does *not* establish

The supplied oneDAL evidence (35:47.83, 17.82 GiB peak on six libraries)
could not be reproduced: the operands named in the brief
(`/mnt/cached_oses/napetrov/tmp-abi/l2b6/`) are not present in this
workspace, so the command, output options, frontend, cache state and worker
count behind those figures were never established. **None of the reductions
above is a measured oneDAL reduction**, and no claim is made here about
fitting a nominal-16-GB runner. The remaining owners, and the exact next
experiment, are recorded in
[Known gaps](known-gaps.md#multi-library-compare-memory-the-owners-left-after-the-three-retention-fixes-pr-1332).
