# Case 178: Unnamed Type Leaks Into the Public ABI

**Category:** C++ Hygiene / Deployment Risk | **Verdict:** COMPATIBLE_WITH_RISK

## What this case is about

```cpp
// v1.h                                    // v2.h (adds a convenience overload)
extern "C" int pick_larger(int a, int b);  extern "C" int pick_larger(int a, int b);
                                            extern "C" int pick_by_policy(int a, int b);
```

```cpp
// v2.cpp
inline bool (*descending)(int, int) = [](int a, int b) { return a > b; };

int pick_by_policy(int a, int b) { return descending(a, b) ? a : b; }
```

`pick_by_policy` is a completely ordinary `extern "C"` function — its own
name is stable. But its implementation is built from a namespace-scope
lambda (`descending`) used as a default comparison policy, a common
header-only convenience pattern. Because that lambda has external linkage
(it is declared outside any function), the compiler-generated invoker for
its closure type is exported with **default visibility**, and its Itanium
mangling embeds the lambda's closure-type encoding:
`_ZN10descendingMUliiE_4_FUNEii`. Storing the lambda in a plain
function-pointer variable (rather than calling it inline through a
template) is deliberate: an optimizing build can fully inline a
template-dispatched call and eliminate the very symbol this case exists to
demonstrate, but once the lambda's *address* is observably stored in
`descending`, the compiler must keep a real, addressable out-of-line
function for it at every optimization level.

## Why this case matters: the exported name is not a name anyone chose

The Itanium ABI mangles a lambda closure as `Ul<signature>E[<ordinal>]_` —
an ordinal assigned by the compiler based on where it encounters the lambda
in that translation unit. No developer wrote `descending::{lambda#1}` in the
source; the compiler invented it. That means:

- A trivial, semantically-void source edit — reordering unrelated
  declarations, adding another lambda earlier in the same file — can change
  the ordinal and therefore the mangled name, even though `pick_by_policy`
  itself did not change at all.
- Different compiler versions or standard-library implementations may
  mangle or number closures differently.
- If two translation units are meant to share the "same" lambda-derived
  type (an ODR-sensitive design), a numbering mismatch between them is a
  silent ODR violation.

Nobody using the stable `pick_by_policy()` wrapper is affected. The risk is
for anything that depends on the raw symbol directly — a symbol-versioning
tool that freezes the exported symbol list, a debugger/profiler script, or
(rarely, but it happens) a second DSO in the same project that links
directly against the mangled name because "it works today."

## What abicheck detects

- **`unnamed_type_in_public_abi`** — a newly-exported symbol
  (`_ZN10descendingMUliiE_4_FUNEii`, demangled:
  `descending::{lambda(int, int)#1}::_FUN(int, int)`) embeds an Itanium
  unnamed-type token (`Ul...E_` for a lambda closure, `Ut..._` for an
  anonymous struct/enum). **Evidence tier L0** — the mangled name is parsed
  directly from the new side's exported symbol table; reported only for
  symbols *newly introduced* this revision, so a pre-existing leak does not
  spam every comparison.

**Overall verdict: COMPATIBLE_WITH_RISK** — nothing about the declared,
stable ABI surface (`pick_larger`, `pick_by_policy`) broke. The risk is
latent: a future, unrelated-looking change could silently rename this
symbol.

## How to reproduce

```bash
g++ -std=c++17 -shared -fPIC -g v1.cpp -o libv1.so
g++ -std=c++17 -shared -fPIC -g v2.cpp -o libv2.so

python3 -m abicheck.cli dump libv1.so -o /tmp/v1.json
python3 -m abicheck.cli dump libv2.so -o /tmp/v2.json
python3 -m abicheck.cli compare /tmp/v1.json /tmp/v2.json
# → COMPATIBLE_WITH_RISK: unnamed_type_in_public_abi
#   (descending::{lambda(int, int)#1}::_FUN(int, int))
```

The finding survives at any optimization level (`-O0` through `-O2`) — the
symbol's exact composition changes (at `-O0` GCC also emits the lambda's
`operator()` as a separate symbol; at `-O2` only the address-taken `_FUN`
invoker survives), but the `_FUN` invoker containing the `Ul...E_` token is
always present once its address is stored in `descending`.

## Real Failure Demo

```bash
g++ -std=c++17 -shared -fPIC -g v1.cpp -o libv1.so
g++ -std=c++17 app.cpp -o app -ldl
./app
# pick_larger(3, 7) = 7
# direct lookup of _ZN10descendingMUliiE_4_FUNEii: not present in this build

g++ -std=c++17 -shared -fPIC -g v2.cpp -o libv2.so
cp libv2.so libv1.so
./app
# pick_larger(3, 7) = 7
# direct lookup of _ZN10descendingMUliiE_4_FUNEii succeeded -- but do not
# rely on this exact name surviving a rebuild.
```

`app` looks up the raw mangled symbol by name via `dlsym`, deliberately
mirroring the fragile direct dependency the finding warns about. The stable
wrapper (`pick_larger`) resolves identically in both builds; the raw
closure symbol appears only once v2 introduces it, and nothing in the
Itanium ABI promises it will keep this exact spelling across a future
rebuild.

## Mitigation

- Do not link directly against a mangled symbol containing `Ul...E_` /
  `Ut..._` — always go through a named, stable wrapper.
- If a lambda-derived type must be part of a stable ABI, give it a real
  name: assign it to a named function object type, or wrap it in a named
  `struct` instead of using it anonymously.
- Symbol-versioning / ABI-freeze tooling should treat any newly-exported
  `Ul`/`Ut`-bearing symbol as informational noise to exclude from a frozen
  export list, not as a promise to keep.

## References

- [Itanium C++ ABI: closure types (`Ul...E`) and unnamed types (`Ut..._`)](https://itanium-cxx-abi.github.io/cxx-abi/abi.html#mangling-closure)
- Related cases: [case122_template_signature_uninstantiated](../case122_template_signature_uninstantiated/README.md)
