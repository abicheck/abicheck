### Changed

- `workflows/memory_trace.py` now reports a **per-phase** `tracemalloc`
  peak (`tracemalloc_phase_peak_bytes`) beside the run's cumulative one.
  `phase()` starts a fresh peak window on entry and exit; the cumulative
  `tracemalloc_peak_bytes` keeps its published meaning, carried by the
  module rather than read back from `tracemalloc`. Without this every
  phase after the largest one reported the largest one's number, which
  makes a peak observable but not attributable.
- New `workflows/memory_trace.mark()`: the boundary form of `phase()`, for
  a linear pipeline where bracketing a stage in a `with` would mean
  re-indenting a large call expression without changing what is recorded.
- The dump path now records its real stages: the primary dump (binary,
  debug info, the castxml/clang header parse and metadata attach) closes at
  `dump.primary:done`, and the header-graph attach is broken into its
  second clang AST parse, the graph build, and the per-header `clang -M`
  include pass.
