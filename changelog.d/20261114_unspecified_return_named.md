### Changed

- **`unspecified_return_now_named` is `COMPATIBLE` and reports one direction.**
  A return type that goes from a deduced `auto`, a lambda closure or an
  unnamed class to a named type keeps every caller compiling (they could only
  hold the result in `auto`), and a non-template function's mangling does not
  encode its return type. The kind was `API_BREAK` and also fired for the
  reverse direction; a named return becoming unnameable is a return-type
  change, which `func_return_changed` reports. A template whose deduced return
  becomes named re-mangles (`Da` -> the type) and is reported as a removed and
  an added symbol. The detector now recognises clang's `(lambda:...)` closure
  spelling and no longer reads `std::auto_ptr<T>` as a placeholder.

### Fixed

- **castxml no longer hides a lambda return type behind `?`.** castxml emits a
  closure type as a record with no name; it was spelled `?`, the "no evidence"
  sentinel, so a function returning a lambda read as an unresolved return and
  no return-type change was reported. It is now spelled
  `<owner>::(unnamed class)` (extractor generation 63).
- **The native GCC header backend records the export fact the other backends
  record.** A declaration whose symbol is only in `.symtab`, or whose mangled
  name is gone while an `extern "C"` function of the same bare name is
  exported, read as `PRESENT(True)` / `PRESENT(False)`; it is now the same
  tiered `PARTIAL(True)` fact castxml and clang record, so the C-namesake case
  reports `func_deleted_elf_fallback` under both clang and GCC backends.
