# ADR-007: BTF and CTF Debug Format Support

**Date:** 2026-03-17
**Status:** Accepted — implemented
**Decision maker:** Nikolay Petrov

> **2026-07 amendment:** The `TypeMetadataSource` protocol, `BtfMetadata`
> (`abicheck/btf_metadata.py`), and `CtfMetadata` (`abicheck/ctf_metadata.py`)
> all landed as described. CLI surface has drifted from "CLI" below, though:
> `--btf`/`--ctf`/`--dwarf` are now hidden legacy aliases on `abicheck dump`;
> the current primary flag is `--debug-format {auto,dwarf,btf,ctf}` (see
> `abicheck/cli.py`), which supersedes them. `--show-data-sources` is unchanged.

---

## Context

### Current debug format support

abicheck reads debug information from two formats:
- **DWARF** (ELF, Mach-O) via pyelftools — `dwarf_metadata.py`, `dwarf_advanced.py`
- **PDB** (PE/Windows) via custom parser — `pdb_parser.py`, `pdb_metadata.py`

Both produce the same data structures: `StructLayout`, `FieldInfo`, `EnumInfo`
(defined in `dwarf_metadata.py`). The checker's DWARF detectors (`_diff_dwarf`,
`_diff_advanced_dwarf`) consume these structures regardless of source.

Two additional debug formats exist in the Linux ecosystem:

### BTF (BPF Type Format)

BTF is a compact, pre-deduplicated type format:
- **Used by**: Linux kernel (5.x+), eBPF programs, `bpftool`, `libbpf`
- **Size**: 10-100× smaller than DWARF for same types
- **Contents**: structs, unions, enums, typedefs, function prototypes, variables
- **Properties**: Already deduplicated (by `pahole --btf_encode_detached`)
- **Location**: `.BTF` ELF section
- **Spec**: `include/uapi/linux/btf.h` in Linux kernel source

BTF matters because:
1. All modern kernels include it — it's often the **only** debug format available
   in production kernel builds (DWARF stripped, BTF kept)
2. Kernel module ABI analysis needs BTF support
3. It parses faster than DWARF due to pre-deduplication

### CTF (Compact C Type Format)

CTF is an alternative to DWARF originating from Solaris:
- **Used by**: illumos, SmartOS, OmniOS, DTrace
- **Size**: Smaller than DWARF, comparable to BTF
- **Location**: `.ctf` ELF section
- **Relevance**: Niche — mostly illumos derivatives. Lower priority than BTF.

---

## Decision

### Pure Python parsers for both formats

Consistent with ADR-001 (no external tool dependencies), implement parsers in
pure Python using the `struct` module.

### Integration with the data source architecture (ADR-003)

BTF and CTF are **L1 (debug info)** sources. They produce the same `StructLayout`,
`EnumInfo`, and (new) `FuncProto` structures as DWARF. The checker doesn't need
to know which format the data came from.

#### Unified protocol: `TypeMetadataSource`

```python
class TypeMetadataSource(Protocol):
    """Common interface for all debug format readers."""
    def get_struct_layout(self, name: str) -> StructLayout | None: ...
    def get_enum_info(self, name: str) -> EnumInfo | None: ...
    def get_function_proto(self, name: str) -> FuncProto | None: ...
    def get_typedef(self, name: str) -> str | None: ...
    @property
    def has_data(self) -> bool: ...
```

`DwarfMetadata`, `BtfMetadata`, and `CtfMetadata` all implement this protocol.
The checker's detectors accept `TypeMetadataSource` instead of `DwarfMetadata` directly.

#### Updated fallback chain (extends ADR-003)

The default L1 priority depends on the binary type:

```text
L1 debug info resolution (no CLI override):

  Kernel binary (vmlinux, *.ko)?
    BTF present?   → use BTF   (preferred: compact, pre-deduplicated)
    DWARF present? → use DWARF
    CTF present?   → use CTF
    None?          → skip L1

  Userspace binary (*.so, executable)?
    DWARF present? → use DWARF (preferred: richer type info, wider support)
    BTF present?   → use BTF
    CTF present?   → use CTF
    None?          → skip L1
```

**Kernel detection heuristic**: binary name is `vmlinux` or has `.ko`/`.ko.xz`/`.ko.zst`
extension, or ELF contains `.modinfo` section.

CLI flags `--btf` / `--ctf` / `--dwarf` override the auto-detection and force a
specific format regardless of binary type. If the forced format is not present,
emit an error rather than silently falling back.

### BTF parser: `abicheck/btf_metadata.py`

BTF is a simple binary format (~15 type kinds). The parser reads the `.BTF` ELF
section via pyelftools' section API.

#### BTF format structure

```text
.BTF section:
┌───────────────┐
│ btf_header    │  magic=0xEB9F, version, hdr_len, type_off/len, str_off/len
├───────────────┤
│ Type entries  │  Sequential btf_type records, each with:
│               │  - name_off (into string section)
│               │  - info (kind:5 | vlen:16 | kflag:1)
│               │  - size_or_type
│               │  - kind-specific extra data (members, params, etc.)
├───────────────┤
│ String table  │  Null-terminated strings
└───────────────┘
```

#### Type kinds to handle

| BTF Kind | Maps to | abicheck structure |
|----------|---------|-------------------|
| `BTF_KIND_STRUCT` | struct/class | `StructLayout` (name, size, fields) |
| `BTF_KIND_UNION` | union | `StructLayout` (name, size, fields) |
| `BTF_KIND_ENUM` / `BTF_KIND_ENUM64` | enum | `EnumInfo` (name, members, values) |
| `BTF_KIND_TYPEDEF` | typedef | `str → str` mapping |
| `BTF_KIND_FUNC_PROTO` | function prototype | `FuncProto` (return type, params) |
| `BTF_KIND_FUNC` | function declaration | linkage + proto reference |
| `BTF_KIND_VAR` | variable | variable name + type |
| `BTF_KIND_INT` | integer type | base type for size/signedness |
| `BTF_KIND_PTR` | pointer type | type reference |
| `BTF_KIND_ARRAY` | array type | element type + count |
| `BTF_KIND_FWD` | forward decl | opaque type |
| `BTF_KIND_VOLATILE/CONST/RESTRICT` | qualifiers | type modifiers |
| `BTF_KIND_DATASEC` | data section | grouping (not ABI-relevant) |

#### BTF → StructLayout mapping

```python
def _btf_struct_to_layout(btf_type, members, strings) -> StructLayout:
    fields = []
    for m in members:
        fields.append(FieldInfo(
            name=strings[m.name_off],
            type_name=resolve_type_name(m.type_id),
            byte_offset=m.offset // 8,
            bit_offset=m.offset % 8 if is_bitfield else 0,
            bit_size=m.bit_size if is_bitfield else 0,
        ))
    return StructLayout(
        name=strings[btf_type.name_off],
        byte_size=btf_type.size,
        fields=fields,
    )
```

#### Type resolution

BTF types reference each other by 1-based ID (sequential in the type section).
Build an index `{type_id → btf_type}` on first parse, then resolve recursively
with cycle detection.

### CTF parser: `abicheck/ctf_metadata.py` (lower priority)

CTF v3 uses a similar structure (header + type section + string section).
Same mapping to `StructLayout` / `EnumInfo`.

### Snapshot integration

When ADR-003's `DwarfSnapshotBuilder` is implemented, BTF can also serve as a
full snapshot source (`BtfSnapshotBuilder`):

```python
def build_snapshot_from_btf(elf_path: str, elf_meta: ElfMetadata) -> AbiSnapshot:
    """Build AbiSnapshot from BTF, no headers or DWARF required."""
```

This follows the same pattern as DWARF-only mode — produces the same `AbiSnapshot`
model, same detectors fire.

### CLI

```bash
# Auto-detection (default): use best available debug format
abicheck dump vmlinux                    # BTF preferred for kernel
abicheck dump libfoo.so                  # DWARF preferred for userspace

# Force specific format
abicheck dump vmlinux --btf              # Use BTF only
abicheck dump libfoo.so --dwarf          # Use DWARF only

# Show what's available
abicheck dump vmlinux --show-data-sources
# Output:
#   L1 Debug info: BTF (4523 types), DWARF (not present)

# Compare kernel modules
abicheck compare old/vmlinux new/vmlinux --btf
```

### What this enables (future)

BTF support is a prerequisite for future kernel ABI (kABI/KMI) analysis:
- Compare kernel module interfaces between kernel versions
- KMI whitelist support (filter to stable kernel symbols)
- eBPF program type compatibility checking

These are out of scope for this ADR but become feasible once BTF parsing is in place.

## Consequences

### Positive
- Enables kernel binary analysis (BTF is often the only debug format available)
- BTF parsing is fast (pre-deduplicated, compact)
- `TypeMetadataSource` protocol unifies all debug format readers
- Pure Python — no external dependencies
- Prerequisite for kernel ABI analysis

### Negative
- BTF has limited value for userspace libraries (most have DWARF)
- CTF is very niche (illumos only)
- Need BTF/CTF test binaries for the test suite
- BTF versioning (v1 base, v2 with ENUM64, future extensions) requires maintenance

## Implementation Plan

### BTF (Priority: Medium)

| Phase | Scope | Effort |
|-------|-------|--------|
| 1 | BTF section reader + header/type/string parsing | 2-3 days |
| 2 | Type resolution (struct → StructLayout, enum → EnumInfo) | 2-3 days |
| 3 | Function prototype extraction | 1-2 days |
| 4 | `TypeMetadataSource` protocol + refactor `_diff_dwarf` to use it | 1-2 days |
| 5 | `BtfSnapshotBuilder` (depends on ADR-003) | 3-5 days |
| 6 | CLI `--btf` flag + auto-detection | 1 day |

### CTF (Priority: Low)

| Phase | Scope | Effort |
|-------|-------|--------|
| 1 | CTF v3 section reader | 3-4 days |
| 2 | Type resolution + `CtfMetadata` | 1-2 days |
| 3 | Integration + CLI | 1 day |
