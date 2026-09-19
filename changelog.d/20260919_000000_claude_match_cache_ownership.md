### Fixed

- **A compiled matcher vocabulary larger than the match cache's whole byte
  budget is no longer refused admission on every lookup.** The match cache
  charged each compiled pattern's full size as its own incremental
  retention, so a pattern exceeding `MAX_RETAINED_BYTES` could never have a
  single result stored against it — while `VOCABULARY_CACHE` kept that same
  pattern alive regardless, so the refusal released nothing. On a real
  six-member oneDAL release comparison four of seven vocabularies crossed
  that line, taking the match cache from 98.99% to 63.6% hits with 411,232
  bypasses and tripling type-spelling matching from 19.99 s to 58.93 s.
  Reproduced in isolation at that scale: 40,000 repeated lookups against a
  2.69M-character pattern cost 41.68 s bypassed and 0.06 s admitted.

### Changed

- **The type-spelling caches now have three explicit owners with three
  budgets, and each cost is charged exactly once.** A new
  `_PatternRegistry` owns compiled patterns — it issues the stable
  generation token both caches key on (a monotonic counter, never a
  recycled `id()`), holds the one strong reference, and charges each
  pattern's bytes once against the new `MAX_PATTERN_BYTES`. The match and
  vocabulary caches hold non-owning, refcounted references, and the match
  cache's `retained_bytes` now covers strictly what it owns. The vocabulary
  cache is bounded in bytes as well as entries, closing a hole where 64
  multi-megabyte patterns were reported as a tidy "64".

### Added

- **Run-wide cache attribution for a release comparison**, emitted through
  the existing `ABICHECK_MEMORY_TRACE` instrument by the new
  `abicheck.workflows.cache_counters`: the spelling caches' hits, misses,
  bypasses *by reason*, evictions, compilations and retained bytes **by
  owner**, plus the include-tree walk's call count against its *distinct*
  directory count. Both answer questions a sampling profile cannot — whether
  matching is slow because results are being recomputed, and whether a hot
  directory walk is one expensive traversal or many repeats of a cheap one.
- `scripts/bench_release_memory.py` gained `--vocabulary-scale` (grow the
  compiled vocabulary), `--jobs` (a worker-admission sweep over one
  unchanged workload) and `--require-vocabulary-bytes`, the vacuity guard
  that fails a run which did not actually cross the admission threshold it
  claims to measure.

### Documentation

- **`compile_spelling_pattern` no longer claims matching is "independent of
  candidate count".** Measured, it is independent only when the vocabulary
  factors to a shared literal prefix or the subject fails on its first
  character; for the diverse-namespace shape a real C++ vocabulary has, a
  miss is linear in the vocabulary (5.3 µs to 917.6 µs across 1,000 to
  60,000 spellings). The corrected docstring carries the measurements and
  the vocabulary-independent indexed matcher proposed for the cold path.
- **A newly found under-report in `finditer_allow_nested` is recorded in
  `docs/contribute/known-gaps.md`**, with an executable pin in the test
  suite: a shorter registered spelling starting at the same offset as a
  longer match is never reported, so `Foo` is lost inside `Foo<int>`. It is
  deliberately *not* fixed here — the fix adds reachability edges, which can
  change findings, and so needs its own isolated change and rebaseline.
