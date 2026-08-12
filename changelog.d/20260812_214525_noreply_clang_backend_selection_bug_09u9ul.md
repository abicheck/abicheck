### Fixed

- **`abicheck-cc` now resolves the clang source-ABI backend against the
  compiler it is actually wrapping.** `emit_facts_for_command()` previously
  always asked for a backend named literally `clang`, regardless of the
  compiler passed on the command line (`abicheck-cc icpx -c foo.cpp -o
  foo.o`). On an image with a clang-family compiler on `PATH` but no plain
  `clang` binary (e.g. Intel oneAPI's `icpx`/`dpcpp`), the clang backend
  read as unavailable and `abicheck-cc` silently emitted no facts, with no
  diagnostic. It now defaults the backend's `clang_bin` to the wrapped
  compiler (after unwrapping a `ccache`/`distcc`-style launcher) whenever
  that compiler is clang-family, and prints a warning to stderr when no
  usable backend resolves at all instead of failing silently.

