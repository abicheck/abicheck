### Fixed

- **A header-only comparison without `clang` on PATH no longer reports `public_dependency_role_changed` for builtin types.** The CastXML-fed header graph spells fundamental types GCC-style (`long int`, `long unsigned int`), which the type graph's builtin filter did not recognise, so `long int` became a graph node and `void f(long)` → `long f(long)` read as a role change on it. The filter now treats any spelling made only of fundamental-type keywords (any order, including `__int128`) as a builtin, so the clang and CastXML header lanes agree.
