### Fixed

- **gcc-built AArch64 libraries with debug info dump again** — the header
  parse no longer copies target-ABI and byte-order options (`-mabi=`,
  `-mlittle-endian`, `-mbig-endian`, `-mel`/`-meb`, `-m16`) out of the
  binary's `DW_AT_producer`. AArch64 GCC records `-mabi=lp64` in every
  producer and castxml's clang rejects it ("unknown target ABI 'lp64'"), so
  every `dump`/`compare` of such a library with headers exited 1. RISC-V,
  PowerPC and MIPS GCC builds record the same kind of option and are fixed
  too; ISA flags such as `-march=` and `-mavx2` are still forwarded.
