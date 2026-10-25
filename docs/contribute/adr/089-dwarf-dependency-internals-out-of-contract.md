# ADR-089: A DWARF Declaration's File Is Read Correctly, and a Dependency's Unreachable Internals Are Out of Contract

**Date:** 2026-10-23
**Status:** Accepted (2026-10-23) — implemented for binary + DWARF compares without headers.

Governed by: [ADR-061](061-responsibility-package-architecture.md) (owner of each stage), [ADR-062](062-project-snapshot-storage-v2.md) (extractor generation), [ADR-065](065-comparison-scope-selection-and-completeness.md) (absent is not removed), [ADR-067](067-change-intent-acknowledgment-and-disposition-audit.md) (record before disposing).

## Context

A library built against a header-only SIMD dependency (`xvec`-style: templates instantiated inside the library's own TUs) reports tens of `TYPE_ADDED`/`TYPE_REMOVED` findings on the dependency's own instantiations when it is rebuilt with `-mavx2` against `-mavx512f`. The library's real breaks (a struct layout, a mangled parameter) are a handful of findings among them.

Two defects produced this on a binary-plus-DWARF compare:

1. **The declaring file was never read for DWARF 5.** `DW_AT_decl_file` resolution read `entry.name` from the line-program file table. pyelftools exposes DWARF 5 entries as `DW_LNCT_path`/`DW_LNCT_directory_index` and DWARF 4 as `file_entry` with `name`/`dir_index`. The lookup raised and the failure was swallowed, so every DWARF 5 declaration (GCC's default) carried `source_location=None`. Dependency classification keyed on that location could never fire.
2. **Nothing scopes a DWARF-only snapshot.** `dump`'s dependency scope (`resolve_dependency_scope`) returns early unless the snapshot is header-derived. The header path already decides "dependency" by declaring header and "reachable" by a single-hop walk (`dependency_retention`), but no DWARF-only path asks either question.

## Decision

### D1. A DWARF declaration's file is resolved for both layouts

`extract/dwarf_decl_file.py` owns `decl_file_path(die, CU)`. It reads DWARF 4 (`file_entry`, 1-based, directory 0 = compilation directory) and DWARF 5 (`file_names`, 0-based, directory 0 is the table's own first entry), joins a relative directory with the compilation directory, and returns `path` or `path:line`. Index 0 is the "no file" sentinel only before DWARF 5; in DWARF 5 it is the primary source file. `dwarf_snapshot._resolve_decl_file` delegates to it. The extractor generation is bumped to 19 (`storage/versioning.py`; generations 13 to 18 belong to other changes already on main) and its fingerprint re-recorded, because the recorded `source_location` of every DWARF declaration changes for the same input.

Only a **header** declaration records that location. A declaration whose file is a source file (the library's own `.cpp`) keeps no `source_location`, as it did before this ADR. `compare/opaque_types.is_impl_source` treats a type declared in an implementation file as an implementation detail, and a type referenced only through pointers becomes opaque. Recording the `.cpp` would therefore downgrade a real layout change: `catalog/cases/case117_no_unique_address` read `NO_CHANGE` on the first version of this change and reads `BREAKING` with the header-only rule.

### D2. The public surface of a binary-only snapshot is its ELF export table

A DWARF-only snapshot does not carry the exported functions' parameters: the DWARF function DIEs the library defines are declarations the extractor does not keep, so `functions` is empty for them. The exported symbols are the record. `workflows/dump/dwarf_dependency_types.py` demangles each exported C++ symbol and reads its top-level parameter types. A template argument is reached through its template and is not named by the signature. Two spellings are kept per parameter: the full spelling (for exact matching, `basic_vec<float, avx512_tag>`) and the base name (for ownership, `basic_vec`).

### D3. Dependency is the declaring file being outside the library's own roots, or a system header

Own roots are the directories of non-template records that an exported parameter names directly. A library's own declaration (`lib::State` in `lib/api.h`) establishes its directory; a template instance does not, because its declaring file is the template's header. A record is a dependency when its declaring file is a toolchain/system header (`is_dependency_header`) or lies outside every own root. With no own root, nothing is classified: weaker evidence keeps the finding (ADR-050/063).

### D4. A dependency record is reached by a direct name, or it is kept, not dropped

A dependency record is *reached* when:

- an exported parameter names it exactly (full spelling), or
- a kept own record names it in a field, base or signature, through the existing single-hop walk (`dependency_retention._directly_referenced_dependency_names`), with typedef aliases resolved where the snapshot carries a typedef map, or
- an own field spells an **unresolved** by-value alias (a bare name that is no known type and carries no qualifier), in which case its target must have that field's DWARF byte size, so every dependency record of that size is reached; an unresolved pointer or reference could name any dependency type, so every dependency record is reached.

Anything not reached is unreachable. Unreachable names are returned, except a name that is also the name of an own record, which could be either.

### D5. The unreachable presence finding is out of contract, with a reason and a count

`policy/contract_dependency_origin.py` applies the rule after the domain decision and after the toolchain rule, the same shape as `contract_toolchain_origin`. A `TYPE_ADDED` is judged against the new side's unreachable names and a `TYPE_REMOVED` against the old side's; any other kind is left alone. A finding already out of contract, a public root (`public_root_membership`) and a non-entity finding keep their decision. The matched finding becomes `PROVEN_OUT_OF_CONTRACT` with reason `dependency_internal_unreachable`, `COMPLETE` assurance, and does not gate. It is still recorded, so `disposition_audit.scope_reasons` reports the count per reason. The reason is added to `contract_relevance_types.CONTRACT_REASON_CODES`.

Only presence kinds are handled. A size or layout change on the same type is not presence evidence, and stays on its own rule.

Header-derived snapshots are not touched: they are already scoped by `resolve_dependency_scope`, and the rule returns no names for them.

## Consequences

- A binary + DWARF compare without headers reports the library's own changes and its vector-type presence changes that the library's public surface reaches. A dependency's internal template instances and tags read as out of contract, counted under `dependency_internal_unreachable`. On the reproduction (fake header-only dependency, `-mavx2` against `-mavx512f`), the two unreachable presence findings are out of contract and the reached vector type stays in contract; the real break still gates.
- This is a **default change** for DWARF-only compares: a presence finding on an unreachable dependency type no longer gates. It is recorded, not hidden, and `--contract` consumers see it under `scope_reasons`.
- Limits, stated so they are not mistaken for coverage:
  - An exported symbol is read only from its mangled name. A parameter that is a typedef alias of a non-template dependency type is not seen as a name; the size bound covers the field case, not parameters.
  - The DWARF-only fallback (`elf_fallback._build_symbol_only_snapshot`) carries no typedef map by design (its own docstring). So an aliased field is always bounded by size, never resolved to its target.
  - A non-template dependency type named directly by an exported parameter makes its directory an own root. Its whole directory then reads as the library's own. Pinning the own headers with `-H` (a follow-up: thread the header roots into this predicate) would close this.
  - Variables are not read for public names; only functions are.
- Layout kinds on an unreachable dependency type are not handled here (see D5).

## Alternatives considered

- **Drop the unreachable dependency types from the snapshot** (the header path's behaviour). Rejected for this slice: it changes the snapshot and its comparability (`dependency_scope` tags on DWARF-only snapshots, which the comparability gate only checks for header-derived sides), and it would hide the findings, where D5 keeps them visible.
- **Resolve the alias target from the DWARF typedef DIEs.** Not available in the symbol-only path without restoring typedefs there, a design change that needs its own decision.
- **Treat every DWARF-only dependency as out of contract.** Rejected: it would hide a real change to a type the library's public surface reaches.

## Verification

`tests/test_dwarf_decl_file.py` (both DWARF layouts, sentinel and out-of-range), `tests/test_contract_dependency_origin.py` (the rule's judgments per side and kind), and `tests/test_dwarf_dependency_scope_integration.py` (gcc-built fake header-only dependency, with and without an exported vector parameter; the field-alias-only shape exercises the size bound).
