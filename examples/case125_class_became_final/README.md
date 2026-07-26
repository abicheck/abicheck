# Case 125: Class Became `final`

**Category:** API Break | **Verdict:** 🟠 API_BREAK

## Verdict and consumer impact

`Shape` gains the `final` specifier in v2. Its size, alignment, vtable, and
every member's mangled name are unchanged, so already-compiled binaries keep
linking and running against either version — this is not a runtime ABI
break. But any consumer that *derives* from `Shape`
(`struct MyShape : public Shape { ... }`) no longer compiles: recompiling an
existing derived-class consumer against the v2 header fails outright.

## Old/new diff

| v1.h | v2.h |
|------|------|
| `class Shape { ... };` | `class Shape final { ... };` |

## abicheck command

```bash
g++ -shared -fPIC -g v1.cpp -o libshape_v1.so
g++ -shared -fPIC -g v2.cpp -o libshape_v2.so
ABICHECK_AST_FRONTEND=clang abicheck compare libshape_v1.so libshape_v2.so \
    --header old=v1.h --header new=v2.h
```

## Expected abicheck finding

```text
Verdict: API_BREAK (exit 2)

- type_became_final: Class gained `final` specifier: Shape --
  consumers that derive from it no longer compile (non-final -> final)
  > A class/struct gained the `final` specifier. Any consumer that
    derives from it no longer compiles. Type layout and mangled names
    are unchanged so already-built binaries keep running, but
    recompilation against the new header fails.
```

## Minimum evidence

`min_evidence: L2` — `final` is a pure C++ source-level specifier. It is not
recorded in DWARF or anywhere in the object file (`abicheck compare` with no
headers reports `NO_CHANGE` on this pair), so only the public header's AST
can see it. castxml is abicheck's default L2 backend; the run above uses the
clang AST frontend (`ABICHECK_AST_FRONTEND=clang`) as a supported
alternative — either backend records the class-key.

## Why abicheck catches it

The header-AST parser records each class/struct's `final` class-key from the
declaration itself; abicheck compares that flag between the two header
snapshots and reports `type_became_final` when it flips from absent to
present, scoped to declarations in the headers actually passed on the
command line.

## Runtime failure demonstration

**Severity: not a runtime crash — a compile-time break for derived-class consumers**

Because `final` changes no layout or symbol, swapping the library binary
alone is silent; the break only shows up when a *derived-class* consumer is
recompiled against the new header.

```bash
# app.cpp derives from Shape: `struct MyShape : public Shape { ... };`

# Compile + link against v1 (non-final) -- succeeds and runs:
g++ -shared -fPIC -g v1.cpp -o libshape.so
g++ -std=c++17 -g app.cpp -I. -L. -lshape -Wl,-rpath,. -o app
./app
# -> area=1.000000 extra=0

# Recompile the SAME app.cpp against v2.h (final) -- fails to compile:
g++ -std=c++17 -g -DUSE_V2 app.cpp -I. -L. -lshape -Wl,-rpath,. -o app_v2
# -> error: cannot derive from 'final' base 'Shape' in derived type 'MyShape'
```

**Why this matters anyway:** the compiled `.so` files stay ABI-identical
(swapping the library binary produces no crash), so this break is invisible
to any binary/DWARF-only inspection. It only surfaces the moment a
derived-class consumer's source is rebuilt against the new header — exactly
the scenario abicheck's header mode is built to catch ahead of time.

## Safe redesign

Keep public base classes non-`final`, or document the inheritance contract
explicitly. Adding `final` to a previously-extensible public class is a
breaking API change and warrants a major version bump.

## Cross-tool comparison

`abidiff`/`abidw` operate on the compiled ELF and DWARF debug info only —
`final` leaves no trace there (the v1 and v2 `.so` files are ABI-identical),
so a binary-only comparison tool reports no change at all here, the same way
abicheck's own object-only mode does. Only a header-aware comparison
(abicheck's L2 mode) sees this class of change.
