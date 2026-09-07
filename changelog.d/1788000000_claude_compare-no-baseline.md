### Added

- **`abicheck compare --no-baseline NEW`** (ADR-068 D2, plan §5 P1 / §6
  Phase 2e): declare that no prior surface exists for a candidate build and
  run an audit instead of a comparison -- the first replacement for `scan`'s
  audit-only mode (no `--against`) and ADR-047 §8's S5. `--no-baseline` is an
  explicit declaration, never inferred from argument count: `compare NEW`
  (one operand, no flag) and `compare --no-baseline OLD NEW` (the flag plus
  two operands) are both usage errors (exit `64`). The OLD side carries a
  new ADR-065 `MemberAcquisition` state, `declared_absent` -- distinct from
  `not_supplied`, since the user explicitly declared there is no prior
  surface rather than the run failing to find one. A `--no-baseline` report
  never emits an addition, a removal, or a compatibility verdict --
  `run_outcome.compatibility` is `null`, `changes` is always empty, and the
  process exits `0` unless an orthogonal axis (analysis assurance, contract
  coverage) independently contributes. Only a single artifact is supported
  for now (`--format json`/`markdown`); a directory/package operand raises a
  usage error until ADR-065 S3's package component inventories land.
