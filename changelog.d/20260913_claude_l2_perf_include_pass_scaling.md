### Fixed

- The full-CLI L2 perf harness now requires one include/dependency pass per
  header the run was asked to parse, not one per live side. The flat per-side
  floor accepted 2 passes for the extended `simple-h8`/`simple-h32` arms, which
  really perform 16 and 64 (measured), so a regression processing only the first
  top-level header of each side stayed under the floor while the evidence depth
  still resolved to `headers` and the deliberate break — which lives in
  `part0.h` — was still found. The expectation is derived from the measured
  argv's own `--header`/`-H` arguments rather than a fixture table, so it cannot
  drift from what the run was told to do.
- The performance workflow's path filter now classifies the whole
  `abicheck/service*.py` family rather than the `service.py` facade alone.
  `_attach_header_graph` lives in `service_header_graph_attach.py` and is only
  re-exported for import stability, so a PR changing the real header-graph
  attach implementation — code both the in-process benchmark and every full-CLI
  live dump execute — skipped every performance job.
