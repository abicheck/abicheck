# Case 66: Language Linkage Changed (extern "C" removed)

**Category:** Function ABI | **Verdict:** 🔴 BREAKING

## Verdict and consumer impact

The `extern "C"` wrapper is removed from the public header. In v1, functions
export with C linkage — the `.dynsym` name is exactly `parse_config`. In v2,
the same functions use C++ linkage, so the exported name is mangled to
`_Z12parse_configPKc`. Any consumer (C or C++) linked against v1 has
recorded the unmangled name as the symbol it needs; when v2 is loaded, that
name doesn't exist and the dynamic linker fails to resolve it. The source
still compiles fine against v2's headers — only the binary symbol table
changes — so this break is easy to miss without an ABI diff.

## Old/new diff

```cpp
// v1.h — C linkage (symbol: "parse_config")
extern "C" {
    int parse_config(const char *path);
}

// v2.h — C++ linkage (symbol: "_Z12parse_configPKc")
int parse_config(const char *path);
```

## abicheck command

```bash
g++ -shared -fPIC -g v1.cpp -o libfoo_v1.so
g++ -shared -fPIC -g v2.cpp -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

- func_language_linkage_changed: Language linkage changed: validate_config
  (extern "C" -> C++)
  > Language linkage changed (extern "C" <-> C++); the mangled symbol name
    changes, so old binaries reference a symbol that no longer exists under
    that name.
- func_language_linkage_changed: Language linkage changed: parse_config
  (extern "C" -> C++)
```

## Minimum evidence

`min_evidence: L0` — the exported-symbol table alone shows the unmangled
name disappear and a demangled-equivalent mangled name appear in its place;
abicheck's mangled/unmangled reconciliation reports this as a linkage
change rather than an unrelated add+remove, with no debug info or headers
required.

## Why abicheck catches it

abicheck demangles each exported C++ symbol and, when a v1 unmangled name
and a v2 mangled name demangle to the same underlying function signature, it
reports `func_language_linkage_changed` instead of a plain
`func_removed`/`func_added` pair — turning what would otherwise look like an
unrelated symbol swap into the actual linkage-change root cause.

## Runtime failure demonstration

**Severity: CRITICAL**

**Scenario:** compile a C app against v1, swap in v2 `.so` without recompile.

```bash
# Build v1 (extern "C") and app
g++ -shared -fPIC -g v1.cpp -o libparser.so
gcc -g app.c -L. -lparser -Wl,-rpath,. -o app
./app
# → parse_config = 1 (expected 1)
# → validate_config = 1 (expected 1)

# Verify v1 exports unmangled names
nm -D libparser.so | grep parse_config
# → T parse_config

# Build v2 (no extern "C")
g++ -shared -fPIC -g v2.cpp -o libparser.so

# Verify v2 exports mangled names
nm -D libparser.so | grep config
# → T _Z12parse_configPKc
# → T _Z15validate_configPKc

./app
# → ./app: symbol lookup error: ./app: undefined symbol: validate_config
```

**Why CRITICAL:** neither unmangled name (`parse_config`, `validate_config`)
exists in v2's dynamic symbol table anymore — only their C++-mangled
equivalents do. The dynamic linker resolves the app's needed symbols eagerly
at load time and fails on whichever one it processes first (`validate_config`
here); the process is killed before `main()` ever runs.

## Safe redesign

Always keep a public C-compatible API inside `extern "C"` — treat it as a
public contract, not an implementation detail. Keep the public header pure
C and provide a separate C++ header for C++-only consumers, and enforce it
in CI with a check that every public `.dynsym` symbol matches the expected
unmangled name list (`nm -D libfoo.so | grep -v '^_Z'`).

**Real-world example:** this commonly happens during "modernization"
refactors when a C library is rewritten in C++ and a developer removes
`extern "C"` without realizing downstream C consumers (and pre-built C++
binaries) depend on the unmangled names. libpng, zlib, and SQLite all
maintain `extern "C"` blocks specifically to preserve their C ABI contract
even when compiled as C++.

## Cross-tool comparison

```bash
abidw --out-file v1.xml libfoo_v1.so
abidw --out-file v2.xml libfoo_v2.so
abidiff v1.xml v2.xml
```

## References

- [C++ Standard §10.5 — Linkage specifications](https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2023/n4950.pdf)
- [Itanium C++ ABI — Name Mangling](https://itanium-cxx-abi.github.io/cxx-abi/abi.html#mangling)
