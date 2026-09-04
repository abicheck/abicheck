### Fixed

- **BTF parsing now validates the string section's mandatory leading NUL
  sentinel** — offset 0 in a BTF string section is reserved for the empty
  string per the format's own spec; a corrupt or hand-crafted blob whose
  string section doesn't actually store that byte let a `name_off=0`
  ("anonymous") reference read whatever bytes happened to sit at offset 0
  as a plausible, valid-looking name — fabricating or renaming a struct,
  enum, function, or typedef with no completeness signal at all.
- **DWARF CFI (frame-register/callee-saved) extraction now records facts
  for every exported symbol name at an address, not just one** — multiple
  exported names can legitimately share one address (a strong/weak symbol
  pair, or several public entry points folded onto identical code by the
  linker); the previous first-seen-wins address→symbol map attached an
  FDE's decoded facts to only one of those names while marking the whole
  address covered, so every other alias silently never received its own
  `frame_registers`/`callee_saved_regs` entry, with no completeness
  signal.
