### Fixed

- Detect SysV AMD64 callback calling-convention changes when an exported C++ interface exposes its callbacks through a public operations table.
- Identify a changed by-value argument at the inner call when an operations-table callback receives another callback.
