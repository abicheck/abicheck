### Added

- **CI now gates peak memory against the base branch, not just against an
  absolute ceiling.** `scripts/benchmark_scaling.py` has always *recorded*
  `peak_mb` for every scenario point, but the only memory gates were the
  absolute `--max-memory-mb`/`--max-rss-mb` ceilings. An absolute ceiling
  catches a regression only once it crosses the ceiling, so a change that
  doubled a scenario's allocation from 200 MiB to 400 MiB passed the 2048 MiB
  ceiling in silence — the gradual drift the *timing* side has had a
  base-branch comparison for since PR #768 had no equivalent on the memory
  side.

  `--regress-memory-tolerance` / `--regress-min-delta-mb` add that comparison,
  and a new `memory-regression` job in `.github/workflows/performance.yml`
  runs it on every PR the perf lane already triggers on. A point regresses
  once its peak exceeds the baseline's by more than
  `max(tolerance x baseline, min_delta_mb)` — the same combined
  relative/absolute rule as the timing gate, computed by the same
  `perf_measurement.combined_regression_threshold`, so the two cannot drift
  apart. Defaults are deliberately tighter than the timing ones (20 % / 4 MiB
  against 50 % / 0 s): a `tracemalloc` peak counts bytes actually allocated
  and does not move with GC timing or scheduler preemption, so the noise that
  forces a loose timing tolerance is largely absent.

  The memory baseline is read from the *same* `--baseline` report, so there is
  no second file to keep in sync. A baseline carrying no `peak_mb` (produced
  with `--no-memory`, or by a build predating this gate) reports an inactive
  memory gate rather than being read as "allocated nothing", which would
  otherwise flag every point of every run and make the gate impossible to
  introduce; the timing gate still fails closed on an empty baseline.

  Two ways the gate could have run without being able to fail are closed
  explicitly. A baseline point whose value is non-finite is dropped rather
  than kept: `json.loads` accepts `Infinity`/`NaN`, an infinite baseline makes
  every finite head value an infinitely large *improvement*, and every
  comparison against `NaN` is False, so such a point reads as "no regression"
  for any input a run could produce. And a memory baseline that shares zero
  comparable points with what was measured is now a hard failure, the same way
  the timing side has always treated its own zero overlap — previously only
  the timing overlap was aggregated, so a memory gate that compared nothing
  reported OK.

  It is a separate CI job rather than another flag on `regression` because
  tracing the heap is not timing-neutral — `measure()`'s memory pass clears
  every live `lru_cache` between sizes and runs an extra untimed cold call,
  the exact bias that job's own `--no-memory` comment documents. Memory is on
  for both sides here so the bias cancels, and the timing tolerance is
  explicitly neutralised so a distorted timing can never fail a memory job.
