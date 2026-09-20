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
(`workflows/release_jobs.py`'s `_RELEASE_JOB_MEM_BUDGET_GIB_BY_DEPTH`) defaults
to 4.0 GiB at `headers` depth, derived from a different bundle
(~3.4 GiB/member), while a single oneDAL library pair measures 5.7 GiB through
the CLI. It is a default, not a fixed value: `ABICHECK_RELEASE_JOB_MEM_GIB`
overrides it at every depth (`release_job_mem_budget_gib`), which is how the
worker counts in these measurements were forced. On
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

---

# Follow-up: the overlap was not the peak (2026-09-19)

The section above says "reducing a member's peak means not materialising
that tree" and names two candidate directions. The first thing actually
built was neither: the graph build was made to consume a *projection* of the
AST (`buildsource/header_graph_ast_projection.py`) so the tree could be
released before the graph is allocated, removing the overlap. This section
records what that measured, on the real library rather than a synthetic
stand-in.

## Setup

oneDAL 2024.7 from conda-forge (`dal`/`dal-devel`, `libonedal_core.so.2`,
164 MB), one `include/daal.h` with `-I include -I include/dal`, default
`castxml` backend, C++ mode, Linux, Python 3.13. **Cold AST cache on every
run**: `XDG_CACHE_HOME` pointed at a fresh empty directory per run, never
inherited. No `tracemalloc` in any run quoted here.

## Result

Three fresh processes per side, each with its own empty cache directory.
Every figure MiB.

| point | before (3 runs) | after (3 runs) |
|---|---|---|
| clang AST parsed | 1332.7 / 1335.5 / 1332.7 | 1332.5 / 1332.5 / 1332.7 |
| graph built | 1480.1 / 1483.0 / 1475.4 | **1361.3 / 1363.6 / 1366.4** |
| **graph build's own cost** | **+147.4 / +147.5 / +142.7** | **+25.2 / +20.5 / +25.2** |
| **attach peak (`VmHWM`)** | **2215.4 / 2218.0 / 2215.4** | **2215.2 / 2215.3 / 2215.3** |
| retained after attach (post-`gc`) | 1287.6 / 1286.1 / 1275.9 | 1291.5 / 1294.1 / 1289.1 |
| graph nodes / edges | 49481 / 98330 | 49481 / 98330 |

The reordering does what it was built to do — the graph build's own
residency cost drops from **~147 MiB to ~24 MiB**, since it now reuses
arenas the AST parse freed — and the projection is cheap (23.6 MiB against a
1044 MiB tree, 2.3%).

**But the peak is unchanged: 2216.3 mean before, 2215.3 after — 0.05%.** The
premise — that the member's peak was the AST and the graph held at the same
time — is wrong.

**And the retained figure comes out slightly *worse*, not better** — which
three runs per side made visible and one run per side would have read as
noise. Every *after* run sat above every *before* run.

The first cause was a real bug in the change: the projection is a local of
`_attach_header_graph`, so holding it to function exit kept its indexes and
edge lists — the same 23.6 MiB — alive past the graph build, their only
consumer. Releasing it at the build's end (`projection = None`) is in the
shipped version, and it moves the graph build from *adding* ~24 MiB to
*subtracting* ~27:

| with the projection released | run 4 | run 5 |
|---|---|---|
| clang AST parsed | 1332.5 | 1332.5 |
| graph built | **1306.9** | **1302.5** |
| graph build's own cost | **−25.6** | **−30.0** |
| attach peak (`VmHWM`) | 2215.2 | 2214.9 |
| retained after attach | 1295.2 | 1289.8 |

So *during* the attach the change is a clear, reproducible win — residency
at the graph-build point is ~175 MiB lower (1302–1307 vs 1475–1483). **But
the steady-state figure, once the attach returns, stays ~8–12 MiB higher
than baseline** (1289.8 / 1295.2 against 1275.9 / 1286.1 / 1287.6), and that
survived the fix. The likely mechanism is the inverse of the pinning effect
the original measurement identified: freeing the AST early lets its arenas
go back to the allocator, and the graph then faults in fresh pages instead
of reusing ones the parse had already dirtied.

**State that plainly rather than rounding it away: on this library the
change does not improve either number a release fan-out's per-member budget
is sized from.** The peak is unchanged and the steady-state retention is
marginally worse. What it does buy is a lower mid-attach residency and — the
reason it is worth keeping — an executable statement of exactly what the
graph needs from the AST, which is the precondition for the prune below.

## Where the peak really is

`dump.header_graph.clang_ast` *ends* at 1332 MiB; `VmHWM` over that same
window is 2215 MiB. The ~880 MiB difference is transient and occurs inside
`json.load`: the document is held as one `bytes`/`str` while the dict tree is
built from it. The peak is **document + tree**, never **tree + graph**.

Checked, not assumed, that the document copy is reducible: on a 247 MB AST,

| strategy | peak |
|---|---|
| `json.load(fh)` | 640.9 MiB |
| `read()` → `decode()` → `del raw` → `loads()` | 641.0 MiB |

CPython already releases the source buffer; no stdlib spelling holds fewer
than one full copy of the document during the parse.

## The remaining lever, with its ceiling measured

Not the document — the *tree*. The four readers the graph build uses touch a
bounded key set (`kind`, `inner`, `name`, `id`, `qualType`, `file`, `type`,
`mangledName`, `range`, `loc`, `referencedDecl`, `ownedTagDecl`, `bases`,
and a short tail). A recursive key-whitelist copy of that same 247 MB AST
measures **224.9 MiB of 359.8 MiB — 62%**, so ~38% of the tree is fields
nothing reads. Scaled to oneDAL's 1044 MiB tree that is ~400 MiB off a
2215 MiB peak (~18%).

Two reasons it is not attempted here, both concrete: it has to run as a
`json` `object_pairs_hook`, and this repository has already measured that
hook costing 13-30% wall time across the *whole* document (see
`dumper_clang_streaming.py`'s own "Measured trade-off" section) against an
`attach_ms` gate that allows 50%; and a whitelist missing one key drops
edges silently rather than raising, so it would need the differential
invariant in `tests/test_header_graph_ast_projection.py` extended to
adversarially-generated ASTs before it could be trusted.

## What was kept, and why

The projection ships. Not as a memory fix — it is not one on this library —
but because it is strictly non-worse, evidence-identical, and defines
exactly what a future pruned tree would have to preserve. Evidence
equivalence was verified through the **real `compare` CLI** (the L5 graph
diff runs from `cli_buildsource_helpers`, never from `checker.compare()`) on
a real old/new pair: 1913 findings, same verdict, same exit code, every
report section byte-equal apart from one `extractor.duration_seconds`.

The attach's memory is also no longer ungated: `check_header_graph_perf.py`
now carries `attach_peak_rss_mib` and `attach_end_rss_mib`, measured in a
fresh subprocess (a `VmHWM` never resets, and a figure measured after an
earlier repeat dirtied the arenas reads low — both would report a *better*
number the more repeats you ask for). Both are absolute RSS rather than a
delta against the pre-attach reading, because `is_gateable` rejects
`<= 0` and a retained *delta* is legitimately negative on the clang
backend, whose AST comes from the primary pass's memo — so the attach
frees a tree it never allocated over an STL-bearing
fixture, because the pre-existing fixture retains 0.1-2.8 MiB and cannot
carry a memory gate at all.

## Caveat

One library, one pair, one run per side for the table above. The peak
figures are extremely stable across fresh processes (2215.4 / 2215.2, and
793.7 / 793.7 / 793.5 on the synthetic fixture), so the "peak unchanged"
conclusion is solid; the retained pair (1287.6 vs 1291.5) is a difference
smaller than the spread and should be read as "unchanged", not as a
regression.

---

# Follow-up 2: the fix was not to parse cheaper, but not to parse (2026-09-20)

The section above ends by naming one remaining lever — pruning the tree,
ceiling measured at 62% of its size. That lever was measured and is *not*
the one that paid. This section records what was tried, what each attempt
actually bought, and the one that did.

## Four approaches, measured on one real 263 MB clang AST

Each in a fresh process; the projection's digest is compared so a faster
approach cannot quietly be a different answer.

| approach | peak RSS | time | projection |
|---|---|---|---|
| baseline (`json.load`, then project) | 698.3 MiB | 4.5 s | — |
| key-whitelist prune (`object_pairs_hook`) | 660.0 MiB (−5%) | 6.3 s (+40%) | identical |
| element streaming, naive char loop | 626.8 MiB | 31.1 s | — |
| element streaming, regex-jump scanner | **261.5 MiB (−63%)** | 7.4 s (parse ×4.3) | — |
| **cached projection (warm)** | **88.4 MiB (−87%)** | **0.11 s (×40)** | identical |

Two things this rules out. **Pruning does not pay**: the ceiling from the
earlier `deep_size` walk assumed a tighter key set than a correctness-safe
one turns out to be, and the peak barely moves because the document
transient is untouched — 5% of peak for 40% more time. **Streaming does
pay on peak but is a real trade**: −63% for ×4.3 on the parse, and a
production version needs both reader passes re-expressed over a stream
(two passes over the file), so the true cost is higher than that row.

## Two incidental findings worth keeping

* **62% of clang's JSON document is pretty-print whitespace.** The file is
  263 MB; the same content serialized compactly is 99 MiB. The parse
  transient tracks the *file* size, so we pay memory for indentation.
* **`-ast-dump-filter=hv` produces 884 KB instead of 263 MB** — 300x less,
  showing how little of the AST belongs to the library rather than its
  dependencies. Not usable: it drops the dependency declarations that
  public signatures reference, which changes evidence rather than cost.

## What shipped: cache the projection

The projection is the complete input to the graph build — that is what the
differential invariant in `tests/test_header_graph_ast_projection.py`
establishes — and it is 50x smaller than the AST it comes from. So a warm
run has no reason to reconstruct it.

oneDAL 2024.7 `libonedal_core.so.2`, castxml backend, fresh process per run,
cold cache directory created for the COLD row and reused for WARM:

| | COLD | WARM |
|---|---|---|
| peak RSS | 2093.5 MiB | **434.7 MiB (−79%)** |
| attach wall time | 22.4 s | **6.4 s (−71%)** |
| graph | 49481 nodes / 98330 edges | identical |
| graph digest | `b0ea90456d38cb8a` | **identical** |
| AST cache entry | 822.4 MiB | 822.4 MiB |
| projection entry | 16.5 MiB | 16.5 MiB |

Of that peak, 286 MiB is the primary dump, which this change does not
touch — so the *attach's own* contribution falls from ~1806 MiB to
~148 MiB, a 92% cut. The 6.4 s that remain are the include-graph pass
(`clang -M` per header) and graph construction, not AST parsing.

**A cold run is unchanged.** It parses once, exactly as before, and now
stores what it computed. So the first analysis of a given header set on a
given toolchain still pays the full price; every repeat does not.

## Cold and warm, precisely

Three states, which this page previously left implicit:

1. **In-process memo** — under `--ast-frontend clang` the primary snapshot
   pass already parsed this AST and hands it over (`dumper_cache`'s memo
   slot). Free. Never populated under the default castxml backend, which is
   why the attach pays at all.
2. **Warm** — an AST cache entry exists on disk. Before this change that
   saved only the `clang` subprocess: abicheck still read 822 MiB and
   rebuilt the same ~1 GiB of dicts, so warm and cold had *the same peak*.
   Now it reads 16.5 MiB and builds nothing.
3. **Cold** — no entry. `clang` runs, the document is parsed once, and both
   the AST entry and the projection are stored.

The state that changed is (2), and its old behaviour is the reason this
looked like a memory problem in the extractor rather than a caching one.
