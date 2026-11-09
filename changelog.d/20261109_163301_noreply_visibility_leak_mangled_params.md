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
- **A local class or static of a libstdc++ function is the runtime's, not a
  user specialization.** The `std::` customization-point check let its
  inline-namespace match run on into template arguments, so
  `std::__cxx11::basic_string<char, std::char_traits<char>>::_M_construct()::_Guard`
  read as a user `char_traits` specialization. Such locals are now owned by
  the runtime (the alignment-only exemption and `visibility_leak` both see
  it), while a real `std::hash<MyType>` specialization stays the library's.
  A local entity of `mylib::detail::run()` and an exported
  `operator""_impl` are still reported by `visibility_leak`, and
  `__gnu_debug::` RTTI is filtered like `__gnu_cxx::`'s.
