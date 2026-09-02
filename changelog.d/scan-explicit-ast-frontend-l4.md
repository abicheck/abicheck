### Fixed

- `scan` now honors its own `--ast-frontend` (and a config `compile.frontend`)
  when replaying the L4 source-ABI layer. It previously accepted the value,
  folded it into the compile context exactly as `dump`/`compare` do, and then
  ignored it — the candidate's L4 replay always ran through clang. A
  `scan --against` candidate therefore disagreed with a `dump` baseline taken
  with the identical flag, reporting a spurious `COMPATIBLE_WITH_RISK`
  (`source_fact_coverage_incomplete`) verdict on completely unchanged source.
  An explicit request now resolves through the same primitive `dump`/`compare`
  use, so both sides select the same backend. The *unflagged* default is
  deliberately unchanged: `scan` still resolves an unstated `auto` to clang, so
  no invocation newly requires castxml.
