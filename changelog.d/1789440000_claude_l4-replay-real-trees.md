### Fixed

- `dump --depth source` (L4 source-ABI replay) now records declarations for a
  public header reached through a relative include such as
  `lib/common/../zstd.h`: the clang extractor's exact-file public-root match
  compared un-normalized path segments, so such a library's whole public
  surface was classified non-public and L4 came back empty. Replay also skips
  assembler translation units (`.s`, `.S`, `.sx`, `.asm`), which declare no
  C/C++ ABI. They are dropped before `headers-only` picks its representative
  units, so a target whose first unit is assembly keeps its C-family coverage.
  A castxml run whose output is unparseable or that defusedxml refuses (an
  entity declaration) is now that unit's own extraction failure, instead of
  aborting the whole dump.
