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
| Reconciling two backends' `SemanticIR` (the hybrid merge, ADR-063 Phase 6) | `semantic_ir_merge.py` |
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
type, built-in-file/qualtype/location/deprecation helpers, the
`access_level`/`visibility`/`qualified_name` node-inspection primitives
promoted alongside `functions.py` below, and `RecordVtableIndex` — the
lazily-built, memoized record/specialization/base-lookup/vtable indices
that back clang's own recovery of a keyword-less virtual override). Every
castxml function in these modules takes its `CastxmlParserContext` object
as an explicit parameter rather than reading `self`; clang's `context.py`
helpers are the same shape, but clang's `enums.py::parse_enums` takes the
pre-categorized `_typedefs`/`_enums` decl lists and a constant-expression
evaluator as separate explicit parameters instead of one wrapping context
object, and clang's `functions.py::parse_functions` similarly takes its
categorized `_Decl` list plus a `default_value` evaluator, the exported-
symbol sets, a precomputed `virtual_mangled_names` frozenset, and the
target triple as separate explicit parameters (not a wrapping "parser"
context) — `_ClangAstParser._walk`/`__init__` already produce or compute
each of those once, so there is no per-parser state left for a *second*
context type to hold beyond what `context.py` itself already covers.
`dumper_castxml.py`/`dumper_clang.py` keep every migrated method/
module-level name as a thin delegating wrapper, so every existing caller
(including tests reading a parser's private attributes/methods directly,
e.g. `p._visibility(...)`, `_ClangAstParser._symbol_candidates(...)`, or
importing a module-level name like `_clang_exception_spec` straight off
`dumper_clang`) is unaffected. Both backends' `functions.py` are now the
second entity module split out (after `enums.py`): castxml's moved first;
clang's followed once investigating its three pieces of extra instance
state (`_virtual_mangled_names()`, `_id_index`, `_target_triple`) showed
none of them needed a second, competing context shape — the vtable index
fit `context.py`'s existing "read by more than one entity kind" charter
(record-entity parsing needs the same index), the id-index evaluator took
the same explicit-parameter treatment `enums.py` already established for
its own excluded evaluator, and the target triple turned out to be a
stateless pass-through. `records.py` is now the third entity module split
out **on both backends**. `headers/castxml/records.py` holds
`parse_types`/`build_record_type`/`parse_record_fields`/
`expand_anonymous_field`/`parse_bitfield_bits` plus the vtable/RTTI layout
walk (`build_vtable`/`collect_virtual_methods`/`inherited_vtable_slots`/
`resolved_override_keys`/`vtable_slot_key`), all as free functions taking
`CastxmlParserContext` explicitly. This needed no context-shape change:
`ctx.vtable_slot_root`/`ctx.vtable_slot_extra_roots` already lived on the
context object from the `functions.py` slice above, so `records.py` only
had to move the code that reads and mutates them —
`collect_virtual_methods`/`vtable_slot_key` are the first functions in
this package that mutate shared context state rather than only read it,
proof the "entity module takes context explicitly" shape generalizes past
the read-only case `enums.py`/`functions.py` exercised.

`headers/clang/records.py` followed in the next slice, once investigating
`_ClangAstParser._build_record`/`parse_types` in full (deliberately
deferred by the prior slice pending exactly this investigation) found no
remaining state that didn't already fit either a per-declaration `_Decl`
parameter, an existing `context.py` free function, an already-public
sibling-module helper, or a record-only pure helper with one caller.
`parse_types`/`_build_record`/`_parse_fields`/`_collect_fields`/
`_make_field` moved as free functions taking the categorized `_Decl` lists
plus explicit `evaluate_bitfield_int`/`field_default_value` evaluators
(the same `extract -> compare` layering reason `functions.py`'s
`default_value` and `enums.py`'s `evaluate_int` already take one — the
real evaluators depend on `dumper_clang_expr.py`, which imports
`diff_cxx_rules`, classified `compare`), alongside five record-only
helpers with exactly one caller (`_clang_record_is_final`/
`_bitfield_width`/`_anonymous_member_names`/`_parse_bases`/
`_owned_tag_id`). Two cross-entity-kind findings applied proactively per
this file's own "public-ize in place" rule below (the lesson the prior
castxml `records.py` slice's `is_record_definition` review round
established): `decl_is_public` — read by both record parsing and
constant parsing (`dumper_clang.py`'s still-unmigrated `parse_constants`)
— moved into `context.py` alongside this module, taking the three
`provenance.build_public_set` outputs as explicit parameters per clang's
established context-less convention; and six `dumper_clang_qualifiers.py`
helpers this module needs (`record_kind`, `reduce_opaque_kind_set`,
`clang_record_type_traits`, `clang_record_is_abstract`,
`field_own_cv_source`, `desugared_qualtype`) were still private with
exactly one external caller apiece — public-ized in place, each keeping
its old private spelling as a back-compat alias, rather than physically
relocated.

`templates.py` closes Phase 5 item 1 on both backends. castxml has none: its
XML resolves a specialization to an ordinary `Struct`/`Class` element,
indistinguishable from a non-template record. `headers/clang/templates.py`
holds template-parameter-kind/default/name reconstruction and
specialization-spelling/indexing (`_index_template_param_*`,
`_specialization_spelling`, `build_specialization_index`), moved out of
`dumper_clang_vtable.py` as free functions -- no `parse_templates()` entry
point, since a `ClassTemplateSpecializationDecl` never joins a categorized
`_Decl` list, so `_walk`/`_categorize` stayed put. `_SCOPE_NODE_KINDS` moved
out of `dumper_clang_expr.py` (unimportable here -- pulls in `compare`) into
`templates.py`; `build_specialization_index` takes `is_record_definition` as
an explicit keyword-only parameter rather than importing it back through
`dumper_clang_vtable.py`'s back-compat re-export. See ADR-061 Phase 5.

## Rules that are easy to get wrong

- **A parser produces a fact; it does not decide relevance.** Public/private
  scoping, suppression, and severity are `policy` concerns even when the
  parser has the information to compute them — hand the raw fact upward.
- **Don't reach back into a flat legacy module's private helpers.** A new
  module here imports the *public* surface of an unmigrated sibling (or, if
  none exists, that is itself a sign the shared piece needs its own leaf
  module both sides can depend on — see ADR-061 on `itanium_scope_components`).
- **A migrated module must never import back through its old flat facade**
  (`abicheck.dumper`, `abicheck.service`, `abicheck.cli`) — that reintroduces
  exactly the reverse coupling this package exists to remove.

## Product invariant (local consequence)

Extraction **retains facts with provenance and status**. Not requested,
unavailable on this input, unsupported by this backend, not applicable, and
collected-then-failed are distinct `FactStatus` outcomes; a failed
collection never returns `PRESENT` with an empty value, so a downstream
detector cannot read a failure as an empty surface. Optional evidence
(DWARF, build data, sources) is consumed when present and never becomes a
prerequisite. Root `AGENTS.md` "Product decisions and change routing"
states the rule; ADR-063 Phase 5's fact registry is the mechanism.
