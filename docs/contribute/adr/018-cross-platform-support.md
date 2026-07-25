# ADR-018: Cross-Platform Binary Format Support

**Date:** 2026-03-18
**Status:** Accepted — implemented. **Amendment:** for the current
parser-unit-test vs. native-E2E vs. blocking-vs-experimental-CI maturity
breakdown per platform, see the "Validation status" and "Windows Toolchain
Support Matrix" sections of
[Platform Support](../../reference/platforms.md#windows-toolchain-support-matrix)
rather than treating this ADR's platform tables as current status.
**Decision maker:** Nikolay Petrov

---

## Context

While ABI compatibility checking is most commonly needed for Linux shared
libraries (ELF), the same problem exists for Windows DLLs (PE) and macOS
dylibs (Mach-O). A tool that only supports ELF cannot serve cross-platform
SDK vendors or projects targeting multiple operating systems.

### Requirements

- Support the three major shared library formats: ELF, PE, Mach-O
- Detect binary format automatically from file content (not extension)
- Each platform's specific ABI concepts must be captured (SONAME vs
  compat_version vs DLL exports)
- Debug info support: DWARF (ELF, Mach-O), PDB (PE)
- Pure Python — no platform-specific native dependencies (ADR-001)

---

## Decision

### Independent metadata modules

Each binary format has a dedicated metadata module with no shared base class:

| Module | Format | Library | Metadata class |
|--------|--------|---------|---------------|
| `elf_metadata.py` | ELF | pyelftools | `ElfMetadata` |
| `pe_metadata.py` | PE/COFF | pefile | `PeMetadata` |
| `macho_metadata.py` | Mach-O | macholib | `MachoMetadata` |

**No shared base class** was chosen because the three formats have
fundamentally different concepts:

| Concept | ELF | PE | Mach-O |
|---------|-----|-----|--------|
| Library identity | SONAME | DLL name | install_name |
| Versioning | Symbol versioning (.gnu.version) | File/product version | compat_version / current_version |
| Dependencies | DT_NEEDED | Import table | LC_LOAD_DYLIB |
| Export mechanism | .dynsym (STV_DEFAULT) | Export table (ordinals) | LC_DYLD_INFO exports trie |
| Symbol binding | GLOBAL / WEAK / LOCAL | Exported / forwarded | External / weak |

Forcing these into a common base would require lossy abstraction. Instead,
each module captures platform-native concepts in full fidelity, and the
checker has platform-specific detectors.

### Platform detection

Format detection uses magic bytes, not file extensions:

```python
def detect_platform(path: str) -> str:
    # ELF: magic \x7fELF
    # PE:  magic MZ (DOS stub) + PE\0\0 signature
    # Mach-O: magic 0xFEEDFACE / 0xFEEDFACF / 0xCAFEBABE (fat)
```

This handles cases where libraries have non-standard extensions or no
extension at all.

### Platform-specific ChangeKinds

| ChangeKind | Platform | Rationale |
|------------|----------|-----------|
| `SONAME_CHANGED` | ELF | Dynamic linker looks up by SONAME |
| `SONAME_MISSING` | ELF | No SONAME = version tracking failure |
| `COMPAT_VERSION_CHANGED` | Mach-O | Equivalent to SONAME for dylib compatibility |
| `NEEDED_ADDED` / `NEEDED_REMOVED` | ELF | DT_NEEDED dependency changes |
| `RPATH_CHANGED` / `RUNPATH_CHANGED` | ELF | Library search path changes |
| `SYMBOL_VERSION_*` | ELF | GNU symbol versioning (.gnu.version_d / .gnu.version_r) |
| `SYMBOL_BINDING_*` | ELF | GLOBAL / WEAK binding changes |

PE and Mach-O have fewer platform-specific ChangeKinds because their
versioning and dependency models are simpler. Type-level checks (struct
layout, function signatures) work identically across platforms via the
shared `AbiSnapshot` model.

### Debug info: PDB support

Windows PE binaries use PDB (Program Database) files for debug information
instead of DWARF. A custom PDB parser was implemented:

| Module | Role |
|--------|------|
| `pdb_parser.py` | MSF container parser + TPI/DBI stream reader |
| `pdb_metadata.py` | PDB → `DwarfMetadata` adapter |

**Custom parser rationale**: No suitable pure-Python PDB library exists.
The alternatives are:

- `pdbparse` (PyPI) — unmaintained, GPL licensed, incomplete TPI support
- `llvm-pdbutil` — requires LLVM installation (~500MB)
- `dia2dump` / DIA SDK — Windows-only COM interface

The custom parser reads the MSF (Multi-Stream File) container format, then
parses the TPI (Type Information) stream to extract struct layouts, enum
definitions, and function prototypes. It handles CodeView leaf types:
`LF_STRUCTURE`, `LF_CLASS`, `LF_UNION`, `LF_ENUM`, `LF_MEMBER`,
`LF_PROCEDURE`, `LF_MFUNCTION`, etc.

**PDB → DWARF adapter**: `pdb_metadata.py` converts PDB type records into
the same `StructLayout`, `FieldInfo`, and `EnumInfo` structures used by the
DWARF metadata module. This allows the checker's DWARF detectors to operate
on PDB data without modification.

**Scope**: The PDB parser is intentionally minimal:

- **In scope**: Type information (struct layouts, enum definitions, function
  signatures) needed for ABI comparison
- **Out of scope**: Source line info, local variables, optimization flags,
  PDB-specific debug optimizations, edit-and-continue streams
- **Rationale**: Minimizing scope reduces maintenance burden while covering
  the type-level ABI checks that matter for compatibility

PDB (Program Database) is the Windows equivalent of ELF DWARF debug
sections — it contains type information and function prototypes needed for
ABI analysis. DBI (Debug Information) stream parsing is limited to what's
needed for function prototype extraction.

### Reference specifications

| Format | Specification source |
|--------|---------------------|
| PDB MSF container | LLVM PDB documentation |
| CodeView type records | `cvinfo.h` (MIT licensed, Microsoft) |
| TPI stream | `microsoft/microsoft-pdb` GitHub repository |

### Snapshot model integration

All platform-specific metadata is optional in `AbiSnapshot`:

```python
@dataclass
class AbiSnapshot:
    elf: ElfMetadata | None     # ELF-specific
    pe: PeMetadata | None       # PE-specific
    macho: MachoMetadata | None # Mach-O-specific
    platform: str | None        # "elf" | "pe" | "macho"
```

The checker detects which platform metadata is present and runs the
appropriate platform-specific detectors. Cross-platform comparison (e.g.,
ELF vs PE) is not supported — both snapshots must be the same format.
Attempting a cross-platform comparison produces a clear error:
`"platforms must match (elf vs pe)"`.

---

## Consequences

### Positive

- Single tool for multi-platform SDK vendors
- Pure Python — no platform-specific build dependencies
- Custom PDB parser avoids GPL/LGPL licensing issues and LLVM dependency
- Platform detection from magic bytes handles non-standard file naming
- DWARF detector code reused for PDB data via adapter pattern

### Negative

- Three metadata modules with no shared interface increases code surface
- Custom PDB parser is maintenance burden (but minimal scope)
- PDB support is less mature than ELF/DWARF support
- Mach-O support relies on macholib which has a smaller user base than
  pyelftools
- Platform-specific ChangeKinds (ELF-heavy) create an asymmetric feature set

---

## References

- `abicheck/elf_metadata.py` — ELF metadata extraction (pyelftools)
- `abicheck/pe_metadata.py` — PE metadata extraction (pefile)
- `abicheck/macho_metadata.py` — Mach-O metadata extraction (macholib)
- `abicheck/pdb_parser.py` — PDB container/TPI/DBI parser
- `abicheck/pdb_metadata.py` — PDB → DwarfMetadata adapter
- `abicheck/model.py` — `AbiSnapshot` platform fields
- ADR-001 — Technology stack (library choices)
