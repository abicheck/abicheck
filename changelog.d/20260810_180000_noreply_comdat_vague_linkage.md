### Added

- **COMDAT-group introspection over L3 object files**
  (`buildsource/comdat_groups.py`) — the evidence that proves *vague linkage*,
  which no other source in the tree can supply. An inline function, a template
  instantiation, an implicit special member and a vtable without a key function
  are all emitted into COMDAT groups because the language requires every using
  translation unit to define them; an ordinary strong definition is not. The
  export table cannot tell the two apart — both land in `.dynsym` as `WEAK`,
  including `__attribute__((weak))` out-of-line functions, which carry no
  consumer-side copy at all.

  Reads object files specifically, because the alternatives were checked and
  ruled out: the linker resolves and discards section groups, so a linked `.so`
  carries nothing; castxml (through 0.7.0, its only output format version)
  emits a declaration and a definition byte-identically apart from `line`; and
  `DW_AT_inline` appeared on g++ `-O2` and on none of g++ `-O0`, clang++ `-O0`
  or clang++ `-O2`, so its absence means "this compiler said nothing" rather
  than "not vague".

  Pure parsing, no subprocess. `ComdatScan.resolvable` keeps "scanned, found
  none" distinct from "nothing was scanned", so a consumer can never read an
  unscanned build as positive evidence; unreadable objects degrade to
  diagnostics rather than raising, per ADR-028 D3.

  Section-group words are decoded in the ELF file's own `EI_DATA` byte order:
  hardcoding little-endian reads `GRP_COMDAT` as `0x01000000` on s390x/ppc64
  and skips every group, returning an empty set indistinguishable from "this
  build has nothing vague".

  ELF only: `SHT_GROUP` is an ELF construct, so a Mach-O or PE/COFF object
  reads as "not an ELF file" and leaves the scan unresolvable rather than
  claiming the build has nothing vague. Mach-O and PE express the same idea
  through different structures and would need their own extractor.
