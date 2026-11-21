### Added

- **`consumer_impact_summary` in SARIF and JUnit.** A `compare --used-by`
  run's "N of M consumers affected" summary, previously JSON-only, now also
  appears as SARIF's `runs[0].properties.scopedGate.consumerImpactSummary`
  and as JUnit `abicheck.consumer_impact.*` testsuite properties. All three
  formats read it from the report envelope's `ScopedGateFacts`, and SARIF's
  `scopedGate` block no longer recomputes its counts from the result.

### Fixed

- **GitHub Action: a non-JSON `extra-args` export no longer reads as
  blinding the coverage/assurance/severity floors.** `-o` is repeatable, so
  the Action's internal JSON export runs beside a caller's own
  `-o text=...`; this is now pinned by a regression test across every
  non-JSON format, and the former known gap is closed in the docs.
