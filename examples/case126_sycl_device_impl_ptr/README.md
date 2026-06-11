# Case 126: SYCL `device` impl pointer — `shared_ptr` → raw pointer

**Category:** C++ ABI | **Verdict:** 🔴 ABI BREAK (exit 4)

## Provenance — this is a real DPC++ ABI break

This case is distilled from a real change in the **Intel DPC++ / oneAPI SYCL**
runtime (the `intel/llvm` project):

- **Breaking change:** [intel/llvm#20821 — *"[SYCL] Use `device_impl *` instead
  of `shared_ptr<device_impl>` inside `sycl::device`"*](https://github.com/intel/llvm/pull/20821)
  (carries the `abi-break` label).
- **Symptom it produced:** [intel/llvm#20915 — *`sycl_symbols_windows.dump`
  test failing in post-commit*](https://github.com/intel/llvm/issues/20915),
  fixed up by [intel/llvm#21028](https://github.com/intel/llvm/pull/21028).
- **Guarded upstream by:** the ABI test suite at
  [`sycl/test/abi/`](https://github.com/intel/llvm/tree/sycl/sycl/test/abi)
  (`layout_*.cpp` record-layout goldens and the `sycl_symbols_{linux,windows}.dump`
  exported-symbol goldens).

> The code here is an original, self-contained miniature that reproduces the
> *shape* of the change (a `shared_ptr` member replaced by a raw pointer). It
> is **not** copied from intel/llvm and pulls in no SYCL headers.

## What breaks

`sycl::device` stored its implementation as a reference-counted
`std::shared_ptr<detail::device_impl>`. On a 64-bit target that member is two
pointers wide, so `sizeof(sycl::device) == 16`.

PR #20821 replaced it with a raw `detail::device_impl *` (the `device_impl` is
owned by the parent platform for the lifetime of the runtime, so the refcount
was unnecessary overhead). A raw pointer is one word, so
`sizeof(sycl::device)` shrinks to **8 bytes**.

Every consumer compiled against the old 16-byte layout is now wrong:

- code that holds a `device` **by value** (on the stack, or embedded in another
  struct) reserves the wrong amount of storage and computes wrong offsets for
  anything placed after it;
- a base/derived or container-of-`device` relationship shifts every following
  member.

The mangled names of `device`'s methods do **not** change, so a pure
exported-symbol dump (`sycl_symbols_*.dump`) does *not* see the layout change
directly — which is exactly why upstream's symbol-dump guard only caught the
*downstream* symbol churn on Windows and needed follow-up PRs (#20902, #21028)
to stabilise. abicheck's layout diff catches the **root cause**.

## Code diff

| v1 (before #20821) | v2 (after #20821) |
|--------------------|-------------------|
| `std::shared_ptr<detail::device_impl> impl;` | `detail::device_impl *impl;` |
| `sizeof(sycl::device) == 16` | `sizeof(sycl::device) == 8` |

## Why abicheck catches it

With DWARF or headers (L1/L2) abicheck reports:

```
type_size_changed  sycl::device   (16 → 8 bytes)  → BREAKING
```

**Without any DWARF (L0, symbols only)** abicheck still flags the related
binary-only signals through `diff_elf_layout.py`: had the change instead added
or removed a *virtual* method or a *base class*, the `_ZTV`/`_ZTI` object sizes
would shift and surface as `vtable_slot_count_changed` /
`rtti_inheritance_changed` — see `docs/reference/sycl-test-abi-coverage.md`.

## Reproduce manually

```bash
g++ -shared -fPIC -g -O0 -fvisibility=hidden -fvisibility-inlines-hidden v1.cpp -o libdev_v1.so
g++ -shared -fPIC -g -O0 -fvisibility=hidden -fvisibility-inlines-hidden v2.cpp -o libdev_v2.so
abicheck compare libdev_v1.so libdev_v2.so
echo "exit: $?"   # → 4 (ABI break)
```

## How to fix

Keep the public class size stable across releases with the PIMPL idiom: store a
single opaque pointer whose pointee layout (and ownership model) can change
freely, and never change the *public* class's size. If the ownership model must
change from shared to raw, bump the library SONAME so consumers relink.

## References

- [intel/llvm#20821](https://github.com/intel/llvm/pull/20821) — the breaking change
- [intel/llvm#20915](https://github.com/intel/llvm/issues/20915) — the symptom
- [intel/llvm#21028](https://github.com/intel/llvm/pull/21028) — the fixup
- [`sycl/test/abi/`](https://github.com/intel/llvm/tree/sycl/sycl/test/abi) — upstream ABI test suite
- [Itanium C++ ABI: Data layout](https://itanium-cxx-abi.github.io/cxx-abi/abi.html#data)
