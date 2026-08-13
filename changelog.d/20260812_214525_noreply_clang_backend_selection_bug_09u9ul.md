### Fixed

- **`abicheck-cc` now resolves the clang source-ABI backend against the
  compiler it is actually wrapping.** `emit_facts_for_command()` previously
  always asked for a backend named literally `clang`, regardless of the
  compiler passed on the command line (`abicheck-cc icpx -c foo.cpp -o
  foo.o`). On an image with a clang-family compiler on `PATH` but no plain
  `clang` binary (e.g. Intel oneAPI's `icpx`/`dpcpp`), the clang backend
  read as unavailable and `abicheck-cc` silently emitted no facts, with no
  diagnostic. It now reuses the same `resolve_source_frontend_clang_bin()`
  resolver `dump --sources`/`--build-info`'s own `--gcc-path` handling
  already uses — one tested implementation for "derive the real
  clang-compatible driver from context instead of hardcoding a default" —
  and prints a warning to stderr when no usable backend resolves at all
  instead of failing silently.
- **`strip_launchers()` (the shared compiler-launcher-unwrap helper used by
  `abicheck-cc`, `pick_compiler_binary`, and the `compile_commands.json`
  CL-style/response-file detection in `build_context.py`) now also skips
  ccache's own documented per-invocation config-override form,
  `ccache KEY=VALUE ... compiler [compiler options]`
  (`ccache compiler_check=content gcc -c foo.c`). Previously only the
  launcher name itself was dropped, leaving the first override token
  mistaken for the compiler — this also fixes a latent CL-style
  misdetection for a `compile_commands.json` action shaped
  `sccache KEY=VALUE clang-cl @args.rsp`, not just `abicheck-cc`'s own
  compiler resolution.

