### Changed

- **ADR-061 Phase 2 (D9 "decisions computed once")**: JSON, SARIF, and the
  HTML report's CI-gate card now all resolve the severity gate through one
  shared call site, `abicheck.policy.gate_decision.gate_decision_for_result`,
  instead of each format independently importing
  `severity.compute_gate_decision` and reassembling its arguments from a
  `DiffResult`. `scan --against`'s severity-scheme summary and a
  directory/package release compare's per-library gating buckets route
  through the same function. No output changes -- the value computed is
  identical to before, just owned in one place instead of three-plus. A new
  property test (`tests/test_gate_decision_shared.py`) sweeps several
  finding/severity-config combinations and asserts JSON's `severity` block,
  SARIF's `properties.severityGate`, and the HTML gate card all agree with
  the one `GateDecision` the shared resolver returns.
