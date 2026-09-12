### Changed

- **The new perf scripts no longer contain any D/E-grade function.**
  `check_l2_cli_perf.main` was an E (cyclomatic complexity 37) and
  `run_scenario` and `perf_receipt.run_measured` were both D; all three are now
  C, by extracting named pieces rather than by moving lines around — the host
  suitability check, scenario selection, coverage-claim reporting, baseline
  reading and baseline gating out of `main`; the fixture build and the untimed
  setup-step loop out of `run_scenario`; and the process-group reaping out of
  `run_measured`. Each extracted function states the one decision it owns, which
  is also why their docstrings are where the reasoning now lives.
