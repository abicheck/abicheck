### Documentation

- **`docs/contribute/performance.md` now separates the three performance
  measurement levels** — synthetic/in-process, real-L2/in-process, and full-CLI
  — and states for each what its numbers do and do not cover, because quoting
  one level's figure as another's is the most common way to misread that page.
  Adds the measured cost of the new full-CLI lanes, their per-scenario
  coefficients of variation (which is what sets the gate's absolute noise
  floor), and the cache-state, native-invocation-observation and memory-reporting
  contracts the harness holds itself to.

- **The L2 performance bottlenecks found while building that harness are
  recorded in `docs/contribute/known-gaps.md`**, measured but deliberately not
  fixed: interpreter startup is ~60% of a small stored-snapshot `compare`; the
  `clang -M` include-graph pass runs once per top-level header per side, is
  never cached, and grows superlinearly (1/8/32 headers → 1.1/2.5/10.4 s); a set
  of five libraries pays full cost per library with no sharing; `compare
  --format` repeated silently keeps only the last format; and a labelled
  side-scoped `--include` appeared to suppress unlabelled global include roots
  on a real project (observed once, not yet minimally reproduced).
