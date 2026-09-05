### Changed

- **Split the calibration catalog out of `examples/`** — the 197
  `caseNN_*` compatibility fixtures, `ground_truth.json`, `CMakeLists.txt`,
  `probes/`, and `catalog_rules.yaml` now live under `catalog/` (Phase 4 of
  the [examples/catalog split](docs/contribute/plans/examples-catalog-split.md)),
  leaving `examples/` for the curated, task-oriented workflow walkthroughs.
  Every case's contents, its ground-truth entry, and every gate that scores
  it are unchanged — this is a physical relocation, not a change in
  behavior, verdicts, or the public CLI/API/Action surface.
