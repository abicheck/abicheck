### Fixed

- **An `-O2` rebuild of an old-ABI `std::string` library no longer reads as a risk** — under
  `-D_GLIBCXX_USE_CXX11_ABI=0` at C++20 and later, the weak `std::less<const char*>::operator()`
  copy the `-O0` build exported was attributed to the library, so its disappearance kept
  `symbol_leaked_from_dependency_changed`'s RISK. A standard customization-point specialization is
  now the library's only when a program-defined type appears in its name; one over builtins,
  their pointers, references and cv-qualifications, or standard types is the implementation's
  and coalesces under `coalesced-dependency-copy`. The symbol-hygiene audit uses the same owner.
