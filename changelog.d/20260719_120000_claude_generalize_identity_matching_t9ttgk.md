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
  `qualified_name`. Two more gaps in the same area are fixed: (1)
  `EnumType.qualified_name` is now deserialized (`serialization.py`'s
  `_enum_type_from_dict` serialized but never read it back), so a save/load
  round trip no longer silently loses an enum's namespace identity; (2) a
  castxml constructor whose real mangled name is omitted gets a synthesized
  snapshot key (`SYNTHETIC_CTOR_KEY_PREFIX + "scope(params)"`) that isn't
  Itanium-mangled, so `owner_class_of` couldn't parse it and fell back to the
  bare class name — dropping such namespaced constructors from
  `CTOR_OVERLOAD_AMBIGUITY_RISK` even between two fully fresh snapshots;
  `_diff_ctor_overload_ambiguity` now recovers the scope directly from the
  synthetic key's own encoding. (3) A doubly-legacy mix (neither side's
  RecordType carries `qualified_name`) left `common_classes` holding only
  the bare leaf, while a real Itanium-mangled constructor's owner is always
  fully qualified regardless of `RecordType`'s own schema — the class-
  membership check now also accepts the bare leaf of a fully-qualified
  owner when the qualified form isn't present. (4) `_diff_ctor_overload_ambiguity`
  now groups constructors by a normalized *canonical* class identity
  (`_class_identity_aliases`) instead of the raw owner spelling: a persisted
  snapshot from before synthetic ctor keys were namespace-qualified
  (`__abicheck_ctor__Widget(...)`) compared against a fresh one
  (`__abicheck_ctor__ns::Widget(...)`) previously grouped the SAME class's
  unchanged overloads under two different keys, fabricating a
  `CTOR_OVERLOAD_AMBIGUITY_RISK` for every one of them.
