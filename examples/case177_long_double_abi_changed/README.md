# Case 177: long double ABI Changed

**Category:** Floating-Point ABI | **Verdict:** BREAKING

## What this case is about

```cpp
// v1.h                                  // v2.h
long double compute(long double x);      __float128 compute(__float128 x);
```

`compute`'s name and purpose are unchanged — "double the input" — but v2's
library was rebuilt to use IEEE 754 *quad* precision (`__float128`, from
`libquadmath`) instead of the platform's native 80-bit x87 `long double`.
Real toolchains make exactly this kind of representation swap: ppc64 moving
between IBM double-double and IEEE binary128, or a library opting into
`__float128` on x86/ARM for reproducible extended precision. Itanium
mangles `long double` as `e` and `__float128` as `g`, so **the exported
symbol name changes even though the C++ declaration still reads
"`compute`"**: `_Z7computee` → `_Z7computeg`.

## Why this case matters: same spelling, different wire format, different symbol

A plain symbol diff sees this as an ordinary remove-and-add (`_Z7computee`
disappeared, `_Z7computeg` appeared) — technically true, but it hides the
*reason*, which matters for anyone deciding whether this is safe to ship: it
is not a renamed function, it is the **same** function with a **different
floating-point representation** for its parameter and return value. A caller
that still passes an 80-bit `long double` bit pattern to what is now a
128-bit `__float128` slot gets silently wrong results if the mismatch is
ever papered over by an implicit conversion at a mixed build boundary — or,
far more commonly, the caller's binary simply fails to load or link at all
because `_Z7computee` no longer exists.

## What abicheck detects

- **`long_double_abi_changed`** — abicheck re-pairs the removed
  `_Z7computee` and added `_Z7computeg` symbols by their **demangled**
  signature (`compute(long double)` vs `compute(__float128)`, differing only
  in a long-double-family type), producing one finding that names the real
  transition instead of an unrelated-looking remove+add. **Evidence tier
  L0** — visible from the ELF symbol table's mangled names alone,
  demangled; no DWARF needed. (When DWARF is present, a *same-mangling*
  width flip — e.g. `-mlong-double-64` — is separately caught via the
  `long double` DWARF base-type size, tier L1.)
- A plain **`func_removed`** for `compute(long double)` also fires alongside
  it in this case (the mangled-name re-pairing does not suppress every
  companion finding) — both point at the same root cause.

**Overall verdict: BREAKING**

## How to reproduce

```bash
g++ -shared -fPIC -g v1.cpp -o libv1.so
g++ -shared -fPIC -g v2.cpp -o libv2.so -lquadmath

python3 -m abicheck.cli dump libv1.so -o /tmp/v1.json
python3 -m abicheck.cli dump libv2.so -o /tmp/v2.json
python3 -m abicheck.cli compare /tmp/v1.json /tmp/v2.json
# → BREAKING: long_double_abi_changed
#   (compute(long double) -> compute(__float128))
```

## Real Failure Demo

**Severity: BREAKING / UNDEFINED SYMBOL AT LOAD TIME**

```bash
g++ -shared -fPIC -g v1.cpp -o libv1.so
g++ -g app.cpp -I. -L. -lv1 -Wl,-rpath,. -o app
./app
# compute(21.0) = 42.000000 (expected 42.0)

g++ -shared -fPIC -g v2.cpp -o libv2.so -lquadmath
cp libv2.so libv1.so
./app
# app: symbol lookup error: app: undefined symbol: _Z7computee
```

The old app was linked against `_Z7computee`. v2's library exports
`_Z7computeg` instead — a different symbol, not a modified one — so the
dynamic linker refuses to resolve the reference at load time. There is no
silent-wrong-value case to demonstrate here precisely *because* the mangled
names differ: the load fails cleanly rather than corrupting data.

## Mitigation

- Treat a `long double`-family representation change as a full ABI break,
  even when no C++ signature in the header text visibly changed spelling.
- Bump the library's SONAME when changing the underlying floating-point
  representation for any exported symbol.
- If both representations must be supported (e.g. during a ppc64
  IEEE128 migration), ship both under distinct symbol versions rather than
  silently replacing one.

## References

- [Itanium C++ ABI: builtin type encodings (`e`, `g`)](https://itanium-cxx-abi.github.io/cxx-abi/abi.html#mangling-builtin)
- [GCC: `__float128` and the ppc64 `long double` transition](https://gcc.gnu.org/onlinedocs/gcc/Floating-Types.html)
