### Fixed

- **A callback's signature change is reported through headers** — castxml
  function types now spell their full declarator (`int (*)(int, int)`)
  instead of the opaque `FunctionType` tag, so a function-pointer parameter,
  a pointer to one, a callback typedef, a struct field or a returned
  callback that changes arity, a parameter or return type, variadic-ness or
  calling convention reports `func_params_changed`, `typedef_base_changed`
  or `type_field_type_changed` instead of `NO_CHANGE`. `EXTRACTOR_GENERATION`
  moves to 16: a baseline dumped earlier compares its `FunctionType*` against
  the new spelling as generation drift (`COMPATIBLE_WITH_RISK`, re-dump the
  baseline), and a callback typedef is no longer read as an opaque handle.
  The DWARF path still spells subroutine types `fn(...)`.
