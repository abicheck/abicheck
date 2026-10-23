# ADR-086: A Static Archive Is a `compare --no-baseline` Audit Operand, Read Through Its Own Symbol Index

**Date:** 2026-10-22
**Status:** Accepted (2026-10-23) — implemented (`compare --no-baseline LIB.a -H include/`). Every other command still rejects an archive.

Governed by: [ADR-061](061-responsibility-package-architecture.md) (owner of each stage), [ADR-063](063-one-semantic-pipeline.md) / [ADR-064](064-canonical-gate-algorithm-and-exit-decision.md) (missing evidence narrows conclusions, never fabricates them), [ADR-068](068-one-comparison-product-and-scan-retirement.md) (the `--no-baseline` single-build audit). Amends the G8 "option A" decision recorded on `UC-ARCH-static-lib` (`docs/contribute/usecase-registry.yaml`).

## Context

Libraries such as oneDAL and oneMKL ship static archives (`libfoo.a`, often in lp64/ilp64 variants) beside or instead of shared objects. The question maintainers ask of such a build is the declared-vs-defined one `compare --no-baseline LIB -H include/` already answers for a `.so`: *does every function a public header declares have a definition a consumer can link against, and does the library define symbols no header declares?* (`public_not_exported`, `exported_not_public`).

Until now `resolve_input` rejected every `ar` archive with exit 64, because abicheck compares single linkable images. That stance is right for compatibility comparison: an archive has no SONAME, no dynamic symbol table, no symbol versioning and no visibility boundary, so most of the runtime ABI is not there to observe.

The declared-vs-defined question needs none of that. For a static archive, "exported" means *a global or weak symbol defined by some member object*. That is exactly what the archive's own symbol index lists (`/` and `/SYM64/` for GNU `ar`, `__.SYMDEF*` for BSD/`llvm-ar --format=bsd`). The linker reads the same index to decide which member to pull in. `buildsource/archive_graph.py` already parsed that index for the L5 archive-member graph, with a degrade-to-no-answer rule.

## Decision

### D1. Only the `--no-baseline` audit accepts an archive

`compare --no-baseline LIB.a -H include/` (and the typed `run_no_baseline_compare` path it shares) accepts an `ar` archive. `dump`, two-sided `compare`, and every other command keep rejecting it with exit 64; the rejection message now names the one command that does read an archive. The opt-in is the `static_archive=True` keyword on `workflows.input_resolution.resolve_input`, passed only by `workflows.no_baseline_compare.resolve_no_baseline_candidate`.

Two-sided comparison of archives is out of scope: comparing two indexes would report additions and removals, but with no dynamic section the comparison could not tell an ABI change from a link-model change, and the change is not trivial.

### D2. The export table is the archive's symbol index, and nothing else is read from the archive

`extract/ar_archive.py` owns the index parser (moved from `buildsource/archive_graph.py`, which now imports it; one parser, two consumers). `read_archive_exports` returns the index names and the members' shared ELF identity (`e_machine`, class, data encoding, OSABI). `archive_elf_metadata` turns that into an `ElfMetadata` export table:

- every name in the index is a row, global and weak definitions alike (the index does not distinguish them, and both satisfy a consumer's reference), with `sym_type=NOTYPE` because the index records no type;
- C++ names stay mangled, as in the index, and match header declarations' `mangled` spelling directly;
- `machine` is set, so an indexed archive that defines nothing is a *read, empty* table, not an unread one.

The declared surface is parsed exactly as a header-only dump parses it (`build_header_only_snapshot`), then classified (provenance, dependency scoping, ownership) like any extracted operand. Headers are required; without them there is nothing to audit.

### D3. What the archive cannot answer is an error or `not_collected`, never empty or clean

- **No symbol index** (`ar S`, or a tool that omits it): a usage error (exit 64) whose message says to run `ranlib`. An unindexed archive is no evidence of what it defines, and it is never read as an empty export set (which would report every declaration as `public_not_exported`).
- **Unsupported archives**: a thin archive (members outside the file), Mach-O or COFF/`.lib` members, non-object members (LLVM bitcode), or members for different targets are refused with a reason. Mach-O's leading-underscore convention and COFF import libraries are follow-ons, not silent guesses.
- **Dynamic-section facts** (SONAME, `DT_NEEDED`, symbol versioning, visibility) are not collected. The runtime-only `AbiSnapshot.static_archive` marks the snapshot. The cross-source `unversioned_exported_symbol` check then reports `not_collected` with that reason instead of a clean `skipped`. A run notice (`AbiSnapshot.load_notices` → `coverage_warnings`) names what was not collected.

A symbol with hidden visibility in a member is still listed: inside a static link it is resolvable, so it is a definition the archive offers.

## Consequences

- `compare --no-baseline libfoo.a -H include/` gates CI on `public_not_exported` for archive-only products, with the same findings and exit code as the `.so` audit.
- `exported_not_public` counts every global definition of the archive, internal helpers included. That matches what a static consumer can link against; projects that want it quieter suppress it as for a `.so`.
- `AbiSnapshot.static_archive` is runtime-only and never serialized, so no schema version changes.

## Alternatives considered

- **Walk each member's `.symtab` with pyelftools.** It would give binding and type, but duplicates what the linker's own index already states and costs a full ELF parse per member. Rejected for the first slice; the index is the linker's definition of "defines".
- **Accept archives everywhere.** Rejected: a two-sided or `dump` result would carry an export table with no dynamic section and invite the very false comparisons the original rejection prevents.
