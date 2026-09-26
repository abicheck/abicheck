### Fixed

- A header-only `dump` (no binary), `compat dump`/`compat check` and
  `appcompat` now record target ownership (ADR-075) on the snapshots they
  extract, like every other entry point. A snapshot without recorded
  ownership (pre-v52) says so in the `public_not_exported` coverage row.
