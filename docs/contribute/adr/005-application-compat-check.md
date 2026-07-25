# ADR-005: Application Compatibility Checking

**Date:** 2026-03-17
**Status:** Accepted — implemented
**Decision maker:** Nikolay Petrov

---

## Context

`abicheck compare` answers: **"Did the library's ABI change?"**

A related but distinct question is: **"Will my application still work with the
new library version?"** This is the consumer perspective — the answer depends on
which subset of the library's ABI the application actually uses.

### Why it matters

A library diff may report 50 breaking changes, but if an application only calls
3 functions and none of them changed, the application is safe. Conversely, a change
classified as COMPATIBLE (new function added) is irrelevant to the app, while a single
removed function the app depends on is fatal.

Use cases:
- **CI pipeline**: "Will our product binary work with the new SDK release?"
- **Distro upgrade**: "Will upgrading libssl break openssh-server?"
- **Embedded firmware**: "Is our firmware compatible with the updated BSP?"

### What data is available

An ELF application binary contains:
- **`DT_NEEDED`** entries: which `.so` files it links against
- **`.dynsym` UNDEF symbols**: the exact symbols it imports (mangled names)
- **`.gnu.version_r`**: required symbol versions (`GLIBC_2.17`, `FOO_1.0`, etc.)

A PE application contains:
- **Import Table** (`IMAGE_IMPORT_DESCRIPTOR`): DLL names + imported symbol names

A Mach-O application contains:
- **`LC_LOAD_DYLIB`**: dependent dylib paths
- **Undefined symbols** in symbol table

This is sufficient to determine which library symbols an application
statically imports via `DT_NEEDED`/import-table/`LC_LOAD_DYLIB` — no DWARF or
headers needed.

> **2026-07 amendment:** This does *not* fully describe an application's
> runtime dependencies — binary imports only capture the static/dynamic-linker
> view. Symbols resolved via `dlopen()`/`dlsym()` (plugins, lazily loaded
> optional dependencies) are invisible to `parse_app_requirements()` since they
> never appear as `DT_NEEDED`/undefined-symbol entries. The codebase treats
> this as a distinct, tracked gap (internally "gap G5", see
> `abicheck/appcompat.py` `PluginHostContractResult` docstring): the
> `abicheck plugin-check` command (`abicheck/cli_plugin.py`) exists
> specifically to answer the dlopen-direction question — "does plugin vN+1
> still satisfy host H's required `dlsym()` entrypoints?" — as a separate
> check from `appcompat`, not as something `appcompat` itself covers.

### Reference

libabigail's `abicompat` provides similar functionality. Our design differs:
we reuse the existing `compare()` pipeline and filter its output, rather than
building a separate comparison engine.

---

## Decision

### New command: `abicheck appcompat`

```bash
abicheck appcompat <APP> <OLD_LIB> <NEW_LIB> [options]
```

### Architecture

```text
abicheck appcompat myapp libfoo.so.1 libfoo.so.2
  │
  ├── 1. Read app requirements
  │     parse_app_requirements(myapp)
  │     → AppRequirements {
  │         needed: ["libfoo.so.1"],
  │         undefined_symbols: {"foo_init", "foo_process", ...},
  │         required_versions: {"foo_init": "FOO_1.0", ...}
  │       }
  │
  ├── 2. Run standard comparison
  │     compare(old_lib, new_lib, headers=..., policy=...)
  │     → DiffResult (full, unfiltered)
  │
  ├── 3. Check symbol availability
  │     For each app.undefined_symbols:
  │       present in new_lib exports? → ok
  │       missing? → missing_symbols list
  │     For each app.required_versions:
  │       version tag in new_lib .gnu.version_d? → ok
  │       missing? → missing_versions list
  │
  ├── 4. Filter diff by app usage
  │     For each Change in DiffResult:
  │       affects symbol in app's required set? → breaking_for_app
  │       affects type used by app's required symbols? → breaking_for_app
  │       otherwise → irrelevant_for_app
  │
  └── 5. Compute app-specific verdict
        missing_symbols → BREAKING
        breaking_for_app with BREAKING severity → BREAKING
        breaking_for_app with API_BREAK severity → API_BREAK
        otherwise → COMPATIBLE
```

### New module: `abicheck/appcompat.py`

```python
@dataclass
class AppRequirements:
    """Symbols and versions an application binary requires from a library."""
    needed_libs: list[str]              # DT_NEEDED / import table entries
    undefined_symbols: set[str]         # mangled symbol names the app imports
    required_versions: dict[str, str]   # symbol → version tag (ELF only)

@dataclass
class AppCompatResult:
    """Result of checking app compatibility with a library update."""
    app_path: str
    old_lib_path: str
    new_lib_path: str

    # App's requirements
    required_symbols: set[str]
    required_symbol_count: int

    # Filtered results
    breaking_for_app: list[Change]
    irrelevant_for_app: list[Change]
    missing_symbols: list[str]          # app needs X, new lib doesn't have X
    missing_versions: list[str]         # app needs version tag, new lib doesn't provide

    # Full library diff (for reference)
    full_diff: DiffResult

    # App-specific verdict
    verdict: Verdict

    # Coverage
    symbol_coverage: float  # % of app's required symbols present in new lib
```

### Reading app requirements

```python
def parse_app_requirements(
    app_path: str, library_soname: str,
) -> AppRequirements:
    """Extract app's requirements for a specific library."""
```

For ELF:
- `pyelftools`: read `.dynsym` for `STB_GLOBAL`/`STB_WEAK` + `SHN_UNDEF` symbols
- `pyelftools`: read `.gnu.version_r` (SHT_GNU_verneed) for required versions
- Filter to symbols associated with the target library's SONAME

For PE:
- `pefile`: read import table, filter by DLL name

For Mach-O:
- `macholib`: read undefined symbols, filter by dylib path

### Filtering diff by app usage

The key logic: intersect the `DiffResult.changes` with the app's symbol set.

```python
def _is_relevant_to_app(change: Change, app: AppRequirements) -> bool:
    """Does this change affect a symbol the application uses?"""
    # Direct symbol match
    if change.symbol in app.undefined_symbols:
        return True

    # Type change affecting app's symbols (via affected_symbols enrichment)
    if change.affected_symbols:
        if app.undefined_symbols & set(change.affected_symbols):
            return True

    # ELF-level: SONAME change affects all consumers
    if change.kind == ChangeKind.SONAME_CHANGED:
        return True

    # Symbol version change for a version the app requires.
    # Match by (symbol, version) pair — not just version string — to avoid
    # false positives when unrelated symbols share the same version tag.
    if change.kind in (ChangeKind.SYMBOL_VERSION_REMOVED,):
        sym = change.symbol
        required_ver = app.required_versions.get(sym)
        if required_ver and required_ver == change.old_value:
            return True

    return False
```

### Report format

```markdown
# Application Compatibility Report

**Application:** /usr/bin/myapp
**Library:** libfoo.so.1 → libfoo.so.2
**Verdict:** COMPATIBLE

## Symbol Coverage

App requires **47** of **312** library symbols (15%).
All 47 required symbols present in new version.

## Relevant Changes (2 of 50 total)

These library changes affect symbols your application uses:

| Kind | Symbol | Description |
|------|--------|-------------|
| func_params_changed | foo_process | parameter `flags` type changed: int → unsigned int |
| type_size_changed | Config | size changed 64 → 72 bytes (affects foo_init, foo_process) |

## Irrelevant Changes (48)

48 library ABI changes do NOT affect your application.
Use `--show-irrelevant` to see them.
```

### Weak mode (single-library check)

When the old library isn't available:

```bash
abicheck appcompat myapp --check-against libfoo.so.2
```

Answers: "Does the new library provide everything myapp needs?" — symbol
availability check only, no diff.

### CLI

```bash
# Full check
abicheck appcompat myapp libfoo.so.1 libfoo.so.2
abicheck appcompat myapp libfoo.so.1 libfoo.so.2 -H /usr/include/foo/

# Output formats
abicheck appcompat myapp old.so new.so --format json
abicheck appcompat myapp old.so new.so --format sarif

# Diagnostics
abicheck appcompat myapp old.so new.so --show-irrelevant
abicheck appcompat myapp --list-required-symbols

# Weak mode
abicheck appcompat myapp --check-against libfoo.so.2

# Suppression + policy
abicheck appcompat myapp old.so new.so --suppression ignore.yaml --policy sdk_vendor
```

### Exit codes

Same as `compare`: 0 (COMPATIBLE), 2 (API_BREAK), 4 (BREAKING).

## Consequences

### Positive
- Answers the most actionable question: "Will my app break?"
- Reuses existing `compare()` pipeline — no new detection logic
- Shows users that most library changes are irrelevant to their app
- Works for ELF, PE, Mach-O
- Weak mode works without the old library

### Negative
- Symbol-level filtering may miss indirect type usage not captured by `affected_symbols`
- Requires app to be linked (can't check against header-only usage)
- Additional CLI command adds surface area

## Implementation Plan

| Phase | Scope | Effort |
|-------|-------|--------|
| 1 | `parse_app_requirements()` for ELF (pyelftools) | 2-3 days |
| 2 | `_is_relevant_to_app()` filter + `AppCompatResult` | 1-2 days |
| 3 | `abicheck appcompat` CLI + markdown/JSON reporters | 2-3 days |
| 4 | Weak mode (`--check-against`) | 1-2 days |
| 5 | PE/Mach-O support for `parse_app_requirements()` | 2-3 days |
| 6 | Tests with real app+library pairs | 2-3 days |
