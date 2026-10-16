---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# MinGW toolchain headers are not classified as dependencies (2026-09-23)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/extract/declaration_provenance.py:270 _is_toolchain_compiler_include_dir still recognises only lib/gcc/<triple>/<ver>/include, lib/clang, and castxml layouts. provenance.py has no handling for MinGW's <prefix>/<triple>/include.


Found by `tests/test_header_only_dump.py::TestHeaderOnlyDependencyScoping` on
the `windows-latest` integration lane. After #1347 made header-only dumps apply
the default toolchain-declaration exclusion, a castxml dump on MinGW still kept
`_mingw.h`'s `__debugbreak`. castxml reports that header as
`C:/mingw64/bin/../lib/gcc/x86_64-w64-mingw32/15.2.0/../../../../x86_64-w64-mingw32/include/_mingw.h`,
which collapses to `<prefix>/x86_64-w64-mingw32/include/`: GCC's per-target
sysroot include directory. `provenance._is_toolchain_compiler_include_dir`
recognises `lib/gcc/<triple>/<version>/include` but not this layout, so it
isn't treated as a system header. This affects binary+header dumps on MinGW too,
not only header-only ones.

Not fixed in #1347 because the obvious pattern, `<prefix>/<triple>/include`, is
not safe on path shape alone. `_TARGET_TRIPLE_RE` accepts any 2-4 hyphenated
components, so an ordinary project directory like `my-lib/include` would match
and the project's own API would be dropped: exactly the failure this
classifier must never cause. A sound fix needs positive evidence that the
directory belongs to the toolchain. Options: a sibling `lib/gcc/<triple>/`
under the same prefix, or the compiler's own reported search list
(`-print-search-dirs` / `-v`).

On the same lane, clang cannot parse MinGW's libstdc++ `<cstdio>` for a
header-only dump (`__STRICT_ANSI__` warnings, then errors). This is an
environment limitation of clang with MinGW headers, not something abicheck
causes. That test is skipped on Windows until both are resolved.
