### Added

- **`abicheck project history` (ADR-066 S1)** — derive per-API lifecycle
  events (`first_observed`/`introduced`/`deprecated`/`removed`/
  `reintroduced`) and coverage gaps from an ordered chain of stored
  snapshots, by composing the existing pairwise `compare()` engine across
  each adjacent pair — no new N-way diff engine. Typed API:
  `abicheck.workflows.history.run_history_request`/
  `build_longitudinal_history`. Offline only; see the ADR-066 amendment for
  S0's design trade-offs and S1's scope boundaries.
