# Case 16 — Inline → Non-inline (ODR / Symbol Appearance)


**Verdict:** 🟢 COMPATIBLE
## What changes

| Version | Where is `fast_hash`? |
|---------|----------------------|
| v1 | Header-only inline — callers have their own copy |
| v2 | Moved to `.so` — now an exported symbol |

## What breaks at binary level

**Scenario A — Stale callers (compiled with v1 header):**
The caller has `fast_hash` inlined. The v2 `.so` also has `fast_hash`. At link time,
the linker sees two definitions — caller's inlined copy and the `.so` export. Normally
the inline version "wins" locally. But if the implementation diverges between v1
(inlined) and v2 (in `.so`), results differ. This is an **ODR violation**.

**Scenario B — Fresh callers (compiled with v2 header, linked against v1 `.so`):**
The caller expects `fast_hash` as an imported symbol. But v1 `.so` has **no**
`fast_hash` symbol at all. Link fails with "undefined symbol: fast_hash".

In both scenarios the breakage is subtle and depends on build order.

## Why abidiff misses it

`abidiff` compares two `.so` files. v1 `.so` has **no** `fast_hash` symbol (it was
inline). v2 `.so` **adds** `fast_hash`. abidiff reports this as a new export, not a
breaking change. It cannot know that callers were compiled with the old inline version.

## Why ABICC catches it

ABICC parses both header ASTs. It sees `fast_hash` was `inline` in v1 and non-`inline`
in v2. This is a semantic change: the inline assumption is gone. ABICC flags:
> "Function 'fast_hash' changed: inline removed".

## Real-world example

In **abseil-cpp**, several string utility functions were moved from headers into the
`.so` during the monorepo refactor (2021). Users who pinned to old `.so` files but
updated their headers got linker errors. Some projects shipped both a header-inline
and a `.so` symbol — causing ODR violations with LTO builds.

## Code diff

```diff
-// v1.hpp
-inline int fast_hash(int x) {
-    return static_cast<int>(static_cast<unsigned>(x) * 2654435761U);
-}

+// v2.hpp
+int fast_hash(int x);   // declaration only

+// v2.cpp
+int fast_hash(int x) {  // now in .so
+    return static_cast<int>(static_cast<unsigned>(x) * 2654435761U);
+}
```

## Reproduce steps

```bash
cd examples/case16_inline_to_non_inline

# Build .so files
g++ -shared -fPIC -std=c++17 -g v1.cpp -o libv1.so
g++ -shared -fPIC -std=c++17 -g v2.cpp -o libv2.so

# Check symbol table
nm --dynamic libv1.so | grep fast_hash || echo "v1: no fast_hash symbol (expected)"
nm --dynamic libv2.so | grep fast_hash            # v2: symbol present

# abidiff: shows fast_hash as NEW addition (not a break)
abidw --out-file v1.xml libv1.so
abidw --out-file v2.xml libv2.so
abidiff v1.xml v2.xml || true

# ABICC: catches inline→non-inline semantic change
abi-compliance-checker -lib fast_hash -v1 1.0 -v2 2.0 \
  -header v1.hpp -header v2.hpp
```

## Real Failure Demo

**Severity: ⚠️ Requires coordinated deployment — not an ABI break**

Existing binaries are unaffected (see "Why runtime result may differ from
verdict" below) — the risk here is a *specific build-mismatch* scenario,
not a break in any already-compiled consumer. See case47
(`inline_to_outlined`) for the same mechanism on a class method, framed at
the same severity, plus the compatibility matrix below spelling out
exactly which of the four header/library combinations fails.

**Scenario B — linker error:** compile app with v2.hpp (no inline), link against v1.so (no symbol).

```bash
# Build v1.so (fast_hash is inline — NOT in .so)
g++ -shared -fPIC -std=c++17 -g v1.cpp -o libhash.so

# Compile app with v2.hpp (declaration only, expects symbol in .so)
g++ -std=c++17 -g app.cpp -I. -L. -lhash -Wl,-rpath,. -o app
# → /usr/bin/ld: app.cpp:(.text+0x...): undefined reference to 'fast_hash(int)'
# → collect2: error: ld returned 1 exit status

# Only works when linking against v2.so (has the symbol)
g++ -shared -fPIC -std=c++17 -g v2.cpp -o libhash.so
g++ -std=c++17 -g app.cpp -I. -L. -lhash -Wl,-rpath,. -o app  # links OK
./app
# → fast_hash(42) = ...
```

**Why this needs coordinated deployment (not a binary ABI break):**
Existing binaries compiled against v1 (inline) are unaffected — they have
the inline body baked in, and abicheck's COMPATIBLE verdict is correct
for them. The failure hits **new consumers**: any code compiled against
v2.hpp (declaration only) that links against v1.so gets a hard linker
error because the symbol doesn't exist in v1.so. This forces v2.hpp and
v2.so to ship together — a packaging/release-coordination requirement, not
an ABI incompatibility abicheck's binary-vs-binary comparison would (or
should) flag.

## Compatibility matrix (consumer headers × runtime library)

| Consumer built against | Runtime library | Result |
|---|---|---|
| v1 header (inline, no symbol) | v1.so | ✅ works — caller uses its own inlined copy |
| v1 header (inline, no symbol) | v2.so (symbol exported) | ✅ works — caller's inlined copy is used; the new export is simply unused |
| v2 header (declaration only) | v2.so (symbol exported) | ✅ works — caller resolves the exported symbol |
| v2 header (declaration only) | v1.so (no symbol) | ❌ **link failure** — `fast_hash` was never exported by v1 |

Only the last row fails, and it requires a specific build mismatch (new
headers, old library) that a coordinated release naturally avoids.

## Why runtime result may differ from verdict
Inline→non-inline: old binary uses inlined copy, runtime unaffected

## References

- [C++ One Definition Rule](https://en.cppreference.com/w/cpp/language/definition)
- [libabigail `abidiff` manual](https://sourceware.org/libabigail/manual/abidiff.html)
