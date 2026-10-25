### Fixed

- **A baseline dumped before extractor generation 15 no longer reports every
  header-path array field as a breaking type change** — generation 15 (#1791)
  spells a castxml array extent as its element count instead of its last
  index (`double[1]` became `double[2]`). The stored spelling cannot be
  rewritten soundly on load, because a snapshot does not record which backend
  wrote each extent. The generation-15 `SPELLING_MIGRATIONS` row now declares
  that growth (`extent_shift=1`). A finding whose old and new spellings differ
  only by it, between operands that straddle generation 15, is demoted to
  `COMPATIBLE_WITH_RISK` with the reason "extractor-generation drift; re-dump
  baseline". The finding stays in the report. Generation 14 is now frozen in
  `tests/fixtures/generations/`.
