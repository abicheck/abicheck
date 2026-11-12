### Fixed

- **castxml header dumps no longer record compiler-declared builtins as library functions** — when an implicit copy of an array member uses `__builtin_memcpy`, castxml declares that builtin itself (`artificial="1"`) and locates the declaration at the record in your header. The castxml backend recorded it as a public-header function, so any run with a public-header set reported `public_not_exported` for `__builtin_memcpy`. Such declarations are now skipped. Extractor generation 57.
