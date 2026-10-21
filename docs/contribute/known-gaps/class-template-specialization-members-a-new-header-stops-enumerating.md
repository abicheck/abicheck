---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Class-template specialization a new header stops enumerating (mostly fixed)

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-21): MOSTLY FIXED.** A template instance whose primary template is still declared in the new header graph is no longer reported as removed. That now covers a class-template specialization the new header stops instantiating too: the specialization reports no `type_removed`, and its implicit constructors, destructor and `operator=`, its inline methods and its static data members report no `inline_function_removed`, `func_removed` or `var_removed`. An export the new binary dropped is `func_visibility_changed` or `var_visibility_changed` (BREAKING); nothing is reported when the new binary still exports the symbol or the old one never did (`compare/template_instance_absence.py`, called from `diff_symbols` and `diff_types`). Still open: an old snapshot that does not record whether the specialization was instantiated from its primary template (clang header backend, pre-v61 snapshot) keeps the old rows.

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

castxml emits an implicit instantiation and an explicit specialization of `Box<double>` with the same attributes, so the record alone cannot say whether deleting it from the new enumeration deleted a declaration. The location can: an implicit instantiation points at the primary template's class-head (`struct Box {`), an explicit or partial specialization at its own (`struct Box<double> {`). The castxml extractor reads the declaring line and records `RecordType.specialization_declared_fact` (schema v61, `extract/headers/castxml/specialization.py`): `PRESENT(False)` for an instantiation, `PRESENT(True)` for a declared specialization, `NOT_COLLECTED` when the line cannot be read or a macro produces the declaration.

For an old specialization recorded `PRESENT(False)`, whose primary class template is still declared in the new header graph (a `type://<template>` node), and which the new snapshot does not enumerate:

| Row | Finding |
|---|---|
| the specialization record | none (no `type_removed`) |
| a synthetic constructor/destructor key (`__abicheck_ctor__ns::Box<double>(…)`, `~ns::Box<double>`) | none: it names no symbol |
| a mangled member (`operator=`, a method, a static data member) | the export-table table above, with `var_visibility_changed` for a variable |

The member's owner is read from its spelling: the synthetic keys carry it, and a mangled member is demangled when its enclosing component carries a template-argument list. Spellings are compared without whitespace (`Box<int *>` against `Box<int*>`); a mismatch makes no claim.

Kept as removals: a deleted explicit or partial specialization (`PRESENT(True)`), a removed class template, a non-template class, a member of a nested template's specialization (`Outer<int>::Inner`), and any record whose origin was not proven.

Verified by `tests/test_template_export_loss_declaration_presence.py` (class-template cells: {header still instantiates, stops instantiating} x three export transitions, judged on every `Box<double>` member by `readelf` FUNC/OBJECT exports, plus explicit-specialization-removed and template-removed controls and a removed non-template class) and `tests/test_class_template_specialization_absence_unit.py` (the full truth table of the three facts, the owner parser against independently built spellings, the detectors on constructed snapshots, and the extractor's line reading). With the rule disabled, the integration run fails exactly the three stops-instantiating cells.

## What remains

1. **Old snapshots without the origin fact.** The clang header backend does not record `specialization_declared_fact`, and a snapshot written before schema v61 has none, so a specialization they hold keeps `type_removed` and its members keep their removal kinds. Re-dumping the old side with castxml closes it.
2. **A class-head the line reading cannot see.** A specialization or primary template whose class-head a macro produces reads `NOT_COLLECTED`, which keeps the removal path: a residual false positive, never a hidden removal.

## Tried and rejected

- **Export presence alone as the removal test.** Treating "the new binary still exports the same name" as "not removed" with no declaration gate silences a plain function the header dropped while the binary still exports it (`ns::bar`). That is a real API removal, so it reports `func_removed` today. The gate on the primary template's declaration is what keeps this case. Pinned by `test_plain_function_dropped_from_header_stays_a_removal`.
- **Demoting a WEAK export loss to RISK by default.** `case217` pins BREAKING without a policy. A default change needs an ADR; the binding policy already expresses the demotion. Not changed here.
- **Demoting on a build fact.** The reverted attempts in [Linkage-blind removal](linkage-blind-removal-attempted-twice-reverted-twice-the.md) apply here too. The rule uses the new export table and the header graph, not a property of the library's build.
