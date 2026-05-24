# Case 93: Bundle — instantiation manifest drift

**Category:** Bundle / manifest
**Bundle verdict (with `--manifest`):** 🔴 BREAKING
**Bundle verdict (without `--manifest`):** 🟢 NO_CHANGE
**Combined verdict (per-library worst-of × bundle):** 🔴 BREAKING — the
per-library diff still flags `func_removed` even when no manifest is
supplied; `--manifest` upgrades the diagnosis from "a symbol vanished" to
"a documented public promise was broken".

## What changed
The release ships a single library `libcore.so` with four explicit template
instantiations:

| Symbol                | v1  | v2  |
|-----------------------|-----|-----|
| `train_float_dense`   | ✅  | ✅  |
| `train_float_sparse`  | ✅  | ✅  |
| `train_double_dense`  | ✅  | ✅  |
| `train_double_sparse` | ✅  | ❌ (dropped)  |

In real oneDAL these are mangled C++ symbols for
`train_ops<Float, Method, Task>` triples. Dropping one without
documentation is a silent contract violation: downstream code that
instantiated the dropped triple will fail to link.

## Real Failure Demo

**Severity: BREAKING / MANIFEST PROMISE REMOVED**

```bash
cmake -S examples -B /tmp/abicheck-examples-build -DCMAKE_BUILD_TYPE=Debug
cmake --build /tmp/abicheck-examples-build --target case93_bundle_manifest_drift_old_libcore case93_bundle_manifest_drift_new_libcore
PYTHONPATH=. python3 -m abicheck.cli compare-release   /tmp/abicheck-examples-build/case93_bundle_manifest_drift/old   /tmp/abicheck-examples-build/case93_bundle_manifest_drift/new   --manifest examples/case93_bundle_manifest_drift/manifest.yaml   --format markdown
# bundle_manifest_instantiation_removed: train_double_sparse is promised but no longer exported.
```

## Why this needs a manifest
Per-library `func_removed` detection already flags the missing symbol —
but it can't tell whether the symbol was a *promised* part of the public
ABI or an internal helper that happened to be visible. Without an
explicit manifest, the tool has to choose between:
- Treating every removed symbol as BREAKING (lots of false positives for
  internal helpers).
- Treating every removed symbol as a free choice (misses real contract
  violations like this one).

The `--manifest` input externalises the contract: it lists exactly the
symbols the release promises to keep. The bundle layer then enforces
"every manifest entry must be exported by some library in the new
bundle".

## Reproducing
```bash
abicheck compare-release old/ new/ --manifest manifest.yaml
```

Expected output:
```text
## 🔗 Bundle (Cross-Library) Findings
- bundle_manifest_instantiation_removed — train_double_sparse
  - Manifest promises train_double_sparse but no library in the new
    bundle exports it.
```

Exit code: 4 (BREAKING).

Without `--manifest`, the run still flags `func_removed` per-library
(BREAKING), but the diagnosis is "a symbol disappeared" rather than
"the documented contract was violated".

## Real-world analogue
oneDAL maintains explicit instantiation lists for its algorithms (the
build-system file enumerates which `(Float, Method, Task)` triples are
instantiated). Refactors that change these lists are ABI changes — but
only the bundle level can detect them.
