### Fixed

- **Vtable/layout-evidence correlation now scopes ownership by qualified
  identity, and hides "See also" notes filtered out by `--show-only`** —
  the `layout_unverifiable` ↔ `type_vtable_changed` correlation
  (`Change.correlated_change_kind`) could silently fail to attach when the
  matched type shared a bare leaf name with an unrelated record in another
  namespace (its owned-virtual-signature matching used to be
  suffix-based); it now matches on the exact qualified owner. Separately,
  `--show-only` filtering in the default/leaf/root-cause markdown views no
  longer renders a "See also" note pointing at a finding the filter itself
  excluded from the report.

