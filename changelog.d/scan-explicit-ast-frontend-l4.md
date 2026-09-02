### Fixed

- `scan` now honors an explicitly named L4 backend (`--ast-frontend castxml` /
  `clang`, or the same value from a config `compile.frontend`) when replaying
  the L4 source-ABI layer. It previously accepted the value, folded it into the
  compile context exactly as `dump`/`compare` do, and then ignored it — the
  candidate's L4 replay always ran through clang. A `scan --against` candidate
  therefore disagreed with a `dump` baseline taken with the identical flag,
  reporting a spurious `COMPATIBLE_WITH_RISK`
  (`source_fact_coverage_incomplete`) verdict on completely unchanged source.
  A named backend now resolves through the same primitive `dump`/`compare`
  use, so both sides select it.

  `auto` is deliberately **not** covered, whether it was typed or defaulted:
  `scan` still resolves it to clang while `dump`/`compare` resolve it to
  castxml, so `--ast-frontend auto` on both sides still reproduces that
  spurious verdict. Closing that would newly require castxml for a plain
  `scan --depth source` that works with clang today — a default change to make
  deliberately, tracked as item 2 of the CLI-cleanup phase-two plan's PR 3A.
