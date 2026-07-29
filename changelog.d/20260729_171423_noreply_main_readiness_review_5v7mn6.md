### Fixed

- **Classic, pre-oneAPI Intel `icc`/`icpc` is now recognized as its own
  toolchain family.** A declared `compiler_family: icc` profile was
  previously rejected unconditionally, since neither the oneAPI check nor
  the plain GCC/Clang/MSVC checks recognized it — even though
  `CompilerFamily.ICC` is already a recognized, distinct family elsewhere
  in the codebase. `icc`/`icpc` binary names and the classic
  `"Intel(R) C++ Compiler"`/word-boundary `icc`/`icpc` banner signatures
  now resolve to `"icc"`.
- **Equivalent MinGW-w64 target-triple spellings from GCC and Clang no
  longer disagree.** GCC's own `x86_64-w64-mingw32` triple folds OS and
  environment into one 3-component spelling with no separate environment
  component, while Clang spells the identical real environment
  explicitly (`x86_64-pc-windows-gnu`/`x86_64-w64-windows-gnu`) — both
  describe the same MinGW runtime, but the GCC spelling's environment
  previously normalized to `None` while the Clang spelling normalized to
  `"gnu"`, rejecting an otherwise-valid MinGW cross-compiler profile.
- **Direct-reference dependency retention no longer treats a
  private/generated/system-origin kept `RecordType`/`EnumType` as a
  retention root.** `RecordType`/`EnumType` have no `visibility` field,
  but both do carry `origin` — a kept type/enum whose own header is
  private (but still retained under the header-origin-only scoping
  contract) previously could keep an unrelated dependency type alive
  through its own fields even though no public declaration reached it,
  mirroring the earlier hidden-function fix for the same underlying gap.
