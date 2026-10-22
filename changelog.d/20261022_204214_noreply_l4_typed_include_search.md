### Changed

- **Public-header root probe reads typed include-search operands** — `clang_public_roots` no longer rescans the replayed argv for `-iquote`, `-idirafter` and MSVC `/I`/`-I` operands. The compile-invocation model parses them once into `CompilerInvocation.replayed_include_dirs`, in the recorded spelling, so the probe resolves the same directories as before. Internal only; no flag, schema or report output changes.
