### Fixed

- **A header whose only C++ signal is C++20 concept/requires syntax (e.g.
  an abbreviated constrained parameter like `void f(std::integral auto
  x);`, with no class/namespace/template keyword at all) is now correctly
  parsed in C++ mode.** `force_cpp` was previously decided purely from
  `_detect_cpp_headers` (structural C++ syntax), so `force_cpp20` — gated
  on `force_cpp` already being true — never got a chance to matter for
  such a header, and castxml/clang were invoked in C mode where the
  syntax doesn't parse at all. Both the castxml and clang frontends' C++
  auto-detection, and the C→C++ self-heal retry, now also consult
  `_detect_cpp20_headers`.
