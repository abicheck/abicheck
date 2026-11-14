### Fixed

- **`overload_set_rerouted` and `instantiation_missing_from_binary` now fire
  for header-backend snapshots** — a top-level C++ function is named by its
  bare identifier (`kput`, `ktwice`), so its qualified name comes from the
  demangling, which carries the parameter list and, for a function-template
  instantiation, a leading return type that follows the template argument.
  The overload detector keyed each overload on its whole signature
  (`kput(int)`), so no set ever had two members; the instantiation detector
  kept the return type in the template's stem (`double ktwice` vs `int
  ktwice`), so a dropped `ktwice<double>` never matched its surviving
  `ktwice<int>` family. Both now key on the name stem the CPO detector uses.
