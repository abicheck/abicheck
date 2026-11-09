### Fixed

- **`visibility_leak` no longer flags a public C++ function for the types it
  takes.** In a binary-only comparison, the "internal-looking export" check
  read the whole mangled name, so `greet(std::string const&)` counted as
  internal: libstdc++'s `std::__cxx11` namespace sits in every `std::string`
  parameter. The same went for any parameter or template argument from a
  `detail::`/`internal::` namespace. The check now reads only the entity's
  own scope and name. The weak `__gnu_cxx::`, `__gnu_debug::` and
  `__cxxabiv1::` instantiations a C++ library exports from the runtime's
  headers are now treated as transitive runtime symbols in their mangled
  spelling too (`_ZN9__gnu_cxx…`), the way `std::` members already were. A
  library that only exports `std::string` APIs no longer reads `COMPATIBLE`
  instead of `NO_CHANGE` against itself.
