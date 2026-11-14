### Fixed

- **A removed `using std::X;` re-export is reported.** `std_reexport_removed`
  never fired: its detector looked for a function declared in a library
  namespace whose symbol is in `std::`, and no header backend records a
  using-declaration that way. Header dumps with a public-header set now record
  the public headers' namespace-scope `using std::X;` re-exports as their own
  fact (`AbiSnapshot.std_reexports`, the optional `abicheck.std_reexports`
  extension fact of the declarations section; extractor generation 64): castxml
  reads the namespace members bound from `std`, clang the `UsingDecl` nodes,
  and the native GCC plugin its namespace binding tables (rebuild the plugin
  with `python -m abicheck.extract.headers.gcc.build`). The detector compares
  the two tables, and reports nothing when either side has none, such as a
  DWARF-only or older snapshot. castxml and GCC record no position for a
  using-declaration, so a re-export in a namespace with no positioned
  declaration of its own leaves that side's table unestablished rather than
  empty; a multi-TU manifest dump unions the per-TU tables (unestablished if
  any TU's is, or if two TUs bind one name to different targets). An alias
  (`using string = std::string;`) replacing a re-export keeps the name.
