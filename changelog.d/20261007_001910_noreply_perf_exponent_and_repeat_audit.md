### Performance

- **Hot-path complexity is now measured, not only call-counted.**
  `scripts/complexity_bench.py` fits the empirical time exponent of
  `compare()` by symbol and type count, add/remove and rename matching,
  policy classification and history by release count, and the PR lane
  (`verify.py` step `complexity-bench`) fails when one exceeds its ceiling.
  A whole-package ratchet now bounds same-argument repeat calls of every
  first-party function during `compare()`, and the `perf-antipatterns` lint
  gains `quadratic-dedup` and `blocking-io-in-loop` rules plus
  `pickle`/`model_copy(deep=True)` copies in loops. The one existing
  blocking sleep (the build-directory lock poll in
  `abicheck/buildsource/build_query.py`) is exempted in place; no behavior
  changes.
