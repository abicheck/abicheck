### Documentation

- **`docs/integration/scenarios/` batch 4 -- the final scenario batch (G30
  P1.7, ADR-047 §8)** — seven more scenario pages: single-build audit (S5),
  multiple build profiles (S17), cross compilation (S18), application &
  plugin contracts (S22/S23), dependency & container checks (S24), monorepo
  (S25), and migration & rollout (S26/S27). All 15 scenario pages the plan's
  file tree names are now in place; `docs/integration/index.md`'s remaining
  rows point at them. The plan's originally-listed `baselines/`/`reference/`
  page sub-trees are not being built separately — see the plan's own P1.7
  status note for why (they're superseded by `docs/reference/*.md` pages
  that shipped in G30 P1.1-P1.6).
