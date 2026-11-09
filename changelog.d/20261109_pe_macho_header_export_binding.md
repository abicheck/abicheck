### Fixed

- **PE and Mach-O keep the parsed header surface when exports do not match
  it.** With `-H`, a PE or Mach-O dump used to throw the parsed headers away
  whenever no declaration's linker spelling matched an export. That happened
  for an MSVC DLL parsed by castxml or a non-Windows clang (Itanium names),
  and for a template-only header on either format. Header-only changes,
  such as a changed template default or a tightened concept, then read
  `NO_CHANGE`. A header whose `extern "C"` functions matched kept the
  surface but read every C++ declaration as unexported. The dump now binds
  each declaration to its export across mangling schemes (MSVC and Itanium,
  and 32-bit x86 C decoration) by the qualified name both spellings encode.
  It tells overloads apart by their decoded MSVC parameter list. A header set
  that parses is always kept as type-level evidence. When nothing could be
  bound, the snapshot records the new `scope_fallback` code
  `export-binding-unresolved` (an analysis-assurance note) instead of
  `mangling-fallback`, and exports that stay unbound are still compared from
  the export table. The binding pass lives in
  `abicheck.extract.header_export_binding`.
