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
