### Fixed

- **`mandatory_template_param_added` fires for class templates modelled as
  their pattern** — the clang and gcc-plugin header backends record a class
  template as its pattern (`KMb`, `is_template_pattern`) with its parameter
  defaults, not as a spelled `KMb<int>` instantiation, so the arity the
  detector counts from spelled template arguments was never observed and
  `template <class T>` -> `template <class T, class A>` read as a bare
  function re-mangling. The mandatory arity is now also read from the
  pattern's recorded defaults (parameters with no default). A parameter pack
  is not marked in that fact, so appending one reads as a mandatory argument.
