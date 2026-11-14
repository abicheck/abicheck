### Fixed

- From gcc DWARF, a user-provided copy or move constructor now makes a class
  non-trivial for calls, so `VALUE_ABI_TRAIT_CHANGED` fires when a by-value
  parameter switches from registers to an invisible reference. gcc declares
  the constructor under the unified `C4` name, which the `C1`/`C2` linkage
  match never saw. Constructors are now identified by shape (one reference
  parameter to the class itself), so a converting or default constructor no
  longer reads as non-trivial.
