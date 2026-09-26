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
| 4 | Graph facts (`model.graph_facts`) | Every `GraphNode`/`GraphEdge` materialised **three** attrs dictionaries with identical contents -- `facts[0].attrs`, `resolved` and `attrs` -- in the single-producer shape a real graph is almost entirely made of (measured: 10,277 producer facts across 2,771 nodes and 7,506 edges, exactly one each). Two of the three were pure duplication. | `resolve_entity_attrs`'s single-producer fast path returns the fact's own dict and `attrs` aliases it, falling back to the full merge the moment a second fact arrives. Sound only because `attrs`/`resolved` were already *derived* views that `ensure_facts_and_resolve` overwrites on every call. |
| 5 | JUnit OLD-side retention (`junit_report`) | A release JUnit render pinned every member's full `AbiSnapshot` until the release-level fold, to read **four** attributes off it. The module docstring recording that retention named a *fifth* consumer (declaration locations) that did not exist. | `model.symbol_inventory.SymbolInventory`, projected when the member's comparison finishes. `--bundle-facts-out` is now the only full-document consumer. Rendered JUnit is byte-identical. |
| 6 | Compressed writes (`storage.snapshot_stream_write`) | `write_snapshot_text_stream` streamed an *uncompressed* write and did `"".join(chunks)` for a compressed one, so a compressed baseline still peaked at the whole document plus its whole encoded copy. | `storage.incremental_encode` compresses fragment by fragment. gzip output is byte-identical to the previous one-shot path for any chunking down to one byte; zstd is byte-identical whenever the decoded size is known. |

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

## Per-side process isolation (the `low-memory` profile)

The `low-memory` performance profile (`performance.profile: low-memory` in
`.abicheck.yml`, `compare --performance-profile low-memory`, or
`CompareRequest.performance_profile`; see
[`performance:`](../reference/config-file.md#performance)) resolves each
side of a `compare` in its own forked child on Linux and hands back only the
finished snapshot (`abicheck/workflows/side_isolation.py`). It exists because most of a dump's
residency after the parse is not live data: the AST is allocated interleaved
with the snapshot it produces, so freeing the AST leaves arenas pinned by the
few snapshot objects inside each. On a 45-root scan, live data was 366 MiB of
a 2.66 GiB resident set; neither `gc.collect()`, `malloc_trim` nor
`PYTHONMALLOC=malloc` returned the rest (the last was 7% worse on peak and
41% slower). A child that exits returns all of it.

| run | wall | peak (max over processes) | findings |
|---|---|---|---|
| oneDAL 2025.10 -> 2026.1, 3 headers, clang, in-process | 49.5 s | 1.338 GiB | 3548 |
| same, `--performance-profile low-memory` | 58.3 s | 0.888 GiB | 3548, identical |
| 45-root scan, dump each side separately (reporter's measurement) | neutral | -49.8% | identical |

The wall-time cost is copy-on-write: a child's first write into each page it
inherited faults a copy (about twice the minor faults and twice the system
time of the same work in-process), plus ~1 s each way to pickle a snapshot.
It is proportionally smaller as the per-side work grows, which is why the
large scan measured it as neutral. It stays opt-in (the default profile is
`balanced`) until that trade-off is measured on more workloads. The profile
resolves the sides one at a time, so at most one child is alive at a time.

The profile is the user-facing name for a set of execution knobs
(`abicheck/model/performance.py`'s `ExecutionTuning`), not a switch for
this mechanism alone: `low-memory` also compares a release's libraries one
at a time instead of sizing a worker pool to the host. A future memory or
speed mechanism is added as another knob with a value per profile, so a
project config never has to name mechanisms. Every profile must produce
the same findings and exit code; `tests/test_performance_profile.py` holds
that, and checks which execution path actually ran.

## Results

Two rounds are recorded here. The **first** (PR #1332) closed three
retention defects; the **second** (PR #1334) closed the structural costs
that remained, and its table is below.

### Round 2 (PR #1334) -- structural costs

* **Before:** `b4d3780886173741da1bb5c9a571941c09aadedd` (`main`)
* **After:** this branch, with all three changes
* **Fixture:** one real compiled six-member C++ release -- **300 public
  APIs and 20 records/templates/enums per member**, 447-line headers,
  `g++ -shared -g`, with a changed public record and a removed export on
  the NEW side. Both revisions ran `--keep` against the *same* compiled
  tree, so the operands are byte-identical across the comparison.
* **Host:** CPython 3.13.12, Linux, 4 cores, 15 GB; warm cache;
  `--repeat 2`; medians reported, spread stated.

```bash
python scripts/bench_release_memory.py --root FIX --members 6 \
    --variants json,junit,bundle-facts,junit+bundle-facts --repeat 2 \
    --out before.json --label before       # at b4d3780
python scripts/bench_release_memory.py --root FIX --keep \
    --variants json,junit,bundle-facts,junit+bundle-facts --repeat 2 \
    --out after.json --label after         # at this branch
```

| Variant | Parent peak RSS before | after | delta | wall before | after |
|---|---:|---:|---:|---:|---:|
| `json` (control) | 703.5 MiB | 703.2 MiB | **-0.0%** | 116.4 s | 112.1 s |
| `junit` | 918.3 MiB | 705.8 MiB | **-212.6 MiB (-23.1%)** | 117.4 s | 113.5 s |
| `bundle-facts` | 979.8 MiB | 917.1 MiB | **-62.7 MiB (-6.4%)** | 146.4 s | 134.1 s |
| `junit` + `bundle-facts` | 984.8 MiB | 914.3 MiB | **-70.5 MiB (-7.2%)** | 143.6 s | 135.3 s |

Run-to-run spread (max-min over the two runs) was 0.1-5.1 MiB except
`bundle-facts` after, at 13.2 MiB -- so the `json` row's -0.3 MiB is
noise and the other three are well outside it.

**Read the `json` row first.** It is the control: that variant never
retained a snapshot, so nothing here should move it, and nothing did.
That is what makes the other rows attributable rather than a story about
an unrelated allocator change.

* **`junit` -23.1%** is the OLD-side retention removal. JUnit read four
  attributes off each member's `AbiSnapshot`; it now takes the compact
  `model.symbol_inventory.SymbolInventory`, and the snapshot is released
  when the member's comparison finishes instead of at the release-level
  fold. The rendered JUnit XML is byte-identical.
* **`bundle-facts` -6.4%** is the graph-facts compaction alone, since
  `--bundle-facts-out` still retains every member's OLD document by
  design. Every `GraphNode`/`GraphEdge` held three attrs dictionaries
  with equal contents in the single-producer shape a real graph is
  almost entirely made of; it now holds one.
* Wall time fell 4-8% across every variant. That was not the goal and is
  reported as observed rather than explained -- less allocation is the
  obvious candidate, but nothing here measured allocation directly, so
  no causal claim is made.

Every run exits 4 with one finding, before and after: the injected break
is still detected, so none of this was bought with evidence.

**Component-level measurement**, separate from the end-to-end table
(deep heap census perturbs both time and RSS, so it never shares a run
with a timing claim): on a release-sized graph of 2,771 nodes and 7,506
edges with exactly one producer fact each, the single-producer fast path
took the identity-deduplicated reachable heap from 15.68 MiB to 13.25
MiB (**-2.43 MiB, -15.5%**) and 123,877 objects to 110,026
(**-13,851**), with distinct attrs-dictionary identities falling from
24,132 to 10,277 -- exactly one per entity.

### What this round does *not* establish

* **One fixture, one size, two repeats.** The brief asked for at least
  two declaration counts to test scaling; only 300 APIs/member was
  measured, so **nothing here demonstrates how these savings scale with
  declaration count**. The graph-facts saving is per-entity by
  construction and the JUnit saving is per-declaration, so both *should*
  scale -- but that is reasoning, not measurement.
* **No oneDAL validation.** `/mnt/cached_oses/napetrov/tmp-abi/l2b6/`
  does not exist in this workspace, so the 17.82 GiB six-library figure
  could not be reproduced and **none of the reductions above is a
  measured oneDAL reduction**.
* **Concurrency was not re-tuned**, and the four-worker-versus-one
  question was not re-measured this round.
* **Whole-graph sharing across members was investigated and not
  shipped** -- see [Known gaps](known-gaps.md) for why one fixture's
  content-hash coincidence is not a sharing licence.

### Round 1 (PR #1332) -- retention defects

Measured on this fixture, at two recorded SHAs, with one harness over one
compiled tree:

* **Before:** `c7c0be257c58c3b986654d5208a89f4e99a15a0b` (`main`)
* **After:** `c67cb8f` (this branch's head after the ownership refactor).
  The intermediate revision `5739f5f` plus the encoder's delegation bound
  was measured separately and agreed within run-to-run variance, which is
  the check that the refactor moved code and not behaviour.
* **Fixture:** a real compiled six-member C++ release, regenerated
  deterministically from the harness's own generator parameters (so the
  two revisions' operands are identical content, even across a rebuild) — 300 APIs, 20
  records/templates/enums per member, `g++ -shared -g -O0 -std=c++17`, with
  a changed public record (inserted field) and a removed export on the NEW
  side
* **Host:** CPython 3.13.12, CastXML 0.7.0 (the pinned conda build, no
  toolchain check bypassed), warm cache, `--repeat 2`, medians reported

| Variant | Parent peak RSS | | Tree peak PSS | | Wall | |
|---|---:|---:|---:|---:|---:|---:|
| | before | after | before | after | before | after |
| `json` (default) | 804.7 MiB | 812.3 MiB (**+0.9%**) | 871.6 | 880.9 | 91.1 s | 94.3 s |
| `+ junit` | 1280.2 MiB | 1037.2 MiB (**−19.0%**) | 1322.5 | 1101.7 | 93.0 s | 95.8 s |
| `+ bundle-facts` | 1962.4 MiB | 1016.6 MiB (**−48.2%**) | 1958.0 | 1048.4 | 108.2 s | 111.0 s |
| `+ junit + bundle-facts` | 1974.5 MiB | 989.2 MiB (**−49.9%**) | 1968.6 | 984.9 | 109.6 s | 109.6 s |

The `json` row's `+0.9%` is run-to-run variance, not a regression: that
variant retains no snapshot either way, and its two runs at each revision
span more than that difference.

Every run exits 4 (the injected break is detected on both sides), and the
outputs were compared as documents, not by headline verdict, at `5739f5f`
against the base: the JSON report and the JUnit XML are **byte-identical**
before and after, and the
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
