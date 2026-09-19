# Type-spelling cache admission — measurements

**What this file is.** The measured evidence behind the match-cache
ownership change, and an explicit statement of what it does **not**
establish. Read the scope section before quoting any number here.

## Scope and honesty statement

| | |
|---|---|
| Base revision | `950efbc6439d14472c965230bfce619759343008` (remote `main` at the time; no newer commits, no open PRs) |
| Prior revision referenced | `008fbccf` (supplied measurements only — not re-run here) |
| Python | 3.13.12 |
| Platform | Linux 6.18.44 x86-64, glibc 2.39 |
| CPUs | 4 |
| RAM | 15 GiB total, ~13 GiB available; no cgroup memory limit present |
| oneDAL artifacts | **Obtained.** The supplied tree was absent, so real oneDAL was assembled from PyPI wheels: `daal` 2025.0.0 (OLD) vs 2025.11.0 (NEW) runtime libraries plus the matching `daal-include` headers. Six matched members, ~400 headers per side. See "Header scope" below for the twelve headers that cannot be parsed from the *published* package at all. |

**Real oneDAL acceptance: measured. Partly met.** A complete six-member
comparison now runs on this host and was measured at both revisions with the
identical command, the identical header tree and a cold cache each time.

| | BASE `950efbc64` | HEAD `2228aad` | delta |
|---|---|---|---|
| wall | 4974.68 s | 3990.30 s | **-984.4 s, -19.8%** |
| CPU | 5177.81 s | 4172.34 s | -1005.5 s, -19.4% |
| parent peak RSS | 7077.8 MiB | 7109.0 MiB | +31.2 MiB, +0.4% |
| process-tree peak RSS (summed) | 30955.6 MiB | 31078.4 MiB | +122.8 MiB, +0.4% |
| members completed | 6 of 6 | 6 of 6 | - |
| raw detections per member | 18715 / 115379 / 118987 / 928 / 1002 / 857 | **identical** | 0 |
| gating findings per member | 8774 / 76067 / 50189 / 287 / 289 / 248 | **identical** | 0 |

The raw harness output for both runs is committed beside this file as
`onedal-six-member-receipt.json`, so the numbers above can be checked
against what was actually recorded rather than retyped.

Read that table with four qualifications, none of which are rounded away:

* **Wall time is -19.8%, which is *under* the >=20% target.** It is not
  "about 20%" and not "over 20%".
* **Memory did not improve.** +0.4% is noise in both directions; the <=12 GiB
  budget is *not* met at either revision. Nothing here is a "fixed oneDAL
  memory" claim -- that target remains open, and the parent/tree split below
  is why the two numbers must not be conflated.
* The tree figure is **summed** RSS across the process tree, which
  double-counts shared pages and is therefore an upper bound, not the
  footprint. PSS was not collected on these runs; `memory_trace` is the
  owner of that distinction and a traced run is the way to get it.
* **One run per revision.** The wall delta carries unmeasured variance. The
  one asymmetry present favours the *base*: the HEAD run was measured with
  py-spy attached for 120 s at 50 Hz and with unrelated benchmark work
  running concurrently, while the base run had the host nearly to itself.
  Both inflate HEAD's time, so -19.8% is a conservative reading.

What the table does establish, and what the whole change was for: **the
findings are identical member for member**, raw and gating alike. No
evidence depth, member coverage or finding was traded for the time saved.

### Where the remaining time goes

Profiled on the live HEAD run (py-spy, 6628 samples over 120 s at 50 Hz):

| share of CPU | frame |
|---|---|
| **69.21%** | `finditer_allow_nested` (`compare/spelling_pattern.py:251`) |
| 7.54% | `realpath` (`posixpath`) |
| 2.13% | `_worker` (`concurrent/futures/thread.py`) |

reached through exactly one path:

```
scope_snapshot_excluding_dependencies   (dumper_scoping.py:1313)
  _directly_referenced_dependency_names (dumper_scoping.py:878)
    _referenced_from_haystack           (dumper_scoping.py:657)
      spelling_matches                  (spelling_pattern.py:173)
        matches_for                     (spelling_match_cache.py:747)
          finditer_allow_nested         (spelling_pattern.py:251)
```

Time spent *inside* `finditer_allow_nested` is by construction a cache
**miss** -- a hit returns stored results without calling it. So after this
change the cost is no longer re-doing work the cache refused to keep; it is
the volume of genuinely distinct lookups multiplied by the per-lookup cost
of scanning one giant alternation. That is the matcher-scaling question, and
the indexed-scan prototype measured in this document is the lever for it --
still declined, for the reason recorded there: its divergences expose a
pre-existing under-report whose correction changes findings, which is a
behaviour change that needs its own decision rather than riding a
performance PR.

Caution for anyone re-profiling this: five *point* samples taken before the
aggregate pointed at header-graph construction instead, and reading them as
the answer produced exactly the wrong conclusion. Take the aggregate.

### Header scope

**32 header files** are excluded, counted by re-deriving the two trees
against their source wheels rather than by counting the *groups* below -- an
earlier revision of this section said "twelve", which was the group count and
was wrong. Per side: OLD 707 files -> 679 (28 excluded), NEW 726 -> 695 (31
excluded); the union is 32 because each side ships a slightly different set.

Every one is unparseable from the *published* package by any tool, not merely
by this one. Exclusion is symmetric -- a file removed on one side is removed
on the other even where only one side's copy fails -- so no deliberately
retired declaration is manufactured into a missing export:

| class | files | what they are |
|---|---:|---|
| Include `daal/src/...` build-tree headers the package does not ship | 17 | `cpu_info_x86_impl.hpp`, `detail/dispatcher.hpp`, `detail/singleton.hpp`, `detail/profiler.hpp`, and the `algo/*/parameters/{cpu,gpu}/*` + `kmeans/detail/train_init_centroids.hpp` set that reaches `oneapi/dal/backend/*` |
| External connectors | 10 | Arrow, ODBC, kdb, the `data_source/modifiers/sql/` tree, `sql_feature_utils.h` and `mysql_feature_manager.h` |
| Optional MPI dependency | 2 | `detail/mpi/communicator.hpp`, `spmd/mpi/communicator.hpp` |
| Architecture-guarded, never compiled on x86 | 2 | `cpu_info_arm_impl.hpp`, `cpu_info_riscv64_impl.hpp` |
| Needs oneMKL SYCL headers and `icpx` | 1 | `services/internal/sycl/math/mkl.hpp` |

The exact list is in `onedal-six-member-receipt.json`, so the count and the
enumeration cannot drift apart.

The functional workstream's MATCH_CACHE/VOCABULARY_CACHE thread-safety
patch has since landed (#1336) and **is integrated**, not stacked
alongside: this branch was rebuilt on that file as the base and three of
this work's own earlier decisions — a single global cache lock, monotonic
tokens, and its own `clear()` semantics — were surrendered in favour of
#1336's, so there is one locking design rather than two. Its invariants and
tests are preserved; the per-cache locks, generation counters and lock
order documented in `spelling_match_cache.py` are that patch's, extended to
the pattern registry as the innermost lock.

## The defect

`_MatchCache.put` charged each compiled pattern's full estimated size
(`len(pattern.pattern) * 9`) as *this cache's* incremental retention, and
refused admission when that exceeded `MAX_RETAINED_BYTES` (8 MiB). The
refusal freed nothing: `VOCABULARY_CACHE` (64 entries, no byte bound) held
the same pattern regardless, and the matcher's callers
(`type_reachability`, `dumper_scoping`) hold it in their own attributes for
their whole life.

Applying that arithmetic to the seven vocabularies the supplied `leg` run
compiled:

| pattern characters | charged bytes | admission |
|---:|---:|---|
| 9,466 | 85,194 | admitted |
| 291,450 | 2,623,050 | admitted |
| 292,020 | 2,628,180 | admitted |
| 1,123,026 | 10,107,234 | **refused** |
| 1,123,353 | 10,110,177 | **refused** |
| 2,872,019 | 25,848,171 | **refused** |
| 3,962,788 | 35,665,092 | **refused** |

Four of seven refused — which is exactly the supplied `950efbc64` `leg`
counters: 63.6% hit rate against 98.99% at `008fbccf`, 411,232 bypasses
where there had been zero, and matching time 19.99 s → 58.93 s.

## Isolated reproduction and fix (this host, synthetic)

40,000 repeated lookups over 20 distinct short subject strings, against one
compiled vocabulary:

| vocabulary | pattern chars | before | after | hit rate before → after |
|---|---:|---:|---:|---|
| 10,211 spellings | 269,409 | 0.02 s | 0.03 s | 99.95% → 99.95% |
| 102,064 spellings | 2,693,848 | **41.68 s** | **0.06 s** | **0.00% → 99.95%** |

The small vocabulary is the control: it was never refused, and is unchanged.
The large one is the defect, and the ~700× difference is the cost of
recomputing a result that was already available.

## Matcher scaling (this host, synthetic)

Microseconds per lookup on a 34-character subject. This corrects
`compile_spelling_pattern`'s documented claim that one compiled alternation
makes matching "independent of candidate count".

| vocabulary shape | 1,000 | 5,000 | 20,000 | 60,000 |
|---|---:|---:|---:|---:|
| one shared prefix, late miss | 0.7 | 0.7 | 0.7 | 0.7 |
| diverse spellings, miss | 5.3 | 24.0 | 204.3 | 917.6 |
| diverse spellings, easy miss | 0.4 | 0.4 | 0.5 | 0.5 |
| **indexed-scan prototype, same inputs** | **1.7** | **1.8** | **1.8** | **1.7** |

Build cost at 60,000 spellings: 1.44 s for the alternation against 0.024 s
for the index. The alternation is flat only when the vocabulary factors to a
shared literal prefix or the subject fails immediately; for the
diverse-namespace shape a real C++ vocabulary has, a miss is linear in the
vocabulary — 173× across a 60× size range.

**The indexed prototype is not adopted**, because a differential run against
the current matcher over 2,400 randomized vocabulary/text pairs found 70
divergences, **all strict supersets** (0 subsets, 0 incomparable). That is
not a prototype bug — it is a pre-existing under-report in
`finditer_allow_nested`, reproduced on realistic spellings (`Foo` lost
inside `Foo<int>`, `dal::Table` inside `dal::Table<float>`). Adopting the
prototype would therefore change findings, so it needs its own isolated
change and a transparent rebaseline. Recorded in
`docs/contribute/known-gaps.md`; pinned executably in
`tests/test_spelling_match_cache.py`.

## Include-tree walk

One walk of `/usr/include` (4,188 header entries) on this host: 36 ms to
traverse, 43 ms more for the callers' `stat`+hash pass, 78 ms total; 15 ms
warm. The supplied `DIR` profile puts `_cache_header_rel_parts` at 3.67% of
samples (~109 CPU-seconds of 2,962). Those reconcile only at roughly 1,400
walks — far above the ~12 a six-member two-sided run needs at one walk per
cache-key computation.

**So the open question is the call count, not the per-call cost**, and the
supplied profile share alone does not establish that uncached root
resolution caused the regression. `header_scan_statistics()` now answers it
on a real run. On the representative three-member fixture here it already
reports `calls: 20, distinct_directories: 2, repetition_factor: 10` — the
walk *is* repeated, but confirming that the repetition explains 109
CPU-seconds needs the oneDAL header tree. No caching change is made on the
strength of a synthetic repetition factor.

## Worker admission — analysis only, no change made

`workflows/release_jobs.py` already does the right shape of thing: one
coordinator, probed once at pool-sizing time (never per worker), floored at
one, and bypassed by an explicit non-zero `jobs` argument — which no CLI
flag can supply, since ADR-068 D5 removed `--jobs` and the CLI always passes
`jobs=0`. The supplied `224 -> 94`
admission was therefore inert for a six-member release — six members is far
under 94, so all six ran concurrently at a measured ~2.5–3 GiB each.

The per-worker budget is 1.0 GiB at `binary` depth and 4.0 GiB with header
roots. Reaching the proposed ≤12 GiB target on a 16 GiB cgroup implies
roughly `jobs=3` at ~3 GiB/member. The supplied `DIR` CPU/wall ratio of
~1.25 suggests the wall-time cost of that would be small, since the extra
threads are largely not buying parallelism — but **that is a hypothesis,
not a measurement**, and calibrating a per-member budget against a guessed
peak is exactly the "raise the budget until one benchmark passes" failure.

`scripts/bench_release_memory.py --job-mem-gib` now runs the sweep over one
unchanged workload; the numbers it needs are the real per-member peaks,
which require the oneDAL inputs.

## DWARF

Not investigated at artifact level: `_walk_die_iter` appearing on a build
whose operands are said to carry no DWARF is a contradiction that needs the
actual resolved artifacts, their debug sources, and per-CU/DIE attribution.
No shortcut was added — doing so without knowing which artifact is being
walked risks suppressing legitimate external/split-debug discovery and
blurring the absent/uncollected/failed distinction.

## Reproduction

```bash
# isolated admission reproduction and the fix (seconds, this host)
python scripts/bench_release_memory.py \
    --root /tmp/abicheck-bench-fixture \
    --members 6 --apis 300 --records 20 \
    --vocabulary-scale 400 \
    --variants json --trace trace.jsonl \
    --require-vocabulary-bytes 8388608 \
    --out receipt.json

# worker-admission sweep over one unchanged workload.
#
# Driven through the per-worker memory budget, NOT a `--jobs` flag: ADR-068
# D5 removed `-j`/`--jobs` from `compare` outright, and the CLI always passes
# `jobs=0` (auto-detect, then memory-clamp). Forwarding a `--jobs` would fail
# the run with "No such option" before anything was compared. Driving the
# budget also exercises the real clamp instead of bypassing it, which an
# explicit worker count does by design.
#
# The fixture parameters must match the build above, or `--keep` rebuilds it
# and the sweep stops comparing one unchanged workload.
for g in 0.5 1.0 2.0 4.0; do
  python scripts/bench_release_memory.py --keep \
      --root /tmp/abicheck-bench-fixture \
      --members 6 --apis 300 --records 20 --vocabulary-scale 400 \
      --job-mem-gib "$g" --variants json \
      --repeat 3 --out "receipt-membudget-$g.json"
done
```

Each row records `observed_workers`, read back from the fan-out's own
"parallel release workers reduced N -> M" note rather than inferred from the
budget: the budget only reaches a worker count through `release_jobs_mem_cap`,
which also reads available memory, applies a utilization fraction and a
reserve, and floors at one. Two budgets can admit the same count, so a sweep
that reports only what it requested cannot show that concurrency actually
varied. Measured on this host, 4.0 GiB admits 1 worker and 0.5 GiB admits 4.

`--require-vocabulary-bytes` is the vacuity guard: it fails a run whose
compiled patterns never reached the admission threshold the benchmark
claims to measure, so the fixture cannot silently drift into measuring the
happy path.
