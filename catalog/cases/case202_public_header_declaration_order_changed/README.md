# Case 202: Public Header Declaration Order Changed

**Category:** No Change | **Verdict:** ✅ NO_CHANGE

## Verdict and consumer impact

The header lists `plot_reset()` before `plot_point()` instead of after, and
the `.c` file defines them in the new order too. No signature, type, or
export changed. Nothing a consumer can observe changed either: C has no
declaration-order-dependent ABI for free functions, and the exported symbol
set is identical.

This is the **negative control** paired with
[`case201_public_function_parameters_reordered`](../case201_public_function_parameters_reordered/README.md).
The pair separates the two things "reordering" can mean in a header: a
parameter list is an ordered calling contract (case201, flagged), while a
sequence of declarations is not (this case, not flagged).

## Old/new diff

| v1.h | v2.h |
|------|------|
| `void plot_point(int, double);`<br>`void plot_reset(void);` | `void plot_reset(void);`<br>`void plot_point(int, double);` |

## abicheck command

```bash
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -shared -fPIC -g v2.c -o libv2.so
abicheck compare libv1.so libv2.so --header old=v1.h --header new=v2.h
```

## Expected abicheck finding

```text
Verdict: NO_CHANGE (exit 0)

_No ABI changes detected._
```

`func_params_changed` is expected *not* to fire. That absence is the point of
the case: a diff keyed on source position rather than on declaration identity
would report both functions as changed here.

## Minimum evidence

`min_evidence: L2` — the public header AST is the evidence tier at which a
declaration-order change is even *visible*, so it is the tier at which the
"no finding" claim is worth making. At L0/L1 the two sides are trivially
identical and the case proves nothing.

## Why abicheck catches it

Nothing is reported because matching is by identity, not by position:
`diff_symbols.py` joins old and new declarations through
`SymbolIdentityIndex` (keyed on the mangled name, with an ambiguity-checked
`extern "C"` fallback) and `diff_helpers.TypeMap` does the same for types.
Only after a pair is matched are its own ordered parameters compared. A
positional walk over the header's declaration list would report two spurious
findings here.

## Runtime failure demonstration

**Severity: none — verified no observable effect.**

```bash
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -g app.c -L. -lv1 -Wl,-rpath,. -o app
./app
# → plotted (index=7, value=1.5)

gcc -shared -fPIC -g v2.c -o libv1.so   # swap in v2, no recompile
./app
# → plotted (index=7, value=1.5)   (identical output)
```

## Safe redesign

None needed — reordering declarations is a formatting change, and this case
exists to keep it that way in abicheck's own output.

## Cross-tool comparison

`abidiff` and ABICC also report nothing here. The case is a regression guard
for abicheck's identity-based matching rather than a differentiator.
