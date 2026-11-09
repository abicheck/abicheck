### Fixed

- **DWARF-only comparisons see array bounds and C++ member functions** — a
  DWARF array member now spells each dimension's bound (`char[8]`,
  `int[2][3]`; was `char[]` for every array), so `char tag[8]` becoming
  `char tag[6]` reports `type_field_type_changed` even when the record's size
  does not move (it read `NO_CHANGE`). A DWARF-only snapshot now records C++
  member functions and namespace-scope functions defined outside their
  declaration (`DW_AT_specification`), with their `const`/`volatile`/ref
  qualifiers, static-ness, virtuality, access and return type (read from the
  declaration when the definition does not repeat it), so a dropped `const` reads
  `func_cv_changed` and a clang-built virtual that became pure reads
  `func_virtual_became_pure` instead of an ELF symbol removal plus addition.
  Extractor generation 45: a stored pre-45 DWARF baseline's `T[]` against a
  fresh `T[N]` (DWARF-sourced, not PDB or headers) is demoted to
  `COMPATIBLE_WITH_RISK` as extractor drift (re-dump
  the baseline). A GCC-built pure virtual is still read as a removal (GCC
  writes no pure marker in DWARF; see `docs/contribute/known-gaps.md`).
