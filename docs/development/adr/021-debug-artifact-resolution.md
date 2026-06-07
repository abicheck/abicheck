# ADR-021a: Debug Artifact Resolution Subsystem

**Date:** 2026-03-23
**Status:** Accepted
**Decision maker:** Nikolay Petrov

---

## Context

### Current debug info handling

abicheck reads debug information directly from binary files:
- **DWARF**: Read from ELF/Mach-O binaries via pyelftools (`dwarf_metadata.py`,
  `dwarf_snapshot.py`, `dwarf_advanced.py`)
- **PDB**: Read from Windows PE binaries via custom parser (`pdb_parser.py`)
- **BTF/CTF**: Read from ELF `.BTF`/`.ctf` sections (ADR-007)

For package comparison (ADR-006), `compare-release` accepts `--debug-info1/2` and
`--devel-pkg1/2` flags that each take a **single** package path. The package
extractor resolves debug files within a package using build-id trees and path
conventions.

### Gaps in current debug resolution

**1. Split DWARF is not supported.**

Modern builds often use split DWARF (`-gsplit-dwarf`) which places debug info in
separate `.dwo` files or a single `.dwp` package file. The main binary contains
only `DW_FORM_GNU_ref_alt` / `DW_FORM_GNU_strp_alt` references. pyelftools can
parse `.dwo` files, but abicheck does not chase these references.

GCC/Clang flag: `-gsplit-dwarf` produces `.dwo` per TU. `dwp` tool merges them
into a single `.dwp` file.

**2. No `--debug-root` equivalent.**

libabigail's `abidiff` supports `--debug-info-dir1/2` to specify directories
containing split debug files. Fedora, RHEL, and Debian all install debug info
in well-known locations (`/usr/lib/debug/.build-id/`). abicheck has no equivalent
flag for the `compare` command (only `compare-release` has `--debug-info1/2`
for packages).

**3. No debuginfod integration.**

`debuginfod` (standardized by elfutils) serves debug artifacts over HTTP, indexed
by build-id. Many Linux distributions run public debuginfod servers. This enables
"zero-config" debug info resolution — given a binary with a build-id, fetch its
debug info automatically.

**4. macOS dSYM bundles not supported.**

macOS debug info is typically stored in `.dSYM` bundles (directories created by
`dsymutil`). Layout: `Foo.framework.dSYM/Contents/Resources/DWARF/Foo`. abicheck
reads DWARF from the main binary but does not look for dSYM bundles.

**5. Multi-package sets not supported.**

Real-world package comparison often involves sets of packages, not single packages:

```text
# Reality: library split across runtime + debug + devel + arch packages
old_set:
  libfoo-1.0.rpm              (runtime: shared objects)
  libfoo-debuginfo-1.0.rpm    (debug: .debug files)
  libfoo-devel-1.0.rpm        (devel: headers + .pc files)
  libfoo-common-1.0.rpm       (arch-independent: configs)

new_set:
  libfoo-1.1.rpm
  libfoo-debuginfo-1.1.rpm
  libfoo-devel-1.1.rpm
  libfoo-common-1.1.rpm
```

Current CLI only accepts one package per side plus one debug and one devel package.
Distros like Fedora commonly split libraries into 3-5+ subpackages.

### Options considered

| Option | Description | Trade-off |
|--------|-------------|-----------|
| A: Ad-hoc flags per feature | `--split-dwarf-dir`, `--dsym-path`, `--debuginfod-url`, etc. | Flag explosion; no unified model |
| **B: Pluggable resolver subsystem** | `DebugResolver` protocol with backends; unified `--debug-root` | Clean architecture; extensible; one mental model |
| C: External tool delegation | Shell out to `debuginfod-find`, `dsymutil`, etc. | Fragile; dependency-heavy |

---

## Decision

### 1. Debug Artifact Resolver subsystem

New module: `abicheck/debug_resolver.py`

```python
class DebugResolver(Protocol):
    """Locate debug artifacts for a given binary."""
    def resolve(self, binary_path: Path, build_id: str | None) -> DebugArtifact | None:
        """Find debug info for the given binary. Returns None if not found."""
        ...

@dataclass
class DebugArtifact:
    """Resolved debug artifact location."""
    dwarf_path: Path | None        # Path to file containing DWARF sections
    dwp_path: Path | None          # Path to .dwp (DWARF package) file
    dwo_dir: Path | None           # Directory containing .dwo files
    pdb_path: Path | None          # Path to .pdb file (Windows)
    dsym_path: Path | None         # Path to .dSYM bundle (macOS)
    source: str                    # Human-readable provenance ("build-id tree", "debuginfod", "dSYM bundle")
```

### 2. Resolver chain (ordered, first-match wins)

```text
resolve_debug_info(binary_path, build_id):
  │
  ├── 1. Embedded DWARF check
  │     Binary itself has .debug_info section?
  │     → return DebugArtifact(dwarf_path=binary_path)
  │
  ├── 2. Split DWARF check
  │     Binary has DW_AT_GNU_dwo_name / DW_AT_dwo_name references?
  │     Look for .dwo files relative to binary or --debug-root
  │     Look for .dwp file (same stem as binary + .dwp)
  │     → return DebugArtifact(dwp_path=...) or DebugArtifact(dwo_dir=...)
  │
  ├── 3. Build-id tree search
  │     Search --debug-root / default paths:
  │       <debug_root>/.build-id/<ab>/<cdef1234...>.debug
  │       /usr/lib/debug/.build-id/<ab>/<cdef1234...>.debug
  │     → return DebugArtifact(dwarf_path=matched_debug_file)
  │
  ├── 4. Path mirror search
  │     <debug_root>/path/to/binary.debug
  │     Example: /usr/lib/debug/usr/lib64/libfoo.so.1.debug
  │     → return DebugArtifact(dwarf_path=matched_debug_file)
  │
  ├── 5. dSYM bundle search (macOS only)
  │     Look for <binary>.dSYM/Contents/Resources/DWARF/<binary_name>
  │     Also check Spotlight metadata (mdls) if available
  │     → return DebugArtifact(dsym_path=...)
  │
  ├── 6. PDB search (Windows only)
  │     Read PE debug directory for PDB path reference
  │     Look in --debug-root, binary directory, _NT_SYMBOL_PATH
  │     → return DebugArtifact(pdb_path=...)
  │
  ├── 7. debuginfod (opt-in, network)
  │     Query debuginfod server by build-id
  │     Cache downloaded artifacts locally
  │     → return DebugArtifact(dwarf_path=cached_download)
  │
  └── 8. None found
        → return None (symbols-only mode fallback)
```

### 3. Split DWARF support

Split DWARF (`.dwo` / `.dwp`) requires chasing external references in DWARF:

```text
Main binary (.debug_info):
  DW_TAG_compile_unit
    DW_AT_GNU_dwo_name = "src/foo.dwo"    (GCC)
    DW_AT_dwo_name = "src/foo.dwo"        (DWARF 5)
    DW_AT_comp_dir = "/build"
    DW_AT_GNU_dwo_id = 0xABCD...          (hash for matching)

Resolution:
  1. Join comp_dir + dwo_name → /build/src/foo.dwo
  2. Fallback: <debug_root>/<dwo_name>
  3. If .dwp exists: search .dwp index by dwo_id
```

pyelftools can parse `.dwo` files (they are standard ELF with `.debug_*` sections).
The `DwarfSnapshotBuilder` needs modification to merge type info from multiple
`.dwo` files into a single snapshot.

For `.dwp` (DWARF package files), the implementation reads the `.debug_cu_index`
section to locate individual CU contributions within the package.

### 4. debuginfod integration (opt-in)

debuginfod support is network-dependent and must be explicitly enabled:

```bash
# Enable debuginfod resolution
abicheck dump libfoo.so --debuginfod

# Specify server URL (overrides DEBUGINFOD_URLS environment variable)
abicheck dump libfoo.so --debuginfod --debuginfod-url https://debuginfod.fedoraproject.org/

# Environment variable (standard elfutils convention)
DEBUGINFOD_URLS="https://debuginfod.fedoraproject.org/" abicheck dump libfoo.so --debuginfod
```

Implementation uses HTTP requests to the debuginfod API:

```text
GET /buildid/<buildid>/debuginfo   → ELF with debug sections
GET /buildid/<buildid>/executable  → original executable (not needed)
GET /buildid/<buildid>/source/<path> → source file (not needed)
```

Downloaded artifacts are cached in `$XDG_CACHE_HOME/abicheck/debuginfod/` or
`--cache-dir`, using the same directory structure as elfutils' debuginfod client.

**Security considerations:**
- Network access is opt-in (never implicit)
- HTTPS strongly recommended; HTTP requires `--debuginfod-allow-insecure`
- Downloaded files are verified by build-id match after download
- Cache entries are ELF files — validate ELF magic before use

### 5. dSYM bundle support

macOS stores debug info in `.dSYM` bundles created by `dsymutil`:

```text
Foo.dylib.dSYM/
  Contents/
    Info.plist
    Resources/
      DWARF/
        Foo.dylib          ← DWARF debug sections here
```

Resolution strategy:

```python
def _find_dsym(binary_path: Path) -> Path | None:
    """Locate dSYM bundle for a macOS binary."""
    # Strategy 1: Adjacent to binary
    dsym = binary_path.parent / f"{binary_path.name}.dSYM"
    if _is_valid_dsym(dsym):
        return dsym

    # Strategy 2: Framework bundle
    # Foo.framework/Foo → Foo.framework.dSYM/Contents/Resources/DWARF/Foo
    if ".framework" in str(binary_path):
        framework_path = _find_framework_root(binary_path)
        dsym = framework_path.parent / f"{framework_path.name}.dSYM"
        if _is_valid_dsym(dsym):
            return dsym

    # Strategy 3: User-specified --debug-root
    # <debug_root>/<binary_name>.dSYM
    if debug_root:
        dsym = debug_root / f"{binary_path.name}.dSYM"
        if _is_valid_dsym(dsym):
            return dsym

    return None

def _dsym_dwarf_path(dsym_bundle: Path, binary_name: str) -> Path:
    """Get the DWARF file path within a dSYM bundle."""
    return dsym_bundle / "Contents" / "Resources" / "DWARF" / binary_name
```

### 6. Multi-package set support for `compare-release`

Extend `compare-release` to accept multiple packages per side:

```bash
# Current (single package per role):
abicheck compare-release old.rpm new.rpm \
    --debug-info1 old-debuginfo.rpm \
    --devel-pkg1 old-devel.rpm

# New: multiple packages per side (comma-separated or repeated flags)
abicheck compare-release old.rpm new.rpm \
    --debug-info1 old-debuginfo.rpm,old-debugsource.rpm \
    --devel-pkg1 old-devel.rpm \
    --extra-pkg1 old-common.rpm

# New: package set mode (all packages for a side in one flag)
abicheck compare-release \
    --old-packages old.rpm old-debuginfo.rpm old-devel.rpm \
    --new-packages new.rpm new-debuginfo.rpm new-devel.rpm

# New: package list file (for CI with many packages)
abicheck compare-release \
    --old-package-list old-packages.txt \
    --new-package-list new-packages.txt
```

Implementation approach:

```python
@dataclass
class PackageSet:
    """A set of packages that together form one side of a comparison."""
    runtime_packages: list[Path]     # Contains shared libraries
    debug_packages: list[Path]       # Contains debug info (.debug files)
    devel_packages: list[Path]       # Contains headers
    extra_packages: list[Path]       # Additional content (configs, data)

    def extract_all(self, target_dir: Path) -> ExtractResult:
        """Extract all packages in the set into a unified directory tree."""
        # Extract each package into target_dir
        # Later packages overlay earlier ones (like installing packages in order)
        # Merge metadata from all packages
```

Extraction merges all packages into a single directory tree. This mirrors
how a package manager installs a set of related packages into the same root.
The binary discovery, debug resolution, and header discovery then operate on
the merged tree exactly as they do today for single packages.

**Package list file format** (for CI convenience):

```text
# old-packages.txt
# Lines starting with # are comments, empty lines are ignored
libfoo-1.0-1.el9.x86_64.rpm
libfoo-debuginfo-1.0-1.el9.x86_64.rpm
libfoo-devel-1.0-1.el9.x86_64.rpm
```

### 7. CLI changes for `compare` command

The `compare` command (single binary pair) also gains debug resolution:

```bash
# Current
abicheck compare old.so new.so -H include/

# New: specify debug root directories
abicheck compare old.so new.so -H include/ \
    --debug-root1 /usr/lib/debug \
    --debug-root2 /path/to/new/debuginfo

# New: enable debuginfod
abicheck compare old.so new.so --debuginfod

# New: macOS dSYM
abicheck compare old.dylib new.dylib \
    --debug-root1 /path/to/old.dSYM/..
```

### 8. Diagnostic output

```bash
abicheck dump libfoo.so --show-data-sources --debug-root /usr/lib/debug
```

Output:

```text
Data sources for libfoo.so:
  L0 Binary metadata: ELF (x86_64, SONAME=libfoo.so.1, 47 exported symbols)
  L1 Debug info:
    Embedded DWARF:  not present (stripped)
    Split DWARF:     found 12 .dwo files in /build/src/
    Build-id tree:   found /usr/lib/debug/.build-id/ab/cdef1234.debug
    debuginfod:      not enabled (use --debuginfod)
  L2 Header AST:     not available (no -H provided)

Using: DWARF from build-id tree (24/30 detectors active)
```

---

## Consequences

### Positive
- "It just finds the debug info" — works with what CI already has
- Unified model for all platforms (DWARF, PDB, dSYM) and split strategies
- Multi-package sets match real distro packaging workflows
- debuginfod enables zero-config debug resolution for distro packages
- `--debug-root` parity with libabigail's `--debug-info-dir`
- dSYM support unblocks macOS adoption for stripped release builds
- Package list files simplify CI scripts with many packages

### Negative
- Split DWARF adds complexity to DWARF parsing (merging multiple .dwo files)
- debuginfod introduces network dependency (mitigated: opt-in only)
- Multi-package extraction increases temp disk usage
- dSYM resolution heuristics may miss non-standard bundle locations
- `.dwp` index parsing is non-trivial (CU index table format)

---

## Implementation Plan

| Phase | Scope | Effort |
|-------|-------|--------|
| 1 | `DebugResolver` protocol + `DebugArtifact` model | 1-2 days |
| 2 | Build-id tree resolver + `--debug-root` for `compare` command | 2-3 days |
| 3 | Path-mirror resolver (distro convention) | 1 day |
| 4 | Split DWARF: `.dwo` file resolution + DwarfSnapshotBuilder merge | 3-5 days |
| 5 | Split DWARF: `.dwp` package index parsing | 2-3 days |
| 6 | dSYM bundle resolver (macOS) | 2-3 days |
| 7 | Multi-package sets: `PackageSet` model + CLI changes | 3-5 days |
| 8 | Multi-package sets: merged extraction + package list files | 2-3 days |
| 9 | debuginfod HTTP client + local cache | 3-5 days |
| 10 | Diagnostic output (`--show-data-sources` enhancement) | 1-2 days |
| 11 | Tests: split DWARF, dSYM, multi-package, debuginfod mock | 3-5 days |
