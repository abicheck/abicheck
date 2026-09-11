### Fixed

- **`abicheck aggregate` no longer treats an operationally-failed
  `compare --no-baseline` audit as completed** — a report that pinned an
  evidence contract it could not satisfy (`run_outcome.operational:
  evidence_contract_error`) now stays `analyzed=False` with a descriptive
  `reason`, instead of being folded into "audit completed" coverage just
  because its gate axis happened to be clean.
- **`abicheck aggregate --format text`/`profile_matrix` no longer render a
  completed-but-verdict-less audit as "clean"** — `ProfileMatrixEntry`
  gains `audit_only_profiles`, and the text renderer now reports such a
  profile as "audit-only (no compatibility verdict)" rather than folding it
  into a compatibility claim (`compare --no-baseline` never reports one,
  ADR-068 D2).

