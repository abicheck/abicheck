### Fixed

- `overload_set_rerouted` now fires at binary-only evidence: export-table
  overloads, which carry no parameter list, are told apart by the parameter
  list their mangling encodes, so `kput(int)` -> `kput(long)` beside
  `kput(double)` is detected from symbols alone.
