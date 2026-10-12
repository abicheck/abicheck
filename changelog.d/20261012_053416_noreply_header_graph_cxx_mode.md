### Fixed

- **The header graph parses C++ headers as C++ even without a textual C++
  marker** — with no `--lang`, the primary dump decides C or C++ from header
  text *and* the binary's mangled exports, but the header graph's own clang
  parse re-decided from header text alone. A C++ `.h` header whose only C++
  constructs are a non-const reference, a `std::` name, an overload or a
  default argument was parsed as C, failed, and left a declaration-only graph
  (`header_call_graph` degraded). The graph's parse now follows the mode the
  primary pass resolved; under `--ast-frontend clang` it reuses that pass's
  AST instead of running clang a second time.
