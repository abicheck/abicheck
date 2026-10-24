### Fixed

- **A baseline dumped by an older abicheck no longer reports a false break from
  a renamed type spelling.** Single-file snapshots now record the extractor,
  resolver and source generations that produced them (`fact_generations`; a
  snapshot without it reads as unstated, never current). On load the stamp goes
  through the same reader-compatibility check stored packages use, and a drift
  or an unstated generation is reported in `coverage_warnings`. Spellings an
  older extractor generation wrote are migrated on load, and each migration is
  reported: castxml's undeduced `AutoType` (before generation 6) now reads as
  `auto` in return, parameter, field, variable, typedef and template-argument
  spellings, so `AutoType&` against a fresh `auto&` is no longer
  `func_return_changed` plus `unspecified_return_now_named`.
