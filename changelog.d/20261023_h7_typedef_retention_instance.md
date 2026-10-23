### Fixed

- Dump-time dependency scoping now also retains a system/dependency typedef
  (`size_t`, `ptrdiff_t`, `uint32_t`, `std::string`, ...) when the public
  header text spells it, not only when a kept declaration's signature names
  it. Previously a typedef reached only through a member template's implicit
  instance (e.g. `setArrays<double>(double*, size_t)`) was kept while an
  inline body instantiated it and dropped once it stopped, so `compare`
  reported a spurious `typedef_removed size_t` at `--depth headers`/`source`.
