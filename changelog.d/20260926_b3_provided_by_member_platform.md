### Fixed

- A mixed-platform release (ELF beside PE or Mach-O members) now keys each
  `provided_by` relation edge on its own member's export node instead of an
  unjoinable `binary_symbol://mixed/...` id.
