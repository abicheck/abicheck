### Fixed

- **native GCC header backend: C object and type facts** — with the gcc
  header backend in C, `_Atomic` fields are spelled `_Atomic(T)` (was
  `atomic T`), an unknown array bound or flexible array member `T[]` (was
  `T[<unknown>]`/`T[0:]`) and a zero-length array `T[0]`; an array object's
  `const` is read from its element type; and a declared-only `struct x;` is
  recorded as opaque. `atomic_qualifier_changed`,
  `flexible_array_member_changed`, `symbol_size_changed_const_object` and
  `type_became_opaque` are now reported as with castxml. Plugin protocol
  schema 6 (rebuild the plugin). `EXTRACTOR_GENERATION` 59, snapshot cache
  salt v53.
