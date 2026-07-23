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
  plugin-contract` (`--used-by`/`--required-symbols` routing, rejected up front
  when combined with `baseline-channel: none` since `scan` has no equivalent
  scoping flag). `kind: bundle` compares a directory of member binaries and
  fails fast rather than silently degrading if `requested-depth: build`/`source`
  is asked of it, since the underlying per-library release engine can't collect
  that evidence for a directory operand. A `baseline-channel: none` scan run
  that hits a guard (e.g. `--budget` exceeded) is treated as an operational
  error rather than a deferrable compatibility finding, so `gate-mode:
  deferred`/`advisory` can't turn a guard failure into a quiet pass. A
  bundle/directory compare's `--fail-on-removed-library` gate (a dedicated
  exit code that overrides the persisted severity scheme rather than
  feeding into it) is folded into the check's own gate decision too, so
  `gate-mode: local` doesn't silently pass a removed library the caller
  explicitly asked to gate on. See `docs/reference/check-target.md`.

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
<!--
### Fixed

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
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
