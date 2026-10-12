# Case 214: Convention Macro Redefined After Use (Negative Control)

**Category:** Calling Convention | **Verdict:** ✅ NO_CHANGE

## Verdict and consumer impact

Negative control for
[`case212`](../case212_calling_convention_macro_undefined_after_use/README.md).
`vector_dot` is declared through `MATHLIB_CALL = __attribute__((ms_abi))` in
both versions. After the declaration the header `#undef`s and redefines
`MATHLIB_CALL` for later use, and only that later definition differs
(`sysv_abi` in v1, `ms_abi` in v2). Both libraries are `ms_abi`, so an old
consumer works against either.

A reader that uses the final macro table expands the *later* definition into
the earlier declaration and reports a false `calling_convention_changed`;
the 2026-10-11 re-audit saw exactly that on the CastXML lane.

## Old/new diff

| v1.h | v2.h |
|---|---|
| `#define MATHLIB_CALL __attribute__((ms_abi))` | same |
| `MATHLIB_CALL double vector_dot(...);` | same |
| `#undef MATHLIB_CALL` / `#define MATHLIB_CALL __attribute__((sysv_abi))` | `#undef MATHLIB_CALL` / `#define MATHLIB_CALL __attribute__((ms_abi))` |

## abicheck command

```bash
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -shared -fPIC -g v2.c -o libv2.so
abicheck compare libv1.so libv2.so --header old=v1.h --header new=v2.h
```

## Expected abicheck finding

```text
Verdict: NO_CHANGE (exit 0)
  (no calling_convention_changed, no func_return_changed)
```

Same result with the CastXML and the Clang header backend
(`ABICHECK_AST_FRONTEND=castxml|clang`).

## Minimum evidence

`L2` (headers): the case is only meaningful where conventions are read from headers; without them there is nothing to misread.

## Why abicheck catches it

Each declaration is read from its own preprocessed line, so a definition that
comes after it cannot change it, exactly as for the compiler.

## Runtime failure demonstration

```bash
gcc app.c -L. -l:libv1.so -Wl,-rpath,. -lm -o app && ./app   # 32.0, exit 0
cp libv2.so libv1.so && ./app                                 # 32.0, exit 0
```

## Safe redesign

Nothing to fix: this is a compatible change.
