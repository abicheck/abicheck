### Fixed

- **An uninstantiated C++ template is no longer mistaken for a C-linkage
  declaration.** Clang emits no `mangledName` at all for an uninstantiated
  function template, an uninstantiated class-template method, or a
  class-template pattern's static member, so the direct-clang backend's
  `mangled` value fell back to the bare declaration name — which the
  long-standing `mangled == name` heuristic read as `extern "C"`. Two such
  declarations sharing a leaf name in different namespaces were therefore
  reported as C-linkage and given the same scope-free identity. The heuristic
  now requires that clang actually emitted a mangled name (verified against
  real `clang -x c` and `extern "C"` output, both of which do emit one), so a
  mangling-free template keeps its namespace and signature.
