### Fixed

- **`append_only_dispatch_tables` now matches Level Zero names as DWARF reports
  them** — the `type_pattern` accepts the struct-tag spelling with a leading
  `_` (`_ze_device_dditable_t`) and the top-level aggregates `ze_dditable_t`/
  `zes_dditable_t`. Before, the profile matched no finding on a real Level
  Zero comparison. Offset shifts inside the aggregates caused by an embedded
  sub-table growing are still `BREAKING` (recorded in known gaps).
