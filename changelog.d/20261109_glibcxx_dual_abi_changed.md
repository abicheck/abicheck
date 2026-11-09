### Changed

- **A libstdc++ dual-ABI rebuild is reported as an ABI break, not refused.**
  Rebuilding a library with `-D_GLIBCXX_USE_CXX11_ABI=0` (or back to `=1`)
  and dumping each side with its own `-D` used to stop `compare` with exit 16
  (`profile_mismatch`). A header-parse difference confined to that macro is
  now compared, and the flip is reported as the new `glibcxx_dual_abi_changed`
  (BREAKING, exit 4) when the public surface uses `std::string`/`std::list`:
  an export re-mangled into its other-ABI twin, an exported variable of such a
  type, or a public record holding one. Each side's selection (header `-D`,
  `DW_AT_producer`, or mangled names) and the affected entities are named in
  the finding, in every report format; the per-entity `func_removed`,
  `func_params_changed` and `type_size_changed` findings stay beside it. A
  flip that reaches nothing is recorded as `toolchain_flag_drift` and does not
  gate. Another differing macro or profile field still refuses (ADR-050
  amendment 2026-11-09).
