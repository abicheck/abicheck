<!-- CLI cleanup phase two, PR 2: aggregate policy into the manifest schema. -->

### Changed

- **`aggregate --on-missing-required`/`--on-unexpected-target` are gone.**
  The gate policy for an unavailable required target or a report outside
  the expected set now lives in the expected-target manifest's own `gate`
  block (`{"gate": {"missing_required": "warn", "unexpected_target":
  "fail"}}`), published at a new **MAJOR** `aggregate_manifest_version:
  "2.0"` so an old-vintage reader rejects the new field loudly instead of
  silently misapplying the hard-coded default. `--run-plan`-sourced runs
  get the same policy via `abicheck project plan`'s new
  `--gate-missing-required`/`--gate-unexpected-target` options, which stamp
  run-plan.json's own `gate` block for `to_aggregate_manifest()` to
  project. Omitting `gate` entirely keeps the same defaults (`fail`/
  `include`) this command always had. The resolved policy and its source
  (`manifest`/`run-plan`/`default`) are reported back in the JSON output's
  new `effective_policy` block (`aggregate_schema_version` bumped to
  `1.5`).
