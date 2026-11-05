### Fixed

- **A base class written relative to its scope resolves on clang 22+** —
  `class svm_algorithm : public detail::algorithm_iface` inside
  `namespace mylib` now yields the same vtable on clang 23 as on clang 18.
  clang 22 stopped writing the fully qualified `desugaredQualType` on such
  a base specifier, and on an alias base that field is now relative to the
  alias's scope. The clang header backend now resolves each base by C++
  name lookup from the scope that wrote it, and accepts only an exact
  record match. A class whose base (at any depth) still cannot be resolved,
  such as a dependent base of a class template or an uninstantiated
  `std::vector<int>`, now records its `vtable` fact as `partial`. Before,
  it recorded a confirmed, possibly empty, vtable. The vtable diff
  detectors (`type_vtable_changed`, `vptr_introduced`,
  `virtual_method_added`) decline on a `partial` fact and record why,
  instead of reading "no vtable". A clang or hybrid snapshot stored by an
  older generation gets the same treatment on load: a confirmed vtable on a
  record with base classes reads as `partial`. Comparing such a baseline
  with a fresh dump therefore no longer reports a false `vptr_introduced`
  break. The extractor generation is now 40.
