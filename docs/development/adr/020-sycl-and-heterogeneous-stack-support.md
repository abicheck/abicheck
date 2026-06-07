# ADR-020b: SYCL and Heterogeneous Computing Stack Support

**Date:** 2026-03-22
**Status:** Accepted
**Decision maker:** Nikolay Petrov

---

## Context

abicheck currently excels at host-side C/C++ ABI scanning across ELF, PE, and
Mach-O. However, modern SDKs increasingly ship heterogeneous computing stacks
(SYCL, CUDA, HIP) where ABI compatibility spans multiple layers beyond the
host binary.

A feasibility analysis identified SYCL and CUDA as the two major gaps. This
ADR addresses SYCL support and establishes extensibility patterns that CUDA
(and future heterogeneous runtimes) can reuse.

### SYCL-specific ABI layers

SYCL compatibility spans three distinct layers, each with different detection
strategies:

1. **SYCL runtime library ABI** (`libsycl.so` / `libsycl.dll`) — this is a
   standard shared library with exported C++ symbols. Existing ELF/PE/Mach-O
   diff engines already handle this. No new machinery needed.

2. **Plugin interfaces (PI and UR)** — DPC++ dynamically loads backend
   plugins. Two interface generations exist:
   - **PI (Plugin Interface)**: legacy. Libraries named `libpi_*.so`, init
     entry point `piPluginInit()`, symbols prefixed `pi`.
   - **UR (Unified Runtime)**: current. Libraries named `libur_adapter_*.so`,
     init entry point `urAdapterGet()`, symbols prefixed `ur`.
   Both use the same detection approach. A distribution may ship PI plugins,
   UR adapters, or both during the transition period. Missing/changed entry
   points break the runtime ↔ plugin contract regardless of interface.

3. **Backend driver compatibility** — plugins depend on backend drivers
   (Level Zero, OpenCL ICD, CUDA driver). Version requirements flow through
   the plugin layer. This is analogous to CUDA's toolkit↔driver compatibility
   and is best treated as an environment-matrix constraint.

### What "SYCL ABI break" means in practice

| Scenario | Impact | Detection strategy |
|----------|--------|--------------------|
| Exported symbol removed from `libsycl.so` | Applications crash at load time | Existing ELF diff (already works) |
| Type layout changed in `libsycl.so` exports | Silent data corruption | Existing DWARF diff (already works) |
| PI/UR interface version bumped | Old plugins rejected at runtime | Plugin metadata extraction + version diff |
| PI/UR entry point removed from dispatch table | Plugin segfaults or returns errors | Entry point set comparison |
| Plugin `.so` removed from distribution | Backend unavailable | Plugin inventory comparison |
| Plugin discovery path changed | Plugins not found at runtime | Plugin search-path diff |
| SYCL implementation changed (DPC++ → AdaptiveCpp) | Entirely different ABI | Implementation detection |
| Backend driver version requirement raised | Runtime fails on older systems | Environment matrix constraint |

### Design principles

- **Leverage existing engines**: `libsycl.so` is already fully covered by ELF
  diff. Don't duplicate.
- **Follow established patterns**: New metadata → `SyclMetadata` dataclass
  (like `ElfMetadata`, `PeMetadata`). New detector → `@registry.detector`.
  New change kinds → entries in `change_registry.py`.
- **Heterogeneous-stack extensibility**: The PI plugin pattern generalizes to
  any "host runtime loads backend plugins" architecture. The `SyclMetadata`
  model should be specific to SYCL/PI, but the environment-matrix input
  mechanism should be generic (reusable by CUDA).
- **Implementation-aware**: Target DPC++ (Intel's SYCL) as the primary
  implementation. Other SYCL implementations (hipSYCL/AdaptiveCpp,
  ComputeCpp) can be added later.

---

## Decision

### 1. New metadata model: `SyclMetadata`

```python
@dataclass
class SyclPluginInfo:
    """Metadata for a single backend plugin (PI or UR)."""
    name: str                          # e.g. "level_zero", "opencl", "cuda"
    library: str                       # e.g. "libpi_level_zero.so" or "libur_adapter_level_zero.so"
    interface_type: str = "pi"         # "pi" (Plugin Interface) or "ur" (Unified Runtime)
    pi_version: str                    # interface version (heuristic from symbols)
    entry_points: list[str]            # exported pi*/ur* function names
    backend_type: str                  # "level_zero" | "opencl" | "cuda" | "hip"
    min_driver_version: str | None     # minimum backend driver version if known

@dataclass
class SyclMetadata:
    """SYCL runtime + plugin interface metadata."""
    implementation: str = ""           # "dpcpp" | "adaptivecpp" | "computecpp"
    runtime_version: str = ""          # e.g. "2025.2.0"
    pi_version: str = ""               # PI interface version of the runtime
    plugins: list[SyclPluginInfo] = field(default_factory=list)
    plugin_search_paths: list[str] = field(default_factory=list)
    # Future: SPIR-V module metadata for device-code compat
```

Stored on `AbiSnapshot` as `sycl: SyclMetadata | None` — same pattern as
`elf`, `pe`, `macho`.

### 2. New extraction module: `sycl_metadata.py`

**Static extraction** (no SYCL compiler or runtime needed):
- Glob for plugin libraries: `libpi_*.so` (PI) and `libur_adapter_*.so` (UR)
- Parse `.dynsym` via pyelftools to extract exported `pi*`/`ur*` symbols
- Detect interface version from symbol presence heuristics
- Inventory plugin libraries in known search paths
- Check `SYCL_PI_PLUGINS_DIR` and `SYCL_UR_ADAPTERS_DIR` environment variables

**No special tooling required.** The entire extraction uses pyelftools (pure
Python, already a project dependency) and filesystem checks. No SYCL compiler,
no SYCL runtime, no SDK tools.

### 3. New detector: `diff_sycl.py`

Registered via `@registry.detector("sycl", requires_support=...)`.

Detects:
- PI version mismatch between runtime and plugins
- PI entry points removed from plugin dispatch tables
- Plugins removed from distribution
- Plugin search path changes
- Backend availability changes

### 4. New change kinds (registered in `change_registry.py`)

| ChangeKind | Default verdict | Impact |
|------------|----------------|--------|
| `sycl_implementation_changed` | BREAKING | SYCL implementation swapped (e.g. DPC++ → AdaptiveCpp); entirely different ABI |
| `sycl_pi_version_changed` | BREAKING | Runtime rejects plugins with incompatible PI version |
| `sycl_pi_entrypoint_removed` | BREAKING | Plugin dispatch table missing required function; runtime crashes |
| `sycl_pi_entrypoint_added` | COMPATIBLE | New PI capability; existing code unaffected |
| `sycl_plugin_removed` | BREAKING | Backend no longer available; apps targeting it fail |
| `sycl_plugin_added` | COMPATIBLE | New backend available |
| `sycl_plugin_search_path_changed` | COMPATIBLE_WITH_RISK | Plugins may not be found in new location |
| `sycl_runtime_version_changed` | COMPATIBLE | Informational; actual breaks caught by symbol/type diffs |
| `sycl_backend_driver_req_changed` | COMPATIBLE_WITH_RISK | Newer driver required; may fail on older systems |

### 5. Environment matrix model (generic, reusable)

```python
@dataclass
class EnvironmentMatrix:
    """Declared deployment constraints — shared across SYCL, CUDA, etc."""
    # Host toolchain
    compilers: list[str] = field(default_factory=list)       # ["gcc-13", "clang-17"]
    abi_version: str | None = None                           # -fabi-version value
    libstdcxx_dual_abi: str | None = None                    # "cxx11" | "old"

    # SYCL-specific
    sycl_backends: list[str] = field(default_factory=list)   # ["level_zero", "opencl"]
    sycl_implementation: str | None = None                   # "dpcpp"

    # CUDA-specific (future)
    cuda_gpu_architectures: list[str] = field(default_factory=list)  # ["sm_80", "sm_90"]
    cuda_driver_range: tuple[str, str] | None = None         # ("525.0", "580.0")
    cuda_toolkit_version: str | None = None

    # Generic
    target_os: str = "linux"
    target_arch: str = "x86_64"
```

Passed to `compare()` and used by detectors to parameterize verdicts. When
constraints are unspecified, detectors emit conditional results (e.g.,
"breaking if backend X is required").

### 6. Policy integration

Add a new built-in policy profile `sycl_stack` alongside `strict_abi`,
`sdk_vendor`, `plugin_abi`:

- `sycl_pi_entrypoint_removed` → BREAKING (no downgrade)
- `sycl_plugin_removed` → downgraded to COMPATIBLE_WITH_RISK under
  `sdk_vendor` (vendor may intentionally drop backends)
- `sycl_plugin_search_path_changed` → downgraded to COMPATIBLE under
  `plugin_abi`

### 7. Snapshot serialization

`SyclMetadata` serializes to the existing JSON snapshot format with a new
top-level key `"sycl"`. Schema version bumped to 4. Backward-compatible:
older snapshots without `"sycl"` key load with `sycl=None`.

---

## Architecture diagram

```
AbiSnapshot
├── elf: ElfMetadata          ── existing (libsycl.so covered here)
├── pe: PeMetadata            ── existing
├── macho: MachoMetadata      ── existing
├── dwarf: DwarfMetadata      ── existing
├── dwarf_advanced: ...       ── existing
├── sycl: SyclMetadata        ── NEW (PI/UR plugins, versions, search paths)
│   ├── pi_version
│   ├── plugins[]
│   │   ├── SyclPluginInfo (interface_type, entry_points, pi_version, backend_type)
│   │   └── ...  (PI: libpi_*.so, UR: libur_adapter_*.so)
│   └── plugin_search_paths[]
└── (future) cuda: CudaMetadata

Detectors (registry)
├── "functions"               ── existing
├── "types"                   ── existing
├── "elf"                     ── existing (handles libsycl.so as any .so)
├── "dwarf"                   ── existing
├── "sycl"                    ── NEW (PI/UR version, entry points, plugins)
└── (future) "cuda"

Change Registry
├── func_removed, type_size_changed, ...  ── existing (114+ kinds)
├── sycl_implementation_changed, ...     ── NEW (9 kinds, shared by PI and UR)
└── (future) cuda_*                       ── future
```

---

## How SYCL scanning works (integration guide)

SYCL scanning is **automatic** — no special flags or configuration needed.
It piggybacks on the existing ELF scan pipeline with zero overhead for
non-SYCL libraries.

### Data flow

```
abicheck compare old/lib/libsycl.so new/lib/libsycl.so --header new/include/sycl/
          │
          ▼
    service.py:run_dump()
          │
          ├── 1. _dump_elf()        ── existing ELF pipeline (symbols, types, DWARF)
          │       returns AbiSnapshot with elf=..., functions=..., types=...
          │
          └── 2. _try_attach_sycl_metadata(snapshot, lib_path)
                  │
                  ├── _detect_sycl_implementation(lib_path.parent)
                  │   checks: libsycl.so exists? → "dpcpp"
                  │           libacpp-rt.so exists? → "adaptivecpp"
                  │           neither? → None (skip, zero cost)
                  │
                  └── IF detected → parse_sycl_metadata(lib_dir)
                      │
                      ├── discover_sycl_plugins() — glob both patterns:
                      │   libpi_*.so (PI) and libur_adapter_*.so (UR)
                      │   for each plugin:
                      │   ├── open .so, fstat() to verify regular file
                      │   ├── parse .dynsym via pyelftools
                      │   ├── collect pi*/ur* symbols (filter hidden/internal)
                      │   ├── detect interface version from symbol heuristics
                      │   └── set interface_type = "pi" or "ur"
                      │
                      └── attach result → snapshot.sycl = SyclMetadata(...)

    checker.py:compare(old_snap, new_snap)
          │
          └── detector_registry.run_all()
              │
              ├── "elf" detector  ── runs always (libsycl.so symbol diff)
              ├── "types" detector ── runs always (type layout diff)
              ├── "sycl" detector ── runs ONLY IF both old.sycl and new.sycl
              │   │                   are not None (auto-gated by requires_support)
              │   ├── _diff_implementation()      ── DPC++ → AdaptiveCpp?
              │   ├── _diff_pi_version()           ── PI version changed?
              │   ├── _diff_plugins()              ── plugins added/removed?
              │   ├── _diff_plugin_entrypoints()   ── PI functions missing?
              │   ├── _diff_plugin_search_paths()  ── search paths changed?
              │   ├── _diff_runtime_version()       ── informational
              │   └── _diff_backend_driver_reqs()   ── driver req raised?
              └── ... other detectors
```

### Why no `--sycl-lib-dir` flag?

The library path already tells us everything. When you pass `libsycl.so`, its
parent directory is the lib dir. The auto-detection (`_detect_sycl_implementation`)
runs a few `Path.exists()` calls — effectively zero cost — and only triggers
the full plugin scan when SYCL artifacts are found.

For `abicheck compare libfoo.so.old libfoo.so.new` on a non-SYCL library,
the detection short-circuits immediately (no `libsycl.so` in parent dir)
and adds zero overhead.

### What gets compared

A SYCL scan produces TWO independent layers of results in a single run:

1. **Host ABI** (existing pipeline): symbol additions/removals, type layout
   changes, vtable mutations in `libsycl.so` itself. This is the same as
   scanning any shared library.

2. **Plugin Interface** (SYCL detector): plugin inventory, PI entry points,
   PI version, search paths. This is the SYCL-specific layer that checks
   the runtime ↔ plugin contract.

Both layers are reported together. A single comparison may produce both
"function `sycl::device::get_info` removed" (from ELF diff) and
"PI plugin `libpi_opencl.so` removed" (from SYCL diff).

### Example CI usage

```yaml
# GitHub Action — works with zero SYCL-specific configuration
- uses: ./
  with:
    mode: compare
    old-library: sdk-old/lib/libsycl.so
    new-library: sdk-new/lib/libsycl.so
    header: sdk-new/include/sycl/
    policy: strict_abi
    fail-on-breaking: 'true'
```

The action auto-detects the SYCL distribution because `libsycl.so` is in
the lib dir alongside the `libpi_*.so` and/or `libur_adapter_*.so` plugins.
No additional inputs needed.

### No special tooling needed

The entire SYCL scanning pipeline uses tools already in the project:

| What | Tool | Already a dependency? |
|------|------|-----------------------|
| Detect SYCL distribution | `Path.exists()` | Python stdlib |
| Find plugins | `Path.iterdir()` + regex | Python stdlib |
| Parse plugin symbols | `pyelftools` | Yes (used by core ELF pipeline) |
| Version detection | Symbol name heuristics | No external tool |

No SYCL compiler, no SYCL runtime, no Intel oneAPI SDK, no special
system packages. If abicheck can scan a regular `.so`, it can scan a
SYCL distribution.

### Non-SYCL libraries are unaffected

The only cost for `abicheck compare libfoo.so.old libfoo.so.new` is
`_detect_sycl_implementation(parent_dir)` which does 2-3 `Path.exists()`
calls and returns `None`. No plugin scanning, no pyelftools overhead,
no extra memory allocation.

---

## Consequences

### Positive

- SYCL PI-level compatibility checking fills the biggest gap identified in
  the feasibility analysis for heterogeneous stacks.
- `libsycl.so` ABI diffing requires zero new code — existing ELF engine
  handles it.
- The environment matrix model is generic and directly reusable for CUDA
  support later.
- Self-registering detector pattern means SYCL detector is opt-in: when
  `SyclMetadata` is absent, the detector is automatically skipped.

### Negative

- PI interface is implementation-specific (DPC++). Other SYCL implementations
  may use different plugin mechanisms.
- Static extraction (parsing exports) is less precise than runtime probing
  but works without any SYCL runtime installed, which is the key requirement.
- SPIR-V device-code compatibility checking is deferred (complex, analogous
  to CUDA PTX/cubin problem).

### Risks

- Both PI and UR are DPC++ specific. Other SYCL implementations (AdaptiveCpp)
  may use different plugin mechanisms. The `implementation` field and
  `_detect_sycl_implementation()` heuristic accommodate this — new plugin
  patterns can be added without changing the model.
- Plugin `.so` files rarely ship debug info, so type-level analysis is
  limited to symbol-only mode (entry point presence/absence).
- Current implementation is Linux-only (ELF plugins, pyelftools). Windows
  support (`pi_*.dll`, `ur_adapter_*.dll`) is deferred until PE platform
  support is needed.

---

## Implementation plan

### Phase 1: Foundation (Sprint 1)

1. Add `SyclMetadata` + `SyclPluginInfo` dataclasses to new
   `sycl_metadata.py`
2. Add `sycl: SyclMetadata | None` field to `AbiSnapshot`
3. Register 8 new SYCL change kinds in `change_registry.py`
4. Add `ChangeKind` enum entries in `checker_policy.py`
5. Bump snapshot schema version to 4

### Phase 2: Static extraction and auto-detection (Sprint 2)

1. Implement `parse_sycl_metadata()` — inventory plugin `.so` files, extract
   `pi*`/`ur*` exports via pyelftools (`.dynsym` only, visibility-filtered)
2. Wire extraction into `service.py:run_dump()` via auto-detection: after the
   ELF dump completes, check if the library's parent directory looks like a
   SYCL distribution. If so, attach `SyclMetadata` to the snapshot. No new
   CLI flags — zero overhead for non-SYCL libraries (cost: a few `Path.exists()`
   calls from `_detect_sycl_implementation()`)
3. Implement `diff_sycl.py` detector with `@registry.detector("sycl")`
4. Support both PI (`libpi_*.so`) and UR (`libur_adapter_*.so`) plugins with
   `interface_type` field on `SyclPluginInfo`

### Phase 3: Environment matrix (Sprint 3)

1. Add `EnvironmentMatrix` dataclass (generic)
2. Add `--env-matrix` CLI input (YAML format)
3. Pass matrix through `compare()` to detectors
4. Emit parameterized verdicts when constraints unspecified

---

## References

- DPC++ Plugin Interface design: https://github.com/intel/llvm/blob/sycl/sycl/doc/design/PluginInterface.md
- DPC++ ABI policy: https://github.com/intel/llvm/blob/sycl/sycl/doc/ABIPolicyGuide.md
- SYCL 2020 specification (Khronos)
- Feasibility analysis: "Feasibility of a Complete C/C++/SYCL/CUDA ABI/API Breakage Scanner"
- ADR-018: Cross-Platform Binary Format Support (pattern reference)
