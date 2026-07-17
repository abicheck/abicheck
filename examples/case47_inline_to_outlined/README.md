# Case 47: Inline Function Moved to Outlined

**Category:** Compatible | **Verdict:** 🟢 COMPATIBLE

## What does NOT break

In v1, `Calculator::add()` is defined `inline` in the header — no exported symbol.
In v2, it is moved out-of-line — the symbol is now exported from the `.so`.

Consumers compiled against v1 already have the inlined body baked into their binary.
Consumers compiled against v2 will call the exported symbol. Both work correctly.
No existing binary breaks — this is a **FUNC_ADDED** (compatible extension).

## Why abidiff sees it as compatible

abidiff reports `Function_Symbol_Added` and exits **4** (change detected), but the
change kind is additive. abicheck classifies as `COMPATIBLE` (FUNC_ADDED).

## Code diff

| v1.hpp | v2.hpp |
|--------|--------|
| `inline int add(int a, int b) { return a + b; }` | `int add(int a, int b);` — definition in v2.cpp |
| No exported symbol for `add` | Symbol `_ZN10Calculator3addEii` now exported |

## Real Failure Demo

**Severity: ✅ BASELINE — no failure**

```bash
g++ -shared -fPIC -g v1.cpp -o libv1.so
g++ -shared -fPIC -g v2.cpp -o libv2.so

abidw --out-file v1.abi libv1.so
abidw --out-file v2.abi libv2.so
abidiff v1.abi v2.abi
echo "exit: $?"   # → 4 (FUNC_ADDED — change detected, but compatible)
nm -D libv2.so | grep add   # → T _ZN10Calculator3addEii (now exported)
```

## Reproduce manually

```bash
g++ -shared -fPIC -g v1.cpp -o libv1.so
g++ -shared -fPIC -g v2.cpp -o libv2.so
abidw --headers-dir . --out-file v1.abi libv1.so
abidw --headers-dir . --out-file v2.abi libv2.so
abidiff v1.abi v2.abi
```

## Why this is still a risk

While ABI-compatible, moving inline→outlined is a **source-level change**: any
consumer that relied on the inlined body being optimized away (e.g. in `constexpr`
contexts or LTO-heavy builds) may see different behavior. Document the change.

There is also a build-coordination risk that abicheck's binary-vs-binary
COMPATIBLE verdict does not itself flag, since it compares two libraries,
not a specific consumer's header/library pairing: a consumer compiled
against **v2's** header (declaration only, no inline body) but linked
against **v1's** `.so` (which never exported `add` — it was inline) gets a
hard linker error, `undefined reference to Calculator::add(int, int)`.
That's not an ABI break in the usual sense (no *existing* binary stops
working), but it does mean v2's header and v2's `.so` must ship together —
see the compatibility matrix below and case16 (`inline_to_non_inline`),
which demonstrates the same mechanism with a free function and spells out
that failure mode end to end.

## Compatibility matrix (consumer headers × runtime library)

| Consumer built against | Runtime `.so` | Result |
|---|---|---|
| v1 header (inline, no symbol) | v1 `.so` | ✅ works — caller uses its own inlined copy |
| v1 header (inline, no symbol) | v2 `.so` (symbol exported) | ✅ works — caller's inlined copy is used; the new export is simply unused |
| v2 header (declaration only) | v2 `.so` (symbol exported) | ✅ works — caller resolves the exported symbol |
| v2 header (declaration only) | v1 `.so` (no symbol) | ❌ **link failure** — `add` was never exported by v1 |

Only the last row fails, and it requires a specific build mismatch (new
headers, old library) that a coordinated release naturally avoids.
