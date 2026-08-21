### Fixed

- **`dump --depth source`'s embedded L4 source-ABI surface linked nothing,
  because the write-time embed was given no public-header roots.**
  `cli_buildsource._write_snapshot_output` — the step that folds L3/L4/L5
  evidence into a `dump`-written snapshot — called `embed_build_source`
  without `public_headers`/`public_header_dirs`, so the source-ABI replay ran
  with an empty `public_header_roots` set. Every declaration then classifies
  private, nothing links to the binary's exports, and the snapshot records
  `0/N symbols matched`, `reachable_declarations=0` and
  `fact_family_states: empty-confirmed`. The layer is present and the coverage
  row honestly reports "partial", so nothing fails — but every L4-derived
  source-ABI finding is silently inert for a `dump`-produced baseline.

  Measured directly: a real `dump lib.so -H api.h --sources . --build-info
  compile_commands.json --depth source` recorded `0/2 symbols matched` where
  the identical inputs through `compare`'s implicit-dump operand or the typed
  `DumpRequest` API recorded `1/2` matched, a real
  `source_decl_to_binary_symbol` mapping, and `L4_source_abi: high/present`
  instead of `reduced/partial`. Both the ELF and the PE/Mach-O `dump` paths
  now forward their own roots, so all four resolvers produce the same L4
  surface from the same evidence.
