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
| oneDAL artifacts | **Not available.** `/mnt/cached_oses/napetrov/tmp-abi/l2b6/` does not exist in this environment, and no equivalent tree was locatable. |

**Real oneDAL acceptance is therefore PENDING, not achieved.** Nothing here
is a "fixed oneDAL memory" or "fixed oneDAL wall time" claim. The
six-member release comparison peaks near 19 GiB, which this 15 GiB,
4-CPU host cannot run at all, so no before/after table for the complete
`leg`/`DIR` workloads could be produced. The oneDAL figures quoted below
(hit rates, bypass counts, matching seconds, pattern character counts) are
the **supplied** measurements from the user's machine, used to identify and
size the defect; every figure attributed to *this* host is labelled as such
and is synthetic.

The functional workstream's MATCH_CACHE/VOCABULARY_CACHE thread-safety
patch was **not available** — remote `main` is identical to the last
inspected revision and the repository has no open pull requests. The
integration dependency is stated explicitly in
`abicheck/compare/spelling_match_cache.py`'s `_CACHE_LOCK`.

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
one, and bypassed by an explicit `--jobs`. The supplied `224 -> 94`
admission was therefore inert for a six-member release — six members is far
under 94, so all six ran concurrently at a measured ~2.5–3 GiB each.

The per-worker budget is 1.0 GiB at `binary` depth and 4.0 GiB with header
roots. Reaching the proposed ≤12 GiB target on a 16 GiB cgroup implies
roughly `jobs=3` at ~3 GiB/member. The supplied `DIR` CPU/wall ratio of
~1.25 suggests the wall-time cost of that would be small, since the extra
threads are largely not buying parallelism — but **that is a hypothesis,
not a measurement**, and calibrating a per-member budget against a guessed
peak is exactly the "raise the budget until one benchmark passes" failure.

`scripts/bench_release_memory.py --jobs` now runs the sweep over one
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
