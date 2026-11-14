### Fixed

- **castxml no longer recovers a C++ declaration as C linkage when its
  export is lost.** The extern-C recovery (a guessed C++ mangling not exported
  while the bare name is) now applies only to a header that could be a C
  header parsed as C++. In a header that declares C++-only constructs, or
  whose sibling declarations are exported under their C++ manglings, a
  declaration whose `_Z...` export disappeared while a bare C namesake
  appeared reads as `func_deleted_elf_fallback`, as with the clang and GCC
  backends, not `func_language_linkage_changed`.
