# AGENTS.md — `abicheck/extract/`

## Purpose

This package owns reading binary, debug, header, build, and source evidence
into the facts `abicheck.model` defines, per ADR-061 D1. It answers "what
does this input actually contain" — never whether a fact matters, whether
two facts differ, how a comparison is decided, or how a result is rendered.

The package is new and mostly empty: it is the target owner for a large set
of still-flat modules (`elf_metadata.py`, `dwarf_metadata.py`,
`dumper_castxml.py`/`dumper_clang.py` and their siblings, most of
`buildsource/`, …) recorded as `extract`-targeted `legacy_paths` in
`architecture/modules.yaml`, most of which have not physically moved yet.
Per ADR-061 D2, a subdirectory here is created only once real implementation
and its tests move into it — this file does not describe empty scaffolding.

## Permitted imports

Per ADR-061 D1, `extract/` may import first-party only `model` and
`storage`, plus the flat leaf modules classified `extract` in
`architecture/modules.yaml`. Importing `compare`, `policy`, `workflows`,
`report`, or `frontends` — severity, suppression, gate decisions, or any
user-facing output concern — is a defect, not a case for an exception list;
`scripts/check_architecture.py` enforces it for every module physically
inside this directory.

## Where a change goes

| Change | Module |
|---|---|
| Header-AST parser backend (castxml or clang), split by parsed entity or parser-state responsibility per ADR-061 D9 | `headers/castxml/`, `headers/clang/` |
| ELF/PE/Mach-O binary parsing | `binary/` (not yet created — still `elf_metadata.py`, `pe_metadata.py`, `macho_metadata.py`) |
| DWARF/PDB debug-info parsing | `debug/` (not yet created) |
| compile-commands/CMake/Bazel/Make build evidence | `build/` (not yet created) |
| Source-graph construction, source-ABI replay, provenance | `source/` (not yet created; see ADR-061 Phase 5 item 2) |

`headers/castxml/names.py` was the first tenant: vtable-index, mangled-name,
and synthetic-key helpers moved out of the still-flat `dumper_castxml.py`,
which imports and re-exports them so every existing caller is unaffected.
The shared-context design is now real on both backends, and `enums.py` is
the first entity module split out of each: `headers/castxml/context.py`
(the id-map, tag-grouped element lists, and memoization caches
`_CastxmlParser` used to carry directly on `self`), `location.py`
(built-in-origin/source-location resolution), and `type_resolution.py`
(the full type-graph walk — spelling, pointer depth, alignment, cv/restrict
qualification); `headers/clang/context.py` (the `_Decl` categorized-node
type plus built-in-file/qualtype/location/deprecation helpers). Every
castxml function in these modules takes its `CastxmlParserContext` object
as an explicit parameter rather than reading `self`; clang's `context.py`
helpers are the same shape, but clang's `enums.py::parse_enums` takes the
pre-categorized `_typedefs`/`_enums` decl lists and a constant-expression
evaluator as separate explicit parameters instead of one wrapping context
object — `_ClangAstParser._walk` already produces those lists once, so
there is no per-parser state left for a context type to hold beyond what
`context.py` itself covers. `dumper_castxml.py`/`dumper_clang.py`
keep every migrated method/module-level name as a thin delegating wrapper,
so every existing caller (including tests reading a parser's private
attributes) is unaffected. `functions.py`/`records.py`/`templates.py` on
both backends have not moved yet — see ADR-061's own "Phase 5" section for
exactly what's still on the monolithic parser classes and why.

## Rules that are easy to get wrong

- **A parser produces a fact; it does not decide relevance.** Public/private
  scoping, suppression, and severity are `policy` concerns even when the
  parser has the information to compute them — hand the raw fact upward.
- **Don't reach back into a flat legacy module's private helpers.** A new
  module here imports the *public* surface of an unmigrated sibling (or, if
  none exists, that is itself a sign the shared piece needs its own leaf
  module both sides can depend on — see ADR-061's own account of
  `itanium_scope_components` for the pattern to avoid).
- **A migrated module must never import back through its old flat facade**
  (`abicheck.dumper`, `abicheck.service`, `abicheck.cli`) — that reintroduces
  exactly the reverse coupling this package exists to remove.
