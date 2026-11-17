### Changed

- **Three more kinds declare where they are observable (ADR-093).** `common_symbol_risk` is observable from the ELF symbol table alone (`B`, ELF only); `dwarf_info_missing` needs DWARF on the old side (`{B,D}`); `func_became_inline` reads `is_inline` from the public header only (`{B,H}`): DWARF does not record it. They leave the UNCLASSIFIED baseline (136 -> 133).
