### Fixed

- A `DEFAULT`↔`PROTECTED` visibility change is reported once. A function's
  change reads `func_visibility_protected_changed` and a data object's
  `protected_visibility_changed`, as before, but no longer also
  `symbol_elf_visibility_changed`, which now covers only the symbol types
  neither of those owns (TLS, IFUNC, untyped).
