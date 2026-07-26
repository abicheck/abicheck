### Fixed

- **`sycl_metadata.py` recognizes the current Unified Runtime (UR) adapter
  export shape.** Real, currently-shipping Intel oneAPI UR adapters (2026.1,
  verified during the G30 pilot validation,
  `validation/g30-pilot-validation-2026-07.md`) export only
  `urGet<Category>ProcAddrTable` function-pointer-table getters
  (`urGetAdapterProcAddrTable`, `urGetPlatformProcAddrTable`, ...) — never
  the older per-verb symbols (`urAdapterGet`, `urPlatformGet`, ...) this
  module's plugin-validity check previously required. A real, valid,
  current-generation UR adapter was silently rejected as "not a valid UR
  adapter". `parse_sycl_plugin()` now accepts either UR generation;
  `_detect_ur_version_from_symbols()` correctly reports "" (honestly
  unknown) for the table-getter generation rather than risk a false landmark
  match.
