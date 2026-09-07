<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`compare` now detects private-header leaks, with an evolution state** —
  the `private_header_leak` cross-source check (previously `scan`-only) now
  runs per side inside `compare`'s own pipeline (ADR-068 D3,
  `docs/contribute/plans/one-comparison-product.md` §5 P2). Each finding
  carries a new `evolution` field (`introduced`/`resolved`/`persistent`/
  `not_evaluated`, report schema 3.5): a leak present in both releases but
  invisible on a baseline lacking header evidence reads as `not_evaluated`,
  never as a manufactured `introduced` finding. `private_header_leak` stays
  `RISK` regardless of its evolution state — this never promotes a finding
  to `BREAKING`.
