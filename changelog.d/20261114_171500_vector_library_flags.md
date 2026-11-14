### Changed

- `vector_abi_changed` is graded `COMPATIBLE_WITH_RISK` when only the
  vector-math library selection flags changed (`-mveclibabi=`, `-fveclib=`):
  they choose which library the binary calls and change no entry point it
  exports. An Intel `-vecabi=` change, which re-targets the binary's own
  SIMD clones, stays `BREAKING`. Modulation reason
  `vector_library_selection_only`.
