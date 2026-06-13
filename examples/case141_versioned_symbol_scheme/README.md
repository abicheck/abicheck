# Case 141: Versioned-Symbol Scheme (library-wide rename)

**Category:** Symbol API | **Verdict:** 🔴 BREAKING

## What happens
The library carries its MAJOR version as a suffix on every exported symbol
(`mylib_init_3`, `mylib_open_3`, …) — the ICU `u_<name>_<major>` convention. v2
bumps the whole API to major 4, so **every** symbol is renamed `_3` → `_4` at
once. Source that spells the unsuffixed name via a version macro keeps compiling,
but the shipped `.so` drops all `_3` symbols and adds `_4` symbols.

## Why it is BREAKING
At the same SONAME, every consumer linked against a `_3` symbol fails at load
with `undefined symbol`. It is a real ABI break — abicheck reports the removals
and recommends a SONAME bump.

## What abicheck adds
Because the removed symbols reappear as added symbols differing only by the
version token, abicheck emits one advisory **`versioned_symbol_scheme_detected`**
finding explaining that the wall of churn is a library-wide rename, not
independent API removals. It never downgrades the artifact-proven removals;
opt in with `compare --collapse-versioned-symbols` to reclassify the
version-rename pairs as compatible and see the real delta.

## Code diff

| v1.h | v2.h |
|------|------|
| `int mylib_init_3(int x);` | `int mylib_init_4(int x);` |
| `int mylib_open_3(int x);` | `int mylib_open_4(int x);` |
| …(6 symbols, all `_3`) | …(same 6, all `_4`) |
