### Added

- **ADR-063 Phase 5's fact/capability registry: `Variable`'s case-(b)
  fields converted to `Fact[T]`** (schema v34) — `source_header`/
  `alignment_bits`/`elf_binding` now carry `Fact[...]` siblings
  (`source_header_fact`/`alignment_bits_fact`/`elf_binding_fact`), the
  same case-(b) "`None` already unambiguously means not captured"
  pattern already applied to `RecordType`/`EnumType`'s twin fields.
  `elf_binding_fact`'s decoded value is reconstructed as a real
  `SymbolBinding` enum member rather than left as a bare JSON string
  (`storage/fact_codec.py`'s new `decode_variable_facts`), since
  existing readers (`diff_symbols.py`, `diff_platform.py`)
  unconditionally access `.value` on it.
  `dumper_elf_symbols._populate_elf_visibility`'s post-construction
  `var.elf_binding = ...` assignment (a real mutation-trap site, the
  same shape already fixed in `tu_merge.py`/`provenance.py`) now also
  keeps `elf_binding_fact` in sync explicitly.
