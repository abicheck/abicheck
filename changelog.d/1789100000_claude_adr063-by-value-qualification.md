### Fixed

- **Opaque-type by-value exposure detection now recognizes a bare spelling
  against a qualified `RecordType.name`.** `find_by_value_types`'s
  substring scan compared a signature's rendered type text only against
  the opaque type's exact (possibly qualified, e.g. `ns::Handle`) name.
  When a public function/variable's signature rendered the same type bare
  (`Handle`), the scan missed the by-value exposure and the type wrongly
  stayed in the opaque set. That gap used to be silently compensated by
  `diff_filtering`'s own equally spelling-based suppression join failing
  for the identical reason — but once `OpaqueTypeIndex`'s stable identity
  tier can reliably join the two sides across that same qualification
  mismatch, the missed exposure turned into a real, silent false-negative
  suppression of a genuine layout-change finding (Codex review on
  PR #1041). The scan now also tries the type's unqualified leaf spelling
  alongside the full one.
