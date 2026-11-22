### Fixed

- **A `-H` umbrella under an `include/` root no longer shadows libc headers.**
  abicheck added each `-H` file's own directory to the include search path,
  so a library shipping a flat `endian.h` (protobuf's
  `include/google/protobuf/endian.h`) captured glibc's `<endian.h>` and the
  header parse failed. Under an `include`/`inc` root only the root is added
  now; a flat layout without one still adds the file's directory.
