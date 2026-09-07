<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`compare` now detects private-header leaks, with a real evolution
  state** — the `private_header_leak` cross-source check (previously
  `scan`-only) now runs per side inside `compare`'s own pipeline (ADR-068
  D3, `docs/contribute/plans/one-comparison-product.md` §5 P2 / §6 Phase
  2a), reusing the `FindingEvolution` vocabulary
  (`introduced`/`resolved`/`persistent`/`not_evaluated`) ADR-068 Phase 1
  item 2 already added to the canonical finding model. Its own findings now
  carry a real, non-default value, visible in the existing top-level
  `finding_evolution.counts` report block: a leak present in both releases
  but invisible on a baseline lacking header evidence reads as
  `not_evaluated`, never as a manufactured `introduced` finding.
  `private_header_leak` stays `RISK` regardless of its evolution state —
  this never promotes a finding to `BREAKING`.
