<!--
A new scriv changelog fragment.

Uncomment the section that is right (remove the HTML comment wrapper).
-->
### Fixed

- Reverted an unconditional `pattern_verdicts=True` forcing added earlier
  in this PR at four Tier-2 comparison call sites (`workflows.
  compare_policy.compare_snapshots`, `service_compare_pipeline.
  classify_compare_pair`, `bundle_side_input.py`,
  `workflows/bundle_stored_pair_compare.py`). It cited ADR-068 D4's
  "no legitimate off position" principle, but ADR-068 is "Proposed — not
  implemented" and the accepted ADR-027 explicitly defers flipping
  `--pattern-verdicts` to default-on pending a release cycle's worth of
  FP-rate and parity validation. It had also silently broken `scan
  --against`'s own working, documented `--pattern-verdicts/
  --no-pattern-verdicts` opt-in flag (default off), which routes through
  the same chokepoint. `surface_metrics`'s own unconditional forcing at
  these sites is unaffected and stays (a separate, pre-existing/
  intentional decision under ADR-027 Phase 5).

### Documentation

- Synced `abicheck/model/change_catalog/source.py`'s `public_surface_grew`/
  `public_surface_shrank`/`undocumented_export_ratio_increased` impact text
  and `docs/learn/surface-growth.md` to describe `--surface-metrics`
  computation as unconditional (matching `docs/reference/cli-reference.md`,
  which already said so) instead of "emitted only with `--surface-metrics`".
