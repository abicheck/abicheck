# Where a member's peak memory actually goes (oneDAL, one library)

Measured 2026-09-19 on `libonedal_core.so.3` from the oneDAL 2024.x conda
packages, Linux, Python 3.13, 4 cores / 15 GiB, default `castxml` header
backend. Every figure below is real RSS from `/proc/self/statm`, sampled in
the process doing the work.

**The headline: the peak is the clang JSON AST, not the header graph.**

## The measurement

One `service.run_dump(...)`, with `buildsource.header_graph.
build_header_only_graph` wrapped so RSS can be read at the exact moment the
AST is parsed and again the moment the graph exists:

| point | RSS (MiB) |
|---|---|
| process start | 53.9 |
| **clang AST parsed, before the graph build** | **1968.0** |
| graph built, AST still referenced | 2239.5 |
| dump returned | 2407.3 |
| AST reference dropped | 2185.3 (−222.0) |
| `malloc_trim(0)` | 2121.7 (−63.6) |
| **graph dropped** | **635.2 (−1486.5)** |
| `malloc_trim(0)` | 605.3 |

The primary dump (binary + debug info + the castxml header parse + metadata
attach) completes at ~494 MiB in a separate phase-instrumented run, so the
clang AST parse alone accounts for roughly **1.4-1.5 GiB**, and the graph
build for **271 MiB** on top of it.

## Two conclusions, and why the obvious reading of them is wrong

**1. Dropping the graph does not measure the graph.** Releasing it returns
1486 MiB, but the graph's own objects total only **251.7 MiB** by a full
`gc.get_referents` walk (2,830,703 objects). Dropping the *AST* returns only
222 MiB of the ~1.9 GiB it allocated. The rest comes back only when the graph
goes, because the graph's long-lived objects are spread through pymalloc
arenas the AST parse dirtied, and an arena cannot be returned while anything
in it is live. So "the header graph costs 1.2 GiB" is an attribution error:
the graph does not *spend* that memory, it *pins* it.

**2. Making the graph smaller does not help much.** Tested, not assumed: the
graph allocates 959,016 lists, of which ~958,000 are empty (711,436
`conflicts` lists hold **6** `FactConflict` objects in the entire graph, and
node `attrs` averages 0.0 entries). Replacing the empty ones with the
interpreter's singleton empty tuple removed **603,290 objects (21%)** and
saved **40 MiB** — not the ~240 MiB a naive `objects x average bytes/object`
estimate predicts. That estimate divides total RSS by object count and then
applies the average as a marginal cost; empty lists are among the cheapest
objects in the population. The prototype was reverted.

## What this points at

`service_header_graph_attach._attach_header_graph` wraps its
`_clang_header_dump` call in `suppress_streaming_prune()` — deliberately
disabling `dumper_clang_streaming`'s pruner, because
`buildsource.call_graph.parse_clang_ast_calls` walks the raw AST directly for
`DECL_CALLS_DECL` edges (Codex review, PR #840). That decision is sound on
its own terms and was never measured; this is the measurement. The whole AST
is materialised as a Python dict tree — the on-disk cached document for these
headers is **1.3 GiB** of JSON — and the graph is then built while it is
still resident.

Reducing a member's peak therefore means not materialising that tree, not
making the graph denser. Whether the graph build can consume the AST
incrementally, or the AST can be pruned to what the graph needs before being
materialised, is a design question this measurement does not answer.

## What the graph buys, for the same library pair

Through the real `compare` CLI (the L5 graph diff runs from
`cli_buildsource_helpers`, never from `checker.compare()`), on
`libonedal_parameters.so.3` old vs new:

| | with the graph | without |
|---|---|---|
| wall | 452.7 s | 296.4 s |
| peak RSS | 5665.9 MiB | 1382.3 MiB |
| findings | 2454 | 2448 |

The six are three `declaration_renamed` (one of them a real typo fix,
`dispath_by_policy` -> `dispatch_by_policy`) and three
`public_reachability_changed`, plus the `evidence_metrics` and
`layer_coverage` report sections. All six are `severity: risk` with a null
verdict: **the verdict and the exit code are the same either way**. This is
not an argument for turning the graph off — it is why its cost is worth
attacking rather than its output.

## The six-member release peak is not driven by concurrency

Measured after the above, on the same host: the full six-member oneDAL
release comparison at **one** admitted worker peaks at **7080.6 MiB** tree
PSS (wall 3168.9 s, exit 4). Earlier runs of the same comparison measured
7077.8 and 7109.0 MiB.

This refutes a model stated during the prior work -- that the release peak is
`admitted workers x per-member peak`. The arithmetic had coincided (2 workers
x 5.7 GiB per pair plus the parent is also ~7 GiB), but with the members run
one at a time the peak is unchanged, so concurrency is not what produces it.
What does is not established here. Three candidates, none checked:
accumulation across members; one large member (`libonedal_dpc.so.3`, 260 MB)
costing that alone in the release path; or the once-per-side acquired
`ReleasePublicSurface` held for the whole run.

The per-worker admission budget
(`workflows/release_jobs.py`'s `_RELEASE_JOB_MEM_BUDGET_GIB_BY_DEPTH`, 4.0 GiB
at `headers` depth) is a constant derived from a different bundle
(~3.4 GiB/member) while one oneDAL pair measures 5.7 GiB through the CLI. On
this host the default admits **1 worker out of 4 cores**. Since concurrency
does not drive the peak, the budget's practical effect today is idle cores
rather than overcommit.

## Caveats

* Warm AST cache (~4 GiB under `~/.cache/abi_check/clang`). Wall-clock
  figures therefore understate a cold run, where the second parse is a real
  `clang` invocation rather than a disk read. The RSS figures are about
  materialising the parsed tree and apply to both.
* One library, one pair. The share of graph-origin findings will differ
  elsewhere.
* `tracemalloc` inflates both time and RSS and was kept out of every run
  quoted here; an earlier attribution that reported 4218 MiB for this same
  dump was measuring its own instrumentation (the same dump is 2138 MiB
  without it).
