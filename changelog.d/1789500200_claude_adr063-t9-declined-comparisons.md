### Added

- The JSON report's `detectors[]` entries carry `declined`: the entities a
  detector ran but declined to judge because the evidence it needed was
  incomplete or unsupported (for example a PDB or partial-DWARF vtable),
  each with its reason. A declined entity was previously indistinguishable
  from a clean one. Report schema 5.11 (ADR-063 T9).
