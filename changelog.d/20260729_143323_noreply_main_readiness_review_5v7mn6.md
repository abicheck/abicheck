### Fixed

- **G34 Phase A toolchain-identity probe: fixed two false-positive/false-negative
  sources found by review, and closed a CLI/MCP parity gap.** A cross-prefixed
  compiler binding (e.g. Debian/Ubuntu's `x86_64-linux-gnu-gcc-13`) had its
  declared `compiler_version` checked against the first bare digit
  substring in the invoked name (`86`, from the target triple) instead of
  the compiler's real, always-dotted version number — a valid profile was
  rejected. A generic driver alias/symlink (`cc`, `c++`, or a Clang-backed
  `gcc`) was classified by its own basename alone, accepting or rejecting
  the wrong family. Both are fixed in
  `abicheck.buildsource.toolchain_probe`: version extraction now prefers a
  dotted version token, and family detection also checks the resolved
  realpath and a `--version` banner signature phrase, skipping the
  comparison rather than guessing when still inconclusive. The MCP
  `abi_project_validate` tool now runs the same identity check the CLI's
  `project validate` command does, instead of only the binding-resolution
  check.

