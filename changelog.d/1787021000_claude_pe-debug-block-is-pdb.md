### Fixed

- Legacy snapshot loading no longer credits a PE document's debug block to
  the DWARF producer. `pdb_metadata.parse_pdb_debug_info` stores a
  successfully-parsed PDB *in* `DwarfMetadata`/`AdvancedDwarfMetadata` with
  `has_dwarf=True`, and the PE dump path never invokes the DWARF parser at
  all, so a `platform: "pe"` block evidences `pdb`, not `dwarf`. Reading it
  as DWARF kept legacy PDB-derived `TypeField.is_const`/`is_volatile` at
  `PRESENT` even though the fresh PDB model states `UNSUPPORTED` for both.
