# ADR-006: Package-Level Comparison

**Date:** 2026-03-17
**Status:** Accepted — implemented
**Decision maker:** Nikolay Petrov

> **2026-07 amendment:** The scope below (RPM, Deb, tar archives) undersells
> current support. `abicheck/package.py` also ships `CondaExtractor` (`.conda`
> v2 zip format and legacy `.tar.bz2`) and `WheelExtractor` (`.whl`), alongside
> `RpmExtractor`/`DebExtractor`/`TarExtractor`/`DirExtractor` — all through the
> same `detect_extractor()` auto-detection and the same path-traversal/symlink
> safe-extraction checks described below. Treat the "Implementation Plan" table
> and format list in this ADR as a historical snapshot of the initial scope,
> not the current supported-format list.

---

## Context

### Current state

abicheck has two comparison entry points:
- `abicheck compare` — single binary pair
- `abicheck compare-release` (ADR-002) — directory-vs-directory with auto-matching

ADR-002 covers the **directory** case (matching by filename stem / SONAME). This ADR
extends input support to **packaged** formats: RPM, Deb, tar archives.

### Why packages, not just directories?

In practice, distro maintainers and SDK vendors work with packages directly:
- `libfoo-1.0-1.el9.x86_64.rpm` → `libfoo-1.1-1.el9.x86_64.rpm`
- `libfoo_1.0-1_amd64.deb` → `libfoo_1.1-1_amd64.deb`
- `sdk-2.0.tar.gz` → `sdk-2.1.tar.gz`

The current workflow requires manual extraction before comparison. This ADR adds
a `PackageExtractor` layer that sits **before** the comparison pipeline.

### Relationship to ADR-002

ADR-002 defines the comparison + matching + aggregation logic for multiple binaries.
This ADR adds the **extraction** layer that converts packages into directories,
then delegates to ADR-002's `compare-release` pipeline:

```text
Package → Extract → Directory → [ADR-002 compare-release] → AggregateResult
```

---

## Decision

### Extend `compare-release` to accept packages

```bash
# Directories (ADR-002, existing)
abicheck compare-release release-1.0/ release-2.0/ -H include/

# RPM packages (NEW)
abicheck compare-release libfoo-1.0.rpm libfoo-1.1.rpm

# Deb packages (NEW)
abicheck compare-release libfoo_1.0.deb libfoo_1.1.deb

# Tar archives (NEW)
abicheck compare-release sdk-2.0.tar.gz sdk-2.1.tar.gz

# With debug info packages
abicheck compare-release libfoo-1.0.rpm libfoo-1.1.rpm \
    --debug-info1 libfoo-debuginfo-1.0.rpm \
    --debug-info2 libfoo-debuginfo-1.1.rpm

# With development packages (for header-based analysis)
abicheck compare-release libfoo-1.0.rpm libfoo-1.1.rpm \
    --devel-pkg1 libfoo-devel-1.0.rpm \
    --devel-pkg2 libfoo-devel-1.1.rpm
```

The CLI auto-detects format by extension and magic bytes. No separate `pkg-compare`
command — it's the same `compare-release` with broader input support.

### New module: `abicheck/package.py`

```python
class PackageExtractor(Protocol):
    """Extract package contents to a temporary directory."""
    def extract(self, pkg_path: str, target_dir: str) -> ExtractResult: ...
    def detect(self, pkg_path: str) -> bool: ...

@dataclass
class ExtractResult:
    lib_dir: str          # path to extracted shared libraries
    debug_dir: str | None # path to extracted debug info (if debug pkg provided)
    header_dir: str | None # path to extracted headers (if devel pkg provided)
    metadata: dict        # package-specific metadata (name, version, arch, etc.)

# Concrete extractors
class RpmExtractor(PackageExtractor): ...
class DebExtractor(PackageExtractor): ...
class TarExtractor(PackageExtractor): ...
class DirExtractor(PackageExtractor): ...  # passthrough, no extraction

def detect_extractor(path: str) -> PackageExtractor:
    """Auto-detect package format and return appropriate extractor."""
```

### Extraction strategies

| Format | Detection | Extraction | Dependencies |
|--------|-----------|------------|--------------|
| **RPM** | `.rpm` ext or RPM magic `0xedabeedb` | `rpm2cpio \| cpio -id` | cpio (ubiquitous on RPM distros) |
| **Deb** | `.deb` ext or `!<arch>` magic | `ar x` + `tar xf data.tar.*` | ar, tar (ubiquitous on Deb distros) |
| **Tar** | `.tar`, `.tar.gz`, `.tar.xz`, `.tgz` | `tarfile` stdlib | None (Python stdlib) |
| **Directory** | `os.path.isdir()` | No extraction | None |

**Python-only alternatives**: `rpmfile` (PyPI) for RPM, `python-debian` for Deb.
These are optional extras (`pip install abicheck[rpm]`, `abicheck[deb]`) for
environments without system tools.

### Extraction security

All extractors **must** implement safe-unpacking checks before writing any file:

1. **Path traversal rejection**: Reject archive members containing `../` segments
   or absolute paths. Every extracted member's destination must satisfy
   `Path(dest).resolve().is_relative_to(target_root)`.

2. **Symlink validation**: Symlink targets must resolve within the extraction root.
   Do not follow symlinks during extraction unless the resolved target is within
   `target_root`. Reject hardlinks pointing outside the extraction root.

3. **Canonicalized destination check**: After joining `target_root` + member path,
   canonicalize with `Path.resolve()` and verify it remains under `target_root`.
   This catches edge cases where Unicode normalization or repeated separators
   could escape the root.

4. **Fail-fast on violations**: If any member fails these checks, abort extraction
   entirely (do not skip the member silently). Raise `ExtractionSecurityError`
   with the offending member path.

5. **Per-extractor implementation**:
   - **TarExtractor**: Use `tarfile.data_filter` (Python 3.12+) or manual member
     filtering with the checks above. Never use `tar.extractall()` without filtering.
   - **RpmExtractor** (cpio): Pipe through `cpio -id --no-absolute-filenames` and
     post-validate extracted paths.
   - **DebExtractor** (ar + tar): Same tar filtering as TarExtractor for `data.tar.*`.

These checks are mandatory — not optional or configurable.

### Debug info resolution

When a separate debug info package is provided:

```text
Main package: libfoo-1.0.rpm
  └── /usr/lib64/libfoo.so.1.0

Debug info package: libfoo-debuginfo-1.0.rpm
  └── /usr/lib/debug/.build-id/ab/cdef1234.debug

Resolution:
  1. Match by build-id (NT_GNU_BUILD_ID ELF note → .build-id directory)
  2. Fallback: match by path convention (/usr/lib/debug/usr/lib64/libfoo.so.1.0.debug)
  3. Pass debug_dir to dumper.py for DWARF loading
```

### Binary discovery within packages

```python
def discover_shared_libraries(extract_dir: str) -> list[Path]:
    """Find all shared libraries in an extracted package directory."""
    # Walk directory, check ELF magic + ET_DYN type
    # Skip non-public DSOs by default (RPM: not in Provides)
    # --include-private-dso flag overrides
```

### Integration with ADR-002 compare-release

```text
compare-release(input1, input2, ...)
  │
  ├── Is input a package?
  │   ├── YES → extract to tempdir → lib_dir
  │   └── NO  → use directory directly
  │
  ├── Discover binaries in lib_dir
  ├── Match binaries (SONAME → filename stem → mapping file)
  ├── For each pair: compare(old_bin, new_bin, ...)
  │   (with debug_dir from debug package, header_dir from devel package)
  ├── Aggregate results
  └── Cleanup tempfiles
```

### Cleanup

Extracted packages use `tempfile.TemporaryDirectory()` with automatic cleanup.
`--keep-extracted` flag preserves temporary files for debugging.

### Additional CLI options

```bash
# Only compare shared objects, skip executables
abicheck compare-release old.rpm new.rpm --dso-only

# Include private (non-public) shared objects
abicheck compare-release old.rpm new.rpm --include-private-dso

# Keep extracted temp files
abicheck compare-release old.rpm new.rpm --keep-extracted

# Parallel comparison (default: cpu_count)
abicheck compare-release old.rpm new.rpm --parallel 4
```

## Consequences

### Positive
- One-command package comparison without manual extraction
- Reuses ADR-002 matching + aggregation — no duplicate logic
- Debug info + devel package support enables full type-level analysis from packages
- Cross-format: RPM, Deb, tar, directory all work the same way
- Auto-detection means no format-specific commands to learn

### Negative
- System tool dependencies for RPM/Deb (mitigated by Python-only extras)
- Temporary file management (mitigated by `TemporaryDirectory()`)
- Binary matching in packages may fail for unusual layouts
- Build-id matching for debug info isn't always available

## Implementation Plan

| Phase | Scope | Effort |
|-------|-------|--------|
| 1 | `PackageExtractor` protocol + `TarExtractor` + `DirExtractor` | 2-3 days |
| 2 | Binary discovery (`discover_shared_libraries()`) | 1-2 days |
| 3 | `RpmExtractor` + debug info build-id resolution | 2-3 days |
| 4 | `DebExtractor` | 1-2 days |
| 5 | Integration with `compare-release` CLI (auto-detect + extract) | 2-3 days |
| 6 | Devel package header extraction | 1-2 days |
| 7 | Tests with real RPM/Deb packages | 2-3 days |
