### Fixed

- **The same-leaf-name matching bug found in the pvxs acceptance spike is
  now generalized, not just patched at the one site it was first found.**
  `diff_symbols.py`'s virtual-method-owner resolution, constructor-overload-
  ambiguity grouping, field-access-level-change, and anonymous-field-change
  detectors now build their old/new `RecordType` comparison maps through
  `diff_helpers.build_type_map`/`lookup_matched_type` (the same ambiguity-
  safe, namespace-qualified matching `diff_types.py`'s `RecordType`
  detectors already used) instead of a bare-name `{t.name: t for t in ...}`
  dict, closing the identical cross-attribution risk in those four detectors.
  `EnumType` gained a `qualified_name` field (populated by both the castxml
  and clang header dumpers, mirroring `RecordType.qualified_name`), and
  `diff_types.py`'s `_diff_enums`/`_diff_enum_renames`/
  `_diff_enum_deprecated` now match old/new enums the same ambiguity-safe
  way — two distinct enums sharing a bare leaf name in different namespaces
  no longer risk being cross-matched, missed, or misattributed. `TypeMap`/
  `type_map_key`/`lookup_matched_type` in `diff_helpers.py` are now generic
  over any entity with the `name`/`qualified_name` shape, so `RecordType`
  and `EnumType` share one implementation instead of duplicating it. A new
  Hypothesis property
  (`test_same_leaf_name_matching_is_order_independent`/
  `test_same_leaf_name_enum_matching_is_order_independent` in
  `tests/test_detector_properties.py`) generates same-bare-name entity pairs
  under randomized snapshot insertion order and asserts the emitted diff is
  order-independent, generalizing regression coverage for this bug class
  across whichever detector happens to be affected rather than requiring a
  hand-written scenario per detector. See ADR-045 for the underlying
  principle ("identity-based old/new entity matching") this codifies.
  `_diff_ctor_overload_ambiguity`'s class-existence filter also now uses
  `lookup_matched_type`'s ambiguity-safe matching instead of a raw canonical-
  key set intersection, fixing a schema-evolution mix (a legacy snapshot
  missing `RecordType.qualified_name` on one side) that previously dropped a
  namespaced class's constructors from `CTOR_OVERLOAD_AMBIGUITY_RISK`
  entirely, since `owner_class_of` derives the real qualified owner from the
  constructor's mangled symbol regardless of which side's `RecordType` lacks
  `qualified_name`.
