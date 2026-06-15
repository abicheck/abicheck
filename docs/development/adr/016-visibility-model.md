# ADR-016: Three-Tier Visibility Model

**Date:** 2026-03-18
**Status:** Accepted — implemented
**Decision maker:** Nikolay Petrov

---

## Context

When analyzing a shared library's ABI, not all symbols are equally important.
A function may be:

- **Declared in public headers and exported** — part of the intended public API
- **Not in headers but exported** — may be an implementation detail that leaked
  into the symbol table due to missing `-fvisibility=hidden`
- **Hidden** — explicitly marked as internal via compiler attributes

ABICC and libabigail use a binary model: a symbol is either "public" or not.
This fails to distinguish intentional API from accidental exports, leading to
false positives when visibility cleanup removes leaked symbols.

### Problem

Without a third tier, removing an accidentally exported symbol is classified
as BREAKING — the same severity as removing a documented public API function.
This produces noise in reports and discourages library authors from cleaning
up their symbol tables.

---

## Decision

### Three-tier `Visibility` enum

```python
class Visibility(str, Enum):
    PUBLIC   = "public"    # Default visibility, exported, declared in headers
    HIDDEN   = "hidden"    # __attribute__((visibility("hidden")))
    ELF_ONLY = "elf_only"  # Present in ELF symbol table, NOT in headers
```

### Semantics

| Tier | How detected | Meaning |
|------|-------------|---------|
| **PUBLIC** | Symbol exported in binary AND declared in provided headers | Intentional public API — changes are fully tracked |
| **HIDDEN** | Symbol has `STV_HIDDEN` or `STV_INTERNAL` visibility in ELF | Internal implementation — excluded from ABI analysis |
| **ELF_ONLY** | Symbol exported in binary but NOT found in provided headers | Accidental export or internal-use symbol — tracked with reduced severity |

### Impact on change detection

`ELF_ONLY` is not just a visibility attribute — it's a **detection confidence
indicator**. When a function is `ELF_ONLY`, we have less certainty about its
intended API surface:

| Change | PUBLIC severity | ELF_ONLY severity |
|--------|----------------|-------------------|
| Symbol removed | `FUNC_REMOVED` → **BREAKING** | `FUNC_REMOVED_ELF_ONLY` → **COMPATIBLE** |
| Symbol added | `FUNC_ADDED` → COMPATIBLE | `FUNC_ADDED` → COMPATIBLE |
| Type/signature change | Full detection (via headers) | Not detected (no type info) |

The key classification decision: `FUNC_REMOVED_ELF_ONLY` is COMPATIBLE (not
BREAKING) because the symbol was never part of the declared public API. Its
removal is treated as visibility cleanup — the library author is tightening
the export surface, which is a positive maintenance action.

### How `ELF_ONLY` is assigned

During snapshot creation (`dumper.py`):

1. Parse headers with castxml → get the set of declared function/variable names
2. Parse ELF symbol table → get the set of exported symbols
3. For each exported symbol:
   - If name matches a header declaration → `Visibility.PUBLIC`
   - If not in headers → `Visibility.ELF_ONLY`
4. If no headers provided (`elf_only_mode=True`), ALL functions are
   `ELF_ONLY` — the entire snapshot operates at reduced confidence

### Interaction with DWARF-only mode (ADR-003)

In DWARF-only mode (no headers available), visibility is determined by
intersecting DWARF functions with ELF exported symbols:

```python
exported = {s.name for s in elf_meta.symbols
            if s.binding in ('GLOBAL', 'WEAK') and s.defined}
for func in dwarf_functions:
    if func.linkage_name in exported or func.name in exported:
        func.visibility = Visibility.PUBLIC
    else:
        continue  # skip internal functions
```

In this mode, all exported functions are treated as PUBLIC because DWARF
provides type information equivalent to headers. The `ELF_ONLY` tier only
applies when we have headers but a symbol is not declared in them.

Note: in DWARF-only mode, `elf_only_mode` is set to `True` at the snapshot
level (no headers were provided), but individual functions get
`Visibility.PUBLIC` (because DWARF substitutes for headers). This is not a
contradiction — `elf_only_mode` records the data source used, while
`Visibility` records the classification outcome.

### `elf_only_mode` flag

**Distinction**: `Visibility.ELF_ONLY` is a per-symbol visibility tier
indicating "this symbol is exported but not declared in headers."
`AbiSnapshot.elf_only_mode` is a snapshot-level boolean indicating "this
snapshot was created without public header files." They are related but
distinct concepts — the flag describes the snapshot's data source, while
the enum describes individual symbol classification.

The `AbiSnapshot.elf_only_mode` boolean is set to `True` whenever no
public headers are provided — including both pure ELF-only mode and
DWARF-only mode. The per-function `Visibility` assignment depends on
which data sources are available:

| Mode | Headers | DWARF | `elf_only_mode` | Per-function Visibility |
|------|---------|-------|-----------------|------------------------|
| Full (castxml + ELF) | Yes | Optional | `False` | PUBLIC (if in headers), ELF_ONLY (if not) |
| DWARF-only | No | Yes | `True` | PUBLIC (DWARF-exported functions intersected with ELF exports) |
| Pure ELF-only | No | No | `True` | ELF_ONLY (all functions) |

When `elf_only_mode` is `True` and no DWARF data is available:

- All functions have `Visibility.ELF_ONLY`
- AST-based detectors (24 of 30) are skipped
- Only L0 (binary metadata) detectors run
- `FUNC_REMOVED_ELF_ONLY` is used instead of `FUNC_REMOVED`

---

## Consequences

### Positive

- Visibility cleanup (removing leaked symbols) is no longer flagged as BREAKING
- Reports are less noisy for libraries with many accidentally exported symbols
- Encourages library authors to adopt `-fvisibility=hidden`
- Clear provenance tracking: users know which findings come from headers vs
  ELF symbol table

### Negative

- `ELF_ONLY` conflates "data provenance" (where the info came from) with
  "visibility" (how the symbol is intended to be used) — these are related
  but distinct concepts
- If a symbol IS intentionally public but the user forgot to include its
  header, it will be classified as `ELF_ONLY` and removal will be COMPATIBLE
  instead of BREAKING — a false negative
- The three-tier model is novel and may surprise users coming from ABICC/
  libabigail

---

## References

- `abicheck/model.py` — `Visibility` enum, `AbiSnapshot.elf_only_mode`
- `abicheck/checker_policy.py` — `FUNC_REMOVED_ELF_ONLY` → COMPATIBLE
- `abicheck/dumper.py` — Visibility assignment logic
- ADR-003 — DWARF-only mode visibility filtering
