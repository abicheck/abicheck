### Changed

- **`compare --used-by`/`--required-symbol(s)` no longer replace the
  compatibility gate — this is a behavior change.** Previously, a supplied
  consumer's own scoped result (worst-app-wins) drove the process's exit
  code and overwrote the JSON `verdict`/`severity`/`run_outcome`/`summary`
  fields outright, with the full-library result demoted to `full_verdict`/
  `full_severity`/`full_run_outcome`/`full_summary`. As of this release, the
  exit code and those JSON fields **always** describe the full-library
  comparison — exactly as an unscoped `compare` invocation would produce —
  and a supplied consumer's own confirmed/potential/unresolved impact is
  reported **beside** that result, never in place of it:

  - JSON gains a `consumer_scope` object (`verdict`, `scope`, and, under the
    severity exit-code scheme, `exit_code`/`exit_code_scheme`) alongside the
    existing `used_by`/`required_symbol_contract` per-app/per-host detail.
    `full_verdict`/`full_severity`/`full_run_outcome`/`full_summary` are no
    longer emitted, since there is nothing for them to be "full" beside any
    more.
  - SARIF's `scopedGate` block, JUnit's `abicheck.gate_*` properties, the
    HTML report's "Consumer-scoped verdict" box, and the PR comment's own
    consumer summary are unchanged in shape but are now purely informational
    — none of them drive that surface's own exit code, result `level`, or
    failure count.
  - A `compare --used-by`/`--required-symbol` invocation's exit code can
    differ from a prior release's: it now equals what plain `compare` (same
    OLD/NEW/headers/policy) would report, rather than the worst supplied
    consumer's own scoped result. If you relied on the previous scoped-gate
    behavior to gate CI on a specific consumer's impact rather than the
    library's own compatibility result, gate on the JSON report's `used_by`/
    `required_symbol_contract`/`consumer_scope` fields directly instead.

  See `docs/contribute/plans/vision-api-abi-evolution.md`, section
  "D. Optional prebuilt-consumer lifecycle" (workstream D-S1), for the full
  rationale.
