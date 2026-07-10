# UXL / oneAPI plugin source-scan run — binary↔header symbol mapping (2026-07)

Real `abicheck` run across the six UXL Foundation / oneAPI "plugin" libraries the
maintainer named, on a **clang-only L2 host** (`ABICHECK_AST_FRONTEND=clang`, no
castxml). Goal: confirm the plugin source-scan path (`dump` L2 → `surface-report`
→ `scan` → D4/D8 cross-checks) works end-to-end on each, that it *catches* the
right things, and to measure the **binary↔header/source AST symbol mapping** — the
fraction of exported dynamic symbols that resolve to a declaration abicheck parsed
from the public headers.

Raw consolidated data: `data/uxl_plugin_source_scan_2026-07.json`.
Reproduce the per-library measurement: `scripts/symbol_mapping_audit.py`.

## Subjects & artifacts

| Project | Conda-forge (linux-64) | Shared object | Headers | Lang |
|---------|------------------------|---------------|---------|------|
| oneTBB | `tbb 2023.0.0` + `tbb-devel` | `libtbb.so.12.18` (0.4 MB) | `oneapi/tbb.h` | C++ |
| oneDNN | `onednn 3.12` | `libdnnl.so.3.12` (74.7 MB) | `dnnl.hpp` + `dnnl_graph.hpp` + `dnnl_debug.h` + `dnnl_threadpool.hpp` | C++ |
| oneDAL | `dal 2026.1.0` (+ oneDAL git tag `2026.1.0` headers) | `libonedal_core.so.4` (110.9 MB) | `daal.h` | C++ |
| oneCCL | `oneccl-devel 2022.0.0` (**static-only**) | `libccl.a` → reconstructed `.so` | `oneapi/ccl.hpp` | C++ |
| level-zero | `level-zero 1.29.0` + `level-zero-devel` | `libze_loader.so.1.29.0` (1.3 MB) | `ze_api.h`/`zes_api.h`/`zet_api.h`/`zer_api.h` + `*_ddi.h` | C |
| compute-runtime | `intel-compute-runtime 24.52.32224.14` | `libigdrcl.so` (22.8 MB) | *(OpenCL ICD — no library headers shipped)* | n/a |

Two projects don't ship a header+`.so` pair on conda-forge and are handled
honestly rather than skipped:
- **oneDAL** — `dal-devel` ships **no headers** (cmake/pkgconfig only, every
  version checked back to 2023.x); headers came from the matching `oneapi-src/oneDAL`
  git tag.
- **oneCCL** — `oneccl-devel` ships a **static** `libccl.a`, no shared object.
  A `.so` was reconstructed with `gcc -shared -Wl,--whole-archive libccl.a` purely
  to exercise the scanner; because a static archive has no visibility scoping it
  exports every TU-internal symbol (95 k), so its mapping ratio is *not* a
  public-ABI figure — only its `public_not_exported` count (236) is meaningful.

## Result: everything runs, and it catches the right things

**The pipeline works on all six** — no crashes, correct coverage reporting across
C and C++, driver runtimes, a 110 MB binary, and a 95 k-symbol archive. The `scan`
front-end verdicts are correct (COMPATIBLE/advisory) with honest per-layer
coverage rows (e.g. `crosscheck:public_not_exported … capped at 200 of 1981`).

### Symbol-mapping table (authoritative, variant-aware matcher)

| Project | exports | mapped | unmapped | **mapping** | public-not-exported |
|---------|--------:|-------:|---------:|:-----------:|--------------------:|
| oneTBB | 106 | 103 | 3 | **97.2 %** | 1981 |
| oneDAL | 7072 | 5841 | 1231 | **82.6 %** | 7124 |
| level-zero | 1206 | 826 | 380 | **68.5 %** | 0 |
| oneDNN | 661 | 376 | 285 | **56.9 %** | 1189 |
| oneCCL¹ | 95190 | 39520 | 55670 | 41.5 %¹ | 236 |
| compute-runtime² | 4 | 0 | 4 | n/a² | 0 |

¹ archive-reconstructed, unscoped — ratio not a public-ABI figure (see above).
² GPU driver: the only exports are 4 OpenCL-ICD hooks (`clIcdGetPlatformIDsKHR`,
`clGetPlatformInfo`, `clGetExtensionFunctionAddress`, `GTPin_Init`). A driver has
**no header-defined public ABI**; its contract is the runtime-discovered dispatch
table, so "mapping" is not defined here — itself the correct finding.

### Is "100 % symbol mapping" real?

Not literally — and it *shouldn't* be, because every one of these libraries
exports symbols that no public header declares. The right reading is: **100 % of
the header-declared public API maps**, and the unmapped remainder is exactly the
accidental-ABI / leak surface the scan is designed to surface. Every unmapped
symbol falls into a legitimately-non-public bucket; **no false resolver gap was
found** (a public, header-declared symbol that failed to resolve):

- **Statically-leaked third-party code** — oneDNN leaks **168** libstdc++ symbols
  (`std::regex`/`__detail` guard variables etc.); level-zero leaks **50 {fmt}** +
  **37 libstdc++**. These are real build-hygiene problems in the upstream packages;
  abicheck correctly refuses to map them and flags them as `exported_not_public`.
- **Internal namespaces** exported as accidental ABI — `dnnl::impl::*` (83),
  `daal::…::internal` (871). Real accidental-surface findings.
- **RTTI/vtable for internal types** — internal `_ZTI…/_ZTV…` (oneTBB's r1 exception
  classes; level-zero internals).
- **Explicit template instantiations** — oneDAL `Result::allocate<double>` /
  `<float>`: the header declares the *template*, the binary exports the
  *instantiation*, so there is no single mangled decl to match. Correct, and a
  candidate future enhancement (recognise `extern template` instantiations).
- **Interop entries needing an unshipped SDK header** — oneDNN OpenCL/SYCL/L0
  interop (`dnnl_ocl_*`, `dnnl_sycl_*`); level-zero internal loader C symbols.
- **oneTBB's 3 unmapped** are `tbb::detail::r1::{enter,exit}_parallel_phase` /
  `construct` — preview-gated runtime-interface entry points, not in the default
  public headers. So oneTBB is effectively **100 % of its stable public surface**.
- **level-zero `public_not_exported = 0`** — every public `ze*/zes*/zet*` C-API
  declaration has a matching loader export. Clean both directions.

## Methodology note that mattered (avoid a false negative in *validation*)

A naive exact mangled-name compare badly under-counts mapping: on oneDAL it reported
26 % because the binary exports the C2 (base-object) constructor variant while the
header parse yields the C1 (complete-object) variant, and the two strings differ.
abicheck's own `exported_not_public` check **already normalises** Itanium ctor/dtor
variants (C1/C2/C3, D0/D1/D2) and ABI-tag drift (`crosscheck.py`), recovering ~4000
oneDAL symbols → the real 82.6 %. The audit script therefore measures with
abicheck's matcher (cap disabled), not a string compare. Confirmed pair:
`kmeans::init::interface1::Result::Result()` — binary `…ResultC2Ev` (unmapped by
string compare) ↔ parsed `…ResultC1Ev` (public), matched by the variant-aware check.

Second methodology note: mapping is only as complete as the umbrella headers fed to
`-H`. oneDNN rose 26 % → the reported figure once `dnnl_graph.hpp` / `dnnl_debug.h`
/ `dnnl_threadpool.hpp` were added alongside `dnnl.hpp` (which pulls only the core C
API). To validate full public-API coverage, feed the complete public header set, not
just the top umbrella.

## Follow-ups (opportunities, not defects)

| # | Sev | Item |
|---|-----|------|
| M1 | Low | Recognise explicit/`extern template` instantiations so template-heavy surfaces (oneDAL) map their exported instantiations, not just the template decl. |
| M2 | Info | `exported_not_public` per-check cap (200) truncates the *summary* count; the coverage row is honest (`capped at 200 of N`), but a headline audit reader may miss the true total. Consider surfacing the uncapped total in `surface-report --audit` JSON. |
| M3 | Info | conda-forge `dal-devel` ships no headers and `oneccl-devel` is static-only; document that a header+`.so` mapping run for these needs upstream git headers / a source build. |

No tool changes were required for the pipeline to run correctly on any of the six;
the findings above are hygiene issues in the *scanned* libraries (which is the
point) plus two small enhancement ideas.
