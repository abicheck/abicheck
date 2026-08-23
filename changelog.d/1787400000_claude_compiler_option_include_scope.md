### Fixed

- **A header reached only through a `--compiler-option -I<dir>`/`-isystem
  <dir>` search path stayed classified `PRIVATE_HEADER` even though the
  caller had named that directory explicitly, just not via a bare `-I`.**
  `perform_elf_dump`/`handle_non_elf_dump` already treat a
  `--compiler-option`-supplied include directory as "as explicit as `-I`"
  for purposes of suppressing the L2 include-dir seed, but the
  declaration-provenance widening set (`public_include_search_dirs`) only
  ever collected the plain `-I`/`--include` list. Fixed by also folding in
  the include-search directories carried by the caller's own
  `--compiler-option` tokens (via the existing `header_utils.
  include_operand_dirs` helper), sourced from the pre-L3-fold explicit
  tokens on both the ELF and PE/Mach-O `dump` paths so an L3-derived
  directory is never mistaken for a caller-supplied one.
