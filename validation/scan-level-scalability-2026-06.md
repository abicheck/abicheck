# Scan-level scalability sweep — findings (2026-06)

A progressive-complexity sweep of every `scan` **level** against a synthetic
C++ corpus of growing size, to find where a level's cost scales worse than its
*coverage* does. Distinct from the UXL run
([`uxl-scan-levels-timing-2026-06.md`](uxl-scan-levels-timing-2026-06.md)),
which fixed the corpus (two real libs) and varied the level; this one **varies
the corpus size** at each level so a per-level scaling exponent can be read off.

Harness: [`eval/scan_level_scaling.py`](../eval/scan_level_scaling.py) — no
network/repo, synthesises `N`-TU trees (STL/template-heavy, tunable depth),
builds them with the host compiler, runs `abicheck scan` at each level against a
slightly-changed baseline, and records wall time + **peak child RSS**
(`os.wait4`, so clang's native memory counts) per (size, level).

Subject: `funcs_per_tu=8`, template `depth=6`, sizes `n_tus ∈ {4, 8, 16}` (each
TU pulls in `<vector>/<map>/<string>/<memory>` and a recursive template, so a
single TU's clang JSON AST is large — the L4 cliff is priced on exactly this).
Host: 4 vCPU / 15 GiB, clang 18, `ABICHECK_L4_JOBS=4`.

## Raw sweep

| level | n=4 | n=8 | n=16 | wall exp | rss exp |
|---|---|---|---|---|---|
| `binary`        | 0.49 s / 38 MB   | 0.49 s / 38 MB   | 0.51 s / 38 MB   | ~0 (flat) | ~0 (flat) |
| `headers` (L2)  | 4.98 s / 452 MB  | 7.39 s / 650 MB  | 7.48 s / 674 MB  | ~0 | ~0 |
| `build` (L3/s1) | 4.99 s / 452 MB  | 5.49 s / 452 MB  | 5.76 s / 453 MB  | ~0.1 | ~0 |
| `graph` (s4)    | 7.64 s / 457 MB  | 10.20 s / 459 MB | 13.99 s / 463 MB | ~0.5 | ~0 |
| `source_seeded` (s5 + 1-file seed) | 17.3 s / 1105 MB | 20.2 s / 1107 MB | 23.9 s / 1109 MB | ~0.2 | ~0 |
| `source` (s5, **seedless**) | 32.3 s / 2584 MB | 49.9 s / 3044 MB | 88.8 s / 2757 MB | **~0.8** | ~0 |
| `full` (s6)     | 54.1 s / 3443 MB | 87.6 s / 4034 MB | 149.2 s / 3177 MB | ~0.8 | ~0 |

(`exp` = log-log slope of the largest two sizes; ~1 = linear in TU count, ~0 =
flat / size-independent. The L4-bearing levels' wall slope reads ~0.8 rather than
1.0 because the per-TU clang replay parallelizes across the 4 vCPUs — the
*serial* L4 time scales linearly, e.g. `full`'s L4 phase 23.7 → 34 → 64 s.)

**RSS is bounded by concurrent worker count, not TU count.** The L4/L5 passes hit
~2.6–4 GB regardless of size (n=16 is not the peak) because peak RSS ≈ `jobs` ×
per-TU clang AST, and `jobs` is capped (here 4), not the total TU count. That is
exactly the quantity the memory clamp (Gap 2) governs: on a host with a tighter
RAM budget, fewer workers run, so peak RSS drops — at the cost of wall time.

## Conclusions

The truly cheap tier (`binary`/`headers`/`build`) is **flat in TU count** — it is
dominated by the binary dump + the L2 header AST + the L3 compile-DB parse, none
of which grow with the number of `.cpp` files. Confirms the UXL "cheap tier is one
price" result, now with a scaling exponent rather than a single point. `graph`
(s4) sits just above them: it is still far cheaper than the L4 tiers (no AST
replay) but **does** grow mildly with TU count (~0.5 tail exponent, 7.6 s → 14.0 s
from n=4 to n=16) because the L5 graph fold visits every compile unit — so it is
not in the same "size-independent" class as binary/headers/build. `full` (s6) is
**linear** in TU count (every TU is replayed). So far, so expected.

The interesting result is two scalability *gaps* on the `source` (s5) rung,
both quantified below and both now addressed/filed.

## Gap 1 — seedless `--depth source` pays a full-tree call-graph cost the report hides

`source` (seedless s5) costs **~2× the wall time and ~2.5× the RSS of the
seeded run, for the *identical* L4 coverage** — both report `L4=1/1` (one TU
parsed), the same verdict, the same findings. And the wall-time gap **widens
with TU count**: seedless-vs-seeded is 1.9× at n=4 (32 s vs 17 s), 2.5× at n=8
(50 s vs 20 s), **3.7× at n=16** (89 s vs 24 s) — because the seeded run stays
flat (it scopes to one TU) while the seedless run's call-graph half grows with
the whole tree.

Root cause (`buildsource/inline.py:_fold_call_graph`): an s5 scan runs an L4
replay **and** an L5 clang call-graph pass. With a `--changed-path`/`--since`
seed, *both* are scoped to the changed TU. **Without** a seed, the L4 replay
falls back to "headers-only" (here 1 TU) but the call-graph pass keeps its
**broad, whole-compile-DB scope** — a second `clang -ast-dump=json` over *every*
TU. That whole-DB pass is the entire extra cost, and the coverage line only
reports the L4 replay's `1/1`, so the cost is invisible in the output.

This refines the documented "s5 only beats s6 with a seed" rule: even the
*scoped* part of a seedless s5 (the L4 replay) is cheap, but the run as a whole
still pays an unscoped full-tree clang pass — so seedless s5 is not merely "as
expensive as s6", its call-graph half scales with the whole tree while its
reported L4 coverage stays at one TU.

**Status:** documented here + in `docs/development/performance.md`. The
honest-cost fix (either scope the call-graph pass to the L4 replay's effective
scope when unseeded, or report its TU count in the coverage line) is filed as a
follow-up — it is a behaviour/UX change, not a hot-path bug.

## Gap 2 — the L5 call-graph pass had no memory clamp (OOM-guard parity with L4) — **fixed**

The same whole-DB call-graph pass is what drives the RSS column: `source`
(seedless) hits **2.6 GB at just 4 TUs** and climbs from there, and `full` hits
**4 GB at 8 TUs**. The L4 replay grew a RAM-aware, cgroup-aware worker clamp
(`source_replay._l4_jobs` → `_l4_mem_cap`, default 3 GiB/worker) precisely
because a template-heavy TU's clang JSON AST can be multiple GiB and N
concurrent ASTs OOM-killed the UXL oneTBB/oneDNN replays. **The L5 call-graph
pass — which shells out to the *same* `clang -ast-dump=json` per TU — never got
that clamp**: `call_graph._call_graph_jobs` was purely `min(n_units, cpu, 8)`.

So a constrained host (a small cgroup / CI container) was protected on the L4
replay but **not** on the unseeded full-DB call-graph pass that `--depth source`
and `--mode pr-deep` run — the exact OOM mode the L4 clamp was added to prevent,
reintroduced in the parallel sibling pass.

**Fix (this change):** `_call_graph_jobs` now shares the L4 memory cap
(`_call_graph_mem_cap` → `source_replay._l4_mem_cap`), so the call-graph worker
count is reduced to fit available RAM just like L4, honouring the same
`ABICHECK_L4_JOB_MEM_GIB` budget and cgroup-headroom probe. A dedicated
`ABICHECK_CALL_GRAPH_JOBS` still overrides the CPU count, but memory now wins
over an over-eager override (mirroring `_l4_jobs`). The clamp is logged, never
silent. Unit-tested in `tests/test_call_graph.py`
(`test_call_graph_jobs_clamped_by_available_memory`,
`test_call_graph_mem_cap_shares_l4_budget`).

Measured effect (seedless `source`, n=8, 4 vCPU / 15 GiB host):

| run | call-graph workers | peak RSS | wall |
|---|---|---|---|
| default | 4 (`min(TUs, cpu)`) | 2373 MB | 51.9 s |
| `ABICHECK_L4_JOB_MEM_GIB=8` (cap → 1) | 1 | **1217 MB (−49 %)** | 60.5 s (+17 %) |

(This `default` row was a separate before/after run from the Raw sweep table
above, which recorded 3044 MB / 49.9 s for the same n=8 seedless `source` config.
The ~20 % RSS spread between the two `default` measurements is run-to-run variance:
peak RSS depends on *which* of the 4 concurrent template-heavy clang ASTs happen
to overlap at their high-water mark, which shifts between runs. Read both as
"~2.4–3 GB, bounded by the 4 workers" — the point is the **−49 %** drop to one
worker, not the absolute baseline.)

Before the fix, `ABICHECK_L4_JOB_MEM_GIB` had **no effect** on this run (it only
clamped the L4 replay, which was a single TU) — the call-graph pass ran 4 workers
regardless of the RAM budget. After it, a host with a tighter budget trades wall
time for ~half the peak RSS, which is what converts an OOM-kill on a constrained
host into a slower-but-successful scan.

## Reproduce

```bash
python eval/scan_level_scaling.py --sizes 4,8,16 --depth 6 \
    --json-out reports/perf/scan_levels.json
# Demonstrate the Gap-2 fix: a tight per-worker budget forces the call-graph
# pass down to 1 worker (lower RSS), where before it ran min(TUs, cpu).
ABICHECK_L4_JOB_MEM_GIB=8 python eval/scan_level_scaling.py --sizes 8 --levels source
```
