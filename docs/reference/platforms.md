# Platform Support

abicheck runs on **Linux, macOS, and Windows** and can analyze binaries from any of these platforms.
However, the depth of analysis depends on the **host platform** and whether headers are available.

---

## Quick Reference: What Works Where

### Scanning Linux ELF binaries

| Host OS | Symbol diff | Type/param diff | Requires |
|---------|------------|----------------|---------|
| Linux ✅ | ✅ Full | ✅ Full | `castxml`, `g++`/`gcc` |
| macOS | ✅ Yes | ❌ No | — |
| Windows | ✅ Yes | ❌ No | — |

**Best results:** run on Linux with headers provided.

### Scanning Windows PE (DLL) binaries

| Host OS | Symbol diff | Type/param diff | Requires |
|---------|------------|----------------|---------|
| Linux | ✅ Yes | ❌ No | `pefile` |
| macOS | ✅ Yes | ❌ No | `pefile` |
| Windows | ✅ Yes | ✅ Yes | `castxml` + `cl.exe` |

### Scanning macOS Mach-O (dylib) binaries

| Host OS | Symbol diff | Type/param diff | Requires |
|---------|------------|----------------|---------|
| Linux | ✅ Yes | ❌ No | `macholib` |
| macOS | ✅ Yes | ✅ Yes | `castxml` (Xcode clang) |
| Windows | ✅ Yes | ❌ No | `macholib` |

---

## Validation status (what is actually exercised in CI)

The matrices above describe *intended* capability. The depth of **automated
validation** differs sharply by platform, and you should calibrate trust
accordingly:

| Platform | Binary/metadata parsing | Workflow end-to-end (compare / appcompat / …) |
|----------|:-----------------------:|:---------------------------------------------:|
| **Linux / ELF** | Unit **and** integration tests | **Validated in CI** (the baseline) |
| **Windows / PE+PDB** | Unit tests for the PE/PDB parsers | **Validated in CI** for MinGW: `cross-platform-e2e` lane runs `compare` on MinGW-built DLLs. The `windows-msvc` lane additionally asserts MSVC+PDB verdicts (PDB layout depth best-effort) but runs **non-blocking** (`continue-on-error`, informational) until proven stable |
| **macOS / Mach-O** | Unit tests for the Mach-O/ARM64 layer | **Validated in CI**: `cross-platform-e2e` lane runs `compare` on Apple-clang-built dylibs; AArch64 AAPCS64 HFA/HVA + 16-byte boundary modeled and unit-tested |

Concretely: the core `compare` workflow is now exercised end-to-end on native
PE and Mach-O binaries (built by the platform's own toolchain) in the
`cross-platform-e2e` CI lane (gap **G1** closed). What remains a deliberate
Linux-anchored subset is the **example catalog**: every entry in
[`examples/ground_truth.json`](https://github.com/napetrov/abicheck/blob/main/examples/ground_truth.json)
is validated on Linux, and a `platforms` tag of `macos`/`windows` expresses
*intended* portability rather than a per-case CI result — some cases carry an
explicit `known_gap` describing where the non-Linux path diverges. This
invariant (Linux = universal baseline; macOS/Windows = strict subset) is guarded
by `tests/test_platform_coverage_honesty.py`. See
[Use-Case Coverage Evaluation](../development/usecase-coverage-evaluation.md)
(gap **G1**) for context.

### Castxml-free validation (no external tools)

While the full header-driven pipeline uses `castxml`, a large slice of the
catalog needs **no castxml at all**: a plain `-g` build embeds DWARF in the
shared object, and `abicheck` reads type/layout/calling-convention facts
straight from it. `tests/test_castxml_free_examples.py` validates **40 catalog
cases** end-to-end on the Linux baseline using only a C/C++ compiler — building
v1/v2, dumping with no headers (DWARF + symbol table only), and asserting the
`ground_truth.json` verdict. This guards the pure-Python, drop-in path that many
CI environments and developer machines actually run (no castxml installed). The
~11 cases that genuinely require castxml (concept tightening, explicit-ctor
mangling, header-only scoping) remain covered by the castxml integration lane.

---

## What "Symbols Only" Mode Means

When scanning a binary **without headers**, or scanning a non-native binary cross-platform,
abicheck operates in **symbol-table mode**:

✅ **Detected:**
- Function added / removed (`func_added`, `func_removed`)
- SONAME changed (`soname_changed`)
- Symbol visibility changed (`symbol_visibility_changed`)
- Variable added / removed

❌ **Not detected** (requires type information from headers + castxml):
- Parameter type changes (`func_params_changed`)
- Return type changes (`func_return_type_changed`)
- Struct/class layout changes (`type_size_changed`, `type_field_*`)
- vtable changes (`type_vtable_changed`)
- Inline/noexcept changes

**Recommendation:** for complete ABI analysis, always provide `-H <header_dir>` and run on the
native platform (Linux for ELF, macOS for Mach-O, Windows for PE).

---

## Cross-Platform Examples

### Scan a Windows DLL from Linux

```bash
# Symbol-level diff (works cross-platform)
abicheck compare mylib_v1.dll mylib_v2.dll

# With headers (only useful if castxml+cl.exe is available)
abicheck compare mylib_v1.dll mylib_v2.dll \
  -H include/
```

**What you get:** `func_removed`, `func_added`, ordinal changes.
**What you miss:** parameter type changes, struct layout changes.

### Scan a macOS dylib from Linux

```bash
abicheck compare libmylib.1.dylib libmylib.2.dylib
```

**What you get:** exported symbol diff.
**What you miss:** type-level analysis (no DWARF walk cross-platform today).

### Scan a Linux .so from macOS

```bash
abicheck compare libmylib.so.1 libmylib.so.2
```

ELF parsing is pure Python — works on macOS. DWARF walk also works.
Full type analysis requires `castxml` and headers available on the host.

---

## GitHub Actions: Multi-Platform CI

To get full analysis on each platform:

```yaml
jobs:
  abi-check:
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - name: Install abicheck (source)
        run: pip install -e .
      - name: Install castxml (Linux/macOS only)
        if: runner.os != 'Windows'
        run: |
          # sudo apt-get install -y castxml   # Ubuntu
          # brew install castxml              # macOS
      - name: ABI check
        run: |
          abicheck compare -lib mylib \
            -old old/libmylib.so -new new/libmylib.so \
            -H include/
```

---

## Known Limitations by Platform

### Windows host
- `castxml` with `cl.exe` backend is **untested in CI** — may work but is not validated
- MSVC vtable layout differs from Itanium ABI; vtable diff results may be inaccurate
- `__stdcall`/`__cdecl` calling-convention changes appear as `func_removed + func_added`
  (mangled-name churn) — no dedicated change kind; see [#50](https://github.com/CastXML/CastXML/issues/50) below
- Tracked: abicc upstream issues #9, #50, #56, #121

### macOS host
- ARM64 Apple AAPCS differs from Itanium for small structs (≤16 bytes passed in registers)
- `install_name` (`LC_ID_DYLIB`, macOS SONAME equivalent) changes are tracked and emit `SONAME_CHANGED`
- Two-level namespace (`LC_LOAD_DYLIB`) not fully analyzed
- Tracked: abicc upstream issues #116, #119

### All platforms (symbols-only mode)
- `int → long` parameter change: mangled name changes → detected as `func_removed + func_added`
  (not `func_params_changed`) when headers are absent
- Template inner-type changes (`std::vector<T>` with changed `T`) — not detected (tracked: #38)

---

## Dependency Summary

| Feature | Required tools | pip / system install | conda-forge install |
|---------|----------------|----------------------|---------------------|
| ELF analysis | `pyelftools` | `pip install -e .` | `conda install -c conda-forge abicheck` |
| PE analysis | `pefile` | `pip install -e .` | `conda install -c conda-forge abicheck` |
| Mach-O analysis | `macholib` | `pip install -e .` | `conda install -c conda-forge abicheck` |
| Type/param analysis (Linux) | `castxml` + C/C++ compiler | `pip install -e .` + `apt/yum` (`castxml`, `gcc/g++`) | `conda install -c conda-forge abicheck` |
| Type/param analysis (macOS) | `castxml` + Apple toolchain | `pip install -e .` + `brew install castxml` (+ Xcode CLT) | `conda install -c conda-forge abicheck` |
| Type/param analysis (Windows) | `castxml` + `cl.exe` | `pip install -e .` + Visual Studio Build Tools + castxml | `conda install -c conda-forge abicheck` |

For conda-based workflows, install only `abicheck` from conda-forge.
Recipe dependencies pull required analysis tooling automatically.

---

## Windows Toolchain Support Matrix

| Toolchain | castxml backend | Type/param diff | Calling-convention tracking | Status | Notes |
|-----------|------------------|-----------------|-----------------------------|--------|-------|
| MinGW (GCC) | `--castxml-cc-gnu gcc` | ✅ Yes | ⚠️ Partial (`__cdecl`/`__stdcall` not a dedicated kind, #50) | **Experimental** | Covered by CI smoke tests; full MinGW integration coverage on `windows-latest` is best-effort and not guaranteed on every run. |
| MSVC (`cl.exe`) | `--castxml-cc-msvc cl.exe` | ✅ Yes | ⚠️ Partial (#9, #50) | **Untested in CI** | May work locally with Visual Studio Build Tools; ABI details differ from Itanium assumptions in several detectors. |

### Known Limitations (Windows)

- **#9 (MSVC headers / Windows SDK edge cases):** castxml+`cl.exe` handles common cases, but complex SDK-specific declarations are not yet validated in project CI.
- **#50 (calling conventions):** `__stdcall`/`__cdecl` deltas are represented as mangled-name churn (`func_removed + func_added`) instead of a dedicated calling-convention change kind.
- **#56 (PE visibility semantics):** `__declspec(dllexport/dllimport)` transitions are currently reflected at symbol-level only; no dedicated PE export-visibility change kind.
- **#121 (MinGW-specific behavior):** MinGW export/import edge-cases (import libs, ordinals, toolchain flags) are only partially covered by current smoke/integration tests.

## macOS ARM64 — Known ABI Differences

ARM64 (Apple Silicon) has a different calling convention from x86-64 that affects how small structs and floating-point aggregates are passed.

| Feature | x86-64 (System V) | ARM64 (Apple AAPCS) | Detected by abicheck? |
|---------|-------------------|----------------------|-----------------------|
| Small struct (≤16 B) | Stack or reg pair | Passed in GP registers | ✅ `TYPE_SIZE_CHANGED` catches size delta |
| HFA (Homogeneous Floating-point Aggregate ≤4 floats) | Stack | SIMD/FP registers | ⚠️ Size same, registers differ — NOT detected |
| HVA (Homogeneous Vector Aggregate) | Stack | SIMD/FP registers | ⚠️ Size same, registers differ — NOT detected |
| Return in registers | RDX:RAX (x86-64) | x0:x1 (ARM64) | ⚠️ Not tracked (no calling-convention change kind) |

**Tracked:** abicc issues **#116** (small-struct register passing) · **#119** (install_name).

### install_name tracking (#119)

`install_name` (`LC_ID_DYLIB`) is the macOS equivalent of ELF `SONAME`.
abicheck **now tracks this** — a change emits `SONAME_CHANGED` with `symbol="LC_ID_DYLIB"`.

| Scenario | Status |
|----------|--------|
| install_name changes between versions | ✅ `SONAME_CHANGED` emitted |
| install_name absent → set | ✅ tracked |
| No change | ✅ no false positive |

### Support claim

**ARM64/macOS: Experimental**
- Symbol diff: fully supported (export table via `macholib`)
- Type/param diff: requires Xcode clang + `castxml` (≥ 0.9.0 with Apple toolchain backend)
- HFA/HVA calling-convention drift: **not directly detected** — workaround: always check `TYPE_SIZE_CHANGED` on structs
