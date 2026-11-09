### Fixed

- **A C++ parameter-list change reads `func_params_changed` with headers too,
  and a removed virtual reports `func_virtual_removed`** — with headers on
  both sides, rewriting the signature of the one function by that name
  (`int add(int, int)` -> `int add(int, int, int)`, or `int` -> `long`) in the
  same header now pairs its old and new mangled names into one
  `func_params_changed`, as the binary-only and DWARF lanes already did
  (it read `func_removed` + `func_added`). An overloaded name, a moved
  declaration, a changed return type, or a parameter retyped to a record the
  old release never declared (catalog case189) still reads as a removal. A
  virtual method removed together with its vtable slot now also reports
  `func_virtual_removed` beside its `func_removed` (header and DWARF lanes);
  removing an override, whose slot stays, does not. Verdicts are unchanged.
