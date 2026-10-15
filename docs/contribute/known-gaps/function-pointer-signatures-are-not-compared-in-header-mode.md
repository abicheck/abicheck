---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A function-pointer parameter's signature is not compared, so a callback arity change reads `NO_CHANGE` on the header path (2026-10-15)

**Status (2026-10-15): OPEN.** Reproduced in plain C, so this is not Rust-specific.

Repro:

```c
/* old.c, new.c: typedef int (*cb_t)(int);  ->  typedef int (*cb_t)(int, int);
   int set_cb(cb_t cb);  (same declaration in both headers) */
```

`abicheck compare libold.so libnew.so -H old=old.h -H new=new.h` reports `NO_CHANGE`, and the DWARF-only comparison of the same two `.so` files also reports `NO_CHANGE`. The inline spelling `int set_cb(int (*cb)(int))` vs `(int, int)` is also `NO_CHANGE`.

The parameter record stores `type: "cb_t"` (or nothing, inline) with `kind: pointer`. The signature of the function the pointer points at is not part of the record, so no comparator sees it.

Rust exposes the same gap unevenly:

- `Option<extern "C" fn(i32) -> i32>` is caught on the DWARF path, but only because its generic type name carries the signature. That is a side effect of naming, not a comparison of signatures.
- A raw `extern "C" fn(c_int) -> c_int` parameter is `NO_CHANGE` on the DWARF path too.

Fix direction: capture the return type and parameter list of the pointee for function-pointer parameters and typedefs, in both the castxml and DWARF extractors, and compare them as a signature. This changes the type representation in `model/`, so it is not patched here.

Index: [Known gaps](../known-gaps.md)
