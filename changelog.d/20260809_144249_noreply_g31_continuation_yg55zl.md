<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **The direct-clang (`--ast-frontend clang`) L2 header backend now
  populates `deprecated` (on functions, variables, fields, types, and
  enums), `EnumType.is_scoped`, and `RecordType.is_standard_layout`/
  `is_trivially_copyable`** — previously castxml-only (or, for the last
  two, populated by neither backend), which left the `FUNC_DEPRECATED_*`/
  `VAR_DEPRECATED_*`/`FIELD_DEPRECATED_*`/`TYPE_DEPRECATED_*`/
  `ENUM_DEPRECATED_*`, `ENUM_BECAME_SCOPED`/`ENUM_LOST_SCOPED`, and
  `STANDARD_LAYOUT_LOST`/`TRIVIALLY_COPYABLE_LOST` detectors silently dead
  on a `--ast-frontend clang` run. A castxml-vs-clang or clang-vs-clang
  comparison for `deprecated`/`is_scoped` now correctly detects a real
  transition instead of being unconditionally skipped as a producer
  mismatch.
- **Fixed two follow-on gaps in the same-named work above**: a declaration
  present on both snapshot sides only via the clang leg of a
  `--ast-frontend hybrid` merge now gets its `deprecated`/`is_scoped`
  provenance recorded (previously it silently fell back to "unknown
  producer" and the comparison was skipped even though the value was
  genuinely known); and `RecordType`/`EnumType` matching in
  `dumper_hybrid.merge_snapshots` and the class-layout descriptor detector
  (`STANDARD_LAYOUT_LOST`/`TRIVIALLY_COPYABLE_LOST`/`BASE_CLASS_OFFSET_CHANGED`/
  `VPTR_INTRODUCED`/`TAIL_PADDING_REUSE_CHANGED`) now uses namespace-qualified
  identity instead of the bare declaration name, so two distinct types
  sharing only a bare leaf name in different namespaces (e.g. `a::Foo` /
  `b::Foo`) no longer collide.
- **Snapshot schema bumped to v19** to gate the clang-side `deprecated`/
  `is_scoped` extraction above: a snapshot serialized on the
  `--ast-frontend clang` header path under an older schema version never
  actually extracted these two facts, so reloading it now correctly marks
  them unreliable (`AbiSnapshot.clang_deprecation_facts_reliable`,
  mirroring `header_cv_facts_reliable`'s existing v9 pattern) instead of
  treating a stale `None` as a trustworthy "not deprecated"/"not scoped"
  answer.
- **Fixed a follow-on collision in the same-named provenance-recording fix
  above**: `dumper_hybrid.merge_snapshots`'s per-fact provenance dict now
  keys `type`/`field`/`enum` `deprecated` and `enum` `is_scoped` by
  namespace-qualified identity rather than bare declaration name, so two
  distinct types sharing only a bare leaf name in different namespaces
  (e.g. `a::Foo` / `b::Foo`) no longer silently overwrite each other's
  provenance entry in the shared dict — independent of, and one layer
  below, the old/new matching fix above.
- **Fixed a backward-compatibility regression the qualification fix above
  introduced**: a `--ast-frontend hybrid` baseline persisted before that
  fix has real `deprecated`/`is_scoped` provenance recorded under the
  former bare key, and qualifying only the lookup key silently suppressed
  every genuine transition on such a baseline. `diff_types.py`'s four
  affected detectors now fall back to the bare key when it's unambiguous
  (`diff_helpers.fact_known_qualified`, mirroring `lookup_matched_type`'s
  own bare-name-retry pattern), so a real transition on an existing
  hybrid baseline is detected again without reopening the bare-name
  collision the qualification fix closed.
- **Fixed the bare-key fallback above only checking one side's qualified
  identity**: a matched old/new pair can have different qualified
  identities (e.g. a legacy `old` snapshot predating `qualified_name`
  entirely vs. a freshly-merged, real-namespaced `new` snapshot), so
  probing both sides with only `old`'s qualified key could never find
  `new`'s real, qualified-keyed provenance entry. `fact_known_qualified`
  now derives and probes each side's own qualified key independently.
- **Fixed a phantom `LAYOUT_UNVERIFIABLE` finding on every record when
  comparing a persisted pre-v19 direct-clang snapshot against a fresh
  dump of unchanged headers**: `diff_layout._has_layout_descriptor()`
  no longer counts `RecordType.is_standard_layout`/`is_trivially_copyable`
  as layout-descriptor evidence — those are semantic traits the
  direct-clang backend now populates independent of any real layout pass
  (G31 Phase C), not the size/offset evidence `LAYOUT_UNVERIFIABLE` is
  actually about, and a `None`→real-value flip on those two facts alone
  was tripping the detector's asymmetric-evidence gate on every record.
  `STANDARD_LAYOUT_LOST`/`TRIVIALLY_COPYABLE_LOST` themselves were
  unaffected — they already correctly stay silent when the old side's
  trait is unknown.

