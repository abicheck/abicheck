### Fixed

- **A changed alignment specifier on an exported variable is no longer read
  as `NO_CHANGE`.** castxml writes no `align` on a variable, so the castxml
  header backend recorded `int`'s natural alignment as the *declared* one
  and a dropped `__attribute__((aligned(64)))` vetoed the reduction the
  binary showed. It now records that value as a lower bound wherever the
  header (or a typedef's header) spells an alignment specifier: the
  reduction is reported, demoted with the new rule
  `address-alignment-with-unknown-declared-requirement`. Under DWARF 5 a
  variable without `DW_AT_alignment` now records its type's default
  alignment, so raising a header's specifier reports `var_alignment_changed`
  (a GCC declaration/definition split records it as a lower bound, since GCC
  drops a definition-only specifier). Extractor generation 51; re-dump
  stored baselines to pick it up.
