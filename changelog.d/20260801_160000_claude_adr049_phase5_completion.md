### Added

- `compare` and `scan --against` accept `--pack` (repeatable), selecting
  ADR-049 D8 pack manifests: a `kind: policy` pack overrides per-`ChangeKind`
  verdicts, `kind: contract` and `kind: gate` packs assign their namespace's
  own fields. Two selected packs assigning different values to the same field
  or `ChangeKind`, and a malformed manifest, are usage errors (exit 64) in
  both commands.
- `scan --against --contract-evaluation` now emits `contract_context` and the
  contract-coverage ledger in its JSON `diff` block, serialized by the same
  encoder `compare`'s report uses — previously the scan computed a contract
  context, stamped its findings from it, and then dropped it. Scan schema
  `1.7`; absent without the flag.
- SARIF reports the contract-coverage ledger as tool-level
  `toolExecutionNotifications`, and JUnit as an `abicheck.contract_coverage`
  suite of `<error>` cases — never as passing tests. Both leave the run's own
  gate and exit code untouched.

### Changed

- `scan --against` resolves its configuration through the same canonical
  resolver `compare` and the MCP tool use, so its persisted
  `evaluation_context` carries real per-field ADR-049 D7 provenance
  (`--contract` → `explicit_cli`, `--policy` → `legacy_alias`) instead of
  `api_request` for everything.
