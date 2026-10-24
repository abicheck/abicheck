### Fixed

- **A header baseline dumped before extractor generation 15 no longer reports
  every array as a breaking type change** — castxml used to spell
  `double items[2]` as `double[1]` (its last index); generation 15 spells the
  element count. Loading an older castxml snapshot now rewrites those stored
  array counts by provenance and says so in a load notice, so it compares
  clean against a fresh dump of the same headers. The frozen extractor-generation
  corpus also gains its generation-14 snapshot.
