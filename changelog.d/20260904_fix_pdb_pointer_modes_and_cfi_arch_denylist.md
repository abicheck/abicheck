### Fixed

- **PDB/CodeView pointer members now decode every documented
  `SimpleTypeMode` width, not just near32/near64** — `NearPointer` (2
  bytes), `FarPointer`/`HugePointer` (4 bytes), `FarPointer32` (6 bytes),
  and `NearPointer128` (16 bytes) previously all fell through to a
  generic 8-byte default; a mode value the CodeView spec doesn't define
  at all now correctly marks the reference unresolved instead of
  guessing.
- **DWARF CFI per-function coverage tracking now runs by default for
  every architecture, not just x64/x86/aarch64** — the coverage check was
  gated on the same small allowlist `_reg_name`'s register-name tables
  use, which silently disabled missing-FDE detection for every other
  architecture (RISC-V, ARM32, MIPS, ...) even though register-name
  support has nothing to do with whether a function symbol's address
  names its real code entry. Flipped to a denylist of the architectures
  actually known to use function-descriptor indirection (PPC64, IA-64).
