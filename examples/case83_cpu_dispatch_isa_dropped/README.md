# Case 83: CPU-dispatch ISA family dropped

**Category:** Dispatch ABI | **Verdict:** ⚠️ COMPATIBLE_WITH_RISK

## Verdict and consumer impact

Performance-oriented libraries (oneDAL's `libonedal_core.so`, OpenBLAS, many
ML runtimes) ship multiple ISA-specialized symbols per algorithm — a
runtime dispatcher plus `_avx512`/`_avx2`/`_sse42`/`_scalar` variants callers
can pin to directly. In v2 the project drops AVX-512 support to shrink the
binary: **every** `*_avx512` symbol vanishes across **all** algorithms. The
dispatcher itself still works — consumers who never pinned to a specific ISA
are unaffected. Consumers who linked directly against `kmeans_compute_avx512`
(common in test scaffolding, micro-benchmarks, or integrations that bypass
the dispatcher for reproducibility) get an unresolved symbol at load time.
Binary-compatible for the common case; verify no deployed consumer pins the
dropped ISA before shipping.

## Old/new diff

| v1.h | v2.h |
|------|------|
| `kmeans_compute_avx512(int)`, `knn_compute_avx512(int)`, `linreg_compute_avx512(int)` | *(all three removed)* |
| `kmeans_compute_avx2/sse42/scalar(int)`, ... | *(unchanged, still present)* |

## abicheck command

```bash
g++ -shared -fPIC -g -std=c++17 -I. v1.cpp -o libfoo_v1.so
g++ -shared -fPIC -g -std=c++17 -I. v2.cpp -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so \
  --ast-frontend clang -H old=v1.h -H new=v2.h --lang c++
```

## Expected abicheck finding

```text
Verdict: COMPATIBLE_WITH_RISK (exit 0)

Deployment Risk Changes:
- cpu_dispatch_isa_dropped: CPU dispatch ISA 'avx512' tier removed: 3
  specialisations across 3 algorithms (kmeans_compute, knn_compute,
  linreg_compute).
  > Runtime dispatcher continues to work; consumers that pinned directly
    to 'avx512' symbols get unresolved references at load time.
```

## Minimum evidence

`min_evidence: L0` — the removed/surviving symbol sets alone carry the
clustering signal (ISA-token infixes on the mangled names, correlated
against which algorithm stems still have a sibling ISA). The command above
also supplies header/AST evidence (`--ast-frontend clang`, since `castxml`
is unavailable in this environment) so the demangled, namespace-qualified
names the ISA-token matcher keys off are available deterministically; a
production install with `castxml` reaches the same finding from `-H` alone,
or from raw exported-symbol names once demangled.

## Why abicheck catches it

`detect_cpu_dispatch_isa_dropped` clusters removed exported functions by ISA
infix token (`avx512`, `avx2`, `sse42`, ..., `scalar`). When three or more
removed symbols share one token *and* the same algorithm stem still exists
under a sibling ISA in the new snapshot, it emits one grouped
`CPU_DISPATCH_ISA_DROPPED` finding (`RISK_KINDS`) instead of N independent
`func_removed` findings — the per-symbol removals are suppressed as children
so the report names the deployment-level event once.

## Runtime failure demonstration

**Severity: RISK (load-time failure for ISA-pinned callers only)**

**Scenario:** app calls both the dispatcher (`kmeans_compute`, unaffected)
and the pinned AVX-512 specialization (`kmeans_compute_avx512`, removed).

```bash
# Build old library + app
g++ -shared -fPIC -g -std=c++17 -I. v1.cpp -o libfoo.so
g++ -g -std=c++17 -I. app.cpp -L. -lfoo -Wl,-rpath,. -o app
./app
# → dispatch=10 avx512=522

# Swap in new library (no recompile)
g++ -shared -fPIC -g -std=c++17 -I. v2.cpp -o libfoo.so
./app
# → ./app: symbol lookup error: ./app: undefined symbol: _ZN5mylib21kmeans_compute_avx512Ei
```

**Why RISK not BREAKING:** the dispatcher-using majority of callers observe
no change at all — only callers that bypassed the dispatcher to pin a
specific ISA are affected, which is why this is `COMPATIBLE_WITH_RISK`
rather than a hard break.

## Safe redesign

Never drop an ISA tier outright if any consumer might pin to it directly.
Keep the specialized symbol as a thin wrapper that forwards to the next-best
available ISA, or gate the removal behind a major version bump and document
it as a supported-hardware change, not a routine binary-compatible release.

**Real-world example:** oneDAL's `cpp/daal/src/services/service_environment.cpp`
and its per-kernel dispatch tables follow exactly this convention —
ISA-specialized symbols suffixed `_avx512_`, `_avx2_`, `_sse42_`, `_sse2_`,
`_ref_` per algorithm.

## Cross-tool comparison

```bash
abidw --out-file v1.xml libfoo_v1.so
abidw --out-file v2.xml libfoo_v2.so
abidiff v1.xml v2.xml
```

Not independently re-verified in this environment (`abidiff` unavailable
here) — see case01's symbol-removal case for a documented `abidiff`
exit-code comparison on the same kind of raw symbol removal.
