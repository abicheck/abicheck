### Fixed

- **A public inline function's static local is no longer an undeclared export** —
  clang exports a header inline function's static local (`_ZZ8edit_ctrvE1n`) and
  its guard weak; `exported_not_public` counted it as an undeclared export and the
  addition read as a risk, though no source can name it. An Itanium local entity
  whose owning function a public header declares is now accounted as
  `public_function_local_entity` (documented), and the `*_added_elf_only` wording
  names its owner. A local of a function no public header declares is still
  reported.
