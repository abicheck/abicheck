# ADR-015: Snapshot Serialization and Schema Versioning

**Date:** 2026-03-18
**Status:** Accepted
**Decision maker:** Nikolay Petrov

---

## Context

`abicheck dump` produces a JSON snapshot file (`.abi.json`) that captures the
complete ABI surface of a library. These snapshots serve multiple purposes:

- **Offline comparison**: `abicheck compare old.abi.json new.abi.json` without
  needing the original binaries or headers
- **Baseline storage**: Check snapshots into version control as ABI baselines
- **Cross-mode comparison**: A DWARF-derived snapshot can be compared against
  a castxml-derived snapshot (ADR-003)
- **CI caching**: Generate once, compare many times

The snapshot format is a user-facing contract. Changes to the format can break
stored baselines and downstream tooling.

---

## Decision

### 1. `AbiSnapshot` as the canonical interchange model

All pipeline stages — dumper, checker, reporter — operate on the same
`AbiSnapshot` dataclass. Serialization converts this dataclass to/from JSON.

```python
@dataclass
class AbiSnapshot:
    library: str
    version: str
    functions: list[Function]
    variables: list[Variable]
    types: list[RecordType]
    enums: list[EnumType]
    typedefs: dict[str, str]        # default: {}
    constants: dict[str, str]       # default: {} (populated from header #defines)
    elf: ElfMetadata | None
    pe: PeMetadata | None
    macho: MachoMetadata | None
    dwarf: DwarfMetadata | None
    dwarf_advanced: AdvancedDwarfMetadata | None
    platform: str | None         # "elf" | "pe" | "macho"
    language_profile: str | None # "c" | "cpp" | "sycl"
    elf_only_mode: bool
    dependency_info: DependencyInfo | None
```

### 2. Integer schema versioning

```python
SCHEMA_VERSION: int = 6
```

Version history:

| Version | Change | PR |
|---------|--------|-----|
| 1 | Initial format (no `schema_version` field) | — |
| 2 | `schema_version` field added | PR #89 |
| 3 | `pe` and `macho` metadata fields added (multi-format support) | — |
| 4 | Provenance metadata (`git_commit`, `git_tag`, `created_at`, `build_id`) | — |
| 5 | `build_mode` capture (compiler/stdlib/std normalization) | — |
| 6 | Declaration provenance: `source_header` + `origin` on functions/variables/types/enums | — |

**Integer versioning** was chosen over semver because:

- Snapshot format changes are always backward-incompatible (new fields change
  the meaning of existing data)
- There is no concept of "minor" or "patch" format changes — either the schema
  is compatible or it isn't
- Monotonic integers are simpler to compare (`if version < 3: migrate(...)`)

### 3. Backward compatibility rules

**Reading old snapshots**: Snapshots without a `schema_version` field are
treated as v1. The deserializer handles missing fields by using dataclass
defaults (empty lists, `None` values).

**Reading future snapshots**: If `schema_version > SCHEMA_VERSION`, emit a
warning suggesting the user upgrade abicheck. The deserializer attempts to
read the snapshot anyway — forward compatibility is best-effort.

**Writing**: Always writes the current `SCHEMA_VERSION`. There is no option
to write in an older format.

### 3a. Declaration provenance fields (v6)

`Function`, `Variable`, `RecordType`, and `EnumType` each carry two
provenance fields:

- `source_header` — the defining header path, derived from the existing
  `source_location` with any trailing `:line` / `:line:col` stripped. Always
  populated when a source location is available; it is descriptive metadata.
- `origin` — a `ScopeOrigin` classification of `source_header` against the
  user-provided public-header set. This is the *Origin* axis of ADR-024's
  two-axis Linkage × Origin surface model:

  | Value | Meaning |
  |-------|---------|
  | `public_header` | Header matches a `--public-header` / `--public-header-dir` input |
  | `private_header` | A project header outside the public set |
  | `system_header` | A toolchain/system header (`/usr/include`, MSVC, Xcode SDK, …) |
  | `generated` | A machine-generated header (`moc_*`, `*.pb.h`, `generated/`, …) |
  | `export_only` | Exported by the binary but absent from any header (no provenance) |
  | `unknown` | No public set was provided, or no source location was available |

Classification is **opt-in** (decision D4): without `--public-header` /
`--public-header-dir`, every `origin` is `unknown` and downstream behaviour
is unchanged. Matching is done on path *segments* (suffix / basename /
directory containment) so absolute build-tree prefixes that never appear on
the command line (e.g. `/build/abc/src/include/api.h`) still resolve against
`include/api.h` (decision D3).

### 4. Serialization mechanics

**Serialization** (`snapshot_to_dict()`):
1. `dataclasses.asdict()` converts the snapshot tree to a plain dict
2. `_sets_to_lists()` recursively converts sets to sorted lists (JSON has no
   set type)
3. Enum values are converted to their string representation
4. Internal cache fields (`_func_by_mangled`, `_var_by_mangled`,
   `_type_by_name`) are reset to `None` before serialization
5. `schema_version` is embedded at the top level

**Deserialization** (`snapshot_from_dict()`):
1. Inspect `schema_version` (default to 1 if absent)
2. Reconstruct typed objects: `Function`, `Variable`, `RecordType`,
   `EnumType`, etc.
3. Reconstruct enum instances (`SymbolBinding`, `SymbolType`, `Visibility`,
   etc.) from string values
4. Platform-specific metadata reconstructed via `_elf_from_dict()`,
   `_pe_from_dict()`, `_macho_from_dict()`, `_dwarf_from_dict()`,
   `_dwarf_advanced_from_dict()`

### 5. JSON determinism

To ensure reproducible snapshots (important for diffing baselines in version
control):

- Sets are converted to sorted lists
- Dict keys are naturally ordered by `json.dumps(sort_keys=True)`
- Floating-point values are avoided in the schema

### 6. Cross-mode snapshot equivalence

A snapshot produced from DWARF data (`--dwarf-only`) and a snapshot produced
from castxml headers produce the same JSON schema. The `checker.compare()`
function treats them identically. This enables:

```bash
# Generate snapshots from different sources
abicheck dump lib.so --dwarf-only > dwarf.abi.json
abicheck dump lib.so -H include/  > ast.abi.json

# Cross-compare works
abicheck compare dwarf.abi.json ast.abi.json
```

Fields that only one source can populate differ in their empty
representation based on their type:

- **Optional fields** (`T | None`, e.g., `elf`, `pe`, `macho`,
  `dwarf_advanced`): `null` in JSON when the source doesn't provide them
- **Collection fields** (`dict` / `list`, e.g., `constants`, `typedefs`,
  `functions`): empty `{}` or `[]` when the source doesn't populate them

For example, a DWARF-only snapshot has `constants: {}` (empty dict — no
header parsing to extract `#define` values), `pe: null` (wrong platform),
and `dwarf_advanced` populated with DWARF-specific data. Consumers should
handle both `null` and empty-collection cases.

---

## Consequences

### Positive

- Offline comparison without original binaries or headers
- Baselines can be checked into version control
- Cross-mode comparison (DWARF vs castxml) works transparently
- Deterministic JSON enables meaningful diffs of snapshot files
- Simple integer versioning avoids semver complexity

### Negative

- Schema version bumps break stored baselines (users must regenerate)
- Forward compatibility is best-effort — new fields may be silently ignored
- `dataclasses.asdict()` with post-processing is slower than custom
  serialization (acceptable for file sizes in practice)
- No compression — snapshots for large libraries can be several MB

---

## References

- `abicheck/serialization.py` — `SCHEMA_VERSION`, `snapshot_to_dict()`,
  `snapshot_from_dict()`
- `abicheck/model.py` — `AbiSnapshot` dataclass
- ADR-003 — Data source architecture (DWARF vs castxml snapshot equivalence)
