# Scanning a Conda-Forge Package

This runbook covers the specific friction of pointing `abicheck` at a library
you got from **conda-forge** (or any binary-distribution channel): finding the
pieces, mapping versions, and the packaging shapes that need special handling.
For the general flow and report walkthrough, see
[Worked Example: Scanning a Library](real-world-example.md).

## 1. The pieces live in different packages

A conda package is usually split, and `abicheck` needs parts from more than one:

| You need | Typically in | Example |
|----------|--------------|---------|
| the runtime `.so` (the **binary** to scan) | the **runtime** package | `tbb`, `onednn`, `dal` |
| the **public headers** | the `*-devel` package | `tbb-devel` |
| headers in a **third** package (some recipes) | a dedicated `*-include` | `dal-include` (oneDAL) |

!!! warning "`*-devel` may be CMake/pkgconfig only"
    For some recipes (e.g. oneDAL) the `-devel` package ships only
    `lib/cmake` + `lib/pkgconfig` and the **headers live in a separate
    `*-include` package**. If your `-H` directory looks empty of `.h`/`.hpp`,
    you're pointed at the wrong package.

You don't need the `conda` CLI to get these — a `.conda` file is a zip of
zstd-compressed tarballs; `unzip pkg.conda`, then extract the `pkg-*` member
(`zstd -d < pkg-*.tar.zst | tar -x`) to recover its `lib/` and `include/` trees.

## 2. Map the conda version to an upstream tag

Conda's version is the **product** version, which often differs from the
upstream git tag. Read the version header to pin the matching source:

| Library | Conda version | Version header to read | Upstream tag |
|---------|---------------|------------------------|--------------|
| oneTBB | `2023.0.0` | `oneapi/tbb/version.h` (`TBB_VERSION_*`, `TBB_INTERFACE_VERSION`) | `v2023.0.0` |
| oneDNN | `3.12` | `oneapi/dnnl/dnnl_version.h` (`DNNL_VERSION_*`) | `v3.12` |
| oneDAL | `2026.1.0` | `services/library_version_info.h` | `2026.1.0` |

When in doubt, the SONAME (`libtbb.so.12.18` → interface 12, patch 18) and the
version header together identify the release.

## 3. Pass the public surface with the umbrella header

Point `-H` at the library's **umbrella header** — the single header a consumer
includes (`oneapi/tbb.h`, `dnnl.hpp`, `oneapi/dal.hpp`). abicheck now adds the
header's include root (the umbrella's directory and any ancestor named
`include`/`inc`) to the compiler search path automatically, so for a standard
layout no separate `-I` is needed. A non-standard layout still needs `-I`, and
any `-I`/`--include` you pass takes precedence over the auto-added roots:

```bash
export ABICHECK_AST_FRONTEND=clang        # on a clang-only host (no castxml)
abicheck scan --binary lib/libtbb.so.12.18 \
  -H include/oneapi/tbb.h \
  --public-header-dir include \
  --lang c++ --audit --depth headers
```

`--public-header-dir` establishes the public/internal boundary so the
single-release hygiene cross-checks run (it is what lets abicheck classify
which declarations are public).

!!! tip "Prefer the umbrella over the include *directory*"
    Passing `-H <include-dir>` makes abicheck parse **every** header in the
    tree as one translation unit — which pulls in *optional backend* headers
    (OpenCL/SYCL: `dnnl_ocl.h` → `CL/cl.h`) and *preview* headers gated by
    `#error` macros (oneTBB's `blocked_rangeNd.h`). Those need their SDK or a
    `--gcc-option=-DXXX_PREVIEW` to parse. The umbrella header includes only the
    library's curated, default-public surface, so it sidesteps both. Use the
    umbrella unless you specifically want a preview/backend header analysed.

## 4. Going deeper needs a build

`--depth headers` (L2) works from the binary + headers alone. The deeper levels
(`build`/`source`/`full` → L3/L4/L5) read a **`compile_commands.json`**:

- abicheck auto-discovers one under the source tree (`.`, `build/`, `builddir/`,
  `out/`, `_build/`, `cmake-build-debug/`, **or any immediate subdirectory**);
- a *fresh checkout has none* — generate it (CMake: configure with
  `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`; Meson: emitted by `meson setup`;
  Make/Autotools: `bear -- make`), or pass `--build-info <dir|compile_commands.json>`.

Without one, L3/L4/L5 report `not_collected` and only the compiler-free pattern
pre-scan runs. On a large tree, scope that pre-scan with `--since <ref>` /
`--changed-path <file>` (or set `ABICHECK_PATTERN_SCAN_JOBS` to fan it out).

## 5. Packaging shapes that need a workaround

| Shape | Symptom | What to do |
|-------|---------|------------|
| **Static-only** (e.g. oneCCL on conda-forge ships `libccl.a`, no `.so`) | `scan` rejects the `.a`: "static/import library archive … not analysed" | extract members (`ar x lib.a`) and scan the resulting objects, or scan a shared library built from them |
| **Headers in a third package** | `-H` dir has no headers | fetch the `*-include` package (see [§1](#1-the-pieces-live-in-different-packages)) |
| **Stripped release `.so`, no DWARF** | header-aware L2 still works; DWARF cross-checks skip | pass `-H` headers (recommended anyway) |

## See also

- [Worked Example: Scanning a Library](real-world-example.md) — the full flow and reports
- [Source-Scan Levels](scan-levels.md) — what L0–L5 collect and cost
- [CLI Usage](cli-usage.md) — every flag
