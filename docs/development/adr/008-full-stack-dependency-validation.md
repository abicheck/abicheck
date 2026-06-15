# ADR-008: Full-Stack Dependency Validation

**Date:** 2026-03-17
**Status:** Accepted — implemented
**Decision maker:** Nikolay Petrov

---

## Context

`abicheck compare` operates on a single library pair: old version vs new version.
This answers "Did this library's ABI change?" but not "Will my binary still load
and run correctly in the updated environment?"

In practice, a binary links against a tree of shared libraries. Upgrading one
library may break the binary not because of a direct ABI change, but because:
- A transitive dependency is missing in the new environment
- A symbol version required by the binary is no longer provided
- An interposed symbol now resolves to a different provider
- A dependency's ABI change affects symbols the binary actually imports

Answering these questions requires resolving the full dependency graph, simulating
symbol binding, and intersecting ABI changes with actual usage.

### Reference

The Linux dynamic linker (`ld-linux.so`) resolves dependencies using a specific
search order: DT_RPATH (when no DT_RUNPATH) -> LD_LIBRARY_PATH -> DT_RUNPATH
(direct deps only) -> default directories. This order must be replicated for
accurate offline analysis.

libabigail's `abicompat` provides single-library app-compat checking. Our
approach goes further: full transitive graph resolution with per-symbol binding
simulation across all loaded DSOs.

---

## Decision

### New modules

| Module | Role |
|--------|------|
| `abicheck/resolver.py` | Transitive DT_NEEDED resolution with loader-accurate search order |
| `abicheck/binder.py` | Symbol binding simulation (import-to-export matching across BFS load order) |
| `abicheck/stack_checker.py` | Stack-level comparison (baseline vs candidate) with verdict computation |
| `abicheck/stack_report.py` | JSON and Markdown output for stack-level results |

### Extended modules

| Module | Changes |
|--------|---------|
| `abicheck/elf_metadata.py` | Import symbol extraction (SHN_UNDEF), per-symbol version correlation from `.gnu.version`/`.gnu.version_r`/`.gnu.version_d` |
| `abicheck/cli.py` | `--follow-deps` flag on `dump` and `compare`; `stack-check` and `deps` subcommands |

### Architecture

```text
CLI (--follow-deps / stack-check / deps)
  |
  +-- resolver.py
  |     Walks DT_NEEDED transitively
  |     Expands $ORIGIN / $LIB / $PLATFORM tokens
  |     Implements RPATH vs RUNPATH propagation semantics
  |     Target-aware defaults (multiarch triples from PT_INTERP)
  |     Sysroot prefix for cross/container analysis
  |     -> DependencyGraph (nodes, edges, unresolved)
  |
  +-- binder.py
  |     BFS load order from dependency graph
  |     For each import: search providers in loader order
  |     Symbol version matching, visibility filtering
  |     Interposition detection
  |     -> list[SymbolBinding] with status per import
  |
  +-- stack_checker.py
  |     Resolve graphs in both environments
  |     Compute bindings in both environments
  |     Detect changed DSOs (content hash)
  |     Run per-library ABI diff on changed DSOs (reuses checker.py)
  |     Intersect ABI changes with actual import bindings
  |     -> StackCheckResult with verdicts and risk score
  |
  +-- stack_report.py
        JSON output: graph, bindings summary, stack changes
        Markdown output: dependency tree, binding failures, ABI changes
```

### Key design decisions

1. **Loader-accurate search order**: DT_RPATH (only when no DT_RUNPATH) ->
   LD_LIBRARY_PATH -> DT_RUNPATH (direct deps only) -> default dirs. DT_RUNPATH
   does NOT propagate to transitive dependencies.

2. **Target detection from PT_INTERP**: The ELF interpreter path (e.g.,
   `/lib/ld-linux-x86-64.so.2`) determines the target architecture and multiarch
   triple, which selects the correct default library search directories.

3. **RPATH propagation**: When a DSO has DT_RPATH but no DT_RUNPATH, its RPATH
   entries propagate through the dependency tree (merged with ancestor RPATHs).
   This matches `ld.so` behavior.

4. **$ORIGIN sysroot handling**: `$ORIGIN` expands to the DSO's actual directory
   (which already includes the sysroot prefix if the DSO was found under the
   sysroot). The sysroot prefix is NOT prepended again to avoid double-prefixing.

5. **Symbol binding simulation**: Uses BFS load order (matching the dynamic
   linker) with visibility filtering (STV_HIDDEN/STV_INTERNAL cannot satisfy
   external references) and version matching.

6. **Reuse of existing checker**: Changed DSOs are diffed using the existing
   `compare()` pipeline (all 80+ detectors), then results are filtered by
   actual import usage to determine real impact.

### Binding statuses

| Status | Meaning |
|--------|---------|
| `resolved_ok` | Import matched to a visible, version-compatible export |
| `missing` | No provider found for a required symbol |
| `version_mismatch` | Symbol found but required version not provided |
| `weak_unresolved` | Weak symbol with no provider (acceptable at runtime) |
| `visibility_blocked` | Symbol exists but all versions have hidden/internal visibility |
| `interposed` | Resolved, but from a different provider than expected (interposition) |

### Stack verdicts

| Verdict | Meaning |
|---------|---------|
| `pass` | Binary will load; no harmful ABI changes |
| `warn` | Binary may load but there are ABI risks |
| `fail` | Binary will not load or has breaking ABI changes on used symbols |

## Consequences

### Positive
- Answers "Will this binary work in the new environment?" end-to-end
- Catches load-time failures (missing DSOs, missing symbols) before deployment
- Identifies which ABI changes actually affect the binary (impact intersection)
- Reuses all existing detection logic — no new ABI diff engine
- Target-aware: correct defaults for x86-64, aarch64, armhf, riscv64, s390x, ppc64le
- Sysroot support enables cross-compilation and container analysis

### Negative
- Linux ELF only (PE and Mach-O dependency resolution not yet supported)
- Does not parse `ld.so.cache` (uses filesystem search instead)
- Does not handle `dlopen()` plugins (only static DT_NEEDED dependencies)
- LD_PRELOAD simulation is limited (tracked as risk flag, not fully modeled)

## Scope limitations (explicit)

| Feature | Status | Notes |
|---------|--------|-------|
| DT_NEEDED resolution | Implemented | Full transitive closure |
| RPATH/RUNPATH semantics | Implemented | Correct propagation rules |
| $ORIGIN/$LIB/$PLATFORM | Implemented | Token expansion with sysroot awareness |
| Symbol versioning | Implemented | .gnu.version + .gnu.version_d + .gnu.version_r |
| Symbol visibility | Implemented | STV_DEFAULT/HIDDEN/INTERNAL/PROTECTED |
| Interposition detection | Implemented | Detected and flagged |
| `dlopen()` plugins | Out of scope | Requires runtime tracing or manifest |
| `ld.so.cache` parsing | Out of scope | Binary cache format; use search paths |
| LD_PRELOAD/LD_AUDIT | Partial | Tracked as flag, not fully simulated |
| PE/Mach-O dependency graphs | Out of scope | Future work |
