<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`private_header_leak` migrated onto `CrossSourceEvolution` too** — the
  second cross-source hygiene check (ADR-068 D3, plan §5 P2 / §3 #3-#4,
  `docs/contribute/plans/one-comparison-product.md`) now runs per side
  inside `compare`'s opt-in `cross_source_checks=True` pipeline alongside
  `unversioned_exported_symbol`, via
  `workflows.cross_source_evolution.compute_cross_source_evolution`. A leak
  present in both releases but invisible on a baseline lacking header
  evidence reads as `not_evaluated`, never a manufactured `introduced`
  finding; `private_header_leak` stays `RISK` regardless of its evolution
  state, same as before.

### Fixed

- **`cross_source_evolution`'s per-finding identity now handles a check
  whose findings aren't uniquely keyed by symbol alone** — pairing OLD and
  NEW findings by a bare `Change.symbol` silently collided two distinct
  findings sharing one symbol (`private_header_leak`: a single function can
  leak more than one distinct private type). `_run_one_side` now looks up a
  per-check identity function (`_IDENTITY_FUNCS`), defaulting to `symbol`
  for checks where that remains correct, with `private_header_leak`
  registering `(symbol, new_value)` instead.
