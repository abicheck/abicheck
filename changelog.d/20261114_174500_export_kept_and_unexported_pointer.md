### Fixed

- A function removed from the public headers while the library still
  exports it is still reported as `func_removed`, but graded `API_BREAK`
  instead of `BREAKING` when the new export table was read and lists the
  symbol: built consumers keep binding it, only source that names it stops
  compiling. Modulation reason `declaration_removed_export_kept`.
- `param_pointer_level_changed` and `return_pointer_level_changed` on a
  function that neither side exports (a `static inline` header function) are
  graded `API_BREAK`, matching `header_only_function_params_changed` for the
  same function. Modulation reason `declaration_never_exported`.
