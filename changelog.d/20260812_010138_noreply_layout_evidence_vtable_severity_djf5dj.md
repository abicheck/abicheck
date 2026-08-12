### Changed

- **Compare report schema bumped to 2.28** — `correlated_change_kind` now
  has a second producer: a `layout_unverifiable` finding that shares the
  identical asymmetric-layout-evidence gap as a co-reported
  `type_vtable_changed` finding on the same type is annotated with it
  (previously the field was documented as exclusive to
  `public_api_internal_dependency_added`). Purely additive — the field's
  shape is unchanged, and a consumer already treating the value as an
  opaque `ChangeKind` slug needs no change.
