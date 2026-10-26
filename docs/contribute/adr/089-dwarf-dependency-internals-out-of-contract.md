# ADR-089: A DWARF Declaration's File Is Read Correctly, and a Dependency's Unreachable Internals Are Out of Contract

**Date:** 2026-10-23
**Status:** Accepted (2026-10-23) — implemented for binary + DWARF compares without headers; amended 2026-10-25 for compares with headers and 2026-10-26 for the leak finding's sides (see the amendments below).

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

Header-derived snapshots are not touched: they are already scoped by `resolve_dependency_scope`, and the rule returns no names for them. *(Superseded by the 2026-10-25 amendment: that scoping drops only toolchain/system declarations, so a dependency reached through `-I` was never scoped.)*

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

## Amendment (2026-10-25): compares with headers

### Context

D5's last paragraph assumed a header-derived snapshot was already scoped. It is only for toolchain and system headers: `resolve_dependency_scope` keeps every declaration of a header-only dependency reached through `-I`. The field report (intel/xvec, `-H api.h -I xvec/include --build-info`, `-mavx2` against `-mavx512f`) produced 98 findings, about 90 of them xvec's own, all in contract: 68 `HIDDEN_FRIEND_ADDED`/`_REMOVED` on `basic_vec<float, simd_fixed_size_abi<8, x86_avx2_tag>>` against `<16, x86_avx512_tag>`, `VAR_ADDED` for `xvec::simd::detail` tables, `TYPE_ADDED`, `TYPEDEF_REMOVED`/`TYPEDEF_BASE_CHANGED` on `vec_traits` members, `TAG_TYPE_RENAMED`, `OVERLOAD_SET_REROUTED`. The library's own break was `State`'s size, alignment and offset and the vector it holds.

### Decision

**A1. One owner for both paths.** `workflows/dump/dependency_internals.py` answers, per snapshot, a `DependencyInternals` (`policy/contract_dependency_origin.py`): unreachable record names, typedef names, function/variable symbols and overload stems. A DWARF-only snapshot delegates to D2-D4 (`dwarf_dependency_types`, unchanged); a header-derived one is answered there. The rule (D5) and its reason `dependency_internal_unreachable` are reused; no second mechanism.

**A2. Dependency, on the header path, needs both a path and a namespace.** The public headers (`ScopeOrigin.PUBLIC_HEADER`) are the library's. Any other header is placed under its deepest *root*: the recorded include directories (`-I`, `-isystem`, `-iquote`, `-idirafter` in `ast_compile_args`) plus the public headers' own directories. A root holding a public header is the library's; a header under any other root, or under none, is at a dependency path. So `xvec/include` nested under the library's own `-I .` is a dependency root. In addition the declaration's outermost namespace must be one only dependency-path declarations use (read from the linkage name where there is one, since castxml keys implicit members by a synthetic `__abicheck_ctor__ns::C(...)`). A library's own generated or private header outside its public directories keeps its `lib::` entities on path evidence alone. With no public header nothing is classified.

**A3. Reached, per entity.**
- A record is reached by a direct name from a public declaration (D4's single-hop walk, with typedef aliases). `basic_vec<float>` named by `State::acc` (`V`) stays in contract, presence and layout; its template arguments do not.
- A function or variable is the library's only when the library's binary defines it: a `GLOBAL` ELF export (any export on PE/Mach-O, which carry no binding). `WEAK`/`GNU_UNIQUE` exports are COMDAT copies every consumer instantiates itself; one in a dependency-only namespace with no header declaration (`fakesimd::reduce<float>`, emitted by the library's build) is internal too.
- **Hidden friends of a reached dependency record are out of contract.** They are the dependency's API on that type, compiled into every consumer from the dependency's header and not defined by the library. What the library promises about the type (identity, size, layout wherever its declarations use it) keeps gating through the record's own findings. A friend the library exports as `GLOBAL` stays in contract. The same rule applies to a reached record's member typedefs.
- A typedef (member of a dependency record, or in a dependency-only namespace) is reached when its name occurs in a public declaration's signature, field or default, or in the library's own header text. Without that header-text scan (`public_header_identifiers` absent) no typedef is classified.

**A4. The rule's kinds.** Added: judged on the new side; removed: on the old; modified: on both. Records: `TYPE_ADDED`, `TYPE_REMOVED`, `TAG_TYPE_RENAMED` (both names), and `INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API` raised on such a record (either side). *(Superseded by the 2026-10-26 amendment: the leak follows the record's presence, and a record both sides declare must be internal on both.)* Typedefs: `TYPEDEF_REMOVED`, `TYPEDEF_BASE_CHANGED`. Any function- or variable-entity kind on an internal symbol; `OVERLOAD_SET_REROUTED` when the stem's overloads are all internal on both sides. Layout kinds on records stay out of the rule (D5).

**A5. Only a caller's declaration keeps a finding in contract.** D5 skipped any decision with reason `public_root_membership`, assuming it meant "declared public". The header domain stamps that reason on its whole closure, which runs through the dependency's headers, so under the default public contract the rule never fired. The rule now skips only a finding the caller declared public (`--public-symbol`, a post-manifest commitment), the two overlays `contract_evaluation` trusts outright.

### Consequences

- A **default change** for compares with headers, stored or live: an unreachable dependency internal no longer gates. It is recorded, counted in `scope_reasons`. On the field report, 85 of xvec's findings move out of contract (91 counted, with deduplicated ones), and the 6 library-owned layout findings gate as before.
- Limits, stated so they are not mistaken for coverage:
  - Without a recorded include directory (no build data), a dependency vendored under a public header's own directory cannot be told from the library's; it stays in contract.
  - Paths are compared as recorded. A home-prefixed include directory redacted in `ast_compile_args` no longer matches its headers; only the public-header roots are then used.
  - A dependency header declaring into a namespace the library also uses (both in the global namespace, a C dependency) is not classified.
  - A dependency relying on `extern template` with the explicit instantiation in the library's binary exports `WEAK` symbols consumers do need; they read as internal.
  - `INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API` names its subject by display name, not linkage name, and is not matched.

### Verification

`tests/test_header_dependency_internals.py` (an exhaustive layout × relation domain against a truth table: sibling, nested and unrecorded include roots, the library's own generated header; named/unnamed records and typedefs; GLOBAL/WEAK/absent exports; through the policy rule), `tests/test_contract_dependency_origin.py` (each kind's sides), `tests/test_family_f1_evidence_ablation.py::test_dependency_internals_never_grow_under_ablation` (losing the export table, the public-header origin, the header-text scan or a declaration's location never grows any internal set), `tests/test_header_dependency_internals_integration.py` and the field shape `tests/field_shapes/header_only_dependency_internals` (g++ + castxml, stored and live, with and without a library break).

## Amendment (2026-10-26): a leak on a dependency record is judged on the sides that declare it

### Context

A4 moved an `INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API` raised on a record out of contract when the record was a dependency internal on **either** side. That contradicts A4's own rule that a modified finding must be internal on both sides, and it hides a real break: when a release makes a public declaration start naming a dependency record (internal on the old side, reached on the new), the record's layout change now propagates to consumers through that declaration, yet the leak read `dependency_internal_unreachable` and did not gate. The mirror (reached on the old side, internal on the new) is the same: consumers built against the old declaration still see the change.

"Either side" was chosen for the field report's shape. There the leak is raised on a record only one side declares: `detail::vec_traits<x86_avx2_tag, float, 8>` exists in the `-mavx2` build and `vec_traits<x86_avx512_tag, float, 16>` in the `-mavx512f` build, each an internal reached by no public declaration, through the dependency's own implicit members. A plain "both sides" rule puts that leak back in contract (`tests/test_header_dependency_internals_integration.py` fails on it), although no side's public surface reaches the record.

### Decision

**A6. The leak follows the record's presence.** `DependencyInternals` carries `record_names`: every record and enum name the side declares, internal or not, filled by `dependency_internals` for both the DWARF-only and the header path (`None` when a side was not read). The leak is then judged like the record's own presence finding:

- declared only on the new side: internal on the new side (as `TYPE_ADDED`);
- declared only on the old side: internal on the old side (as `TYPE_REMOVED`);
- declared on both: internal on **both**, as every modified finding;
- a side with no presence evidence (`record_names` is `None`) is assumed to declare the record, so the record must be listed internal there. Listing it internal is itself a declaration, and the leak is then judged as above. The only unread side in production, `NO_DEPENDENCY_INTERNALS`, lists nothing, so there the leak stays in contract.

A record internal on one side and reached on the other keeps its leak in contract, and the leak gates.

### Consequences

- A **default change**, narrow: a leak on a dependency record reached on one side and internal on the other gates again. The field report's leaks (records each declared on one side only) stay out of contract; its counts are unchanged.
- `record_names` is presence evidence only. It never moves a finding by itself and does not make `DependencyInternals` truthy, so a side with no internals still skips the rule.

### Verification

`tests/test_contract_dependency_origin.py::test_internal_type_leak_follows_the_record_on_each_side` (every pair of absent / reached / internal / unread, against a hand-written truth table), `tests/test_dependency_record_leak_integration.py` (the corpus shape with a `vec_traits` whose size the library picks and a public function naming it on one side, both directions, stored and live, through the CLI) and the unchanged `tests/test_header_dependency_internals_integration.py`.
