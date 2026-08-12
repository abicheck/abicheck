### Fixed

- **Vtable-evidence ownership matching now uses one normalized identity for
  both snapshot sides** — the `layout_unverifiable` ↔ `type_vtable_changed`
  correlation's owned-virtual-signature check used to derive each side's
  matching identity independently from its own `RecordType.qualified_name`;
  a legacy snapshot leaving that field unset on one side while a fresher
  snapshot sets it on the other (for the same, already-matched type) could
  permanently mismatch an unchanged virtual method between the two sides
  and silently withhold the correlation. Both sides now match against one
  shared, normalized qualified identity.

