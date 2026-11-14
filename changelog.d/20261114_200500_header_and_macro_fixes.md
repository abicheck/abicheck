### Fixed

- The native gcc header backend records `static inline` C function
  definitions, and a header declaration any backend recorded as `PUBLIC`
  but the library does not export keeps its public-contract fact, so
  removing it reads `inline_function_removed` and a signature change
  `header_only_function_params_changed` under `--ast-frontend gcc`.
- A header parse that differs only in glibc's ABI-selecting macros
  (`_TIME_BITS`, `_FILE_OFFSET_BITS`) is comparable: the rebuild reads
  `time64_abi_changed` instead of being refused as a profile mismatch. Any
  other differing `-D` still refuses.
- One member retype is reported once: the DWARF `struct_field_type_changed`
  folds into the header lane's `union_field_type_changed` (which now names
  the member), and an array member spelled `char [8]` by the gcc header
  backend matches DWARF's `char[8]`.
