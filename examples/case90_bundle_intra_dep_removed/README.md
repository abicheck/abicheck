# Case 90: Bundle — intra-bundle removed symbol

**Category:** Bundle / cross-library | **Verdict:** 🔴 BREAKING
(per-library: NO_CHANGE for libalgo; BREAKING for libcore)

## What breaks
`libcore.so` removes `core_mul()`. `libalgo.so` is unchanged but still has an
undefined reference to `core_mul` (DT_NEEDED on `libcore.so.1`, `U core_mul`
in `.dynsym`). The new bundle no longer exports `core_mul` anywhere, so at
runtime `dlopen("libalgo.so")` fails with `undefined symbol: core_mul`.

## Real Failure Demo

**Severity: BREAKING / CROSS-LIBRARY LOAD FAILURE**

```bash
cmake -S examples -B /tmp/abicheck-examples-build -DCMAKE_BUILD_TYPE=Debug
cmake --build /tmp/abicheck-examples-build --target case90_bundle_intra_dep_removed_old_libalgo case90_bundle_intra_dep_removed_new_libalgo
PYTHONPATH=. python3 -m abicheck.cli compare-release   /tmp/abicheck-examples-build/case90_bundle_intra_dep_removed/old   /tmp/abicheck-examples-build/case90_bundle_intra_dep_removed/new   --format markdown
# bundle_intra_dep_removed: libalgo.so imports core_mul, but no new bundle library exports it.
```

## Why per-library compare misses it
- `compare libcore_v1 libcore_v2` correctly flags `func_removed: core_mul`.
- `compare libalgo_v1 libalgo_v2` reports `NO_CHANGE` — the library on its
  own is identical.

Neither comparison knows the **other side of the contract**. Looking at the
bundle as a whole, the contract is broken.

## What the bundle layer detects
`abicheck compare-release old/ new/` adds a cross-library section:

```text
## 🔗 Bundle (Cross-Library) Findings
- bundle_intra_dep_removed — core_mul (consumer: libalgo.so)
  - libalgo.so imports core_mul, but no library in the new bundle exports it.
    Runtime load of libalgo.so will fail with undefined symbol.
```

Exit code: 4 (BREAKING).

## Real-world analogue
oneDAL's `libonedal_thread.so` imports symbols from `libonedal_core.so`.
If a refactor moves or deletes an internal core symbol that thread still
needs, the per-library diff says "thread is fine, core changed", but the
runtime fails.
