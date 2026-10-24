### Fixed

- **`--policy append_only_dispatch_tables` now accepts Level Zero v1.33→v1.34**
  (issue #1759). The profile's type pattern also matches
  `_zes_dditable_driver_t`. A `type_field_offset_changed` inside an aggregate
  that embeds dispatch tables by value (`_ze_dditable_t`, `_zes_dditable_t`) is
  reclassified too, but only when the compare records that the shift is exactly
  the size growth of earlier embedded members whose types also match. A
  reorder, insertion, removal, retype or padding change stays `BREAKING`. The
  finding stays in the report with its cause in `review_evidence.offset_shift_cause`.
  Policy files gain the conjunctive-only `reclassify` selector
  `shifted_by_type_pattern`.
