### Added

- **`compare --write FORMAT=PATH` now works for a directory/package
  (release) operand — CLI cleanup phase two, PR E.** It used to be
  rejected outright (`--write is not supported for directory/package
  (release) comparisons`). The release engine renders the secondary
  format from the exact same already-computed per-library results its
  primary format uses — no second per-library comparison pass — mirroring
  how `--write` already worked for a single-pair operand. Only
  `json`/`markdown`/`junit` are available for a release operand (the same
  set `--format` itself accepts there); `sarif`/`html`/`review` still
  require a single-pair comparison and are now explicitly rejected with a
  usage error instead of silently falling back to markdown output. The
  composite GitHub Action's own `--write json=...` injection for its
  sticky PR comment no longer skips directory/package operands.

### Fixed

- **`annotation_report_entries`'s persisted array now includes
  `--used-by`/`--required-symbol` scoping's synthesized findings**
  (`result.scoped_only_changes`, e.g. `PE_ORDINAL_RETARGETED`) — the same
  fold every other finding-set consumer (`_fold_scoped_compat_into_text`,
  `_attach_suppression_audit`, JUnit) already applies. Without this, a
  comparison whose *sole* gating finding was scope-synthesized could exit
  non-zero with that finding present in the rendered report's `changes`
  but absent from both the stderr annotation and the persisted
  `annotations` array (Codex review).
- `crosscheck_promotion_contribution` is now schema-constrained to exactly
  `0` on a native `compare` report (`const: 0`, not just `minimum: 0`) —
  this axis has no meaning outside `scan --against`'s own maintainer-
  promoted `--crosscheck KEY=error` finding, and `compare_report.
  schema.json` only ever validates native `compare` reports (CodeRabbit
  review).
