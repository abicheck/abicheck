### Fixed

- **`undocumented_export_ratio_increased` can be reported again.** The
  undocumented-export metric counted `EXPORT_ONLY` declarations, but a header
  dump makes no declaration for an export no header declares, so on every
  real header-backed snapshot the count was zero and the finding never fired.
  The metric now accounts the export table the way `exported_not_public` does
  (a public declaration, a C++ compiler artifact, an allocator interposer or a
  public template instantiation counts as documented); a snapshot with no
  export table or no header provenance keeps the declaration count.
