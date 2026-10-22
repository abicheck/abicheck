### Added

- **Built-in `append_only_dispatch_tables` policy** (#1709) — `--policy
  append_only_dispatch_tables` accepts a member appended at the end of a
  Level Zero (`ze_*_dditable_t` and `zet_`/`zes_`/`zer_`/`zel_` siblings),
  Vulkan (`VkLayerDispatchTable`, `VkLayerInstanceDispatchTable`) or OpenCL
  ICD (`cl_icd_dispatch`) dispatch table, and the size growth it causes, as
  compatible. Removing, reordering, inserting or retyping an existing member
  still breaks, and other types are unaffected. It uses selector-scoped
  `reclassify:` rules, so each finding stays in the report with
  `reclassified_by` and a `disposition_audit` row. Copy the block and change
  `type_pattern` to cover your own types.
