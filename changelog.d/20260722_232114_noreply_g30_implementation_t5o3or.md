<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Added

- **`actions/check-target` composite Action** — composes `actions/resolve-baseline`,
  `actions/collect-facts`, and the root Action into one resolved ABI/API check
  (ADR-047 §4, G30 P1.3), always emitting the report-identity envelope (§7):
  unconditional depth-suffixed `check_id`/`target_id`, the new
  `compatibility_verdict`/`policy_gate_decision`/`check_evidence_coverage`/
  `operational_errors`/`publication` fields alongside the legacy `verdict`/
  `severity` fields `abicheck/aggregate.py` already parses. Supports
  `gate-mode: local|deferred|advisory`, a `baseline-channel: none` single-build
  audit bypass (no `resolve-baseline` call), and `target-kind: library|app-consumer|
  plugin-contract` (`--used-by`/`--required-symbols` routing). See
  `docs/reference/check-target.md`.

<!--
### Changed

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Deprecated

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Removed

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
### Fixed

- **GitHub Action `compare` mode now forwards build/source evidence** — `sources`,
  `build-info`, `compile-db`, `build-config`, and `depth` were previously only
  wired to `dump`/`scan` mode in `action/run.sh`, so a `compare`-mode Action run
  requesting `--depth build`/`source` evidence had no way to actually reach the
  CLI's evidence flags. Now forwarded (scoped to the new/candidate side for
  `sources`/`build-info`, matching `compare`'s own `new=`-prefixed syntax).
- **GitHub Action `compare` mode no longer forwards `--depth`/`--sources`/
  `--build-info` for directory or package operands** — the CLI's per-library
  release fan-out rejects those three flags outright for that shape, so
  every `check-target` `kind: bundle` check (or any other directory/package
  `compare` invocation) with a baseline resolved would fail as a usage error
  before comparing anything. `action/run.sh` now skips those three when
  either operand is a directory/package. `--config` is not one of the
  rejected flags and keeps being forwarded unconditionally (an intermediate
  fix had incorrectly grouped it with the other three, silently dropping a
  bundle caller's `build-config`).
- **`actions/check-target` now rejects `target-kind: app-consumer`/
  `plugin-contract` combined with `baseline-channel: none`** — that
  combination routes the analysis step to `scan` (a single-build audit),
  but `scan` has no `--used-by`/`--required-symbols` equivalent to scope the
  contract check against, so the check previously ran as a plain unscoped
  scan under the contract target's name and could pass without ever
  checking the consumer/plugin contract it claimed to. `validate-inputs.sh`
  now fails loud on this combination instead.
- **`compare_report.schema.json` now accepts the operational-error/bootstrap
  report envelopes it's supposed to always be able to produce** — the schema
  required compare-specific fields (`library`, `old_file`, `summary`,
  `changes`, ...) and restricted `verdict` to the five real `Verdict` values
  unconditionally, so `actions/check-target`'s synthesized
  `verdict: "ERROR"`/`"NO_BASELINE"` envelopes (and the pre-existing
  per-library release fan-out's own `verdict: "ERROR"` shape) never actually
  validated against the schema they declare via `report_schema_version`.
  `verdict`'s enum now also allows `ERROR`/`NO_BASELINE`, and the
  compare-specific fields are only required (via an `allOf`/`if`/`then`)
  when `verdict` is one of the five real values.
- **`actions/check-target` no longer falsely claims the compare-report schema
  for scan or `kind: bundle` reports** — `augment_report` unconditionally
  stamped `report_schema_version` (the *compare*-report schema's marker) onto
  every report regardless of its actual shape, so a successful
  `baseline-channel: none` scan report (its own `scan_schema_version`
  shape) or a `kind: bundle` directory-compare report (the release
  fan-out's `verdict`/`old_dir`/`new_dir`/`libraries` shape) would validate
  as broken against `compare_report.schema.json`'s single-pair-compare
  required fields. A scan report now only bumps its own
  `scan_schema_version`; a bundle/release report gets neither marker (it has
  never had a schema of its own).

<!--
### Performance

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Security

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Documentation

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
