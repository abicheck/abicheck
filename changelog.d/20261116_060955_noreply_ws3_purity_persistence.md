### Fixed

- **Header-built snapshots keep binary purity evidence** — a header dump of an
  ELF library now records which of its virtual functions, and which classes,
  the emitted vtable slots prove pure or abstract (the `__cxa_pure_virtual`
  slot a GCC pure virtual leaves behind). `--depth binary` reads that evidence,
  so a virtual that became pure, or a class that became abstract, is reported
  at binary depth exactly as a binary-only dump reports it. The claim is made
  only where the vtable order is unambiguous (a single primary base chain,
  the emitted slot count matches the header model); anywhere else it is
  recorded as not claimed, with the reason. Stored as the optional
  `abicheck.binary_purity` extension fact; the extractor generation is 78.
