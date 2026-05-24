# Case 91: Bundle — intra-bundle extern-C signature drift

**Category:** Bundle / cross-library | **Verdict:** 🔴 BREAKING
(per-library: NO_CHANGE for libalgo; BREAKING for libcore)

## What breaks
`libcore.so` changes `core_add(int,int)` to `core_add(long,long)`.
Because `core_add` has C linkage, the **mangled name is identical** on
both sides — the dynamic linker happily resolves the symbol. But the
calling convention is now wrong:
- `libalgo.so` was compiled against the v1 declaration; its call sites
  push two 32-bit ints onto the stack / into `edi`/`esi`.
- The new `libcore.so` `core_add` reads 64-bit `rdi`/`rsi`, picking up
  garbage in the high halves of the registers.
- Result: undefined behaviour or wrong values, depending on caller layout.

## Real Failure Demo

**Severity: BREAKING / CROSS-DSO CALLING-CONVENTION MISMATCH**

```bash
cmake -S examples -B /tmp/abicheck-examples-build -DCMAKE_BUILD_TYPE=Debug
cmake --build /tmp/abicheck-examples-build --target case91_bundle_intra_signature_drift_old_libalgo case91_bundle_intra_signature_drift_new_libalgo
PYTHONPATH=. python3 -m abicheck.cli compare-release   /tmp/abicheck-examples-build/case91_bundle_intra_signature_drift/old   /tmp/abicheck-examples-build/case91_bundle_intra_signature_drift/new   --format markdown
# bundle_intra_dep_signature_changed: libalgo.so calls core_add but libcore.so changed its DWARF signature.
```

## Why per-library compare misses it
- `compare libcore_v1 libcore_v2` correctly flags `func_params_changed`
  and `func_return_changed` on `core_add`.
- `compare libalgo_v1 libalgo_v2` reports `NO_CHANGE` — libalgo's binary
  is byte-identical between versions.

Per-library compare has no concept of "library A's change affects library
B's calls".

## What the bundle layer detects
`abicheck compare-release old/ new/` flags both:

```text
## 🔗 Bundle (Cross-Library) Findings
- bundle_intra_dep_signature_changed — core_add (consumer: libalgo.so) (provider: libcore.so)
  - libalgo.so calls core_add (mangled name unchanged) but libcore.so
    altered its DWARF signature. Calling convention is now mismatched.
```

Exit code: 4 (BREAKING).

## Real-world analogue
oneDAL's internal `extern "C"` shims between threading layers and
algorithm kernels. A core type-width change (e.g. row count `int32_t →
int64_t`) silently breaks every algorithm `.so` that was compiled against
the old header.
