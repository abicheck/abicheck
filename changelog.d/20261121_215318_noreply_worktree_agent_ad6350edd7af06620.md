### Fixed

- The castxml header backend spells a multi-dimensional array in C order (`int m[2][3]` is `int[2][3]`, was `int[3][2]`), matching the clang backend and DWARF; a stored castxml snapshot from before extractor generation 91 is respelled on load, so an old baseline compares clean. The reversal was in abicheck's castxml reader, not in castxml.
- The castxml header backend now marks a `va_list` parameter (`Param.is_va_list`), read from the parameter's written type (`original_type`) chain to `__builtin_va_list`, so `param_became_va_list`/`param_lost_va_list` are reported between two castxml snapshots. Only the native gcc plugin still never establishes it.
