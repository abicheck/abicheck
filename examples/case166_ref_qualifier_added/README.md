# Case 166: Method Ref-Qualifier Added (`str()` → `str() &`)

**Category:** Function Signature / Mangling | **Verdict:** BREAKING

## What breaks

v2 adds an **lvalue ref-qualifier** to `MessageBuilder::str()`:

```cpp
const char* str();      // v1: callable on lvalues and rvalues
const char* str() &;    // v2: lvalue-only
```

The motivation is sound API hardening: `MessageBuilder().str()` returns a
pointer into a temporary that dies at the end of the expression, and the `&`
qualifier makes that dangling call a compile error. But the ref-qualifier is
part of the Itanium mangling — it is encoded right after the CV-qualifiers in
the nested-name:

| Declaration | Mangled symbol |
|---|---|
| `str()` | `_ZN14MessageBuilder3strEv` |
| `str() &` | `_ZN`**`R`**`14MessageBuilder3strEv` |
| `str() &&` | `_ZN`**`O`**`14MessageBuilder3strEv` |

The old symbol vanishes from the library, so every existing binary fails at
load time with an undefined-symbol error — a source-level annotation that is
invisible at any call site becomes a hard ABI break.

## Why this matters

- **Ref-qualifiers look "free".** Unlike changing a parameter type, adding
  `&`/`&&` doesn't change what well-behaved callers write, so it routinely
  slips through review as a harmless tightening. It renames the symbol
  exactly like adding `const` does (case 22).
- **The `&&`-overload pattern is spreading.** Modern APIs add
  `value() &&`-style move-out accessors (as `std::optional`/`std::expected`
  do); retrofitting qualifiers onto an *existing* exported out-of-line method
  is where the break happens. Header-only/inline methods are immune — exported
  ones are not.
- **Both directions break.** Removing a qualifier renames the symbol right
  back (`ctor_explicit`-style one-way doors don't apply here).

## Code diff

```cpp
// v1
class MessageBuilder {
public:
    MessageBuilder& append(const char* part);
    const char* str();            // _ZN14MessageBuilder3strEv
};

// v2
class MessageBuilder {
public:
    MessageBuilder& append(const char* part);
    const char* str() &;          // _ZNR14MessageBuilder3strEv  ← new symbol!
};
```

## Real Failure Demo

**Severity: CRITICAL**

**Scenario:** compile app against v1, link to v2 `.so` without recompiling.

```bash
# Build old library + app
g++ -shared -fPIC -g v1.cpp -o liblib.so
g++ -g app.cpp -L. -llib -Wl,-rpath,. -o app
./app
# → message = status=ok (expected status=ok)

# Swap in new library (no recompile)
g++ -shared -fPIC -g v2.cpp -o liblib.so
./app
# → ./app: symbol lookup error: ./app: undefined symbol: _ZN14MessageBuilder3strEv
```

The app aborts before `main()` finishes symbol binding — the classic
undefined-symbol loader failure.

## How to fix

1. **Add, don't replace**: keep the unqualified `str()` exported (possibly as a
   deprecated out-of-line definition forwarding to the qualified one) until the
   next SONAME bump.
2. **Qualify at birth**: decide `&`/`&&` when the method is first shipped;
   qualifiers on day one cost nothing.
3. **SONAME bump** if the hardening must land now.

## Real-world example

This is the same mechanics as the `const`-qualifier break (case 22), which the
KDE binary-compatibility policy lists among the "you cannot..." rules: *any*
change to a function's cv- or ref-qualification changes the mangled name. The
`&&`-qualified accessor idiom popularized by `std::optional::value() &&`
(C++17) made retrofit-qualifying older accessors a recurring temptation in
library changelogs.

## abicheck detection

abicheck matches the removed and added declarations by (name, parameters) and
reports `func_ref_qual_changed` (BREAKING) — `'' → '&'` — instead of leaving
an unexplained removed+added pair. Detection needs the header AST
(`min_evidence: L2`): released castxml versions do not emit a ref-qualifier
attribute, so abicheck recovers it from the Itanium mangling (`_ZNR…`/`_ZNO…`).

```bash
abicheck compare libv1.so libv2.so --header old=v1.h --header new=v2.h
# Verdict: BREAKING (func_ref_qual_changed: str, (none) → &)
```

## References

- [Itanium C++ ABI — mangling, `<nested-name>` ref-qualifier](https://itanium-cxx-abi.github.io/cxx-abi/abi.html#mangling)
- [KDE ABI Policy — Binary Compatibility Issues](https://community.kde.org/Policies/Binary_Compatibility_Issues_With_C%2B%2B)
- [P0798 / `std::optional::value() &&` — the move-out accessor idiom](https://en.cppreference.com/w/cpp/utility/optional/value)
