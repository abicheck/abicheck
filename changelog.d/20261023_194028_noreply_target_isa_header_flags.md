### Fixed

- **Target-ISA `-m` flags from build info now reach the header parse** — a
  compile unit built with `-mavx2`, `-mavx512f`, `-msse4.2` or another
  target-machine flag (`-m*`, excluding `-mllvm`) keeps that flag in its
  build context, so castxml and clang see the same `__AVX2__`/`__AVX512F__`
  predefined macros the build did. Before, the flag was dropped by a closed
  prefix list and a header-only SIMD library was parsed under the x86-64
  baseline on every side, producing a false `public_not_exported` and wrong
  header layouts. The build-option drift between the two ISAs is now reported
  as `abi_relevant_build_flag_changed`.

