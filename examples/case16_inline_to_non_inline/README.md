# Case 16: Inline → Non-inline (ODR / Symbol Appearance)

**Category:** Addition | **Verdict:** ✅ COMPATIBLE

## Verdict and consumer impact

Existing binaries are unaffected: `fast_hash` was `inline` in old, so callers
compiled against old's header already have their own copy of the function
body baked in — they never referenced a `.so` symbol for it. new moves the
implementation out of the header into the library and exports it as a real
symbol. abicheck's binary-vs-binary comparison correctly sees this as a pure
addition. The real risk is a *build-coordination* one, not a binary ABI
break: new consumers compiled against new's header (declaration only, no
inline body) require the symbol to exist in the library they link against —
linking such a consumer against old's `.so` fails at link time, because old
never exported `fast_hash` at all.

## Old/new diff

| old/lib.hpp | new/lib.hpp |
|-------------|-------------|
| `inline int fast_hash(int x) { return static_cast<int>(static_cast<unsigned>(x) * 2654435761U); }` | `int fast_hash(int x);` *(declaration only)* |
| old/lib.cpp: *(empty — nothing to compile)* | new/lib.cpp: `int fast_hash(int x) { return static_cast<int>(static_cast<unsigned>(x) * 2654435761U); }` |

## abicheck command

```bash
g++ -shared -fPIC -std=c++17 -g old/lib.cpp -o libfoo_v1.so
g++ -shared -fPIC -std=c++17 -g new/lib.cpp -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so
```

## Expected abicheck finding

```text
Verdict: COMPATIBLE (exit 0)

Quality Issues:
- version_script_missing: Library exports 1 symbol(s) without a version
  script.

Additions:
- func_added: New public function: fast_hash
  > New function available; existing binaries are unaffected.
```

## Minimum evidence

`min_evidence: L0` — the exported-symbol table alone is enough: `fast_hash`
is absent from old's `.dynsym` (it was header-only inline, nothing to
export) and present in new's, with no removed or changed symbols. No debug
info or headers needed.

## Why abicheck catches it

The dynamic symbol table is authoritative L0 evidence — old's `.so` truly
has no `fast_hash` symbol (`nm --dynamic` confirms it), so its appearance in
new is classified `func_added` from pure ELF symbol-table evidence, same as
any other new export. abicheck's binary comparison has no way to see (and
correctly doesn't claim to) that the function existed all along as a header
inline — that fact lives in source, not in either compiled `.so`.

## Runtime failure demonstration

**Severity: INFORMATIONAL for existing binaries — link-time risk for new consumers.**

**Scenario A (existing binary, unaffected):** compile `app` against old's
header (inline `fast_hash`), swap in new's `.so` without recompile.

```bash
g++ -shared -fPIC -std=c++17 -g old/lib.cpp -o libhash.so
g++ -std=c++17 -g app.cpp -I. -L. -lhash -Wl,-rpath,. -o app
./app
# → fast_hash(42) = -182847734

# Swap in new library (no recompile)
g++ -shared -fPIC -std=c++17 -g new/lib.cpp -o libhash.so
./app
# → fast_hash(42) = -182847734   (same — caller uses its own inlined copy)
```

**Scenario B (new consumer, link failure):** compile a fresh translation
unit against new's header (declaration only), link against old's `.so`.

```bash
g++ -shared -fPIC -std=c++17 -g old/lib.cpp -o libhash.so
g++ -std=c++17 -g app.cpp -Inew -L. -lhash -Wl,-rpath,. -o app
# → /usr/bin/ld: app.cpp:(.text+0x...): undefined reference to `fast_hash(int)'
# → collect2: error: ld returned 1 exit status
```

**Why the verdict is still COMPATIBLE:** abicheck compares compiled
binaries, and by that measure this is a strict addition — every symbol old
exported is still present and unchanged in new. Scenario B's failure is a
consequence of mixing a new header with an old library, which a coordinated
release (ship the new header and the new `.so` together) naturally avoids.

## Safe redesign

No fix needed — moving an implementation from header-inline to
library-exported is a normal, compatible evolution as long as the header and
library are always shipped as a matched pair. If old headers might still be
distributed after the library moves on, keep a deprecated inline forwarder
in the header for one release cycle to avoid Scenario B entirely.

**Real-world example:** in abseil-cpp, several string utility functions were
moved from headers into the `.so` during a refactor; users who pinned to an
old `.so` but updated their headers hit exactly Scenario B's linker error.

## Cross-tool comparison

```bash
abidw --out-file v1.xml libfoo_v1.so
abidw --out-file v2.xml libfoo_v2.so
abidiff v1.xml v2.xml || true
# → reports fast_hash as a new addition, same conclusion as abicheck; it
#   has no visibility into the header-level inline→non-inline distinction
#   since it only ever compares the two .so files.
```

## References

- [C++ One Definition Rule](https://en.cppreference.com/w/cpp/language/definition)
- [libabigail `abidiff` manual](https://sourceware.org/libabigail/manual/abidiff.html)
