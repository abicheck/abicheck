### Fixed

- **`--ast-frontend clang --gcc-path` no longer misprobes Intel oneAPI compiler
  aliases as GNU compilers** — the clang-frontend system-include probe in
  `dumper_sysinc.py` used a bare `"clang" not in name` check to decide whether
  a `--gcc-path` binary was GNU or clang-family, so `icx`/`icpx`/`dpcpp`/
  `dpcpp-cl` (clang-based binaries under non-"clang" names) were treated as
  real GCC and probed directly. Their own `-E -v` system-include report is
  incomplete for libc (missing `/usr/include`), which surfaced as
  `stdlib.h`/`cstdlib`-not-found errors even with a manually supplied
  `-isystem`. `_resolve_probe_compiler` now reuses `dumper_clang`'s
  `_is_clang_family_binary` (already used elsewhere for the same alias list)
  so these aliases correctly fall through to a real `g++`/`gcc` on `PATH`.

