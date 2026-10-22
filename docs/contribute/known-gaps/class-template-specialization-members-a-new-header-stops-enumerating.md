---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Class-template specialization a new header stops enumerating (narrowed)

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-21): NARROWED.** A template instance whose primary template is still declared in the new header graph is no longer reported as removed. The same holds for a class-template specialization the new header stops instantiating. The specialization reports no `type_removed`, and its implicit constructors, destructor and `operator=`, its inline methods and its static data members report no `inline_function_removed`, `func_removed` or `var_removed`. If the new binary dropped an export, the finding is `func_visibility_changed` or `var_visibility_changed` (BREAKING). If the new binary still exports the symbol, or the old one never did, nothing is reported (`compare/template_instance_absence.py`, called from `diff_symbols` and `diff_types`).

The specialization's origin is now also read from the structure of clang's AST, so the rule covers a class-head a macro produces and an instantiation of a partial specialization. A partial specialization's instantiation is not removed while that partial specialization is still declared. A line holding both a primary class-head and an explicit specialization no longer reads as an instantiation; before this change that shape hid a real removal.

Still open:
- an old snapshot that carries neither origin record (written before this change and before schema v61);
- a dump made without clang, where the header graph is declaration-only, so a macro-produced or partial-specialization origin is not proven;
- an explicit instantiation (`template struct Box<double>;`) the new header drops.

Each of these keeps the removal: a residual false positive, never a hidden removal.

## Why the removal was wrong

castxml records a template instantiation only while some header translation unit still instantiates it, or an `extern template` names it. When the new header stops using a template, the new snapshot holds no record for the instantiation. The header-depth removal path read that absence as a removal, so the same declaration was reported as `func_removed` (BREAKING) or `inline_function_removed` (API_BREAK) depending on whether the old build exported it. The primary template was still declared, and the new binary often still exported the symbol.

The reproduction is a free function template, a member function template, and a class-template member, each built against a new library in three ways (`-fvisibility-inlines-hidden`, the template's last caller removed, and `-fvisibility=hidden`), with and without the new header instantiating it. Before the fix, every no-instantiation cell of the three template kinds reported a declaration removal, including the cell where `readelf` still shows the symbol exported and unchanged.

## What the fix decides

For an old instantiation whose primary template is declared once in the new header graph (`decl://<qualified name>#…` nodes of `surface_graph`):

| New export table (`readelf` oracle) | Old build exported it | Finding |
|---|---|---|
| still exported | any | none |
| proven absent | yes | `func_visibility_changed` |
| proven absent | no | none (no symbol to bind to; the declaration remains) |
| proven absent | unknown | the existing path decides |
| unknown (no table read) | any | none from this rule; the existing path decides |

The rule does not apply when the primary is undeclared or declared more than once (an overload set cannot tell which declaration the instance belonged to), for an export-table-only old record, or to any name that is not a named template member (operators, constructors, substitutions, synthetic keys). An `extern template` in the new header never downgrades an export loss: the export loss is reported as `func_visibility_changed`, which is BREAKING, whether the rule or the existing enumerated path produces it.

Verified by `tests/test_template_export_loss_declaration_presence.py` (38 integration tests: 36 matrix cells built with g++ and castxml and judged by `readelf` and the header text, plus 2 controls) and `tests/test_template_export_loss_declaration_presence_unit.py` (the same invariants on constructed snapshots). With the rule disabled, the integration run fails nine cells, the exact no-instantiation template cells.

## Class-template specializations (fixed 2026-10-21)

castxml emits an implicit instantiation and an explicit specialization of `Box<double>` with the same attributes, so the record alone cannot say whether deleting it from the new enumeration deleted a declaration. The location can: an implicit instantiation points at the primary template's class-head (`struct Box {`), an explicit or partial specialization at its own (`struct Box<double> {`). The castxml extractor reads the declaring line and records `RecordType.specialization_declared_fact` (schema v61, `extract/headers/castxml/specialization.py`): `PRESENT(False)` for an instantiation, `PRESENT(True)` for a declared specialization, `NOT_COLLECTED` when the line cannot be read, a macro produces the declaration, or the line spells both a primary and a specialization class-head. (An instantiation of a partial specialization points at the partial specialization's head and reads `PRESENT(True)`; the next section covers it.)

For an old specialization recorded `PRESENT(False)`, whose primary class template is still declared in the new header graph (a `type://<template>` node), and which the new snapshot does not enumerate:

| Row | Finding |
|---|---|
| the specialization record | none (no `type_removed`) |
| a synthetic constructor/destructor key (`__abicheck_ctor__ns::Box<double>(…)`, `~ns::Box<double>`) | none: it names no symbol |
| a mangled member (`operator=`, a method, a static data member) | the export-table table above, with `var_visibility_changed` for a variable |

The member's owner is read from its spelling: the synthetic keys carry it, and a mangled member is demangled when its enclosing component carries a template-argument list. Spellings are compared without whitespace (`Box<int *>` against `Box<int*>`); a mismatch makes no claim.

Kept as removals: a deleted explicit specialization (`PRESENT(True)`), a removed class template, a non-template class, a member of a nested template's specialization (`Outer<int>::Inner`), and any record whose origin was not proven.

Verified by `tests/test_template_export_loss_declaration_presence.py` (class-template cells: {header still instantiates, stops instantiating} x three export transitions, judged on every `Box<double>` member by `readelf` FUNC/OBJECT exports, plus explicit-specialization-removed and template-removed controls and a removed non-template class) and `tests/test_class_template_specialization_absence_unit.py` (the full truth table of the three facts, the owner parser against independently built spellings, the detectors on constructed snapshots, and the extractor's line reading). With the rule disabled, the integration run fails exactly the three stops-instantiating cells.

## The origin from the clang AST and partial specializations (narrowed 2026-10-21)

The castxml line reading has three blind spots. A macro-produced class-head reads as the macro's line. An instantiation of a partial specialization is located at the partial specialization's own class-head (`struct Box<T*> {`), which reads as a declared specialization. A primary class-head and an explicit specialization on one line used to read as the primary, which hid the explicit specialization's removal. That shape now reads `NOT_COLLECTED`.

The header graph's clang AST pass (`buildsource/template_class_origin.py`, which runs in every header dump when clang is available) closes the first two. clang nests an implicit instantiation's complete definition under its template's first `ClassTemplateDecl` and gives it the location of the pattern it was instantiated from (`Sema::InstantiateClass`). An explicit specialization, an explicit instantiation and an `extern template` appear there only as an empty stub. Locations are compared as offsets (`offset`, plus the `spellingLoc`/`expansionLoc` offsets of a macro expansion), never as text. The comparison gives three answers:

- the location is the primary's class-head (any redeclaration) and no partial specialization's: `instantiated_from = primary`;
- the location is exactly one partial specialization's of the same template, and not the primary's: `instantiated_from = partial`, and the instantiation's `DECL_INSTANTIATES_TEMPLATE` edge names `template_decl://<template>#<signature>`;
- otherwise (a stub, an incomplete specialization, a location two patterns share): no answer.

The answer rides on the `template_instantiation` node, and every declared partial specialization keeps its `template_decl://<template>#<signature>` node even when nothing instantiates it. That node is how the comparison knows the partial specialization is still declared.

Either producer can prove the origin. A producer that contradicts the other voids it:

| castxml line | clang graph | Origin | Not removed while the new graph has |
|---|---|---|---|
| primary head | primary or none | primary | `type://<template>` |
| own head | partial | partial | the same `template_decl://<template>#<signature>` |
| undetermined | primary / partial | as the graph says | as above |
| own head | primary | none (contradiction) | n/a: `type_removed` |
| primary head | partial | none (contradiction) | n/a: `type_removed` |
| own head or undetermined | none | none | n/a: `type_removed` |

Kept as removals: a deleted partial specialization, a partial specialization replaced by a different one (`Box<T*>` to `Box<T const*>`), a deleted explicit specialization (including one produced by a macro or sharing the primary's line), a removed template.

The clang header backend records the class template (`ns::P`) and not its specializations, so it never produces a specialization row this rule would have to clear.

Verified by `tests/test_class_template_specialization_origin_integration.py` (24 cells, built with g++ and dumped with castxml and the clang header graph). Each scenario runs under the three export transitions: a macro-produced primary, a partial specialization and a macro-produced one that the new header stops instantiating, a still-instantiated partial specialization, and the four removals above. Each cell is judged by the old pattern's class-head in the *preprocessed* new header (`g++ -E -P`) and by `readelf` exports. On the code before this change, exactly 12 cells fail: the nine stops-instantiating cells and the three cells where an explicit specialization shares the primary's line. `tests/test_class_template_origin_unit.py` covers the AST cases, the cross-TU merge, the fold, and the full decision table of both producers against the new graph.

## What remains

1. **Old snapshots without either origin.** A snapshot written before this change has no graph origin. One written before schema v61 also has no castxml fact. Such a snapshot keeps `type_removed` for a specialization whose class-head castxml could not read, and its members keep their removal kinds. Re-dumping the old side closes it.
2. **No clang at dump time.** Without clang the header graph is declaration-only, so a macro-produced class-head or a partial specialization's instantiation is not proven. For a partial specialization, the *new* side also needs the graph, because the declared-pattern node lives there. The removal is kept.
3. **An explicit instantiation the new header drops.** `template struct Box<double>;` is located at its own line, which castxml reads as the specialization's own head. In clang's AST it is a stub, so its origin is unproven, and its removal is reported even though the primary still declares the type. The AST does not distinguish its detached definition from an explicit specialization's without reading member bodies.
4. **The graph's partial-signature edge join.** The template graph's own `(file, offset)` join that names a class instantiation's `template_decl` pattern is not restricted to one template. That join predates this change. The origin rule never claims on it: an origin needs the same-template match described above, and a disagreement keeps the join and drops the origin.

## Tried and rejected

- **Export presence alone as the removal test.** Treating "the new binary still exports the same name" as "not removed" with no declaration gate silences a plain function the header dropped while the binary still exports it (`ns::bar`). That is a real API removal, so it reports `func_removed` today. The gate on the primary template's declaration is what keeps this case. Pinned by `test_plain_function_dropped_from_header_stays_a_removal`.
- **Demoting a WEAK export loss to RISK by default.** `case217` pins BREAKING without a policy. A default change needs an ADR; the binding policy already expresses the demotion. Not changed here.
- **Demoting on a build fact.** The reverted attempts in [Linkage-blind removal](linkage-blind-removal-attempted-twice-reverted-twice-the.md) apply here too. The rule uses the new export table and the header graph, not a property of the library's build.
