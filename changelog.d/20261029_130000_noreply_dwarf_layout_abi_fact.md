### Fixed

- **A DWARF struct member carries its type's ABI fact, so a depth projection
  judges a field retype the way a binary-only dump does** — the persisted DWARF
  layout (`dwarf.structs[*].fields[*]`) now records the fundamental-type fact a
  member's DIE states. `--depth binary` rebuilds record fields from that layout, so
  a retype between ABI-identical types reads `abi_equivalent_type_changed` on both
  the binary-only dump and the projected header dump, not a break on the projected
  side alone. Stored in schema v69 (unreleased, no bump); a pre-v69 document reads
  the member's fact as unset, which keeps the name-based verdict.
