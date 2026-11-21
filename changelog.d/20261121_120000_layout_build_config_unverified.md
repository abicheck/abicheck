### Fixed

- **Stripped binary + headers no longer reads a padding-dependent layout
  change as clean** (ADR-095). When neither side has DWARF, build data or a
  layout switch in the header parse, a field added into padding (record size
  unchanged) or an unfixed enum whose `-fshort-enums` width changed now emits
  `layout_build_config_unverified` (`COMPATIBLE_WITH_RISK`): a library built
  with `-fpack-struct` or `-fshort-enums` breaks there, and the headers cannot
  say which build they describe. Exit code unchanged. Owner:
  `abicheck/compare/layout_build_config.py`.
