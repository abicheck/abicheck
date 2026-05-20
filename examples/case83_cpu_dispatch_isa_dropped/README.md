# Case 83: CPU-dispatch ISA family dropped

**Category:** Dispatch ABI | **Verdict:** COMPATIBLE_WITH_RISK

## What breaks

Performance-oriented libraries (oneDAL's `libonedal_core.so`, OpenBLAS,
many ML runtimes) ship multiple ISA-specialized symbols per algorithm:

```
mylib::kmeans_compute            ← runtime dispatcher (selects best ISA)
mylib::kmeans_compute_avx512
mylib::kmeans_compute_avx2
mylib::kmeans_compute_sse42
mylib::kmeans_compute_scalar
```

In v2 the project drops AVX-512 support to shrink the binary. **All**
`*_avx512_*` symbols vanish across **all** algorithms. The dispatcher
itself still works — consumers who never pinned to a specific ISA are
unaffected. Consumers who linked directly against `kmeans_compute_avx512`
(common in test scaffolding, micro-benchmarks, or external integrations
that bypass the dispatcher for reproducibility) get unresolved symbols at
load time.

## Why a separate ChangeKind (not just N×func_removed)

If reported as 50 independent `func_removed` findings, the deployment-level
event ("we dropped an ISA family") is buried. Suppression becomes painful
(one rule per algorithm × ISA), the SARIF report is dominated by repeats,
and the verdict severity is wrong: each individual removal looks like a
hard break, but the dispatcher-using majority is unaffected.

A single `CPU_DISPATCH_ISA_DROPPED` finding classified as
`COMPATIBLE_WITH_RISK` (RISK_KINDS) names the event once, lists the
affected algorithms, and lets policy decide whether the deployment
target still has callers pinned to that ISA.

## How abicheck detects it

The new detector clusters removed symbols by ISA infix tokens (`avx512`,
`avx2`, `avx`, `sse42`, `sse41`, `sse2`, `sse`, `neon`, `sve`, `scalar`,
`generic`). When ≥ K removed symbols share one ISA token AND a sibling ISA
token still exists for the same algorithm stem in the new snapshot, a
single grouped `CPU_DISPATCH_ISA_DROPPED` finding is emitted. The
per-symbol `func_removed` findings are suppressed as children to avoid
double-counting.

## Real-world reference

oneDAL: `cpp/daal/src/services/service_environment.cpp` and the per-kernel
dispatch tables. ISA-specialized symbols have suffixes like
`_avx512_`, `_avx2_`, `_sse42_`, `_sse2_`, `_ref_`.
