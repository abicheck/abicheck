### Fixed

- **The header-graph L2 performance gate now checks all three of the phases it
  measures, not one.** `scripts/check_header_graph_perf.py` timed the primary
  header-AST `dump` on every sample and then discarded that number at gate
  time, gating only `attach_ms`: a 2x regression in the main L2 extraction path
  printed a larger figure in the table and still exited `0`. The ambiguously
  named `baseline_ms` is now `dump_ms`, a per-sample `total_ms` covering the
  whole dump+attach window has been added (read off the timestamps the two
  phase measurements already take — no additional `dump` is executed, and it
  is never reconstructed by summing two independent medians), and all three are
  gated independently with per-metric thresholds. A non-finite or non-positive
  value on either side of a comparison now reads as *ungated* and is reported,
  instead of silently satisfying a `current > base + allowed` test that is
  `False` for any `NaN`.

- **A built-in per-scenario regression allowance can no longer silently
  outrank an explicitly-requested stricter threshold.**
  `scripts/benchmark_scaling.py` consulted `Scenario.regress_tolerance` first,
  so `--regress-tolerance 0.1` ran the `serialize` scenario at `1.3` and still
  reported `OK`. Precedence is now explicit-CLI > per-scenario default >
  CLI default, and the resolved threshold — with the provenance of both of its
  numbers — is recorded in the JSON report and printed alongside the results it
  judged. The `serialize` scenario's indefinite `1.3` allowance, which licensed
  any future 130% slowdown for an already-completed one-time data-model cost,
  has been removed.
