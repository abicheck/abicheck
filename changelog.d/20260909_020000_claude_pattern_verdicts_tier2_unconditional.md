<!--
A new scriv changelog fragment.

Uncomment the section that is right (remove the HTML comment wrapper).
-->
### Fixed

- The shared Tier-2 comparison chokepoint (`workflows.compare_policy.
  compare_snapshots` / `service.compare_snapshots`, `service_compare_
  pipeline.classify_compare_pair`, and the stored-BundleFacts comparison
  drivers) briefly forced `pattern_verdicts=True` unconditionally, citing
  ADR-068 D4's "no legitimate off position" principle. That citation did
  not hold: ADR-068 is "Proposed — not implemented", not an accepted
  decision, while the accepted ADR-027 explicitly defers flipping
  `--pattern-verdicts` to default-on pending a release cycle's worth of
  FP-rate and parity validation. It also silently broke `scan --against`'s
  own working, documented `--pattern-verdicts/--no-pattern-verdicts` flag
  (default off) by overriding it regardless of what was passed. Reverted:
  every one of these call sites forwards its caller's own `pattern_verdicts`
  value again, restoring the accepted opt-in default everywhere.
