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
  it recorded a confirmed, possibly empty, vtable. The vtable detectors
  decline on a `partial` fact and record why, instead of reading "no
  vtable". The extractor generation is now 40.
