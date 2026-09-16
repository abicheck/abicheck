### Fixed

- Adding public headers to a comparison no longer erases a public binary-object
  loss. A class's compiler-emitted ABI-support objects (`_ZTV`/`_ZTI`/`_ZTS`/
  `_ZTT`) and any other undeclared data export were absent from the
  header-derived variable map on both sides, so no detector could see them
  disappear: the same two binaries compared BREAKING (exit 4) without `-H` and
  NO_CHANGE (exit 0) with it, while an old consumer built against the first
  library crashed against the second. `compare/undeclared_exports.py` now
  answers both directions from the export tables the snapshots already carry —
  no re-extraction and no second comparison — under the new
  `var_removed_elf_only` kind, with relevance left to public-surface scoping,
  contract evaluation and policy rather than re-decided locally.
- A header-defined function or variable template no longer acquires a false
  `public_not_exported` export obligation. The check asked whether the
  *display* name looked templated, and a header backend can report the bare
  name `is_specified` for an entity mangled `_Z12is_specifiedI12OptionalBoolEbT_`;
  template-ness is now read structurally out of the Itanium mangling
  (`model/mangled_name.itanium_name_carries_template_arguments`), with the
  display-name check kept only as the fallback for spellings that parser cannot
  model. Declaration-only specializations that really do require a
  library-provided definition are unaffected.
