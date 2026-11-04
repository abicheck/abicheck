### Fixed

- **gcc-built AArch64 libraries with debug info dump again** — the header
  parse no longer copies an AArch64 binary's recorded `-mabi=lp64` (or
  `ilp32`) into castxml, whose clang rejects GCC's spelling ("unknown target
  ABI 'lp64'"); every `dump`/`compare` of such a library with headers exited
  1. The AArch64 binary is recognised by its ELF machine. `-mabi=` is still
  forwarded for other targets (RISC-V, PowerPC, MIPS), where a non-default
  ABI selects different header declarations. Multilib (`-m32`, `-mx32`, …) and
  byte-order options (`-mbig-endian`, `-meb`, …) are never forwarded. Instead,
  a header parse whose byte order would differ from the binary's is refused
  with a reason that names the remedy (a cross compiler in
  `compile.compiler`, or a `-target` triple in `compile.options`), rather
  than reading byte-order-dependent declarations for the wrong target.
