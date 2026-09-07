### Added

- **`declared_absent` acquisition state** — ADR-065's `MemberAcquisition`
  vocabulary gains a fourth kind of gap (`AcquisitionState.DECLARED_ABSENT`,
  ADR-068 D2/D3, `one-comparison-product.md` Phase 1 item 1): a member whose
  OLD side the run itself declared absent, never a supply gap. It can never
  surface as a proven removal/addition and never counts as a completed
  comparison, so a scope built entirely from it never reads as a clean pass.
  The `comparison_scope` report section's `state` enum and `counts` object
  gain the matching value (`report_schema_version` 3.4 → 3.5, additive).
  This is the model-level primitive only — no CLI command emits it yet.

