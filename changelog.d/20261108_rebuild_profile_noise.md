### Fixed

- **An `-O0` -> `-O2` rebuild of an unchanged C++ library no longer reads as a break or a risk** — weak (`WEAK`/`GNU_UNIQUE`) copies of `std::`/`__gnu_cxx::` templates that one build exports and the other inlines are now demoted to `COMPATIBLE` with rule `coalesced-dependency-copy` (ADR-089 A7), and a new import bound to a version node the old binary already required (`GLIBCXX_3.4` when it needed `GLIBCXX_3.4.21`) is demoted with rule `import-covered-by-required-version`. Both stay in the report. A removal of the library's own export still breaks.
- **clang `std::string` returns no longer report `struct_return_convention_changed` after a rebuild** — a by-value aggregate whose DWARF is declaration-only (clang's type homing at `-O0`) now has unknown triviality instead of "trivial", and a type's own `DW_AT_calling_convention` is used when present. Extractor generation 44; re-dump stored baselines to pick it up.
- **`std::char_traits<char>` and other standard specializations over builtin types are attributed to the C++ runtime**, not to the library as a user customization point.

### Changed

- **A `not_comparable` refusal caused by differing macros names them** — the `profile_mismatch` reason lists the macro operations only one side's header parse ran and, for `_GLIBCXX_USE_CXX11_ABI`, explains that the rebuild renames every `std::string`/`std::list` symbol and how to see those removals. The refusal itself and exit `16` are unchanged (ADR-050).
