# Case 169: Overload Added to a Previously Unique Function

**Category:** Overload Resolution / Source Compatibility | **Verdict:** COMPATIBLE_WITH_RISK

## What breaks

v2 adds a `float` overload next to the until-now unique
`units::to_celsius(double)`:

```cpp
double to_celsius(double fahrenheit);   // v1 and v2 — symbol untouched
float  to_celsius(float fahrenheit);    // NEW in v2
```

Nothing breaks at the binary level: the `double` symbol
(`_ZN5units10to_celsiusEd`) is byte-for-byte untouched and old binaries keep
working. The risk fires at the **next recompile** of every consumer:

1. **Silent re-routing.** A v1-era call like `to_celsius(98.6f)` promoted the
   `float` to `double`. Recompiled against v2 headers, overload resolution now
   prefers the exact-match `float` overload — different precision, different
   rounding, no warning, no diff at the call site.
2. **Address-taking breaks.** `auto fp = &units::to_celsius;` was
   unambiguous in v1; under v2 it fails with *"unable to deduce 'auto' from
   '& units::to_celsius'"*. The same ambiguity hits template argument
   deduction (`std::invoke`, `std::bind`, callback registration).

## Why this matters

- **KDE's BC policy calls this out explicitly**: adding an overload to a
  previously non-overloaded function is binary-compatible but **not
  source-compatible**, and it changes what existing call sites *mean* — the
  most invisible kind of behavior change.
- **It never shows up in symbol diffs as anything but an addition.** Without
  overload-set awareness, this is indistinguishable from a harmless new
  function (case 03). The signal is relational: a name that *was unique*
  gained a sibling.
- **The precision-drift variant is insidious**: numerical output changes
  between two "compatible" consumer builds, and the library diff everyone
  inspects shows only an addition.

## Code diff

```cpp
// v1
namespace units {
double to_celsius(double fahrenheit);   // the unique declaration
}

// v2
namespace units {
double to_celsius(double fahrenheit);   // unchanged
float  to_celsius(float fahrenheit);    // NEW — overload set now has 2 members
}
```

## Real Failure Demo

**Severity: INFORMATIONAL**

**Scenario:** old binaries are untouched — the demo shows binary compatibility
holding, with the hazard waiting at recompile time:

```bash
# Build old library + app
g++ -shared -fPIC -g v1.cpp -o liblib.so
g++ -g app.cpp -L. -llib -Wl,-rpath,. -o app
./app
# → to_celsius(98.6f) = 37.0000 (expected 37.0)

# Swap in new library (no recompile)
g++ -shared -fPIC -g v2.cpp -o liblib.so
./app
# → to_celsius(98.6f) = 37.0000 (expected 37.0)   ← binary-compatible

# The break is at recompile time, against the v2 header:
cat > amb.cpp <<'SRC'
#include "v2.h"
int main() { auto fp = &units::to_celsius; (void)fp; }
SRC
g++ -c amb.cpp -I.
# → error: unable to deduce 'auto' from '& units::to_celsius'
```

And the silent part: recompiling the *original* app source against v2 headers
binds `to_celsius(98.6f)` to the `float` overload — the result is computed in
`float` precision from then on, with no diagnostic.

## How to fix

1. **Name the new function** instead of overloading (`to_celsius_f()`, or a
   template with explicit constraints) when the existing name has shipped as
   unique.
2. **If the overload must land**, document the re-routing in release notes and
   grep consumer code for address-taking (`&to_celsius`) and
   float-argument call sites.
3. `= delete` **the risky overload direction** when the goal is to *forbid*
   lossy calls rather than add a fast path (`float to_celsius(float) = delete;`
   makes float callers explicit instead of silently re-routed — that deletion
   is its own API break, but a loud one).

## Real-world example

The KDE Frameworks binary-compatibility policy ("you can... add new
non-virtual functions **but** note that adding an overload to a function that
previously had none can break source compatibility") is the canonical write-up.
`std::filesystem::path` construction and `std::to_chars` overload growth both
triggered exactly this class of downstream deduction breakage during
standard-library evolution.

## abicheck detection

abicheck groups public functions by their scope-qualified name (parsed
structurally from the mangled symbol) and reports `overload_added` (RISK) when
a name that had exactly one declaration gains siblings while the original
symbol survives — distinguishing it from a plain signature change
(remove+add) and from an unrelated same-leaf name in another scope. Works
from symbols alone (`min_evidence: L0`).

```bash
abicheck compare libv1.so libv2.so --old-header v1.h --new-header v2.h
# Verdict: COMPATIBLE_WITH_RISK (overload_added: units::to_celsius, 1 → 2 overloads)
```

## References

- [KDE ABI Policy — "adding new overloads" bullet](https://community.kde.org/Policies/Binary_Compatibility_Issues_With_C%2B%2B)
- [Itanium C++ ABI — function mangling encodes parameter types](https://itanium-cxx-abi.github.io/cxx-abi/abi.html#mangling)
