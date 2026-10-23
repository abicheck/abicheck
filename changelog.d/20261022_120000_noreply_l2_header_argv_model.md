### Changed

- **The L2 header clang argv is rendered by one typed model** —
  `_build_clang_header_command` now delegates to
  `extract/headers/clang_header_argv.py` (`HeaderCompileContext` and
  `render_clang_header_argv`) instead of building its own argv. The emitted
  argv is unchanged: an 11-case golden recorded before the change
  (`tests/data/compile_invocation/clang_header_argv_golden.json`) is asserted
  byte-identical, covering include dedup, `-isystem` ordering, sysroot and
  `-nostdinc`, C and C++ language selection, SYCL host and multi context, and
  clang-cl. The header path keeps its path-class include dedup.
