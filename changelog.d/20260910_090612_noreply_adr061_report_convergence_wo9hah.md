### Fixed

- **The severity gate decision and per-finding gate contribution now reuse
  a `ReportEnvelope`'s own captured resolution date, and `AbiSnapshot.
  dependency_info` is decoupled from the caller's object** —
  `gate_decision_for_result`/`compute_gate_decision`/`compute_exit_code`
  gained a `today` parameter, closing a narrow race where
  `build_report_envelope` captured one date for every finding but the gate
  decision could independently read a different one. SARIF's and JUnit's
  `gate_contribution_for_change` calls now pass `envelope.resolved_today`
  too, so a finding's reported gate contribution can't disagree with its
  own frozen severity level once a dated `PolicyFile.reclassify` rule
  expires between envelope construction and render. Separately,
  `_snapshot_abi_snapshot` now gives the `dependency_info` field (a single
  mutable object populated under `--follow-deps`, previously outside the
  container-copy loop) the same one-level-deeper shallow copy its list/dict
  fields already get, so mutating `old.dependency_info.nodes` after
  envelope construction can no longer change a later render.
